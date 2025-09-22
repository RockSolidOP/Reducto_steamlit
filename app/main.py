from __future__ import annotations

import traceback
from pathlib import Path

import fitz  # PyMuPDF
import streamlit as st

from app.config import UPLOADS_CLEANUP, AZURE_CONFIG
from app.state.session import get_state
from app.ui.azure_tab import AzureTab, render_cost_estimator
from app.ui.pymupdf_tab import PyMuPDFTab, run_results as run_pymupdf
from app.ui.reducto_tab import ReductoTab, run_results as run_reducto
from app.ui.classify_tab import ClassifyTab
from app.ui.debug import debug_panel
from app.ui.components import file_uploader
from app.utils.pdf_preview import render_pdf_page_png_bytes
from app.utils.storage import (
    save_uploaded_file,
    cleanup_uploads,
    format_bytes,
    dir_size_bytes,
    get_uploads_dir,
)


def run() -> None:
    """Main Streamlit app orchestrator: layout, tabs, and run triggers."""
    st.set_page_config(page_title="Reducto + Azure Doc AI GUI", layout="wide")
    st.title("Reducto + Azure Doc AI GUI")
    st.caption("Upload a PDF → pick a page → run a processor from the tabs.")

    st.sidebar.markdown("### Settings")
    st.sidebar.markdown(
        "- Uses `REDUCTO_API_KEY`, `AZURE_DOC_AI_ENDPOINT`, `AZURE_DOC_AI_KEY` from env/.env."
    )
    st.sidebar.markdown(
        "- Only the selected page is parsed for Reducto (saves cost/time)."
    )

    # Debug section
    debug_panel()

    state = get_state()

    # Upload / reuse
    uploaded = file_uploader()
    pdf_path: Path | None = None
    if uploaded is not None:
        pdf_path = save_uploaded_file(uploaded)
        state.pdf_path = str(pdf_path)
        st.caption(f"Saved locally: {pdf_path}")
    else:
        prev = state.pdf_path
        if prev and Path(prev).exists():
            pdf_path = Path(prev)
            st.caption(f"Using previous upload: {pdf_path}")
        else:
            st.info("👆 Upload a PDF to get started.")
            st.stop()

    # Housekeeping
    if UPLOADS_CLEANUP.get("enabled", False):
        summary = cleanup_uploads(
            max_age_days=UPLOADS_CLEANUP.get("max_age_days"),
            max_total_size_mb=UPLOADS_CLEANUP.get("max_total_size_mb"),
            max_files=UPLOADS_CLEANUP.get("max_files"),
        )
        if summary["deleted_count"]:
            st.caption(
                f"Uploads cleanup: deleted {summary['deleted_count']} file(s), freed {format_bytes(summary['freed_bytes'])}."
            )
        uploads_dir = get_uploads_dir()
        st.sidebar.caption(
            f"Uploads dir: {uploads_dir} • Size: {format_bytes(dir_size_bytes(uploads_dir))}"
        )

    # Determine page count
    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count

    # Two-column layout
    col_left, col_right = st.columns([1, 2], gap="large")
    with col_left:
        page_number = st.number_input(
            "Page number",
            min_value=1,
            max_value=max(1, page_count),
            value=1,
            step=1,
        )
        # Persist in session to allow sub-tabs to reference current context
        st.session_state["page_number_ui"] = int(page_number)
        st.session_state["page_count_ui"] = int(page_count)

        # Options expanders removed: controls are available inside each tab

    # Always show a page preview
    png_bytes = render_pdf_page_png_bytes(pdf_path, int(page_number), zoom=2.0)
    with col_right:
        st.subheader(f"PDF Preview — Page {int(page_number)}")
        st.image(png_bytes, caption=f"Page {int(page_number)}", use_container_width=True)

    # Sidebar: Azure cost estimate
    render_cost_estimator(page_count=page_count, current_page=int(page_number))

    # Tabs registry
    registry = [ClassifyTab(), ReductoTab(), AzureTab(), PyMuPDFTab()]
    tab_names = [t.name for t in registry]
    ctxs = st.tabs(tab_names)
    for t, ctx in zip(registry, ctxs):
        with ctx:
            t.render(state)

    # Gather triggers from tabs only
    do_process = bool(st.session_state.pop("trigger_reducto", False))
    do_process_azure = bool(st.session_state.pop("trigger_azure_default", False))
    do_process_pymupdf = bool(st.session_state.pop("trigger_pymupdf", False))

    # Execute runs (below tabs), preserving original prompts
    if do_process:
        run_reducto(state, pdf_path, int(page_number))

    if do_process_azure:
        from app.ui.azure_tab import run_default_results  # local import to avoid cycles

        run_default_results(state, pdf_path, int(page_number))

    if not (do_process or do_process_azure or do_process_pymupdf):
        st.info("Open a tab above, adjust options, and click Run.")

    if do_process_pymupdf:
        run_pymupdf(state, pdf_path, int(page_number), page_count)


if __name__ == "__main__":
    run()
