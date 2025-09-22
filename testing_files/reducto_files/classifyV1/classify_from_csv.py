
#!/usr/bin/env python3
"""
Per-page classifier using a CSV of labels + descriptions.
- Inputs: --csv <labels.csv> --pdf <multi_page.pdf> [--out <preds.csv>] [--threshold 0.6]
- Behavior: Heuristic regex pass built from descriptions. If REDUCTO_API_KEY is set and --use-reducto is passed,
  falls back to Reducto OCR extraction when heuristics don't match strongly.
- Output: CSV with page, predicted_label, confidence, method
"""

from __future__ import annotations
import os, re, csv, json, argparse, unicodedata
from typing import List, Tuple, Dict
from pathlib import Path

try:
    import fitz  # PyMuPDF
except Exception as e:
    raise RuntimeError("PyMuPDF (fitz) is required. pip install pymupdf") from e

# Optional Reducto fallback
USE_REDUCTO = False
try:
    from reducto import ReductoError  # noqa: F401
    # create_client must be resolvable by user in their environment; here we duck-type call if available.
except Exception:
    pass


def read_labels(csv_path: str) -> Tuple[List[str], Dict[str, str]]:
    labels: List[str] = []
    desc: Dict[str, str] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            label = (row.get("label") or row.get("label_or_form_type") or "").strip()
            d = (row.get("description") or "").strip()
            if not label:
                continue
            if label not in desc:
                labels.append(label)
                desc[label] = d if d else "Generic/non-form content."
    # keep 'Other' last for readability
    if "Other" in labels:
        labels = [x for x in labels if x != "Other"] + ["Other"]
    return labels, desc


def label_to_family(label: str) -> str:
    l = label or ""
    if l == "Other":
        return "Other"
    if l.startswith("1040_") or re.search(r"\bForm\s*1040\b|\b1040-?SR\b|\b\(\s*Form\s*1040\s*\)", l, re.I):
        return "1040"
    if re.search(r"\bFinCEN\s*114\b", l, re.I):
        return "FinCEN 114"
    if re.search(r"\b114a\b", l, re.I):
        return "114a"
    if l.startswith("Form 5329"):
        return "Form 5329"
    if l.startswith("Form 6251"):
        return "Form 6251"
    if l.startswith("Form 6252"):
        return "Form 6252"
    if l.startswith("Form 8582"):
        return "Form 8582"
    # default fallback based on common tokens
    for key in ("6251", "6252", "5329", "8582"):
        if key in l:
            return f"Form {key}"
    return "Other"


def build_family_patterns(labels: List[str], desc_by: Dict[str, str]) -> List[Tuple[re.Pattern, str, float]]:
    """Coarse family patterns: 1040, FinCEN 114, 114a, Form 5329/6251/6252/8582, Other."""
    families = sorted({label_to_family(lbl) for lbl in labels if lbl != "Other"})
    pats: List[Tuple[re.Pattern, str, float]] = []

    def add(rx: str, fam: str, conf: float):
        pats.append((re.compile(rx, re.I), fam, conf))

    # Treat generic Filing Instructions and letters as non-form
    add(r"\bFiling\s+Instructions\b", "Other", 0.99)
    add(r"\bDear\b|\bThis\s+letter\b|\bNotice\s+(?:CP|LT|LTR)?\-?\d+\b", "Other", 0.92)

    if "1040" in families:
        # Prefer strong 1040 signals; avoid picking 1040 family on casual mentions in letters
        add(r"U\.?S\.?\s+Individual\s+Income\s+Tax\s+Return|\bSchedule\s*(?:A|1|E|F|C)\s*\(\s*Form\s*1040\s*\)", "1040", 0.9)
    if "FinCEN 114" in families:
        add(r"\bFinCEN\s*114\b", "FinCEN 114", 0.95)
    if "114a" in families:
        add(r"\bForm\s*114a\b|Record\s+of\s+Authorization\s+to\s+Electronically\s+File\s+FBARs", "114a", 0.95)
    if "Form 5329" in families:
        add(r"\bForm\s*5329\b", "Form 5329", 0.95)
    if "Form 6251" in families:
        add(r"\bForm\s*6251\b|Alternative\s+Minimum\s+Tax", "Form 6251", 0.95)
    if "Form 6252" in families:
        add(r"\bForm\s*6252\b|Installment\s+Sale\s+Income", "Form 6252", 0.95)
    if "Form 8582" in families:
        add(r"\bForm\s*8582\b|Passive\s+Activity\s+Loss\s+Limitations", "Form 8582", 0.95)

    return pats


