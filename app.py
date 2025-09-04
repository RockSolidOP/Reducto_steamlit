from __future__ import annotations

import json
import tempfile
import traceback
from pathlib import Path

import fitz  # PyMuPDF
import streamlit as st

from app.post_processing import parse_page3_blocks_resilient
from app.services.reducto_service import (
    create_client,
    parse_document,
    _present_block_pages,
    _resolve_effective_page,
    extract_page_blocks,
    get_blocks_for_page,
)
from app.services.azure_service import (
    parse_with_azure,
    azure_to_dict,
    azure_kv_to_dict,
)
from app.ui.debug import debug_panel
from app.utils.pdf_preview import render_pdf_page_png_bytes


st.set_page_config(page_title="Reducto + Azure Doc AI GUI", layout="wide")
st.title("Reducto + Azure Doc AI GUI")
st.caption("Upload a PDF → pick a page → click **Process with Reducto** or **Process with Azure AI**.")

st.sidebar.markdown("### Settings")
st.sidebar.markdown("- Uses `REDUCTO_API_KEY`, `AZURE_DOC_AI_ENDPOINT`, `AZURE_DOC_AI_KEY` from env/.env.")
st.sidebar.markdown("- Only the selected page is parsed for Reducto (saves cost/time).")

debug_panel()

uploaded = st.file_uploader("Upload a PDF", type=["pdf"])  # type: ignore
if uploaded is None:
    st.info("👆 Upload a PDF to get started.")
    st.stop()

# Persist upload to a temp file
tmp_pdf = Path(tempfile.mkstemp(suffix=".pdf")[1])
tmp_pdf.write_bytes(uploaded.read())

# Determine page count to bound the selector
with fitz.open(tmp_pdf) as doc:
    page_count = doc.page_count

col_left, col_right = st.columns([1, 2], gap="large")
with col_left:
    page_number = st.number_input(
        "Page number",
        min_value=1,
        max_value=max(1, page_count),
        value=1,
        step=1,
    )
    c1, c2 = st.columns(2)
    with c1:
        do_process = st.button("Process with Reducto", type="primary")
    with c2:
        do_process_azure = st.button("Process with Azure AI")

# Always show a page preview (cheap UX win)
png_bytes = render_pdf_page_png_bytes(tmp_pdf, int(page_number), zoom=2.0)
with col_right:
    st.subheader(f"PDF Preview — Page {int(page_number)}")
    st.image(png_bytes, caption=f"Page {int(page_number)}", use_container_width=True)

# -------- Reducto path --------
if do_process:
    try:
        client = create_client()
        with st.status("Parsing with Reducto…", expanded=False) as status:
            parsed = parse_document(client, tmp_pdf, int(page_number))
            status.update(label="Parsing complete", state="complete")

        # Outputs
        st.divider()
        out1, out2, out3 = st.columns(3)

        with out1:
            st.markdown("#### 1) parsed (raw)")
            st.json(parsed)
            st.download_button(
                "Download parsed.json",
                data=json.dumps(parsed, ensure_ascii=False, indent=2),
                file_name=f"parsed_page{int(page_number)}.json",
                mime="application/json",
            )

        # Gather blocks/text for the requested page (with resilient page mapping)
        present_pages = _present_block_pages(parsed)
        effective_page = _resolve_effective_page(parsed, int(page_number))
        page_text_blocks = list(extract_page_blocks(parsed, int(page_number)))
        page_blocks = get_blocks_for_page(parsed, int(page_number))

        with out2:
            st.markdown("#### 2) block (text of that page)")
            if page_text_blocks:
                st.text("\n\n".join(page_text_blocks))
            else:
                st.info("No text blocks detected on this page.")
            # quick diagnostics + artifact
            st.markdown("**Block diagnostics**")
            st.write({
                "requested_page": int(page_number),
                "effective_page": int(effective_page),
                "present_pages": present_pages,
                "num_blocks": len(page_blocks),
                "text_blocks": len(page_text_blocks),
                "content_lengths": [len(b.get('content') or '') for b in page_blocks[:20]],
            })
            st.download_button(
                "Download page_blocks.json",
                data=json.dumps(page_blocks, ensure_ascii=False, indent=2),
                file_name=f"page{int(page_number)}_blocks.json",
                mime="application/json",
            )

        with out3:
            st.markdown("#### 3) parsed_info (post-processed)")
            try:
                parsed_info = parse_page3_blocks_resilient(page_blocks)
                st.json(parsed_info)
                st.download_button(
                    "Download parsed_info.json",
                    data=json.dumps(parsed_info, ensure_ascii=False, indent=2),
                    file_name=f"parsed_info_page{int(page_number)}.json",
                    mime="application/json",
                )
            except Exception:
                st.error("post_processor raised an exception. See traceback below.")
                st.exception(traceback.format_exc())

        # Reproducer (Reducto-only)
        st.divider()
        st.markdown("### 🔬 Minimal Reproducer (run parser on a single block)")
        idx = st.number_input(
            "Block index (0-based)",
            min_value=0,
            max_value=max(0, len(page_blocks) - 1),
            value=0,
            step=1,
        )
        if st.button("Run on single block"):
            blk = page_blocks[int(idx)]
            st.code((blk.get("content") or "")[:2000], language="text")
            try:
                out = parse_page3_blocks_resilient([blk])
                st.success("Parser returned:")
                st.json(out)
            except Exception:
                st.error("Parser crashed on this block.")
                st.exception(traceback.format_exc())

    except Exception:
        st.exception(traceback.format_exc())

# -------- Azure Doc AI path --------
if do_process_azure:
    st.divider()
    st.subheader("Azure Document AI Output")

    try:
        with st.status("Analyzing with Azure Document Intelligence…", expanded=False) as status:
            azure_result = parse_with_azure(tmp_pdf, int(page_number))
            status.update(label=f"Azure analysis complete (page {int(page_number)})", state="complete")

        st.caption(f"Azure analyzed page: {int(page_number)}")

        colA, colB = st.columns(2)

        with colA:
            st.markdown("#### Raw Azure Output")
            az_raw = azure_to_dict(azure_result)
            st.json(az_raw)
            st.download_button(
                "Download azure_raw.json",
                data=json.dumps(az_raw, ensure_ascii=False, indent=2),
                file_name=f"azure_raw_page{int(page_number)}.json",
                mime="application/json",
            )

        with colB:
            st.markdown("#### Azure Key-Value Pairs (flattened)")
            kv_dict = azure_kv_to_dict(azure_result)
            st.json(kv_dict)
            st.download_button(
                "Download azure_kv.json",
                data=json.dumps(kv_dict, ensure_ascii=False, indent=2),
                file_name=f"azure_kv_page{int(page_number)}.json",
                mime="application/json",
            )

    except Exception as e:
        st.error(f"Azure Doc AI error: {e}")
        st.exception(traceback.format_exc())

# If neither button clicked, just show preview + wait for action
if not (do_process or do_process_azure):
    st.info("Select a page and click **Process with Reducto** or **Process with Azure AI**.")
