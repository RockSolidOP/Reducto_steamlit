# PyMuPDF Form Extraction – Strategy & Starter Kit

Aadi, here’s a compact, reusable plan plus copy‑pasteable code to go from PyMuPDF blocks → clean key‑value JSON, with checkboxes, regex validation, confidence scores, and an optional LLM normalization hook.

---

## 1) Architecture (2 layers)
- **Capture layer (layout‑true):** pages → blocks → lines → spans with `bbox`, `font`, `size`.
- **Semantic layer (business):** normalized fields with provenance `{key, value, page, key_bbox, value_bbox, confidence}`.

Why: you can re‑map/repair without re‑reading the PDF.

---

## 2) Canonical JSON (semantic layer)
```json
{
  "doc_id": "<file>",
  "meta": {"num_pages": 1},
  "fields": {
    "dob": {"value": "06/06/1979", "page": 0, "key_bbox": [..], "value_bbox": [..], "confidence": 0.95},
    "address.city": {"value": "Iron Mountain", "page": 0, "key_bbox": [..], "value_bbox": [..], "confidence": 0.90}
  }
}
```

---

## 3) Heuristics that generalize
1) **Dot‑leader cleanup** ("......") before matching.
2) **Anchor detection** by regex + synonyms.
3) **Value search**: nearest **right** in same y‑band → else nearest **below** within Δy window.
4) **Checkboxes**: detect small `"X"` tokens; attach to closest `Yes/No` near the question anchor.
5) **Regex validation** for IDs/dates; down‑weight if mismatch.
6) **Confidence** = combination of band match, distance, regex hit, and anchor strength.
7) **Optional LLM** pass only to normalize edge cases (synonyms, truncations), then **Pydantic** validate.

---

## 4) Anchor set (extend per form)
```python
ANCHORS = [
  (r"\bFIRST NAME\b", "name.first"),
  (r"\bMIDDLE INITIAL\b", "name.middle_initial"),
  (r"\bLAST NAME OR ORGANIZATION NAME\b", "name.last_or_org"),
  (r"INDIVIDUAL[’']S DATE OF BIRTH", "dob"),
  (r"U\.S\. TAXPAYER IDENTIFICATION NUMBER", "tin.value"),
  (r"\bTIN TYPE\b", "tin.type"),
  (r"\bTYPE OF FILER\b", "filer.type"),
  (r"\bFOREIGN IDENTIFICATION\b", "foreign_id.present"),
  (r"\b4A\b.*\bTYPE\b", "foreign_id.type"),
  (r"\b4B\b.*\bNUMBER\b", "foreign_id.number"),
  (r"\b4C\b.*\bCOUNTRY OF ISSUE\b", "foreign_id.country"),
  (r"\bMAILING ADDRESS\b", "address.line1"),
  (r"\bCITY\b", "address.city"),
  (r"\bSTATE\b", "address.state"),
  (r"ZIP/POSTAL CODE", "address.postal"),
  (r"\bCOUNTRY\b", "address.country"),
  (r"\bAMENDED\b", "amended"),
  (r"PRIOR REPORT BSA IDENTIFIER", "prior_bsa_id"),
  (r"THIS REPORT IS FOR CALENDAR YEAR ENDED", "report.year"),
  (r"\b14A\b.*25 OR MORE", "q14a.question"),
  (r"\b14B\b.*25 OR MORE", "q14b.question"),
]
```

---

## 5) Regex bank (values)
```python
RX = {
  "date": re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b"),
  "ssn": re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b"),
  "zip": re.compile(r"\b\d{5}(-\d{4})?\b"),
  "passport": re.compile(r"\b[0-9A-Z]{6,15}\b"),
}
```

---

## 6) Starter code – end‑to‑end test
> Copy these into a single file (e.g., `extract_form.py`). Adjust thresholds as needed.