def build_patterns(desc_by: Dict[str, str]) -> List[Tuple[re.Pattern, str, float]]:
    """Build ordered (regex, label, confidence) patterns.
    Priority: page/schedule specific > exact form numbers > generic.
    """
    patterns: List[Tuple[re.Pattern, str, float]] = []

    def add(rx: str, lbl: str, conf: float):
        patterns.append((re.compile(rx, re.I), lbl, conf))

    # First, inject high-precision rules for known labels irrespective of description text
    # 1040 main pages with explicit page anchors
    if "1040_Main_Pg1" in desc_by:
        # Require the official header to avoid matching schedules
        add(r"U\.?S\.?\s+Individual\s+Income\s+Tax\s+Return.*?Form\s*1040(?:-SR)?\b.*?Page\s*1\b|Form\s*1040(?:-SR)?\b.*?U\.?S\.?\s+Individual\s+Income\s+Tax\s+Return.*?Page\s*1\b", "1040_Main_Pg1", 0.99)
        # Supporting anchors
        add(r"\bFiling\s+Status\b|\bStandard\s+Deduction\b|\bDependents\b|Your\s+first\s+name\b|Presidential\s+Election\s+Campaign", "1040_Main_Pg1", 0.93)
    if "1040_Main_Pg2" in desc_by:
        add(r"U\.?S\.?\s+Individual\s+Income\s+Tax\s+Return.*?Form\s*1040(?:-SR)?\b.*?Page\s*2\b|Form\s*1040(?:-SR)?\b.*?U\.?S\.?\s+Individual\s+Income\s+Tax\s+Return.*?Page\s*2\b", "1040_Main_Pg2", 0.99)
        add(r"\bRefund\b|\bAmount\s+you\s+owe\b|\bDirect\s+deposit\b|\bRouting\s+number\b|\bSign\s+Here\b|Third\s+party\s+designee|Paid\s+Preparer\s+Use\s+Only", "1040_Main_Pg2", 0.94)

    # Schedule pages: prefer explicit "Schedule X (Form 1040)"
    sched_map = {
        "1040_Schedule_1": (r"\bSchedule\s*1\s*\(\s*Form\s*1040\s*\)\b", 0.98),
        "1040_Schedule_A": (r"\bSCHEDULE\s*A\b.*\bItemized\s+Deductions\b", 0.97),
        "1040_Schedule_C": (r"\bSchedule\s*C\s*\(\s*Form\s*1040\s*\)\b", 0.97),
        "1040_Schedule_E": (r"\bSchedule\s*E\s*\(\s*Form\s*1040\s*\)\b.*\bSupplemental\s+Income\s+and\s+Loss\b", 0.97),
        "1040_Schedule_E_PG_2": (r"\bSchedule\s*E\s*\(\s*Form\s*1040\s*\)\b(?:(?:(?!\n).)*?)\bPage\s*2\b|\bPart\s*IV\b|\bPart\s*V\b", 0.96),
        "1040_Schedule_F": (r"\bSchedule\s*F\s*\(\s*Form\s*1040\s*\)\b.*\bProfit\s+or\s+Loss\s+From\s+Farming\b", 0.97),
    }
    for lbl, (rx, conf) in sched_map.items():
        if lbl in desc_by:
            add(rx, lbl, conf)

    # FinCEN 114 pages with page/part anchors
    if any(lbl.startswith("FinCEN 114") for lbl in desc_by):
        for lbl in list(desc_by.keys()):
            if lbl.startswith("FinCEN 114"):
                # Base header
                add(r"\bFinCEN\s*114\b", lbl, 0.9)
        # Page-specific refinements
        for lbl in desc_by:
            if "Pg 1" in lbl or "Page 1" in (desc_by.get(lbl) or ""):
                add(r"\bFinCEN\s*114\b.*?\bPage\s*1\b|\bPart\s*I\b.*Filer\s+Information", lbl, 0.98)
            if "Pg 2" in lbl or "Page 2" in (desc_by.get(lbl) or ""):
                add(r"\bFinCEN\s*114\b.*?\bPage\s*2\b|\bPart\s*II\b.*Owned\s+Separately", lbl, 0.98)
            if "Pg 3" in lbl or "Page 3" in (desc_by.get(lbl) or ""):
                add(r"\bFinCEN\s*114\b.*?\bPage\s*3\b|\bPart\s*III\b.*Owned\s+Jointly", lbl, 0.98)
            if "Pg 5" in lbl or "Page 5" in (desc_by.get(lbl) or ""):
                add(r"\bFinCEN\s*114\b.*?\bPage\s*5\b|Third\s+Party\s+Preparer\s+Use\s+Only|Filer\s+signature\s+PIN", lbl, 0.98)

    # 114a (Record of Authorization)
    for lbl in desc_by:
        if "114a" in (lbl + " " + (desc_by[lbl] or "")):
            add(r"\bForm\s*114a\b|Record\s+of\s+Authorization\s+to\s+Electronically\s+File\s+FBARs", lbl, 0.97)

    # Other form-number anchors
    for label, d in desc_by.items():
        text = d or ""
        if re.search(r"\bForm\s*5329\b", text, re.I):
            add(r"\bForm\s*5329\b", label, 0.95)
            if re.search(r"pg\s*1|page\s*1", label, re.I):
                add(r"\bForm\s*5329\b.*\bPage\s*1\b|\bPart\s*I\b", label, 0.96)
            if re.search(r"pg\s*2|page\s*2", label, re.I):
                add(r"\bForm\s*5329\b.*\bPage\s*2\b|\bPart\s*IV\b|\bPart\s*V\b", label, 0.96)
            if re.search(r"pg\s*3|page\s*3", label, re.I):
                add(r"\bForm\s*5329\b.*\bPage\s*3\b|\bPart\s*VIII\b|\bPart\s*IX\b", label, 0.95)
        if re.search(r"\bForm\s*6251\b", text, re.I) or re.search(r"Alternative\s+Minimum\s+Tax", text, re.I):
            add(r"\bForm\s*6251\b|Alternative\s+Minimum\s+Tax", label, 0.95)
        if re.search(r"\bForm\s*6252\b", text, re.I) or re.search(r"Installment\s+Sale\s+Income", text, re.I):
            add(r"\bForm\s*6252\b|Installment\s+Sale\s+Income", label, 0.95)
        if re.search(r"\bForm\s*8801\b", text, re.I) or re.search(r"Credit\s+for\s+Prior\s+Year\s+Minimum\s+Tax", text, re.I):
            add(r"\bForm\s*8801\b|Credit\s+for\s+Prior\s+Year\s+Minimum\s+Tax", label, 0.9)
        if re.search(r"\bForm\s*8582\b", text, re.I):
            add(r"\bForm\s*8582\b|Passive\s+Activity\s+Loss\s+Limitations", label, 0.95)
            if re.search(r"pg\s*1|page\s*1", label, re.I):
                add(r"\bForm\s*8582\b.*\bPage\s*1\b|\bWorksheet\b|\bPart\s*I\b", label, 0.96)
            if re.search(r"pg\s*2|page\s*2", label, re.I):
                add(r"\bForm\s*8582\b.*\bPage\s*2\b|\bPart\s*II\b|\bPart\s*III\b", label, 0.96)

        # Federal Statements - Form 6252 Line 29e (if provided as a label)
        if re.search(r"Federal\s+Statements.*6252.*29e", label, re.I) or re.search(r"Line\s*29e", text, re.I):
            add(r"\bLine\s*29e\b|\bStatement\b|\bWorksheet\b.*\b6252\b", label, 0.9)

    # NOTE: avoid generic non-CSV labels like 'invoice'/'bank_statement' to keep outputs in allowed set
    return patterns


