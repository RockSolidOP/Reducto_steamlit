from __future__ import annotations

import os
import sys
from pathlib import Path
import json
from typing import List, Tuple, Any
import re
import time

from dotenv import load_dotenv
from reducto import ReductoError
import fitz  # PyMuPDF, for page counts and per-page text

# Ensure project root is importable for `app.services`
sys.path.append(str(Path(__file__).resolve().parents[2]))
from app.services.reducto_service import create_client


def build_classification_schema(classes: List[str]) -> dict:
    if not classes:
        # Open-ended label if no enum supplied
        return {
            "type": "object",
            "properties": {
                "document_type": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["document_type"],
        }
    return {
        "type": "object",
        "properties": {
            "document_type": {"type": "string", "enum": classes},
            "confidence": {"type": "number"},
        },
        "required": ["document_type"],
    }


def build_multi_document_schema(classes: List[str]) -> dict:
    # Schema that returns a list of detected documents with page spans
    item = {
        "type": "object",
        "properties": {
            "document_type": {"type": "string", **({"enum": classes} if classes else {})},
            "start_page": {"type": "integer", "minimum": 1},
            "end_page": {"type": "integer", "minimum": 1},
            "confidence": {"type": "number"},
        },
        "required": ["document_type", "start_page", "end_page"],
    }
    return {
        "type": "object",
        "properties": {
            "documents": {"type": "array", "items": item}
        },
        "required": ["documents"],
    }


def classification_prompt(classes: List[str]) -> str:
    if classes:
        labels = ", ".join(classes)
        return (
            "Classify the document into exactly one of the following labels: "
            f"{labels}. Return only a JSON object with fields document_type and confidence."
        )
    return (
        "Classify the document by its type. Return only a JSON object with fields "
        "document_type (string) and confidence (0-1)."
    )


def multi_document_prompt(classes: List[str]) -> str:
    if classes:
        labels = ", ".join(classes)
        return (
            "Identify all distinct documents/forms contained in this PDF. "
            "Return a JSON object with 'documents': a list of objects having "
            "document_type, start_page, end_page, and confidence. Use 1-based page numbers. "
            "Document types must be one of: " + labels + "."
        )
    return (
        "Identify all distinct documents/forms contained in this PDF. "
        "Return a JSON object with 'documents': a list of objects having "
        "document_type (string), start_page, end_page, and confidence. Use 1-based page numbers."
    )


def _to_printable(obj):
    for m in ("model_dump", "dict"):
        if hasattr(obj, m) and callable(getattr(obj, m)):
            try:
                return getattr(obj, m)()
            except Exception:
                pass
    return obj


def main():
    load_dotenv()
    if not os.getenv("REDUCTO_API_KEY"):
        raise ReductoError(
            "REDUCTO_API_KEY is not set. Add it to .env or export it before running."
        )

    # Flags: --multi to detect multiple documents with page spans
    #        --per-page to classify each page independently
    #        --threshold=0.6 to set confidence cutoff for 'undetermined'
    #        --no-fast to skip heuristics (always use Reducto)
    args = sys.argv[1:]
    multi_mode = False
    per_page_mode = False
    threshold = 0.6
    use_fast_heuristics = True

    if "--multi" in args:
        multi_mode = True
        args = [a for a in args if a != "--multi"]
    if "--per-page" in args:
        per_page_mode = True
        args = [a for a in args if a != "--per-page"]
    if "--no-fast" in args:
        use_fast_heuristics = False
        args = [a for a in args if a != "--no-fast"]
    # Parse --threshold=<float>
    for a in list(args):
        if a.startswith("--threshold="):
            try:
                threshold = float(a.split("=", 1)[1])
            except Exception:
                pass
            args.remove(a)

    # Define your target classes here (expandable). Canonical labels, lowercase.
    classes = [
        # IRS 1040 family
        "irs_1040", "irs_1040_sr",
        "irs_1040_schedule_a", "irs_1040_schedule_b", "irs_1040_schedule_c", "irs_1040_schedule_d",
        "irs_1040_schedule_1", "irs_1040_schedule_2", "irs_1040_schedule_3",
        # Wage and withholding
        "w2", "w4", "w9",
        # 1099 family (common varieties)
        "1099_nec", "1099_misc", "1099_int", "1099_div", "1099_r", "1099_k", "1099_g",
        # Education/Mortgage
        "1098", "1098_e", "1098_t",
        # Healthcare
        "1095_a", "1095_b", "1095_c",
        # Business/employer returns (common)
        "irs_941", "irs_940",
        # Generic business/finance docs
        "invoice", "bank_statement",
        # Fallback
        "other",
    ]
    if multi_mode:
        schema = build_multi_document_schema(classes)
        prompt = multi_document_prompt(classes)
    elif per_page_mode:
        schema = build_classification_schema(classes)
        prompt = classification_prompt(classes)
    else:
        schema = build_classification_schema(classes)
        prompt = classification_prompt(classes)

    client = create_client()

    # Inputs: pass PDFs on the command line; fallback to sample_1040.pdf
    if not args:
        # args = ["testing_files/reducto_files/sample_1040.pdf"]
        args= ["testing_files/sample_multiple.pdf"]

    for path_str in args:
        pdf_path = Path(path_str)
        if not pdf_path.exists():
            print(f"Skipping {pdf_path}: not found")
            continue
        url = client.upload(file=pdf_path)
        if multi_mode:
            # Analyze entire PDF to identify all document spans
            resp = client.extract.run(
                document_url=url,
                schema=schema,
                system_prompt=prompt,
                options={"extraction_mode": "ocr"},
            )
        elif per_page_mode:
            # Classify each page independently. First try fast regex-based classification on page text.
            # If below threshold (or disabled), fall back to Reducto extract for that page.
            with fitz.open(pdf_path) as doc:
                num_pages = doc.page_count
                for p in range(1, num_pages + 1):
                    label, conf = (None, None)
                    if use_fast_heuristics:
                        text = doc.load_page(p - 1).get_text("text") or ""
                        label, conf = _heuristic_classify(text)
                    conf_val = float(conf) if conf is not None else 0.0
                    if (label is not None) and conf_val >= threshold:
                        print(f"{pdf_path.name}: p{p}: {label} (confidence={conf_val}, method=heuristic)")
                        continue
                    # Fallback to Reducto per-page
                    r = client.extract.run(
                        document_url=url,
                        schema=schema,
                        system_prompt=prompt,
                        options={"extraction_mode": "ocr"},
                        advanced_options={"page_range": {"start": p, "end": p}},
                    )
                    res = _to_printable(r)
                    # Reuse single-label extraction
                    def extract_label(payload):
                        if isinstance(payload, dict):
                            inner = payload.get("result", payload)
                            if isinstance(inner, dict):
                                return inner.get("document_type"), inner.get("confidence") or inner.get("confidence_score")
                            if isinstance(inner, list):
                                for item in inner:
                                    if isinstance(item, dict) and "document_type" in item:
                                        return item.get("document_type"), item.get("confidence") or item.get("confidence_score")
                        if isinstance(payload, list):
                            for item in payload:
                                if isinstance(item, dict) and "document_type" in item:
                                    return item.get("document_type"), item.get("confidence") or item.get("confidence_score")
                        return None, None

                    doc_type, conf2 = extract_label(res)
                    try:
                        conf2_val = float(conf2) if conf2 is not None else 0.0
                    except Exception:
                        conf2_val = 0.0
                    final_label = doc_type if (doc_type is not None and conf2_val >= threshold) else "undetermined"
                    print(f"{pdf_path.name}: p{p}: {final_label} (confidence={conf2_val}, method=reducto)")
            # Move to next file
            continue
        else:
            # Quick single-label classification focusing on first pages
            resp = client.extract.run(
                document_url=url,
                schema=schema,
                system_prompt=prompt,
                options={"extraction_mode": "ocr"},
                advanced_options={"page_range": {"start": 1, "end": 2}},
            )
        result = _to_printable(resp)

        if multi_mode:
            # Expect an object with a 'documents' array; print compact summary
            def to_docs(payload: Any) -> List[dict]:
                if isinstance(payload, dict):
                    inner = payload.get("result", payload)
                    if isinstance(inner, dict) and isinstance(inner.get("documents"), list):
                        return [x for x in inner["documents"] if isinstance(x, dict)]
                return []

            docs = to_docs(result)
            if docs:
                parts = []
                for d in docs:
                    parts.append(
                        f"{d.get('document_type')}[{d.get('start_page')}-{d.get('end_page')}]@{d.get('confidence')}"
                    )
                print(f"{pdf_path.name}: " + ", ".join(parts))
            else:
                print(f"{pdf_path.name}:\n{json.dumps(result, indent=2, ensure_ascii=False)}")
        else:
            # Normalize different response shapes for single-label classification
            def extract_label(payload):
                # payload can be dict with 'result', dict directly, or list
                if isinstance(payload, dict):
                    inner = payload.get("result", payload)
                    if isinstance(inner, dict):
                        return inner.get("document_type"), inner.get("confidence") or inner.get("confidence_score")
                    if isinstance(inner, list):
                        for item in inner:
                            if isinstance(item, dict) and "document_type" in item:
                                return item.get("document_type"), item.get("confidence") or item.get("confidence_score")
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict) and "document_type" in item:
                            return item.get("document_type"), item.get("confidence") or item.get("confidence_score")
                return None, None

            doc_type, conf = extract_label(result)
            if doc_type is not None:
                print(f"{pdf_path.name}: document_type={doc_type} confidence={conf}")
            else:
                # Fallback to dumping the payload if shape differs
                print(f"{pdf_path.name}:\n{json.dumps(result, indent=2, ensure_ascii=False)}")


