from __future__ import annotations

from pathlib import Path
import fitz  # PyMuPDF


def render_pdf_page_png_bytes(pdf_path: Path, page_number: int, zoom: float = 2.0) -> bytes:
    """Return PNG bytes for the given PDF page (1-indexed)."""
    with fitz.open(pdf_path) as doc:
        page_index = max(0, min(page_number - 1, doc.page_count - 1))
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")