def heuristic_classify(text: str, patterns: List[Tuple[re.Pattern, str, float]]) -> Tuple[str | None, float | None]:
    """Aggregate confidence per label and pick the highest-scoring label.
    This is more robust than first-match and helps disambiguate 1040 Pg1 vs Pg2.
    """
    if not text:
        return None, None
    scores: Dict[str, float] = {}
    max_hit: Dict[str, float] = {}
    for rx, label, conf in patterns:
        if rx.search(text):
            scores[label] = scores.get(label, 0.0) + conf
            if conf > max_hit.get(label, -1.0):
                max_hit[label] = conf
    if not scores:
        return None, None
    # pick by total score, tie-break by highest single hit then by label name for determinism
    best_label = sorted(scores.keys(), key=lambda l: (-scores[l], -max_hit.get(l, 0.0), l))[0]
    return best_label, max_hit.get(best_label, 0.0)


def make_reducto_payload(allowed: List[str], desc_by: Dict[str, str]) -> Tuple[dict, str]:
    # schema
    schema = {
        "type": "object",
        "properties": {
            "document_type": {"type": "string", "enum": allowed},
            "confidence": {"type": "number"},
        },
        "required": ["document_type"],
    }
    # prompt with a compact glossary
    lines = []
    for lbl, d in desc_by.items():
        d = (d or "Generic/non-form content.").strip()
        if len(d) > 220:
            d = d[:220].rsplit(" ", 1)[0] + "…"
        lines.append(f"- {lbl}: {d}")
    glossary = "\n".join(lines[:120])
    examples = (
        "Examples:\n"
        "- If header shows 'U.S. Individual Income Tax Return' + 'Form 1040' + 'Page 1' and contains 'Filing Status'/'Dependents' → 1040_Main_Pg1.\n"
        "- If header shows 'U.S. Individual Income Tax Return' + 'Form 1040' + 'Page 2' and contains 'Refund'/'Direct deposit'/'Sign Here' → 1040_Main_Pg2.\n"
        "- If header shows 'Schedule E (Form 1040)' with 'Supplemental Income and Loss' → 1040_Schedule_E (Pg 1). If 'Page 2' or 'Part IV/V' → 1040_Schedule_E_PG_2.\n"
        "- If header shows 'Form 8582' and 'Passive Activity Loss Limitations', pick 'Form 8582 Pg 1'/'Pg 2' if page cues indicate; otherwise 'Form 8582'.\n"
        "- If page is just a letter or filing instructions without an official form header → Other or Filing Instructions.\n"
    )
    prompt = (
        "You are classifying a single page of a tax/finance document.\n"
        "Choose exactly ONE label from the allowed set.\n"
        "Return ONLY JSON: {\"document_type\": <label>, \"confidence\": <0-1>}.\n\n"
        f"ALLOWED LABELS: {', '.join(allowed)}\n\n"
        "GLOSSARY (what to look for):\n"
        f"{glossary}\n\n"
        "Rules:\n"
        "- Prefer exact printed headers (e.g., 'Form 114a', 'FinCEN 114', 'Schedule A', 'Form 1040').\n"
        "- Use page numbers and section titles to disambiguate (e.g., 'Page 1/2', 'Part I/II/III').\n"
        "- If the page lacks strong headers, choose 'Other'.\n\n"
        f"{examples}"
    )
    return schema, prompt


