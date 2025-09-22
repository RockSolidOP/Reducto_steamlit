#!/usr/bin/env python3
"""
Simplified per-page classifier driven by a single JSON config.

- Config JSON defines families (coarse classes) and labels with regex patterns.
- Two-stage: family first, then label within family. Neutral 'Other' labels always allowed.
- Lightweight header/body/footer extraction and weighted scoring.
- Optional document-level smoothing to map multi-page forms to Pg1/Pg2.

Usage:
  python classify_from_json.py \
    --json forms_config.json \
    --pdf /path/to/file.pdf \
    --out predictions.csv \
    [--threshold 0.6] [--debug-pages 16,18-19] [--use-reducto]
"""

from __future__ import annotations
import json, re, argparse, os, unicodedata, csv
from pathlib import Path
from typing import Dict, List, Tuple, Any
import fitz # PyMuPDF


def _norm(s: str) -> str:
    """Normalize text: NFKC + fix dashes + collapse whitespace."""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u2014", "-").replace("\u2013", "-")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def extract_sections(page: "fitz.Page") -> Dict[str, str]:
    """Return header/body/footer text using simple Y-cut thresholds."""
    body_raw = page.get_text("text") or ""
    body = _norm(body_raw)
    try:
        page_h = float(page.rect.height)
        hdr_cut = page_h * 0.20
        ftr_cut = page_h * 0.85
        d = page.get_text("dict")
        header_spans: List[str] = []
        footer_spans: List[str] = []
        for block in d.get("blocks", []) or []:
            for line in block.get("lines", []) or []:
                for span in line.get("spans", []) or []:
                    _, y0, _, y1 = (span.get("bbox") or [0, 0, 0, 0])
                    t = span.get("text") or ""
                    if not t.strip():
                        continue
                    if y0 <= hdr_cut:
                        header_spans.append(t)
                    if y1 >= ftr_cut:
                        footer_spans.append(t)
        header = _norm(" ".join(header_spans))
        footer = _norm(" ".join(footer_spans))
    except Exception:
        header = ""
        footer = ""
    return {"header": header, "body": body, "footer": footer}


def _compile_list(value: Any) -> List[Tuple[re.Pattern, float]]:
    """Compile regex specs (str or {re,w}) into (pattern, weight) tuples."""
    out: List[Tuple[re.Pattern, float]] = []
    for entry in value or []:
        if isinstance(entry, str):
            out.append((re.compile(entry, re.I), 1.0))
        elif isinstance(entry, dict):
            pat = entry.get("re") or entry.get("pattern") or ""
            w = float(entry.get("w", 1.0))
            if pat:
                out.append((re.compile(pat, re.I), w))
    return out


def compile_rules(items: List[Dict]) -> Dict[str, Dict[str, Any]]:
    """Compile items into rules with pos/must/avoid and a base boost.

    Rule format per name:
      {
        'pos': [(regex, weight)],
        'must': [regex, ...],
        'avoid': [regex, ...],
        'boost': float,
        'family': str | None,
      }
    """
    out: Dict[str, Dict[str, Any]] = {}
    for it in items:
        name = it.get("name") or it.get("label")
        if not name:
            continue
        rule: Dict[str, Any] = {
            "pos": _compile_list(it.get("patterns") or []),
            "must": [rx for rx, _ in _compile_list(it.get("must") or [])],
            "avoid": [rx for rx, _ in _compile_list(it.get("avoid") or [])],
            "boost": float(it.get("boost", 0.0)),
        }
        fam = it.get("family")
        if fam:
            rule["family"] = fam
        out[name] = rule
    return out


def score_items(texts: List[Tuple[str, float]], rules: Dict[str, Dict[str, Any]]) -> str | None:
    """Pick best rule name via weighted regex matches; no scores returned."""
    scores: Dict[str, float] = {}
    for name, rule in rules.items():
        pos: List[Tuple[re.Pattern, float]] = rule.get("pos", [])
        must: List[re.Pattern] = rule.get("must", [])
        avoid: List[re.Pattern] = rule.get("avoid", [])
        boost: float = float(rule.get("boost", 0.0))

        any_avoid = False
        must_hit = {i: False for i in range(len(must))}
        total = boost
        for content, w in texts:
            if not content:
                continue
            # avoid
            for ax in avoid:
                if ax.search(content):
                    any_avoid = True
            # must
            for i, mx in enumerate(must):
                if not must_hit[i] and mx.search(content):
                    must_hit[i] = True
            # positives
            for rx, rw in pos:
                if rx.search(content):
                    total += max(0.1, float(w)) * float(rw)

        if any_avoid:
            continue  # disqualify
        if must and not all(must_hit.values()):
            continue  # does not meet must conditions
        if total > 0:
            scores[name] = total

    if not scores:
        return None
    best = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return best[0]


