from __future__ import annotations

"""Lightweight classification service (local, no network).

Exposes helpers to classify an entire PDF using a JSON config of
families and labels (regex patterns with optional weights/must/avoid).

This is a service-only module (no Streamlit/UI) and does not reference
testing_files — it uses an app-bundled default config under app/resources.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import fitz  # PyMuPDF


# ------------------------------
# Config loading
# ------------------------------

def get_default_config_path() -> Path:
    """Return the app-bundled classifier config path (absolute, cross-platform).

    Resolved relative to this file so it works regardless of CWD or OS:
    app/services/classify_service.py -> app/resources/classifyier_regex/forms_config.json
    """
    app_dir = Path(__file__).resolve().parents[1]  # .../app
    return app_dir / "resources" / "classifyier_regex" / "forms_config.json"


def load_config(config_path: str | Path | None = None) -> Dict[str, Any]:
    """Load classifier config JSON; falls back to bundled default."""
    if config_path is None:
        config_path = get_default_config_path()
    p = Path(config_path)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------
# Core helpers (mirrors classifyV2 logic)
# ------------------------------

def _norm(s: str) -> str:
    """Normalize text: NFKC + dash fix + collapse whitespace."""
    import unicodedata

    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u2014", "-").replace("\u2013", "-")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _extract_sections(page: "fitz.Page") -> Dict[str, str]:
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


def _compile_rules(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Compile items into rules with pos/must/avoid and a base boost."""
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


def _score_best(texts: List[Tuple[str, float]], rules: Dict[str, Dict[str, Any]]) -> str | None:
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
            for ax in avoid:
                if ax.search(content):
                    any_avoid = True
            for i, mx in enumerate(must):
                if not must_hit[i] and mx.search(content):
                    must_hit[i] = True
            for rx, rw in pos:
                if rx.search(content):
                    total += max(0.1, float(w)) * float(rw)

        if any_avoid:
            continue
        if must and not all(must_hit.values()):
            continue
        if total > 0:
            scores[name] = total

    if not scores:
        return None
    best = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return best[0]


def _apply_smoothing(rows: List[dict]) -> None:
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


def _norm_label_name(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    s = s.replace("-", "_")
    s = re.sub(r"__+", "_", s)
    return s


def classify_document(pdf_path: Path, config: Dict[str, Any]) -> List[dict]:
    """Classify all pages of a PDF and return rows [{page,family,label}]."""
    fam_rules = _compile_rules(config.get("families", []))
    label_defs: List[Dict[str, Any]] = config.get("labels", [])
    lbl_rules = _compile_rules(label_defs)
    family_of_label: Dict[str, str] = {ld["label"]: (ld.get("family") or "Other") for ld in label_defs}
    neutral_labels = {ld["label"] for ld in label_defs if (ld.get("family") or "Other") == "Other"}
    labels = list(lbl_rules.keys())
    weights = config.get("weights", {"header": 1.4, "body": 1.0, "footer": 1.2})

    rows: List[dict] = []
    with fitz.open(pdf_path) as doc:
        for p in range(doc.page_count):
            page_num = p + 1
            try:
                sections = _extract_sections(doc.load_page(p))
            except Exception:
                sections = {"header": "", "body": "", "footer": ""}

            texts = [
                (sections.get("header", ""), float(weights.get("header", 1.4))),
                (sections.get("body", ""), float(weights.get("body", 1.0))),
                (sections.get("footer", ""), float(weights.get("footer", 1.2))),
            ]

            fam = _score_best(texts, fam_rules) or "Other"
            if fam == "Other":
                allowed_labels = sorted(neutral_labels)
            else:
                # Match CLI classifier behavior: include family labels + neutrals
                allowed_labels = sorted({l for l in labels if family_of_label.get(l) == fam} | neutral_labels)
            allowed_label_rules = {k: v for k, v in lbl_rules.items() if k in allowed_labels}
            lbl = _score_best(texts, allowed_label_rules) or "Other"

            rows.append({
                "page": page_num,
                "predicted_family": fam,
                "predicted_label": _norm_label_name(lbl),
            })

    if bool(config.get("smoothing", {}).get("enable", True)):
        _apply_smoothing(rows)

    return rows


def classify_to_result(pdf_path: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a single JSONable result object for the whole document."""
    rows = classify_document(pdf_path, config)
    return {
        "document": Path(pdf_path).name,
        "config": config.get("name") or "embedded",
        "total_pages": len(rows),
        "pages": [
            {"page": r["page"], "family": r["predicted_family"], "label": r["predicted_label"]}
            for r in rows
        ],
        "smoothing_applied": bool(config.get("smoothing", {}).get("enable", True)),
    }
