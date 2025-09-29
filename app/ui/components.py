from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

import streamlit as st


def fmt_duration(seconds: float | int | None) -> str:
    """Return a human-readable duration string with adaptive units.

    Examples: "120 μs", "3.1 ms", "0.532 s", "2m 10.0s".
    """
    try:
        s = float(seconds) if seconds is not None else 0.0
    except Exception:
        return "-"
    if s < 1e-6:
        return f"{s * 1e9:.0f} ns"
    if s < 1e-3:
        return f"{s * 1e6:.1f} μs"
    if s < 1:
        return f"{s * 1e3:.1f} ms"
    if s < 60:
        return f"{s:.3f} s"
    m, r = divmod(s, 60)
    if m < 60:
        return f"{int(m)}m {r:.1f}s"
    h, m = divmod(int(m), 60)
    return f"{h}h {m}m {r:.0f}s"


def file_uploader(label: str = "Upload a PDF (saved locally)", *, types: Optional[Iterable[str]] = ("pdf",)):
    """Thin wrapper over Streamlit's file_uploader for consistency.

    Returns the UploadedFile or None.
    """
    return st.file_uploader(label, type=list(types or ()))


def download_json_button(label: str, *, data: Dict[str, Any] | list[Any], filename: str) -> None:
    """Render a standardized JSON download button with pretty formatting."""
    st.download_button(
        label,
        data=json.dumps(data, ensure_ascii=False, indent=2),
        file_name=filename,
        mime="application/json",
    )


def count_pages_from_spec(spec: str | None, total_pages: int) -> int:
    """Return number of pages represented by an Azure-style pages spec string.

    Accepts tokens like "6", "18-19", or "1,3,5-7" and clamps to document bounds.
    """
    if not spec:
        return 1
    spec = spec.strip()
    if not spec:
        return 1
    pages: set[int] = set()
    for part in spec.split(","):
        p = part.strip()
        if not p:
            continue
        if "-" in p:
            a, b = p.split("-", 1)
            try:
                start = max(1, min(total_pages, int(a)))
                end = max(1, min(total_pages, int(b)))
            except ValueError:
                continue
            if end < start:
                start, end = end, start
            for i in range(start, end + 1):
                pages.add(i)
        else:
            try:
                i = max(1, min(total_pages, int(p)))
                pages.add(i)
            except ValueError:
                continue
    return max(1, len(pages) or 1)


def pages_list_from_spec(spec: str | None, total_pages: int) -> list[int]:
    """Return sorted page numbers from a pages spec string.

    Returns an empty list if no valid tokens are present.
    """
    if not spec:
        return []
    spec = spec.strip()
    if not spec:
        return []
    pages: set[int] = set()
    for part in spec.split(","):
        p = part.strip()
        if not p:
            continue
        if "-" in p:
            a, b = p.split("-", 1)
            try:
                start = max(1, min(total_pages, int(a)))
                end = max(1, min(total_pages, int(b)))
            except ValueError:
                continue
            if end < start:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            try:
                i = max(1, min(total_pages, int(p)))
                pages.add(i)
            except ValueError:
                continue
    return sorted(pages)
