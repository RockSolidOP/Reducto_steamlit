from __future__ import annotations

import json
import traceback
from pathlib import Path
from time import perf_counter
from typing import Protocol

import streamlit as st

from app.services.pymupdf_service import (
    parse_with_pymupdf,
    simple_page_dump,
    extract_text as pymupdf_extract_text,
)
from app.services.pymupdf_kv import extract_text_pymupdf, postprocess_pymupdf
from app.state.session import AppState
from app.ui.components import fmt_duration, pages_list_from_spec


class Tab(Protocol):
    """Minimal UI tab protocol."""

    name: str

    def render(self, state: AppState) -> None: ...


# Left-column expander removed. All PyMuPDF controls live inside the tab now.


def run_results(state: AppState, pdf_path: Path, page_number: int, page_count: int) -> None:
    """Execute PyMuPDF extraction and render results below the tabs."""
    st.divider()
    st.subheader("PyMuPDF Output (local extraction)")
    try:
        scope_all = bool(state.pym_scope_all)
        pages_spec = (state.pym_pages_spec or "").strip()
        pages_list = pages_list_from_spec(pages_spec, page_count) if pages_spec else []
        if pages_list:
            scope_label = f"pages {','.join(map(str, pages_list))}"
        else:
            scope_label = "all pages" if scope_all else "selected page"
        st.caption(f"PyMuPDF scope: {scope_label} • Selected page: {int(page_number)}")

        pm_t0 = perf_counter()
        if pages_list:
            parts = [pymupdf_extract_text(pdf_path, int(p)) for p in pages_list]
            normalized_text = "\n".join(parts)
        elif scope_all:
            normalized_text = extract_text_pymupdf(pdf_path)
        else:
            normalized_text = pymupdf_extract_text(pdf_path, int(page_number))
        pymupdf_api_secs = perf_counter() - pm_t0

        pm_pp0 = perf_counter()
        heur_kv = postprocess_pymupdf(normalized_text)
        pymupdf_post_secs = perf_counter() - pm_pp0

        st.caption(
            f"⏱️ PyMuPDF timings — Extraction: {fmt_duration(pymupdf_api_secs)} • Post-processing: {fmt_duration(pymupdf_post_secs)}"
        )

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### PyMuPDF Text (normalized)")
            st.text(normalized_text)
            st.download_button(
                "Download pymupdf_text.txt",
                data=normalized_text,
                file_name=f"pymupdf_text_page{int(page_number)}.txt",
                mime="text/plain",
            )
        with col2:
            st.markdown("#### PyMuPDF Heuristic Key-Values")
            st.json(heur_kv)
            st.download_button(
                "Download pymupdf_kv.json",
                data=json.dumps(heur_kv, ensure_ascii=False, indent=2),
                file_name=f"pymupdf_kv_page{int(page_number)}.json",
                mime="application/json",
            )

        with st.expander("Advanced: Raw PyMuPDF structures (selected page)"):
            adv_t0 = perf_counter()
            result = parse_with_pymupdf(pdf_path, int(page_number))
            adv_secs = perf_counter() - adv_t0
            st.markdown("- Page Dict")
            st.json(result.get("dict", {}))
            st.markdown("- Blocks")
            st.json(result.get("blocks", []))
            st.markdown("- Words")
            st.json(result.get("words", []))
            st.markdown("- Notebook-style Dump")
            dump_t0 = perf_counter()
            dump_txt = simple_page_dump(pdf_path, int(page_number))
            dump_secs = perf_counter() - dump_t0
            st.caption(
                f"Advanced timings — dict/blocks/words: {fmt_duration(adv_secs)} • dump: {fmt_duration(dump_secs)}"
            )
            st.text(dump_txt)
    except Exception:
        st.error("PyMuPDF extraction failed.")
        st.exception(traceback.format_exc())


class PyMuPDFTab:
    """PyMuPDF options and triggers UI (tab)."""

    name: str = "PyMuPDF"

    def render(self, state: AppState) -> None:  # noqa: D401 - documented in Protocol
        st.markdown("### PyMuPDF (local)")
        scope_all_opt = st.checkbox(
            "Analyze all pages (text + heuristics)",
            value=state.pym_scope_all,
            help="Unchecked: use the selected page only. Checked: use the entire document.",
            key="pym_scope_all_tab",
        )
        pym_pages_spec = st.text_input(
            "Pages (optional)",
            value=state.pym_pages_spec,
            help="Page list/ranges like 1,3,5-7. If set, overrides the toggle and selected page.",
            key="pym_pages_spec_tab",
        )
        if st.button("Run PyMuPDF", type="primary", key="btn_pym"):
            state.pym_scope_all = bool(scope_all_opt)
            state.pym_pages_spec = pym_pages_spec.strip()
            st.session_state["trigger_pymupdf"] = True
