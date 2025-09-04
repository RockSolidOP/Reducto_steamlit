from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Dict, Any


def get_uploads_dir() -> Path:
    """Return the persistent uploads directory, creating it if missing."""
    d = Path("uploads")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sanitize_filename(name: str) -> str:
    # Keep alnum, dash, underscore, dot; collapse others to underscore
    name = name.strip().replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    # Avoid hidden files and empty names
    return name or "file"


def save_uploaded_file(uploaded_file) -> Path:
    """Persist a Streamlit UploadedFile to the local uploads directory.

    Returns the saved file path (unique timestamped name to avoid collisions).
    """
    uploads = get_uploads_dir()
    original = getattr(uploaded_file, "name", "uploaded.pdf")
    safe = _sanitize_filename(original)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if "." in safe:
        stem = safe.rsplit(".", 1)[0]
        suffix = "." + safe.rsplit(".", 1)[1]
    else:
        stem = safe
        suffix = ""
    final_name = f"{stem}-{ts}{suffix}"
    path = uploads / final_name
    path.write_bytes(uploaded_file.read())
    return path


def list_uploaded_files() -> Iterable[Path]:
    uploads = get_uploads_dir()
    return sorted(uploads.glob("*"))


def dir_size_bytes(path: Path | None = None) -> int:
    d = path or get_uploads_dir()
    total = 0
    for p in d.glob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except FileNotFoundError:
            # File might be removed concurrently
            continue
    return total


def format_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for u in units:
        if size < 1024.0 or u == units[-1]:
            return f"{size:.1f} {u}"
        size /= 1024.0
    return f"{n} B"


def cleanup_uploads(
    *,
    max_age_days: int | None = None,
    max_total_size_mb: int | None = None,
    max_files: int | None = None,
) -> Dict[str, Any]:
    """Delete old uploads to control disk usage.

    Strategy:
    1) Age-based deletion (older than `max_age_days`).
    2) Size cap: if total > `max_total_size_mb`, delete oldest until under cap.
    3) Count cap: keep only most recent `max_files`.
    Returns a summary with counts and bytes freed.
    """
    uploads = get_uploads_dir()
    files: List[Path] = [p for p in uploads.glob("*") if p.is_file()]

    entries = []
    for f in files:
        try:
            st = f.stat()
            entries.append({"path": f, "mtime": st.st_mtime, "size": st.st_size})
        except FileNotFoundError:
            pass

    deleted = []
    freed_bytes = 0

    # 1) Age purge
    if max_age_days is not None and max_age_days >= 0:
        cutoff = time.time() - max_age_days * 86400
        to_delete = [e for e in entries if e["mtime"] < cutoff]
        for e in sorted(to_delete, key=lambda x: x["mtime"]):
            try:
                e["path"].unlink(missing_ok=True)
                deleted.append(e["path"])
                freed_bytes += e["size"]
            except Exception:
                pass
        # Keep survivors
        survivors = [e for e in entries if e["mtime"] >= cutoff]
        entries = survivors

    # Recompute totals
    total_size = sum(e["size"] for e in entries)

    # 2) Size cap purge
    if max_total_size_mb is not None and max_total_size_mb >= 0:
        cap = max_total_size_mb * 1024 * 1024
        if total_size > cap:
            for e in sorted(entries, key=lambda x: x["mtime"]):  # oldest first
                if total_size <= cap:
                    break
                try:
                    e["path"].unlink(missing_ok=True)
                    deleted.append(e["path"])
                    total_size -= e["size"]
                    freed_bytes += e["size"]
                except Exception:
                    pass
            # Refresh survivors after deletion
            survivors = [e for e in entries if e["path"].exists()]
            entries = survivors

    # 3) Count cap purge
    if max_files is not None and max_files >= 0:
        if len(entries) > max_files:
            extras = sorted(entries, key=lambda x: x["mtime"])[: len(entries) - max_files]
            for e in extras:
                try:
                    e["path"].unlink(missing_ok=True)
                    deleted.append(e["path"])
                    freed_bytes += e["size"]
                except Exception:
                    pass
            survivors = [e for e in entries if e not in extras and e["path"].exists()]
            entries = survivors

    return {
        "deleted_count": len(deleted),
        "freed_bytes": int(freed_bytes),
        "remaining_count": len(entries),
        "remaining_size_bytes": int(sum(e["size"] for e in entries if e["path"].exists())),
    }