# ----------------------- Heuristics (fast path) -----------------------

_FORM_PATTERNS = [
    # Strong IRS form headers
    (re.compile(r"\bForm\s+W[-\s]?2\b", re.I), "w2", 0.95),
    (re.compile(r"\bForm\s+W[-\s]?4\b", re.I), "w4", 0.9),
    (re.compile(r"\bForm\s+W[-\s]?9\b", re.I), "w9", 0.9),
    (re.compile(r"\bForm\s+1040\b", re.I), "irs_1040", 0.95),
    (re.compile(r"\bForm\s+1040[-\s]?SR\b", re.I), "irs_1040_sr", 0.95),
    # Schedules (letters or 1-3)
    (re.compile(r"\bSchedule\s+A\b", re.I), "irs_1040_schedule_a", 0.85),
    (re.compile(r"\bSchedule\s+B\b", re.I), "irs_1040_schedule_b", 0.85),
    (re.compile(r"\bSchedule\s+C\b", re.I), "irs_1040_schedule_c", 0.85),
    (re.compile(r"\bSchedule\s+D\b", re.I), "irs_1040_schedule_d", 0.85),
    (re.compile(r"\bSchedule\s+1\b", re.I), "irs_1040_schedule_1", 0.8),
    (re.compile(r"\bSchedule\s+2\b", re.I), "irs_1040_schedule_2", 0.8),
    (re.compile(r"\bSchedule\s+3\b", re.I), "irs_1040_schedule_3", 0.8),
    # 1099 variants
    (re.compile(r"\bForm\s+1099[-\s]?NEC\b", re.I), "1099_nec", 0.9),
    (re.compile(r"\bForm\s+1099[-\s]?MISC\b", re.I), "1099_misc", 0.9),
    (re.compile(r"\bForm\s+1099[-\s]?INT\b", re.I), "1099_int", 0.9),
    (re.compile(r"\bForm\s+1099[-\s]?DIV\b", re.I), "1099_div", 0.9),
    (re.compile(r"\bForm\s+1099[-\s]?R\b", re.I), "1099_r", 0.9),
    (re.compile(r"\bForm\s+1099[-\s]?K\b", re.I), "1099_k", 0.9),
    (re.compile(r"\bForm\s+1099[-\s]?G\b", re.I), "1099_g", 0.9),
    # Education/Mortgage
    (re.compile(r"\bForm\s+1098[-\s]?T\b", re.I), "1098_t", 0.9),
    (re.compile(r"\bForm\s+1098[-\s]?E\b", re.I), "1098_e", 0.9),
    (re.compile(r"\bForm\s+1098\b", re.I), "1098", 0.85),
    # Healthcare
    (re.compile(r"\bForm\s+1095[-\s]?A\b", re.I), "1095_a", 0.9),
    (re.compile(r"\bForm\s+1095[-\s]?B\b", re.I), "1095_b", 0.9),
    (re.compile(r"\bForm\s+1095[-\s]?C\b", re.I), "1095_c", 0.9),
    # Employer/Payroll returns
    (re.compile(r"\bForm\s+941\b", re.I), "irs_941", 0.85),
    (re.compile(r"\bForm\s+940\b", re.I), "irs_940", 0.85),
]

_WEAK_PATTERNS = [
    # Generic invoice and bank cues (weaker confidence)
    (re.compile(r"\bInvoice\b", re.I), "invoice", 0.7),
    (re.compile(r"\bInvoice\s+#|Invoice\s+No\.?\b", re.I), "invoice", 0.75),
    (re.compile(r"\bStatement\s+Period\b", re.I), "bank_statement", 0.7),
    (re.compile(r"\bAccount\s+Number\b", re.I), "bank_statement", 0.65),
]


def _heuristic_classify(text: str) -> Tuple[str | None, float | None]:
    if not text:
        return None, None
    # Strong matches first
    for rx, label, conf in _FORM_PATTERNS:
        if rx.search(text):
            return label, conf
    # Weak matches next
    for rx, label, conf in _WEAK_PATTERNS:
        if rx.search(text):
            return label, conf
    return None, None



if __name__ == "__main__":
    main()
