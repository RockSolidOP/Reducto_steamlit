from __future__ import annotations

"""
Minimal, self-contained suggestion helper used by classifier_module.

This mirrors the core of curation/suggest.py so the module can be copied
to another app with only the dataset artifacts (FAISS index + id_map).
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Tuple
import json
import numpy as np

_MODEL = None
_PREPROCESS = None
_TORCH = None
_INDEX = None
_IDMAP: List[Dict[str, Any]] = []


def _local_faiss_dir() -> Path:
    """Return the module-bundled FAISS dir if present."""
    return Path(__file__).resolve().parent / "data" / "faiss"


def _ensure_open_clip():
    global _MODEL, _PREPROCESS, _TORCH
    if _MODEL is not None:
        return _MODEL, _PREPROCESS, _TORCH
    try:
        # Reduce OpenMP/threading conflicts
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
        os.environ.setdefault("KMP_INIT_AT_FORK", "FALSE")
        import open_clip  # type: ignore
        import torch  # type: ignore
    except Exception as e:  # pragma: no cover - environment specific
        raise RuntimeError("Missing deps: install open_clip_torch and torch") from e
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k"
    )
    model = model.eval().to("cpu")
    _MODEL, _PREPROCESS, _TORCH = model, preprocess, torch
    return _MODEL, _PREPROCESS, _TORCH


def _ensure_faiss(root: Path) -> Tuple["faiss.Index", List[Dict[str, Any]]]:
    global _INDEX, _IDMAP
    if _INDEX is not None and _IDMAP:
        return _INDEX, _IDMAP
    try:
        import faiss  # type: ignore
    except Exception as e:  # pragma: no cover - environment specific
        raise RuntimeError("Missing deps: install faiss-cpu") from e
    # Candidate locations in priority order:
    # 1) Environment override
    # 2) Bundled module data
    # 3) repo dataset/v1/faiss under provided root
    env_dir = os.environ.get("CLASSIFIER_FAISS_DIR")
    candidates: List[Path] = []
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(_local_faiss_dir())
    candidates.append(root / "dataset" / "v1" / "faiss")

    index_path = None
    idmap_path = None
    for faiss_dir in candidates:
        act_file = faiss_dir / "ACTIVE_VERSION.txt"
        active = None
        if act_file.exists():
            try:
                active = act_file.read_text(encoding="utf-8").strip()
                if active and not active.startswith("_"):
                    active = f"_{active}"
            except Exception:
                active = None
        cand_index = faiss_dir / f"clip_vitb32{active or ''}.index"
        cand_idmap = faiss_dir / f"id_map{active or ''}.jsonl"
        if cand_index.exists() and cand_idmap.exists():
            index_path = cand_index
            idmap_path = cand_idmap
            break

    if index_path is None or idmap_path is None:
        raise RuntimeError("FAISS index or id_map.jsonl not found in any known location")
    _INDEX = faiss.read_index(str(index_path))
    _IDMAP = []
    with open(idmap_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                _IDMAP.append(json.loads(line))
            except Exception:
                continue
    return _INDEX, _IDMAP


def _embed_image(img) -> np.ndarray:
    model, preprocess, torch = _ensure_open_clip()
    with torch.no_grad():
        image = preprocess(img).unsqueeze(0)
        feats = model.encode_image(image)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    vec = feats.squeeze(0).cpu().numpy()
    # Final safety normalize
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.astype("float32")


def embed_pdf_page(pdf_path: Path, page: int) -> np.ndarray:
    """Render a 1-based page from a PDF and return CLIP embedding."""
    from PIL import Image  # noqa: F401 - ensures Pillow present
    import pypdfium2 as pdfium  # type: ignore

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        pg = doc[page - 1]
    except Exception as e:
        raise ValueError(f"Page {page} out of range for {pdf_path}") from e
    pil_image = pg.render().to_pil()
    return _embed_image(pil_image)


def search_neighbors(root: Path, query_vec: np.ndarray, topk: int = 5) -> List[Dict[str, Any]]:
    index, idmap = _ensure_faiss(root)
    import faiss  # type: ignore
    D, I = index.search(query_vec.reshape(1, -1), topk)
    out: List[Dict[str, Any]] = []
    for r in range(I.shape[1]):
        idx = int(I[0, r])
        if idx < 0 or idx >= len(idmap):
            continue
        m = idmap[idx]
        out.append({
            "rank": r + 1,
            "score": float(D[0, r]),
            "id": m.get("id"),
            "label": m.get("label"),
            "base_label": m.get("base_label"),
            "page_in_form": m.get("page_in_form"),
        })
    return out