```python
import re, json, math, pathlib
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
import fitz  # PyMuPDF

# ----------  Anchors & Regex  ----------
ANCHORS = [
  (r"\bFIRST NAME\b", "name.first"),
  (r"\bMIDDLE INITIAL\b", "name.middle_initial"),
  (r"\bLAST NAME OR ORGANIZATION NAME\b", "name.last_or_org"),
  (r"INDIVIDUAL[’']S DATE OF BIRTH", "dob"),
  (r"U\.S\. TAXPAYER IDENTIFICATION NUMBER", "tin.value"),
  (r"\bTIN TYPE\b", "tin.type"),
  (r"\bTYPE OF FILER\b", "filer.type"),
  (r"\bFOREIGN IDENTIFICATION\b", "foreign_id.present"),
  (r"\b4A\b.*\bTYPE\b", "foreign_id.type"),
  (r"\b4B\b.*\bNUMBER\b", "foreign_id.number"),
  (r"\b4C\b.*\bCOUNTRY OF ISSUE\b", "foreign_id.country"),
  (r"\bMAILING ADDRESS\b", "address.line1"),
  (r"\bCITY\b", "address.city"),
  (r"\bSTATE\b", "address.state"),
  (r"ZIP/POSTAL CODE", "address.postal"),
  (r"\bCOUNTRY\b", "address.country"),
  (r"\bAMENDED\b", "amended"),
  (r"PRIOR REPORT BSA IDENTIFIER", "prior_bsa_id"),
  (r"THIS REPORT IS FOR CALENDAR YEAR ENDED", "report.year"),
  (r"\b14A\b.*25 OR MORE", "q14a.question"),
  (r"\b14B\b.*25 OR MORE", "q14b.question"),
]

RX = {
  "date": re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b"),
  "ssn": re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b"),
  "zip": re.compile(r"\b\d{5}(-\d{4})?\b"),
  "passport": re.compile(r"\b[0-9A-Z]{6,15}\b"),
}

# ----------  Utilities  ----------
DOTS = re.compile(r"\s(\.[\s\.]*){3,}")  # sequences of dot leaders

def clean_text(s: str) -> str:
    s = DOTS.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

@dataclass
class Line:
    text: str
    bbox: List[float]

@dataclass
class Field:
    key: str
    value: Optional[str]
    page: int
    key_bbox: List[float]
    value_bbox: Optional[List[float]]
    confidence: float

# ----------  Page parsing  ----------

def page_lines(page: fitz.Page) -> List[Line]:
    out = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        for ln in b.get("lines", []):
            txt = clean_text("".join(sp.get("text", "") for sp in ln.get("spans", [])))
            if txt:
                out.append(Line(text=txt, bbox=list(ln.get("bbox", [0,0,0,0]))))
    return out

# ----------  Geometry helpers  ----------

def y_center(bb):
    return (bb[1]+bb[3])/2.0

def same_band(a, b, y_tol=8):
    return abs(y_center(a) - y_center(b)) <= y_tol

def nearest_right(anchor: Line, lines: List[Line], max_dx=220, y_tol=8) -> Optional[Line]:
    ax0, ay0, ax1, ay1 = anchor.bbox
    best, bestd = None, 1e9
    for ln in lines:
        x0,y0,x1,y1 = ln.bbox
        if x0 >= ax1 and same_band(anchor.bbox, ln.bbox, y_tol):
            d = x0 - ax1
            if 0 <= d <= max_dx and d < bestd:
                bestd, best = d, ln
    return best

def nearest_below(anchor: Line, lines: List[Line], max_dy=40):
    ax0, ay0, ax1, ay1 = anchor.bbox
    best, bestd = None, 1e9
    for ln in lines:
        x0,y0,x1,y1 = ln.bbox
        dy = y0 - ay1
        if 0 <= dy <= max_dy:
            d = math.hypot((x0-ax0), dy)
            if d < bestd:
                bestd, best = d, ln
    return best

# ----------  Anchor matching  ----------

def find_anchors(lines: List[Line]) -> List[Tuple[re.Pattern, str, Line]]:
    found = []
    for pattern, key in ANCHORS:
        rx = re.compile(pattern, re.I)
        for ln in lines:
            if rx.search(ln.text):
                found.append((rx, key, ln))
    return found

# ----------  Confidence scoring  ----------

def score(anchor: Line, value: Optional[Line], regex_key: Optional[str]) -> float:
    base = 0.5
    if value is None:
        return 0.2
    # y-band bonus
    band = 0.2 if same_band(anchor.bbox, value.bbox) else 0.0
    # distance (smaller is better)
    ax1 = anchor.bbox[2]
    d = max(1.0, value.bbox[0] - ax1)
    dist = min(0.2, 60.0 / (60.0 + d))
    # regex validation
    rx_bonus = 0.0
    if regex_key and regex_key in RX and RX[regex_key].search(value.text):
        rx_bonus = 0.1
    return round(base + band + dist + rx_bonus, 2)

# ----------  Checkboxes (Yes/No)  ----------
YES_RX = re.compile(r"\bYES\b", re.I)
NO_RX  = re.compile(r"\bNO\b", re.I)
X_RX   = re.compile(r"^X$|^✓$|^✔$", re.I)

@dataclass
class Checkbox:
    label: str  # "Yes"/"No"
    bbox: List[float]
    page: int


def detect_checkboxes(lines: List[Line]) -> List[Checkbox]:
    boxes: List[Checkbox] = []
    yes_lines = [ln for ln in lines if YES_RX.search(ln.text)]
    no_lines  = [ln for ln in lines if NO_RX.search(ln.text)]
    x_tokens  = [ln for ln in lines if X_RX.search(ln.text.strip()) and (ln.bbox[3]-ln.bbox[1]) <= 20]
    for x in x_tokens:
        # tie to nearest YES/NO by center distance
        candidates = [("Yes", yl) for yl in yes_lines] + [("No", nl) for nl in no_lines]
        if not candidates:
            continue
        cx = ((x.bbox[0]+x.bbox[2])/2, (x.bbox[1]+x.bbox[3])/2)
        best_label, best_ln, bestd = None, None, 1e9
        for lbl, ln in candidates:
            cy = ((ln.bbox[0]+ln.bbox[2])/2, (ln.bbox[1]+ln.bbox[3])/2)
            d = math.hypot(cx[0]-cy[0], cx[1]-cy[1])
            if d < bestd:
                bestd, best_label, best_ln = d, lbl, ln
        if best_ln and bestd <= 60:  # proximity cap to avoid false links
            boxes.append(Checkbox(label=best_label, bbox=x.bbox, page=0))
    return boxes

# ----------  Extraction  ----------

VALUE_REGEX_HINT = {
    "dob": "date",
    "tin.value": "ssn",
    "address.postal": "zip",
    "foreign_id.number": "passport",
}


def extract_page_semantics(page: fitz.Page, page_idx: int) -> List[Field]:
    lines = page_lines(page)
    fields: List[Field] = []

    anchors = find_anchors(lines)
    for rx, key, a_ln in anchors:
        # pick candidate value
        v_ln = nearest_right(a_ln, lines) or nearest_below(a_ln, lines)
        rx_hint = VALUE_REGEX_HINT.get(key)
        conf = score(a_ln, v_ln, rx_hint)
        fields.append(Field(
            key=key,
            value=v_ln.text if v_ln else None,
            page=page_idx,
            key_bbox=a_ln.bbox,
            value_bbox=v_ln.bbox if v_ln else None,
            confidence=conf,
        ))

    # checkboxes → map to known questions if present
    boxes = detect_checkboxes(lines)
    if boxes:
        # crude example: attach to q14a/q14b if their anchors exist and are nearby
        def attach_checkbox(q_key: str):
            q_lines = [a for rx,k,a in anchors if k == q_key]
            if not q_lines:
                return
            q = q_lines[0]
            # find any checkbox within y-range
            y0,y1 = q.bbox[1]-10, q.bbox[3]+60
            local = [b for b in boxes if y0 <= b.bbox[1] <= y1]
            if not local:
                return
            # prefer exact Yes/No if unique
            yes = next((b for b in local if b.label.lower()=="yes"), None)
            no  = next((b for b in local if b.label.lower()=="no"), None)
            choice = None
            if yes and not no:
                choice = "Yes"
            elif no and not yes:
                choice = "No"
            # If both present, compare distance to question start
            elif yes and no:
                qx = q.bbox[0]
                dy_yes = abs(y_center(yes.bbox) - y_center(q.bbox))
                dy_no  = abs(y_center(no.bbox)  - y_center(q.bbox))
                choice = "Yes" if dy_yes <= dy_no else "No"
            if choice:
                fields.append(Field(
                    key=q_key.replace(".question", ".answer"),
                    value=choice,
                    page=page_idx,
                    key_bbox=q.bbox,
                    value_bbox=(yes.bbox if choice=="Yes" and yes else (no.bbox if no else None)),
                    confidence=0.65,
                ))
        attach_checkbox("q14a.question")
        attach_checkbox("q14b.question")

    return fields

# ----------  Driver  ----------

def extract_document(path: str) -> Dict:
    doc = fitz.open(path)
    out = {
        "doc_id": pathlib.Path(path).name,
        "meta": {"num_pages": len(doc)},
        "fields": {}
    }
    for i, page in enumerate(doc):
        fields = extract_page_semantics(page, i)
        for f in fields:
            # keep best confidence per key
            g = out["fields"].get(f.key)
            if (g is None) or (f.confidence > g["confidence"]):
                out["fields"][f.key] = {
                    "value": f.value,
                    "page": f.page,
                    "key_bbox": f.key_bbox,
                    "value_bbox": f.value_bbox,
                    "confidence": f.confidence,
                }
    return out

if __name__ == "__main__":
    import sys
    pdf = sys.argv[1] if len(sys.argv) > 1 else "sample_for_reducto.pdf"
    result = extract_document(pdf)
    print(json.dumps(result, indent=2))
```

