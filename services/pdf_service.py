from __future__ import annotations
from pathlib import Path
import fitz  # PyMuPDF

def get_page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count

def render_pdf_page_png_bytes(pdf_path: Path, page_number: int, zoom: float = 2.0) -> bytes:
    with fitz.open(pdf_path) as doc:
        idx = max(0, min(page_number - 1, doc.page_count - 1))
        page = doc.load_page(idx)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")
