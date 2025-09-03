from __future__ import annotations
import os, re, json, hashlib
from pathlib import Path
from typing import Tuple

# Root can be overridden via env
APP_STORAGE_DIR = os.getenv("APP_STORAGE_DIR", "./storage")

ROOT     = Path(APP_STORAGE_DIR).resolve()
UPLOADS  = ROOT / "uploads"
PARSED   = ROOT / "parsed"
PREVIEWS = ROOT / "previews"

for d in (UPLOADS, PARSED, PREVIEWS):
    d.mkdir(parents=True, exist_ok=True)

def save_upload(filename: str, data: bytes) -> Tuple[Path, str]:
    """Write uploaded bytes to disk; returns (path, sha12)."""
    sha = hashlib.sha256(data).hexdigest()[:12]
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", filename or "upload.pdf")
    path = UPLOADS / f"{sha}__{safe}"
    path.write_bytes(data)
    return path, sha

def save_parsed(sha: str, page: int, payload: dict) -> Path:
    """Persist parsed JSON to disk; returns json path."""
    p = PARSED / f"{sha}__page{page}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p

def save_preview(sha: str, page: int, png_bytes: bytes) -> Path:
    """Persist preview PNG to disk; returns image path."""
    p = PREVIEWS / f"{sha}__page{page}.png"
    p.write_bytes(png_bytes)
    return p

__all__ = [
    "save_upload", "save_parsed", "save_preview",
    "ROOT", "UPLOADS", "PARSED", "PREVIEWS",
]
