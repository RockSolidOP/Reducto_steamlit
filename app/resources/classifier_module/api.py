from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from . import _suggester as core


def _resolve_root(root: Optional[str | Path]) -> Path:
    if root is None:
        # Default to repo root if module lives under repo/classifier_module/
        # Otherwise, caller should pass the path that contains dataset/
        return Path(__file__).resolve().parents[1]
    return Path(root).resolve()


def get_suggestions(
    pdf_path: str | Path,
    page: int,
    *,
    topk: int = 5,
    root: str | Path | None = None,
) -> List[Dict[str, Any]]:
    """Return top-k label suggestions for a PDF page.

    - pdf_path: path to a multi-page PDF
    - page: 1-based page index
    - topk: number of neighbors to return
    - root: folder containing `dataset/v1/faiss` (defaults to repo root)
    """
    root_path = _resolve_root(root)
    vec = core.embed_pdf_page(Path(pdf_path), int(page))
    return core.search_neighbors(root_path, vec, topk=topk)


def get_label(
    pdf_path: str | Path,
    page: int,
    *,
    root: str | Path | None = None,
) -> Dict[str, Any]:
    """Return the top predicted label for a PDF page.

    Returns a dict with keys: label, score, and rank (1).
    Raises if no neighbors are found or artifacts are missing.
    """
    results = get_suggestions(pdf_path, page, topk=1, root=root)
    if not results:
        raise RuntimeError("No suggestions available (empty index or missing artifacts)")
    # Keep a simple shape: label + score; include base_label if present
    best = results[0]
    return {
        "label": best.get("label"),
        "score": best.get("score"),
        "base_label": best.get("base_label"),
        "page_in_form": best.get("page_in_form"),
        "rank": 1,
    }


def classify_pdf(
    pdf_path: str | Path,
    *,
    root: str | Path | None = None,
    topk: int = 1,
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Classify all pages in a PDF.

    Returns a list of dicts per page: {page, label, score} if topk==1,
    or {page, suggestions: [...]} if topk>1.
    """
    import pypdfium2 as pdfium  # type: ignore

    pdf_path = Path(pdf_path)
    root_path = _resolve_root(root)
    doc = pdfium.PdfDocument(str(pdf_path))
    n_pages = len(doc)
    if max_pages is not None:
        n_pages = min(n_pages, max_pages)

    out: List[Dict[str, Any]] = []
    for p in range(1, n_pages + 1):
        if topk == 1:
            best = get_label(pdf_path, p, root=root_path)
            out.append({"page": p, "label": best["label"], "score": best["score"]})
        else:
            sugg = get_suggestions(pdf_path, p, root=root_path, topk=topk)
            out.append({"page": p, "suggestions": sugg})
    return out

