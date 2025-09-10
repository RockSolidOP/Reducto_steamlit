from __future__ import annotations

import streamlit as st

from app.state.session import get_state
from app.ui.azure_tab import AzureTab
from app.ui.reducto_tab import ReductoTab
from app.ui.pymupdf_tab import PyMuPDFTab


def _prepare_state() -> None:
    # Minimal UI context expected by tab renderers
    st.session_state.setdefault("page_number_ui", 1)
    st.session_state.setdefault("page_count_ui", 1)


def test_tabs_render_smoke() -> None:
    _prepare_state()
    state = get_state()

    tabs = [ReductoTab(), AzureTab(), PyMuPDFTab()]

    for tab in tabs:
        assert hasattr(tab, "name")
        # Render into an ephemeral container to avoid polluting root
        placeholder = st.empty()
        with placeholder.container():
            tab.render(state)

