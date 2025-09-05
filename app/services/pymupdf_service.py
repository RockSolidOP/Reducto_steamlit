from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Any

import fitz  # PyMuPDF


def extract_page_dict(pdf_path: Path, page_number: int) -> Dict[str, Any]:
    """Return PyMuPDF native dict for the page (blocks/lines/spans with metadata)."""
    page_idx = max(0, page_number - 1)
    with fitz.open(pdf_path) as doc:
        page_idx = min(page_idx, max(0, doc.page_count - 1))
        page = doc.load_page(page_idx)
        return page.get_text("dict")


def extract_text(pdf_path: Path, page_number: int) -> str:
    """Return simple plain text for the page (PyMuPDF 'text' mode)."""
    page_idx = max(0, page_number - 1)
    with fitz.open(pdf_path) as doc:
        page_idx = min(page_idx, max(0, doc.page_count - 1))
        page = doc.load_page(page_idx)
        return page.get_text("text")


def extract_blocks(pdf_path: Path, page_number: int) -> List[list]:
    """Return raw blocks list from PyMuPDF (x0,y0,x1,y1,text,block_no,block_type)."""
    page_idx = max(0, page_number - 1)
    with fitz.open(pdf_path) as doc:
        page_idx = min(page_idx, max(0, doc.page_count - 1))
        page = doc.load_page(page_idx)
        # returns list of tuples; JSON will serialize them as lists
        return page.get_text("blocks")


def extract_words(pdf_path: Path, page_number: int) -> List[list]:
    """Return raw words list from PyMuPDF (x0,y0,x1,y1,word,block_no,line_no,word_no)."""
    page_idx = max(0, page_number - 1)
    with fitz.open(pdf_path) as doc:
        page_idx = min(page_idx, max(0, doc.page_count - 1))
        page = doc.load_page(page_idx)
        return page.get_text("words")


def extract_text_reading_order(pdf_path: Path, page_number: int, y_tol: float = 3.0) -> str:
    """Return plain text reconstructed in top-to-bottom, left-to-right order.

    Groups words by approximate y (line) and sorts by x to stabilize reading order
    when producers emit content in reverse or mixed order.
    """
    words = extract_words(pdf_path, page_number)
    if not words:
        return ""
    # sort by y0 then x0
    words.sort(key=lambda w: (round(w[1] / max(1.0, y_tol)) * y_tol, w[0]))
    lines: List[str] = []
    current_y = None
    current: List[str] = []
    for x0, y0, x1, y1, word, bno, lno, wno in words:
        yq = round(y0 / max(1.0, y_tol)) * y_tol
        if current_y is None:
            current_y = yq
        if abs(yq - current_y) > 0.5:  # new line bucket
            if current:
                lines.append(" ".join(current))
            current = [word]
            current_y = yq
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def parse_with_pymupdf(pdf_path: Path, page_number: int) -> Dict[str, Any]:
    """Return raw text and metadata only — no flattening or heuristics."""
    page_dict = extract_page_dict(pdf_path, page_number)
    text = extract_text(pdf_path, page_number)
    blocks = extract_blocks(pdf_path, page_number)
    words = extract_words(pdf_path, page_number)
    return {
        "page": int(page_number),
        "text": text,
        "text_reading_order": extract_text_reading_order(pdf_path, page_number),
        "blocks": blocks,
        "words": words,
        "dict": page_dict,
    }


def simple_page_dump(pdf_path: Path, page_number: int) -> str:
    """Return a human-readable dump similar to your notebook printout."""
    lines: List[str] = []
    pg = int(page_number)
    lines.append(f"\n--- Page {pg} ---")

    # 1) Plain text
    text = extract_text(pdf_path, pg)
    lines.append("Plain Text:\n " + (text or ""))

    # 2) Blocks with coords
    blocks = extract_blocks(pdf_path, pg)
    if blocks:
        for b in blocks:
            # b = (x0, y0, x1, y1, text, block_no, block_type)
            try:
                coords = tuple(b[:4])
                blk_text = b[4]
            except Exception:
                coords = (None, None, None, None)
                blk_text = str(b)
            lines.append(f"Block: {blk_text}\n (coords: {coords})")

    # 3) Words list
    words = extract_words(pdf_path, pg)
    try:
        word_texts = [w[4] for w in words]
    except Exception:
        word_texts = []
    lines.append("Words on page: " + str(word_texts))

    # 4) Dict keys
    page_dict = extract_page_dict(pdf_path, pg)
    keys = list(page_dict.keys()) if isinstance(page_dict, dict) else []
    lines.append("JSON keys: " + str(keys if keys else []))

    return "\n".join(lines)
