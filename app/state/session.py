"""Session-backed typed state for the Streamlit app.

This module defines:
- AppState: a dataclass that mirrors keys stored in ``st.session_state`` and
  provides write-through behavior (assigning to fields updates session_state).
- get_state(): ensures default values are present in ``st.session_state`` and
  returns a mutable AppState view bound to it.

UI code should call ``get_state()`` once per run and pass the resulting
``AppState`` into tab renderers and helpers instead of reading/writing raw
``st.session_state`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, MutableMapping, Optional, Tuple

import streamlit as st

from app.config import AZURE_CONFIG


_KEYS: Tuple[str, ...] = (
    "pdf_path",
    "use_reducto_range",
    "reducto_start_page",
    "reducto_end_page",
    "azure_default_model",
    "azure_default_pages",
    "azure_1040_model",
    "azure_1040_pages",
    "pym_scope_all",
    "pym_pages_spec",
)


@dataclass
class AppState:
    """A typed, mutable view over Streamlit's session_state for this app.

    Assigning to any field also updates `st.session_state` under the same key.
    """

    _ss: MutableMapping[str, Any] = field(repr=False)

    # Session keys
    pdf_path: Optional[str] = None
    use_reducto_range: bool = False
    reducto_start_page: int = 1
    reducto_end_page: int = 1
    azure_default_model: str = "prebuilt-document"
    azure_default_pages: str = ""
    azure_1040_model: str = "prebuilt-tax.us.1040"
    azure_1040_pages: str = ""
    pym_scope_all: bool = False
    pym_pages_spec: str = ""

    def __post_init__(self) -> None:
        """Align dataclass fields with the underlying session state on construction."""
        for k in _KEYS:
            if k in self._ss:
                object.__setattr__(self, k, self._ss[k])

    def __setattr__(self, name: str, value: Any) -> None:
        """Write-through setter: updates both the dataclass field and session_state."""
        if name.startswith("_") or name not in _KEYS:
            object.__setattr__(self, name, value)
            return
        object.__setattr__(self, name, value)
        self._ss[name] = value

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary snapshot of the current app state values."""
        return {k: getattr(self, k) for k in _KEYS}


def get_state() -> AppState:
    """Ensure defaults in Streamlit session_state and return a typed AppState view."""
    ss = st.session_state

    # Core defaults
    ss.setdefault("pdf_path", None)
    ss.setdefault("use_reducto_range", False)
    ss.setdefault("reducto_start_page", 1)
    ss.setdefault("reducto_end_page", 1)

    # Azure defaults from config
    ss.setdefault("azure_default_model", AZURE_CONFIG.get("model_id", "prebuilt-document"))
    ss.setdefault("azure_default_pages", "")
    ss.setdefault("azure_1040_model", AZURE_CONFIG.get("model_id_1040", "prebuilt-tax.us.1040"))
    ss.setdefault("azure_1040_pages", "")

    # PyMuPDF defaults
    ss.setdefault("pym_scope_all", False)
    ss.setdefault("pym_pages_spec", "")

    return AppState(
        _ss=ss,
        pdf_path=ss["pdf_path"],
        use_reducto_range=ss["use_reducto_range"],
        reducto_start_page=int(ss["reducto_start_page"]),
        reducto_end_page=int(ss["reducto_end_page"]),
        azure_default_model=str(ss["azure_default_model"]),
        azure_default_pages=str(ss["azure_default_pages"]),
        azure_1040_model=str(ss["azure_1040_model"]),
        azure_1040_pages=str(ss["azure_1040_pages"]),
        pym_scope_all=bool(ss["pym_scope_all"]),
        pym_pages_spec=str(ss["pym_pages_spec"]),
    )