def make_family_payload(allowed_families: List[str]) -> Tuple[dict, str]:
    schema = {
        "type": "object",
        "properties": {
            "document_family": {"type": "string", "enum": allowed_families},
            "confidence": {"type": "number"},
        },
        "required": ["document_family"],
    }
    glossary_lines = [
        "- 1040: IRS individual income tax return (includes Schedules A/1/E/F and 1040-SR).",
        "- FinCEN 114: Report of Foreign Bank and Financial Accounts (FBAR).",
        "- 114a: Record of Authorization to Electronically File FBARs.",
        "- Form 5329: Additional taxes on qualified plans, IRAs, and tax-favored accounts.",
        "- Form 6251: Alternative Minimum Tax—Individuals.",
        "- Form 6252: Installment Sale Income.",
        "- Form 8582: Passive Activity Loss Limitations.",
        "- Other: No consistent form headers present.",
    ]
    examples = (
        "Examples:\n"
        "- 'U.S. Individual Income Tax Return' or 'Schedule X (Form 1040)' → 1040.\n"
        "- 'FinCEN 114 – Report of Foreign Bank and Financial Accounts' → FinCEN 114.\n"
        "- 'Form 114a – Record of Authorization to Electronically File FBARs' → 114a.\n"
        "- A letter with 'Dear'/'This letter' or 'Filing Instructions' → Other.\n"
    )
    prompt = (
        "Classify a single PDF page into a broad form family.\n"
        "Return ONLY JSON: {\"document_family\": <family>, \"confidence\": <0-1>}.\n\n"
        f"ALLOWED: {', '.join(allowed_families)}\n\n"
        "Guidance:\n"
        + "\n".join(glossary_lines)
        + "\n\n"
        + examples
    )
    return schema, prompt


