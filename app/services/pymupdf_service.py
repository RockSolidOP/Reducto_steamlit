from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Any

import fitz  # PyMuPDF

from app.config import PYMUPDF_CONFIG


def extract_blocks(pdf_path: Path, page_number: int) -> Dict[str, Any]:
    """Extract text blocks and bboxes from a PDF page using PyMuPDF.

    Returns a dict with page and blocks suitable for JSON display/download.
    """
    text_mode = PYMUPDF_CONFIG.get("text_mode", "blocks")
    page_idx = max(0, page_number - 1)
    blocks: List[Dict[str, Any]] = []
    with fitz.open(pdf_path) as doc:
        page_idx = min(page_idx, max(0, doc.page_count - 1))
        page = doc.load_page(page_idx)
        if text_mode == "dict":
            tdict = page.get_text("dict")
            for b in tdict.get("blocks", []):
                if b.get("type") == 0:  # text block
                    text = "".join([s.get("text", "") for l in b.get("lines", []) for s in l.get("spans", [])]).strip()
                    if text:
                        blocks.append({
                            "text": text,
                            "bbox": [float(x) for x in b.get("bbox", [])],
                        })
        else:
            # Default to "blocks" mode
            for x0, y0, x1, y1, text, block_no, block_type in page.get_text("blocks"):
                text = (text or "").strip()
                if text:
                    blocks.append({
                        "text": text,
                        "bbox": [float(x0), float(y0), float(x1), float(y1)],
                        "block_no": int(block_no),
                        "block_type": int(block_type),
                    })

    return {"page": int(page_number), "blocks": blocks}


def _kv_from_line(line: str) -> List[tuple[str, str]]:
    """Heuristic extract of key-value pairs from a single line.

    Supports:
    - Colon-delimited: "Key: Value"
    - Dot leaders: "Key . . . . . Value"
    Returns possibly multiple (key, value) pairs.
    """
    out: List[tuple[str, str]] = []
    s = line.strip()
    if not s:
        return out

    # Dot-leader (e.g., "Name . . . . John Doe")
    if PYMUPDF_CONFIG.get("kv_enable_dot_leader", True):
        parts = re.split(r"(?:\s*\.+\s*){3,}", s)
        if len(parts) >= 2:
            key = parts[0].strip(" :.-")
            value = parts[-1].strip()
            if key and value:
                out.append((key, value))

    # Colon-delimited
    if PYMUPDF_CONFIG.get("kv_enable_colon", True) and ":" in s:
        k, v = s.split(":", 1)
        k = k.strip(" :.-")
        v = v.strip()
        if k and v:
            out.append((k, v))

    return out


def extract_kv_from_blocks(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract naive key-value pairs from block texts."""
    kv: Dict[str, Any] = {}
    merge = PYMUPDF_CONFIG.get("kv_merge_strategy", "first")

    for b in blocks:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        for line in text.splitlines():
            for key, value in _kv_from_line(line):
                if key in kv:
                    if merge == "list":
                        if isinstance(kv[key], list):
                            kv[key].append(value)
                        else:
                            kv[key] = [kv[key], value]
                    elif merge == "last":
                        kv[key] = value
                    else:  # "first"
                        pass
                else:
                    kv[key] = value

    return kv


def parse_with_pymupdf(pdf_path: Path, page_number: int) -> Dict[str, Any]:
    """Return both raw blocks and heuristic key-values for a page."""
    raw = extract_blocks(pdf_path, page_number)
    kv = extract_kv_from_blocks(raw.get("blocks", []))
    return {"raw": raw, "kv": kv}

