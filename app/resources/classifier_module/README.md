Classifier Module (Local, v1.3 FAISS)

What it is
- A tiny, copyable module that labels PDF pages using your existing suggestion engine (OpenCLIP + FAISS) and the active version from `dataset/v1/faiss/ACTIVE_VERSION.txt` (currently `v1.3`).
- No training code inside. It’s a kNN-style predictor over your curated index.

Bundled artifacts
- This module includes a copy of the active FAISS artifacts under `classifier_module/data/faiss/`:
  - `clip_vitb32_v1.3.index`
  - `id_map_v1.3.jsonl`
  - `ACTIVE_VERSION.txt` (set to `v1.3`)

What you need to copy along
- Usually just copy `classifier_module/` to your other app. Nothing else required.
- If you prefer to use an external dataset path instead of the bundled files, keep your `dataset/v1/faiss/` and either:
  - pass `root=...` to APIs (where `root/dataset/v1/faiss` exists), or
  - set `CLASSIFIER_FAISS_DIR=/path/to/dataset/v1/faiss`.

Dependencies
- Install these (CPU is fine):
  - `numpy`, `Pillow`, `pypdfium2`, `open_clip_torch`, `torch`, `faiss-cpu`
  - In this repo, `requirements-ml.txt` lists suitable versions.

Simple API
- `get_suggestions(pdf_path, page, topk=5, root=None)` → list of `{label, score, ...}`
- `get_label(pdf_path, page, root=None)` → `{label, score}` of the top neighbor
- `classify_pdf(pdf_path, topk=1, root=None)` → list per page

Notes
- By default, the module will use the bundled FAISS data under `classifier_module/data/faiss/`.
- To override, either pass `root` (expects `root/dataset/v1/faiss`) or set `CLASSIFIER_FAISS_DIR`.
- No images are stored; pages are rendered on-the-fly with `pypdfium2`.

Example
```
from pathlib import Path
from classifier_module import get_label, get_suggestions, classify_pdf

# Using bundled FAISS (no root needed)
pdf = Path('Source_PDF/1040_US_FED.pdf')
print(get_label(pdf, 1))
print(get_suggestions(pdf, 1, topk=5))
print(classify_pdf(pdf, topk=1, max_pages=3))

# Or use an external dataset path
# root = Path('/path/to/project')
# print(get_label(pdf, 1, root=root))
```

Troubleshooting
- "Missing deps": install from `requirements-ml.txt`.
- "FAISS files not found": make sure you copied `dataset/v1/faiss/` and `ACTIVE_VERSION.txt` points to your available version (e.g., `v1.3`).
- "Page out of range": ensure the `page` argument is 1-based and within the PDF length.
- "OpenCLIP weights not found": the first run may try to download model weights. Run once on a machine with internet so they cache (typically under `~/.cache`), or set `OPENCLIP_HOME` to a folder containing the `ViT-B-32/laion2b_s34b_b79k` weights.