def _normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u2014", "-").replace("\u2013", "-")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def extract_page_text_sections(page: "fitz.Page") -> Dict[str, str]:
    """Extract header/body/footer text sections with simple layout awareness.

    - Header: top 20% of the page by Y coordinate
    - Footer: bottom 15%
    - Body: full text
    """
    body_raw = page.get_text("text") or ""
    body = _normalize_text(body_raw)
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
                    x0, y0, x1, y1 = (span.get("bbox") or [0, 0, 0, 0])
                    text = span.get("text") or ""
                    if not text.strip():
                        continue
                    if y0 <= hdr_cut:
                        header_spans.append(text)
                    if y1 >= ftr_cut:
                        footer_spans.append(text)
        header = _normalize_text(" ".join(header_spans))
        footer = _normalize_text(" ".join(footer_spans))
    except Exception:
        header = ""
        footer = ""
    return {"header": header, "body": body, "footer": footer}


def score_and_pick(
    texts: List[Tuple[str, float]],
    patterns: List[Tuple[re.Pattern, str, float]],
) -> Tuple[str | None, float | None, Dict[str, float]]:
    """Aggregate weighted scores across multiple text sections and choose best label.

    Returns: (best_label, best_confidence, scores_by_label)
    """
    scores: Dict[str, float] = {}
    best_hit: Dict[str, float] = {}
    for content, weight in texts:
        if not content:
            continue
        for rx, label, conf in patterns:
            if rx.search(content):
                w = max(0.1, float(weight))
                scores[label] = scores.get(label, 0.0) + conf * w
                if conf > best_hit.get(label, -1.0):
                    best_hit[label] = conf
    if not scores:
        return None, None, {}
    best = sorted(scores.items(), key=lambda kv: (-kv[1], -best_hit.get(kv[0], 0.0), kv[0]))[0]
    return best[0], best_hit.get(best[0], 0.0), scores


def apply_document_smoothing(results: List[dict], labels: List[str]) -> None:
    """Light document-level smoothing for ambiguous multi-page forms.

    - 1040 main Pg1/Pg2: ensure ascending pages map to Pg1 then Pg2 when both detected or ambiguous.
    - Schedule E: ensure two pages are assigned Pg1 then Pg2.
    - Form 8582: if two pages both labeled 'Form 8582', map to 'Form 8582 Pg 1/2' if those labels exist.
    - Form 5329: if multiple pages labeled 'Form 5329', map in order to Pg 1/2/3 if labels exist.
    Mutates results in-place.
    """
    label_set = set(labels)

    def indices_where(pred):
        return [i for i, r in enumerate(results) if pred(r)]

    # 1040 main smoothing
    idx_1040_main = indices_where(lambda r: r.get("predicted_family") == "1040" and str(r.get("predicted_label", "")).startswith("1040_Main"))
    if len(idx_1040_main) >= 2:
        pages = sorted(idx_1040_main, key=lambda i: results[i]["page"])[:2]
        results[pages[0]]["predicted_label"] = "1040_Main_Pg1"
        results[pages[1]]["predicted_label"] = "1040_Main_Pg2"

    # Schedule E smoothing
    idx_sched_e = indices_where(lambda r: str(r.get("predicted_label", "")).startswith("1040_Schedule_E"))
    if len(idx_sched_e) >= 2:
        pages = sorted(idx_sched_e, key=lambda i: results[i]["page"])[:2]
        results[pages[0]]["predicted_label"] = "1040_Schedule_E"
        results[pages[1]]["predicted_label"] = "1040_Schedule_E_PG_2"

    # Form 8582 page smoothing
    idx_8582 = indices_where(lambda r: r.get("predicted_family") == "Form 8582")
    if len(idx_8582) >= 2 and {"Form 8582 Pg 1", "Form 8582 Pg 2"}.issubset(label_set):
        pages = sorted(idx_8582, key=lambda i: results[i]["page"])[:2]
        results[pages[0]]["predicted_label"] = "Form 8582 Pg 1"
        results[pages[1]]["predicted_label"] = "Form 8582 Pg 2"

    # Form 5329 multi-page smoothing
    idx_5329 = indices_where(lambda r: r.get("predicted_family") == "Form 5329")
    if len(idx_5329) >= 2:
        pages = sorted(idx_5329, key=lambda i: results[i]["page"])[:3]
        mapping = ["Form 5329 Pg 1", "Form 5329 Pg 2", "Form 5329 Pg 3"]
        for j, idx in enumerate(pages):
            if mapping[j] in label_set:
                results[idx]["predicted_label"] = mapping[j]


