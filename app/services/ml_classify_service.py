from __future__ import annotations

"""ML classifier (FAISS + OpenCLIP) service wrapper.

This wraps the local module under app/resources/classifier_module and exposes a
simple API mirroring the regex-based classify_service so the UI can choose
between engines without changing its expectations.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import os
import re

from app.resources.classifier_module import get_label, get_suggestions


def _norm_label_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = s.replace("-", "_")
    s = re.sub(r"__+", "_", s)
    return s


def _detect_active_version(root: Optional[Path] = None) -> Optional[str]:
    """Try to read ACTIVE_VERSION.txt from likely locations.

    Priority:
      1) CLASSIFIER_FAISS_DIR env var
      2) Bundled module path: app/resources/classifier_module/data/faiss
      3) Provided root/dataset/v1/faiss
    """
    candidates: List[Path] = []
    env_dir = os.environ.get("CLASSIFIER_FAISS_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    # bundled
    candidates.append(Path(__file__).resolve().parents[1] / "resources" / "classifier_module" / "data" / "faiss")
    # external project root
    if root is not None:
        candidates.append(Path(root) / "dataset" / "v1" / "faiss")
    for d in candidates:
        p = d / "ACTIVE_VERSION.txt"
        try:
            if p.exists():
                v = p.read_text(encoding="utf-8").strip()
                return v or None
        except Exception:
            continue
    return None


def classify_document_ml(
    pdf_path: Path,
    *,
    topk: int = 1,
    root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Classify all pages of a PDF using FAISS+OpenCLIP.

    Returns rows with keys: page, predicted_family, predicted_label.
    If topk > 1, also includes suggestions: [{label, score, base_label, page_in_form, rank}].
    """
    import fitz  # PyMuPDF for quick page counting

    rows: List[Dict[str, Any]] = []
    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count

    for p in range(1, page_count + 1):
        if topk <= 1:
            info = get_label(pdf_path, p, root=root)
            fam = info.get("base_label") or "Other"
            lbl = info.get("label") or "Other"
            rows.append(
                {
                    "page": p,
                    "predicted_family": fam,
                    "predicted_label": _norm_label_name(str(lbl)),
                    "score": info.get("score"),
                    "base_label": info.get("base_label"),
                    "page_in_form": info.get("page_in_form"),
                }
            )
        else:
            sugg = get_suggestions(pdf_path, p, topk=topk, root=root)
            # Best suggestion (if any) defines the label shown in pages[]
            best = sugg[0] if sugg else {}
            fam = best.get("base_label") or "Other"
            lbl = best.get("label") or "Other"
            rows.append(
                {
                    "page": p,
                    "predicted_family": fam,
                    "predicted_label": _norm_label_name(str(lbl)),
                    "suggestions": sugg,
                }
            )

    return rows


def classify_to_result_ml(
    pdf_path: Path,
    *,
    topk: int = 1,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Return a JSON-ready result object matching the regex service shape."""
    rows = classify_document_ml(pdf_path, topk=topk, root=root)
    active_ver = _detect_active_version(root)
    return {
        "document": Path(pdf_path).name,
        "engine": "ml-faiss",
        "config": f"faiss:{active_ver or 'embedded'}",
        "total_pages": len(rows),
        "pages": [
            {
                "page": r["page"],
                "family": r.get("predicted_family") or "Other",
                "label": r.get("predicted_label") or "Other",
                **({"suggestions": r.get("suggestions")} if "suggestions" in r else {}),
            }
            for r in rows
        ],
        "smoothing_applied": False,
        "topk": int(topk),
    }