def apply_smoothing(rows: List[dict]) -> None:
    """Assign Pg1/Pg2/etc. by page order for common multi-page forms."""
    by_family: Dict[str, List[int]] = {}
    for i, r in enumerate(rows):
        fam = r.get("predicted_family") or ""
        by_family.setdefault(fam, []).append(i)

    # 1040 main
    idx = [i for i in by_family.get("1040", []) if str(rows[i].get("predicted_label", "")).startswith("1040_Main")]
    if len(idx) >= 2:
        idx = sorted(idx, key=lambda i: rows[i]["page"])[:2]
        rows[idx[0]]["predicted_label"] = "1040_Main_Pg1"
        rows[idx[1]]["predicted_label"] = "1040_Main_Pg2"

    # Schedule E
    idx = [i for i in range(len(rows)) if str(rows[i].get("predicted_label", "")).startswith("1040_Schedule_E")]
    if len(idx) >= 2:
        idx = sorted(idx, key=lambda i: rows[i]["page"])[:2]
        rows[idx[0]]["predicted_label"] = "1040_Schedule_E"
        rows[idx[1]]["predicted_label"] = "1040_Schedule_E_PG_2"

    # 8582
    idx = by_family.get("Form 8582", [])
    if len(idx) >= 2:
        idx = sorted(idx, key=lambda i: rows[i]["page"])[:2]
        rows[idx[0]]["predicted_label"] = "Form_8582_Pg_1"
        rows[idx[1]]["predicted_label"] = "Form_8582_Pg_2"

    # 5329
    idx = by_family.get("Form 5329", [])
    if len(idx) >= 2:
        idx = sorted(idx, key=lambda i: rows[i]["page"])[:3]
        mapping = ["Form_5329_Pg_1", "Form_5329_Pg_2", "Form_5329_Pg_3"]
        for j, k in enumerate(idx):
            rows[k]["predicted_label"] = mapping[j]


def main():
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--json", default=str(here / "forms_config.json"), help="Path to forms config JSON")
    # Default to the project's sample PDF if not provided
    ap.add_argument(
        "--pdf",
        default="/Users/aaditya/Documents/Projects/Reducto_steamlit/testing_files/sample_multiple.pdf",
        help="Path to multi-page PDF",
    )
    ap.add_argument("--out", default=str(here / "predictions.csv"), help="Output CSV path")
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--debug-pages", default="")
    ap.add_argument("--use-reducto", action="store_true")
    args = ap.parse_args()

    cfg_path = Path(args.json)
    if not cfg_path.exists():
        raise FileNotFoundError(cfg_path)
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Compile patterns
    family_defs = cfg.get("families", [])
    label_defs = cfg.get("labels", [])
    weights = cfg.get("weights", {"header": 1.4, "body": 1.0, "footer": 1.2})
    smoothing_enabled = bool(cfg.get("smoothing", {}).get("enable", True))

    fam_rules = compile_rules(family_defs)
    lbl_rules = compile_rules(label_defs)
    family_of_label: Dict[str, str] = {ld["label"]: (ld.get("family") or "Other") for ld in label_defs}
    neutral_labels = {ld["label"] for ld in label_defs if (ld.get("family") or "Other") == "Other"}
    labels = list(lbl_rules.keys())

    # Optional Reducto client
    reducto_client = None
    if args.use_reducto and os.getenv("REDUCTO_API_KEY"):
        try:
            from app.services.reducto_service import create_client  # type: ignore
            reducto_client = create_client()
        except Exception as e:
            print(f"[warn] Reducto disabled: {e}")

    # Parse debug pages
    debug_pages: set[int] = set()
    if args.debug_pages:
        for part in str(args.debug_pages).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                try:
                    lo = int(a); hi = int(b)
                except Exception:
                    continue
                for i in range(min(lo, hi), max(lo, hi) + 1):
                    debug_pages.add(i)
            else:
                try:
                    debug_pages.add(int(part))
                except Exception:
                    pass

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    rows: List[dict] = []
    with fitz.open(pdf_path) as doc:
        for p in range(doc.page_count):
            page_num = p + 1
            try:
                sections = extract_sections(doc.load_page(p))
            except Exception:
                sections = {"header": "", "body": "", "footer": ""}

            texts = [
                (sections.get("header", ""), float(weights.get("header", 1.4))),
                (sections.get("body", ""), float(weights.get("body", 1.0))),
                (sections.get("footer", ""), float(weights.get("footer", 1.2))),
            ]

            # Stage 1: family
            fam = score_items(texts, fam_rules)

            # Stage 2: labels restricted by family (allow neutrals always)
            if fam is None:
                fam = "Other"
            if fam == "Other":
                allowed_labels = sorted(neutral_labels)
            else:
                allowed_labels = sorted({l for l in labels if family_of_label.get(l) == fam} | neutral_labels)
            allowed_label_rules = {k: v for k, v in lbl_rules.items() if k in allowed_labels}
            lbl = score_items(texts, allowed_label_rules)
            if lbl is None:
                lbl = "Other"

            if page_num in debug_pages:
                print(f"--- DEBUG Page {page_num}")
                print("Header:", sections.get("header", "")[:220])
                print("Footer:", sections.get("footer", "")[:220])

            # Normalize predicted label format (e.g., 'Form 8582 Pg 2' -> 'Form_8582_Pg_2')
            def norm_label_name(s: str) -> str:
                s = s.strip()
                s = re.sub(r"\s+", "_", s)
                s = s.replace("-", "_")
                s = re.sub(r"__+", "_", s)
                return s

            rows.append({
                "page": page_num,
                "predicted_family": fam,
                "predicted_label": norm_label_name(lbl),
            })

    if smoothing_enabled:
        apply_smoothing(rows)

    out_path = Path(args.out)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["page", "predicted_family", "predicted_label"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