def main():
    # Defaults: hardcode CSV in the same directory as this script and the sample PDF path
    script_dir = Path(__file__).resolve().parent
    default_csv = script_dir / "classifier_labels_clean.csv"
    # If an alternate CSV exists, prefer it only if the primary is missing
    if not default_csv.exists():
        alt_csv = script_dir / "page_labels_with_descriptions_generic_other.csv"
        if alt_csv.exists():
            default_csv = alt_csv

    default_pdf = Path("/Users/aaditya/Documents/Projects/Reducto_steamlit/testing_files/sample_multiple.pdf")
    default_out = script_dir / "predictions.csv"

    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(default_csv), help="Path to labels CSV (page,label,description)")
    parser.add_argument("--pdf", default=str(default_pdf), help="Path to multi-page PDF")
    parser.add_argument("--out", default=str(default_out), help="Output CSV path")
    parser.add_argument("--threshold", type=float, default=0.6, help="Confidence threshold to accept heuristic/LLM match")
    parser.add_argument("--debug-pages", default="", help="Comma list or ranges of pages to debug, e.g., '16,18-19'")
    parser.add_argument("--use-reducto", action="store_true", help="Enable Reducto OCR fallback if REDUCTO_API_KEY is set and client is available")
    args = parser.parse_args()

    labels, desc_by = read_labels(args.csv)
    patterns_specific = build_patterns(desc_by)
    patterns_family = build_family_patterns(labels, desc_by)

    # optional Reducto client
    reducto_client = None
    if args.use_reducto and os.getenv("REDUCTO_API_KEY"):
        try:
            # Import user-provided factory lazily (must exist in PYTHONPATH in user's env)
            from app.services.reducto_service import create_client  # type: ignore
            reducto_client = create_client()
        except Exception as e:
            print(f"[warn] Reducto disabled: {e}")

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    results = []
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
                    start = int(a)
                    end = int(b)
                except Exception:
                    continue
                lo, hi = sorted((start, end))
                debug_pages.update(range(lo, hi + 1))
            else:
                try:
                    debug_pages.add(int(part))
                except Exception:
                    pass

    with fitz.open(pdf_path) as doc:
        for p in range(doc.page_count):
            page_idx = p + 1  # 1-based
            try:
                page = doc.load_page(p)
                sections = extract_page_text_sections(page)
                text = sections.get("body") or ""
            except Exception:
                sections = {"header": "", "body": "", "footer": ""}
                text = ""

            # Stage 1: family classification (heuristic)
            fam_label, fam_conf, fam_scores = score_and_pick(
                [
                    (sections.get("header", ""), 1.4),
                    (sections.get("body", ""), 1.0),
                    (sections.get("footer", ""), 1.2),
                ],
                patterns_family,
            )
            fam_method = "heuristic"

            # Stage 1 fallback to Reducto
            if (fam_label is None or (fam_conf or 0.0) < args.threshold) and reducto_client is not None:
                try:
                    allowed_families = sorted({label_to_family(l) for l in labels} | {"Other"})
                    schema, prompt = make_family_payload(allowed_families)
                    url = reducto_client.upload(file=pdf_path)
                    resp = reducto_client.extract.run(
                        document_url=url,
                        schema=schema,
                        system_prompt=prompt,
                        options={"extraction_mode": "ocr"},
                        advanced_options={"page_range": {"start": page_idx, "end": page_idx}},
                    )
                    payload = resp.model_dump() if hasattr(resp, "model_dump") else resp
                    if isinstance(payload, dict) and "result" in payload:
                        payload = payload["result"]
                    if isinstance(payload, dict):
                        fam_label = payload.get("document_family") or fam_label
                        fam_conf = payload.get("confidence") or fam_conf
                    fam_method = "reducto"
                except Exception as e:
                    fam_method = f"heuristic(fallback; reducto_error={e})"

            if fam_label is None:
                fam_label, fam_conf, fam_method = "Other", 0.0, "default_other"

            # Restrict specific labels to the detected family (except neutral labels).
            # If family is Other, only allow neutral labels (e.g., Other, Filing Instructions).
            def is_neutral(l: str) -> bool:
                name = (l or "").lower()
                return name in {"other"} or "instruction" in name

            if fam_label == "Other":
                family_specific_labels = [l for l in labels if is_neutral(l)]
            else:
                family_specific_labels = [
                    l for l in labels if label_to_family(l) == fam_label or is_neutral(l)
                ]
            family_specific_patterns = [t for t in patterns_specific if t[1] in family_specific_labels]

            # Stage 2: specific label classification (heuristic)
            label, conf, label_scores = score_and_pick(
                [
                    (sections.get("header", ""), 1.4),
                    (sections.get("body", ""), 1.0),
                    (sections.get("footer", ""), 1.2),
                ],
                family_specific_patterns,
            )
            method = "heuristic"

            # Stage 2 fallback to Reducto within family
            if (label is None or (conf or 0.0) < args.threshold) and reducto_client is not None:
                try:
                    allowed = family_specific_labels if family_specific_labels else labels
                    schema, prompt = make_reducto_payload(allowed, desc_by)
                    url = reducto_client.upload(file=pdf_path)
                    resp = reducto_client.extract.run(
                        document_url=url,
                        schema=schema,
                        system_prompt=prompt,
                        options={"extraction_mode": "ocr"},
                        advanced_options={"page_range": {"start": page_idx, "end": page_idx}},
                    )
                    payload = resp.model_dump() if hasattr(resp, "model_dump") else resp
                    if isinstance(payload, dict) and "result" in payload:
                        payload = payload["result"]
                    if isinstance(payload, dict):
                        label = payload.get("document_type") or label
                        conf = payload.get("confidence") or conf
                    method = "reducto"
                except Exception as e:
                    method = f"heuristic(fallback; reducto_error={e})"

            if label is None:
                # As a last resort, set to Other
                label, conf, method = ("Other", 0.0, "default_other")

            # Optional debug output
            if page_idx in debug_pages:
                print("--- DEBUG Page", page_idx)
                print("Header:", sections.get("header", "")[:200])
                print("Footer:", sections.get("footer", "")[:200])
                print("Family scores:", json.dumps(fam_scores, indent=2))
                print("Label scores:", json.dumps(label_scores, indent=2))

            # Finalize row, keep backward-compatible columns
            results.append({
                "page": page_idx,
                "predicted_family": fam_label,
                "family_confidence": float(fam_conf) if fam_conf is not None else 0.0,
                "family_method": fam_method,
                "predicted_label": label,
                "confidence": float(conf) if conf is not None else 0.0,
                "method": method,
            })

    # Document-level smoothing pass
    apply_document_smoothing(results, labels)

    # Write output CSV
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "page",
                "predicted_family",
                "family_confidence",
                "family_method",
                "predicted_label",
                "confidence",
                "method",
            ],
        )
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} rows to {args.out}")


if __name__ == "__main__":
    main()
