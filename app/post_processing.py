from __future__ import annotations

import importlib
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

POST_PROCESSOR_PATH: Optional[str] = None
_pp = None  # module handle


def load_post_processor() -> Tuple[bool, Optional[Exception]]:
    """Attempt to import the external post processor and record state."""
    global _pp, POST_PROCESSOR_PATH
    try:
        import post_processors.post_processor as _pp  # your file in a package folder
        POST_PROCESSOR_PATH = getattr(_pp, "__file__", None)
        return True, None
    except Exception as e:
        POST_PROCESSOR_PATH = None
        return False, e


def reload_post_processor() -> Tuple[bool, Optional[str]]:
    """Reload the post processor module if previously loaded."""
    global _pp
    if _pp is None:
        ok, err = load_post_processor()
        if not ok:
            return False, f"Import failed: {err}"
        return True, None
    try:
        mod = importlib.reload(_pp)  # type: ignore
        globals()["_pp"] = mod
        return True, None
    except Exception as e:
        return False, f"Reload failed: {e}\n{traceback.format_exc()}"


def get_parser() -> Callable[[List[Dict[str, Any]]], Dict[str, Any]]:
    """Return the parse_page3_blocks_resilient function, or a fallback stub."""
    if _pp is None:
        load_post_processor()
    try:
        return getattr(_pp, "parse_page3_blocks_resilient")  # type: ignore
    except Exception:
        def _fallback(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
            return {
                "warning": "post_processors.post_processor not found or failed to import; returning blocks summary.",
                "blocks_count": len(blocks),
                "sample_keys": sorted({k for b in blocks for k in b.keys()})[:10],
            }
        return _fallback


def parse_page3_blocks_resilient(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Convenience shim so callers can import a stable name."""
    return get_parser()(blocks)