---

## 7) Optional: Pydantic schema + LLM normalize hook
Use this *after* extraction to coerce shapes; only call an LLM when a field fails validation.

```python
from pydantic import BaseModel, Field, validator
from typing import Optional

class Filing(BaseModel):
    report_year: Optional[int]
    filer_type: Optional[str]
    tin_type: Optional[str]
    tin_value: Optional[str]
    dob: Optional[str]
    address_city: Optional[str]
    address_state: Optional[str]
    address_postal: Optional[str]
    country: Optional[str]
    q14a_answer: Optional[str]
    q14b_answer: Optional[str]

    @validator('report_year')
    def _year(cls, v):
        if v and not (1900 <= int(v) <= 2100):
            raise ValueError('bad year')
        return v
```

LLM prompt idea (JSON‑mode): *"Given these raw fields + snippets, return a JSON conforming to Filing with normalized values (state to USPS code, country to ISO‑2), leave unknowns null."*

---

## 8) Tuning knobs
- **y‑tol** for banding: 6–10 px.
- **max_dx** right‑search: 150–250 px.
- **max_dy** below‑search: 30–60 px.
- Raise/Lower regex bonus depending on how noisy the doc is.

---

## 9) Next steps (quick wins)
- Add more anchors for later pages; persist as a YAML so non‑devs can tweak.
- Add country/state normalizers (ISO/USPS maps).
- Persist capture JSONL (per page) for audit and diffs.
- Unit tests with 3–5 real PDFs; assert confidence and value regex.

