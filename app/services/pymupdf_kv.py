from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import fitz  # PyMuPDF


def extract_text_pymupdf(pdf_path: str | Path) -> str:
    """Extract plain text from all pages using PyMuPDF and normalize whitespace.

    - Newlines preserved between pages/lines
    - Collapses multiple spaces/tabs to a single space
    - Collapses multiple blank lines
    """
    p = str(pdf_path)
    doc = fitz.open(p)
    try:
        text = "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text


def postprocess_pymupdf(full_text: str) -> Dict[str, Any]:
    """Heuristic post-processor to derive key-value pairs from plain text.

    Tailored for US tax/FBAR-like forms. Best-effort starter; refine as needed.
    """
    def find_year(text: str) -> Optional[str]:
        m = re.search(r"\b(19|20)\d{2}\b", text)
        return m.group(0) if m else None

    def find_tin(text: str) -> Optional[str]:
        cands = re.findall(r"(?<!\d)(\d{9})(?!\d)", text)
        return cands[0] if cands else None

    def find_dob(text: str) -> Optional[str]:
        m = re.search(r"\b(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/(19|20)\d{2}\b", text)
        return m.group(0) if m else None

    def find_tin_type(text: str) -> Optional[str]:
        m = re.search(r"\bSSN/ITIN\b|\bEIN\b|\bATIN\b", text, re.IGNORECASE)
        return m.group(0).upper() if m else None

    def find_type_of_filer(text: str) -> Optional[str]:
        m = re.search(r"\bIndividual\b|\bCorporation\b|\bPartnership\b|\bLLC\b", text, re.IGNORECASE)
        return m.group(0).title() if m else None

    def find_foreign_id_type(text: str) -> Optional[str]:
        m = re.search(r"\bPassport\b|\bNational ID\b|\bDriver[’']?s License\b", text, re.IGNORECASE)
        return m.group(0).title() if m else None

    def find_foreign_id_number(text: str) -> Optional[str]:
        tin = find_tin(text)
        m1 = re.search(r"\b\d{7,10}\b", text)
        if not m1:
            return None
        val = m1.group(0)
        if tin and val == tin:
            m2 = re.search(r"\b\d{7,10}\b", text[m1.end():])
            return m2.group(0) if m2 else val
        return val

    def find_foreign_id_country(text: str) -> Optional[str]:
        if "Falkland Islands" in text:
            return "Falkland Islands"
        if re.search(r"\bUnited States\b|\bUSA\b|\bUS\b", text):
            return "US"
        return None

    def find_country(text: str) -> Optional[str]:
        m = re.search(r"\bUnited States\b|\bUSA\b|\bUS\b", text)
        return "US" if m else None

    def find_name(text: str) -> Tuple[Optional[str], Optional[str]]:
        if "Fname" in text and "GenInfo" in text:
            m = re.search(r"\bFname\b.*?\bGenInfo\b", text, re.DOTALL)
            if m:
                return ("Fname", "GenInfo")
        return (None, None)

    def find_address_city_state_zip(text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        address = None; city=None; state=None; zipc=None
        for line in text.splitlines():
            ln = line.strip()
            if re.search(r"^\d{1,6}\s+\S", ln):
                if any(lbl in ln for lbl in ["Country","State","City","Zip"]):
                    continue
                if re.search(r"\.{3,}", ln):
                    continue
                address = ln
                break
        lines = [l.strip() for l in text.splitlines()]
        if address and address in lines:
            idx = lines.index(address)
            if idx+1 < len(lines):
                nxt = lines[idx+1]
                if re.search(r"\b(apt|unit|ste|apartment)\s*\w*", nxt, re.IGNORECASE):
                    address = f"{address} {nxt}"
        mzip = re.search(r"\b\d{5}(?:-\d{4})?\b", text)
        if mzip:
            zipc = mzip.group(0)
        mstate2 = re.search(r"\b(AK|AL|AR|AZ|CA|CO|CT|DC|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV)\b", text)
        if mstate2:
            state = mstate2.group(1)
        if state:
            for i, ln in enumerate(lines):
                if re.search(rf"\b{state}\b", ln):
                    j = i - 1
                    while j >= 0 and not lines[j]:
                        j -= 1
                    if j >= 0 and re.search(r"[A-Za-z]", lines[j]) and not re.search(r"\b(State|City|Zip|Country)\b", lines[j], re.IGNORECASE):
                        city = lines[j]
                        break
        return address, city, state, zipc

    def find_amended(text: str) -> Optional[bool]:
        m = re.search(r"Amended", text, re.IGNORECASE)
        if not m:
            return None
        window = text[max(0, m.start()-30): m.end()+60]
        return bool(re.search(r"\bX\b", window))

    data: Dict[str, Any] = {}
    data["report_year"] = find_year(full_text)
    data["type_of_filer"] = find_type_of_filer(full_text)
    data["taxpayer_identification_number"] = find_tin(full_text)
    data["tin_type"] = find_tin_type(full_text)
    data["foreign_id_type"] = find_foreign_id_type(full_text)
    data["foreign_id_number"] = find_foreign_id_number(full_text)
    data["foreign_id_country"] = find_foreign_id_country(full_text)
    data["dob"] = find_dob(full_text)
    first_name, last_name = find_name(full_text)
    data["first_name"] = first_name
    data["last_name_or_organization"] = last_name
    addr, city, state, zipc = find_address_city_state_zip(full_text)
    data["mailing_address"] = addr
    data["city"] = city
    data["state"] = state
    data["zip"] = zipc
    data["country"] = find_country(full_text)
    data["is_amended"] = find_amended(full_text)

    return {k: v for k, v in data.items() if v not in [None, ""]}

