from __future__ import annotations

import json
import traceback
from pathlib import Path
from time import perf_counter


def _fmt_duration(seconds: float) -> str:
    """Format durations with adaptive units: μs, ms, s, or m:s."""
    try:
        s = float(seconds)
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
from app.utils.storage import save_uploaded_file, cleanup_uploads, format_bytes, dir_size_bytes, get_uploads_dir
from app.config import UPLOADS_CLEANUP
from app.services.pymupdf_service import parse_with_pymupdf, simple_page_dump
from app.services.pymupdf_kv import extract_text_pymupdf, postprocess_pymupdf


st.set_page_config(page_title="Reducto + Azure Doc AI GUI", layout="wide")
st.title("Reducto + Azure Doc AI GUI")
st.caption("Upload a PDF → pick a page → click **Process with Reducto** or **Process with Azure AI**.")

st.sidebar.markdown("### Settings")
st.sidebar.markdown("- Uses `REDUCTO_API_KEY`, `AZURE_DOC_AI_ENDPOINT`, `AZURE_DOC_AI_KEY` from env/.env.")
st.sidebar.markdown("- Only the selected page is parsed for Reducto (saves cost/time).")

debug_panel()

uploaded = st.file_uploader("Upload a PDF (saved locally)", type=["pdf"])  # type: ignore
if uploaded is None:
    st.info("👆 Upload a PDF to get started.")
    st.stop()

# Persist upload to a local folder (./uploads)
pdf_path: Path = save_uploaded_file(uploaded)
st.caption(f"Saved locally: {pdf_path}")

# Housekeeping: clean uploads according to policy
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
    # Display current usage in sidebar
    uploads_dir = get_uploads_dir()
    st.sidebar.caption(
        f"Uploads dir: {uploads_dir} • Size: {format_bytes(dir_size_bytes(uploads_dir))}"
    )

# Determine page count to bound the selector
with fitz.open(pdf_path) as doc:
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
    do_process_pymupdf = st.button("Process with PyMuPDF (local)")

# Always show a page preview (cheap UX win)
png_bytes = render_pdf_page_png_bytes(pdf_path, int(page_number), zoom=2.0)
with col_right:
    st.subheader(f"PDF Preview — Page {int(page_number)}")
    st.image(png_bytes, caption=f"Page {int(page_number)}", use_container_width=True)

# -------- Reducto path --------
if do_process:
    try:
        client = create_client()
        with st.status("Parsing with Reducto…", expanded=False) as status:
            t0 = perf_counter()
            parsed = parse_document(client, pdf_path, int(page_number))
            reducto_api_secs = perf_counter() - t0
            status.update(label="Parsing complete", state="complete")

        # Outputs
        st.divider()
        st.caption(f"⏱️ Reducto timings — API: {_fmt_duration(reducto_api_secs)}")
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
                tpp0 = perf_counter()
                parsed_info = parse_page3_blocks_resilient(page_blocks)
                reducto_post_secs = perf_counter() - tpp0
                st.caption(f"Post-processing time: {_fmt_duration(reducto_post_secs)}")
                st.json(parsed_info)
                # Optional: per-block post-processing timing summary
                try:
                    per_blk_times = []
                    for blk in page_blocks:
                        _t0 = perf_counter()
                        _ = parse_page3_blocks_resilient([blk])
                        per_blk_times.append(perf_counter() - _t0)
                    if per_blk_times:
                        cnt = len(per_blk_times)
                        avg = sum(per_blk_times) / cnt
                        mx = max(per_blk_times)
                        st.caption(
                            f"Per-block post-processing — count: {cnt} • avg: {_fmt_duration(avg)} • max: {_fmt_duration(mx)}"
                        )
                except Exception:
                    # Non-fatal: ignore timing errors
                    pass
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
                _t0 = perf_counter()
                out = parse_page3_blocks_resilient([blk])
                _secs = perf_counter() - _t0
                st.success("Parser returned:")
                st.caption(f"Per-block post-processing time: {_fmt_duration(_secs)}")
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
            az_t0 = perf_counter()
            azure_result = parse_with_azure(pdf_path, int(page_number))
            azure_api_secs = perf_counter() - az_t0
            status.update(label=f"Azure analysis complete (page {int(page_number)})", state="complete")

        st.caption(f"Azure analyzed page: {int(page_number)}")
        st.caption(f"⏱️ Azure timings — API: {_fmt_duration(azure_api_secs)}")

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
            kv_t0 = perf_counter()
            kv_dict = azure_kv_to_dict(azure_result)
            azure_post_secs = perf_counter() - kv_t0
            st.caption(f"Post-processing time: {_fmt_duration(azure_post_secs)}")
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

# -------- PyMuPDF (local) path --------
if do_process_pymupdf:
    st.divider()
    st.subheader("PyMuPDF Output (local extraction)")
    try:
        # 1) Normalized plain text from PyMuPDF (all pages)
        pm_t0 = perf_counter()
        normalized_text = extract_text_pymupdf(pdf_path)
        pymupdf_api_secs = perf_counter() - pm_t0
        # 2) Heuristic KVs derived from that text
        pm_pp0 = perf_counter()
        heur_kv = postprocess_pymupdf(normalized_text)
        pymupdf_post_secs = perf_counter() - pm_pp0

        st.caption(
            f"⏱️ PyMuPDF timings — Extraction: {_fmt_duration(pymupdf_api_secs)} • Post-processing: {_fmt_duration(pymupdf_post_secs)}"
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

        with st.expander("Advanced: Raw PyMuPDF structures"):
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
            st.caption(f"Advanced timings — dict/blocks/words: {_fmt_duration(adv_secs)} • dump: {_fmt_duration(dump_secs)}")
            st.text(dump_txt)
    except Exception:
        st.error("PyMuPDF extraction failed.")
        st.exception(traceback.format_exc())
