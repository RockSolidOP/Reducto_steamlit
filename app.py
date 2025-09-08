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
    parse_with_azure_docint,
    azure_to_dict,
    azure_kv_to_dict,
    azure_fields_to_dict,
)
from app.ui.debug import debug_panel
from app.utils.pdf_preview import render_pdf_page_png_bytes
from app.utils.storage import (
    save_uploaded_file,
    cleanup_uploads,
    format_bytes,
    dir_size_bytes,
    get_uploads_dir,
)
from app.config import UPLOADS_CLEANUP, AZURE_CONFIG
from app.services.pymupdf_service import (
    parse_with_pymupdf,
    simple_page_dump,
    extract_text as pymupdf_extract_text,
)
from app.services.pymupdf_kv import extract_text_pymupdf, postprocess_pymupdf


st.set_page_config(page_title="Reducto + Azure Doc AI GUI", layout="wide")
st.title("Reducto + Azure Doc AI GUI")
st.caption("Upload a PDF → pick a page → run a processor from the tabs.")

st.sidebar.markdown("### Settings")
st.sidebar.markdown("- Uses `REDUCTO_API_KEY`, `AZURE_DOC_AI_ENDPOINT`, `AZURE_DOC_AI_KEY` from env/.env.")
st.sidebar.markdown("- Only the selected page is parsed for Reducto (saves cost/time).")

# Azure model selection (overrides config at runtime)
## Azure model/pages selection moved out of sidebar per user preference

debug_panel()


# -----------------
# Helper functions
# -----------------
def _count_pages_from_spec(spec: str | None, total_pages: int) -> int:
    if not spec:
        return 1
    spec = spec.strip()
    if not spec:
        return 1
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start = max(1, min(total_pages, int(a)))
                end = max(1, min(total_pages, int(b)))
            except ValueError:
                continue
            if end < start:
                start, end = end, start
            for p in range(start, end + 1):
                pages.add(p)
        else:
            try:
                p = max(1, min(total_pages, int(part)))
                pages.add(p)
            except ValueError:
                continue
    return max(1, len(pages) or 1)


def _pages_list_from_spec(spec: str | None, total_pages: int) -> list[int]:
    if not spec:
        return []
    spec = spec.strip()
    if not spec:
        return []
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
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
                p = max(1, min(total_pages, int(part)))
                pages.add(p)
            except ValueError:
                continue
    return sorted(pages)


def run_azure_analysis(
    pdf_path: Path,
    page_number: int,
    model_id: str,
    pages: str | None = None,
    prefer_docint: bool = False,
):
    """Analyze with Azure. Prefer Document Intelligence if requested, otherwise Form Recognizer.

    Falls back between clients on errors (e.g., ModelNotFound).
    Returns (result, elapsed_secs, model_id_used, pages_used).
    """
    pages_used = pages or str(int(page_number))
    def _try_docint():
        return parse_with_azure_docint(pdf_path, int(page_number), model_id=model_id, pages=pages)

    def _try_fr():
        return parse_with_azure(pdf_path, int(page_number), model_id=model_id, pages=pages)

    order = (_try_docint, _try_fr) if prefer_docint else (_try_fr, _try_docint)
    last_exc = None
    for fn in order:
        try:
            t0 = perf_counter()
            res = fn()
            return res, (perf_counter() - t0), model_id, pages_used
        except Exception as e:  # fall through to alternate client
            last_exc = e
            if "ModelNotFound" not in str(e) and "Resource not found" not in str(e):
                # Non-model errors: keep propagating after trying both
                continue
    if last_exc:
        raise last_exc


def render_azure_outputs(result, page_number: int, label_prefix: str = "Azure"):
    st.caption(f"⏱️ {label_prefix} timings shown above")
    colA, colB = st.columns(2)
    with colA:
        st.markdown(f"#### Raw {label_prefix} Output")
        az_raw = azure_to_dict(result)
        st.json(az_raw)
        st.download_button(
            f"Download {label_prefix.lower()}_raw.json",
            data=json.dumps(az_raw, ensure_ascii=False, indent=2),
            file_name=f"{label_prefix.lower()}_raw_page{int(page_number)}.json",
            mime="application/json",
        )
    with colB:
        st.markdown(f"#### {label_prefix} Key-Values / Fields")
        kv_t0 = perf_counter()
        kv_dict = azure_kv_to_dict(result)
        fields_dict = azure_fields_to_dict(result)
        post_secs = perf_counter() - kv_t0
        st.caption(f"Post-processing time: {_fmt_duration(post_secs)}")
        st.markdown("- Key-Value Pairs")
        st.json(kv_dict)
        st.markdown("- Document Fields")
        st.json(fields_dict)
        st.download_button(
            f"Download {label_prefix.lower()}_kv.json",
            data=json.dumps(kv_dict, ensure_ascii=False, indent=2),
            file_name=f"{label_prefix.lower()}_kv_page{int(page_number)}.json",
            mime="application/json",
        )
        st.download_button(
            f"Download {label_prefix.lower()}_fields.json",
            data=json.dumps(fields_dict, ensure_ascii=False, indent=2),
            file_name=f"{label_prefix.lower()}_fields_page{int(page_number)}.json",
            mime="application/json",
        )

uploaded = st.file_uploader("Upload a PDF (saved locally)", type=["pdf"])  # type: ignore
pdf_path: Path | None = None
if uploaded is not None:
    # Persist upload to a local folder (./uploads) and remember it
    pdf_path = save_uploaded_file(uploaded)
    st.session_state["pdf_path"] = str(pdf_path)
    st.caption(f"Saved locally: {pdf_path}")
else:
    # Reuse last uploaded file if available
    prev = st.session_state.get("pdf_path")
    if prev and Path(prev).exists():
        pdf_path = Path(prev)
        st.caption(f"Using previous upload: {pdf_path}")
    else:
        st.info("👆 Upload a PDF to get started.")
        st.stop()

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
    # Processing triggers will be shown in tabs below
    do_process = False
    do_process_azure = False
    do_process_pymupdf = False

    # Consistent options panels
    with st.expander("Reducto Options", expanded=False):
        r_col1, r_col2 = st.columns(2)
        with r_col1:
            red_start = st.number_input("Start page", min_value=1, max_value=max(1, page_count), value=int(page_number), step=1, key="red_start")
        with r_col2:
            red_end = st.number_input("End page", min_value=1, max_value=max(1, page_count), value=int(page_number), step=1, key="red_end")
        run_reducto_adv = st.button("Run Reducto", type="primary", key="run_reducto_adv")
        if run_reducto_adv:
            st.session_state["use_reducto_range"] = True
            st.session_state["reducto_start_page"] = int(red_start)
            st.session_state["reducto_end_page"] = int(red_end)
            # trigger execution below
            do_process = True

    with st.expander("Azure AI Options", expanded=False):
        a_col1, a_col2 = st.columns(2)
        with a_col1:
            az_model_opt = st.text_input(
                "Model id",
                value=st.session_state.get("azure_default_model", AZURE_CONFIG.get("model_id", "prebuilt-document")),
                help="e.g., prebuilt-document, prebuilt-invoice, or a custom model id",
                key="az_default_model_input",
            )
        with a_col2:
            az_pages_opt = st.text_input(
                "Pages (optional)",
                value=st.session_state.get("azure_default_pages", ""),
                help="Azure pages string like 6 or 18-19 or 1,3,5-7",
                key="az_default_pages_input",
            )
        run_azure_adv = st.button("Run Azure AI", type="primary", key="run_azure_adv")
        if run_azure_adv:
            st.session_state["azure_default_model"] = az_model_opt.strip()
            st.session_state["azure_default_pages"] = az_pages_opt.strip()
            do_process_azure = True

    with st.expander("PyMuPDF Options", expanded=False):
        scope_all_opt = st.checkbox(
            "Analyze all pages (text + heuristics)",
            value=st.session_state.get("pym_scope_all", False),
            help="Unchecked: use the selected page only. Checked: use the entire document.",
            key="pym_scope_all_opt",
        )
        run_pymupdf_adv = st.button("Run PyMuPDF", type="primary", key="run_pymupdf_adv")
        if run_pymupdf_adv:
            st.session_state["pym_scope_all"] = bool(scope_all_opt)
            do_process_pymupdf = True

# Always show a page preview (cheap UX win)
png_bytes = render_pdf_page_png_bytes(pdf_path, int(page_number), zoom=2.0)
with col_right:
    st.subheader(f"PDF Preview — Page {int(page_number)}")
    st.image(png_bytes, caption=f"Page {int(page_number)}", use_container_width=True)

## (helpers defined above)

# Sidebar: Azure simple cost estimator
st.sidebar.markdown("#### Azure Cost Estimate")
per_page_price = st.sidebar.number_input(
    "Price per page ($)", min_value=0.0, value=0.10, step=0.01, format="%.2f"
)
def _est_line(label: str, pages_spec: str | None):
    n = _count_pages_from_spec(pages_spec, page_count)
    cost = n * per_page_price
    st.sidebar.write(f"{label}: {n} page(s) → ${cost:.2f}")
_est_line("Default Azure", st.session_state.get("azure_default_pages") or str(int(page_number)))
_est_line("Azure 1040", st.session_state.get("azure_1040_pages") or str(int(page_number)))

# Tabs for processors
tab_red, tab_az, tab_1040, tab_pym = st.tabs([
    "Reducto", "Azure AI", "Azure 1040", "PyMuPDF",
])

with tab_red:
    st.markdown("### Reducto")
    r_col1, r_col2 = st.columns(2)
    with r_col1:
        red_start = st.number_input("Start page", min_value=1, max_value=max(1, page_count), value=int(page_number), step=1, key="red_start_tab")
    with r_col2:
        red_end = st.number_input("End page", min_value=1, max_value=max(1, page_count), value=int(page_number), step=1, key="red_end_tab")
    c_run_r1, c_run_r2 = st.columns(2)
    with c_run_r1:
        run_red_page = st.button("Run Reducto (selected page)", type="primary", key="btn_red_page")
    with c_run_r2:
        run_red_range = st.button("Run Reducto (range)", type="primary", key="btn_red_range")
    if run_red_range:
        st.session_state["use_reducto_range"] = True
        st.session_state["reducto_start_page"] = int(red_start)
        st.session_state["reducto_end_page"] = int(red_end)
        do_process = True
    elif run_red_page:
        st.session_state["use_reducto_range"] = False
        do_process = True

with tab_az:
    st.markdown("### Azure Document AI")
    a_col1, a_col2 = st.columns(2)
    with a_col1:
        az_model_opt = st.text_input(
            "Model id",
            value=st.session_state.get("azure_default_model", AZURE_CONFIG.get("model_id", "prebuilt-document")),
            help="e.g., prebuilt-document, prebuilt-invoice, or a custom model id",
            key="az_model_tab",
        )
    with a_col2:
        az_pages_opt = st.text_input(
            "Pages (optional)",
            value=st.session_state.get("azure_default_pages", ""),
            help="Azure pages string like 6 or 18-19 or 1,3,5-7",
            key="az_pages_tab",
        )
    if st.button("Run Azure AI", type="primary", key="btn_az_default"):
        st.session_state["azure_default_model"] = az_model_opt.strip()
        st.session_state["azure_default_pages"] = az_pages_opt.strip()
        do_process_azure = True

with tab_1040:
    st.markdown("### Azure 1040 (prebuilt)")
    default_model_1040 = AZURE_CONFIG.get("model_id_1040", "prebuilt-tax.us.1040")
    # Keep only 1040 by default; allow Custom for future additions
    known_tax_models = [
        "prebuilt-tax.us.1040",
        "Custom…",
    ]
    prev_model = st.session_state.get("azure_1040_model", default_model_1040)
    initial_choice = prev_model if prev_model in known_tax_models else "Custom…"
    choice = st.selectbox(
        "Select model",
        options=known_tax_models,
        index=known_tax_models.index(initial_choice),
        help="Pick a prebuilt tax model or choose Custom to enter your own",
        key="select_model_1040_tab",
    )
    if choice == "Custom…":
        model_1040 = st.text_input(
            "Custom model id",
            value=prev_model if prev_model not in known_tax_models else default_model_1040,
            help="Exact model id as used in your scripts/portal",
            key="input_model_1040_tab",
        )
    else:
        model_1040 = choice
    if model_1040 == "prebuilt-document":
        st.info("'prebuilt-document' usually does not populate document.fields; check Key-Value Pairs instead.")
    pages_1040 = st.text_input(
        "1040 pages (optional)",
        value=st.session_state.get("azure_1040_pages", ""),
        help="Azure pages string like 18-19 or 1,3,5-7. If empty, uses the selected page above.",
        key="input_pages_1040_tab",
    )
    if st.button("Run Azure 1040", type="primary", key="btn_az_1040"):
        # Persist choices for cost estimator and reruns
        st.session_state["azure_1040_model"] = model_1040
        st.session_state["azure_1040_pages"] = pages_1040
        try:
            if pages_1040.strip():
                st.warning(f"Using static pages override '{pages_1040.strip()}', ignoring selected page {int(page_number)}.")

            with st.status("Analyzing with Azure 1040 prebuilt model…", expanded=False):
                try:
                    azure_1040_result, azure_1040_secs, model_used, pages_used = run_azure_analysis(
                        pdf_path, int(page_number), model_id=model_1040, pages=pages_1040.strip() or None, prefer_docint=True
                    )
                except Exception as e:
                    # Fallback to default model if 1040 model is not found
                    if "ModelNotFound" in str(e) or "Resource not found" in str(e):
                        fallback_model = AZURE_CONFIG.get("model_id", "prebuilt-document")
                        azure_1040_result, azure_1040_secs, model_used, pages_used = run_azure_analysis(
                            pdf_path, int(page_number), model_id=fallback_model, pages=pages_1040.strip() or None, prefer_docint=True
                        )
                        st.info(f"Fell back to model: {fallback_model}")
                    else:
                        raise

            st.caption(f"Azure analyzed pages: {pages_used}")
            st.caption(f"Model: {model_used} • Pages: {pages_used}")
            st.caption(f"⏱️ Azure 1040 timings — API: {_fmt_duration(azure_1040_secs)}")
            render_azure_outputs(azure_1040_result, int(page_number), label_prefix="Azure 1040")
        except Exception as e:
            st.error("Azure 1040 extraction failed.")
            st.code(f"{type(e).__name__}: {e}")
            with st.expander("Full traceback"):
                st.exception(traceback.format_exc())

with tab_pym:
    st.markdown("### PyMuPDF (local)")
    scope_all_opt = st.checkbox(
        "Analyze all pages (text + heuristics)",
        value=st.session_state.get("pym_scope_all", False),
        help="Unchecked: use the selected page only. Checked: use the entire document.",
        key="pym_scope_all_tab",
    )
    pym_pages_spec = st.text_input(
        "Pages (optional)",
        value=st.session_state.get("pym_pages_spec", ""),
        help="Page list/ranges like 1,3,5-7. If set, overrides the toggle and selected page.",
        key="pym_pages_spec_tab",
    )
    if st.button("Run PyMuPDF", type="primary", key="btn_pym"):
        st.session_state["pym_scope_all"] = bool(scope_all_opt)
        st.session_state["pym_pages_spec"] = pym_pages_spec.strip()
        do_process_pymupdf = True

# -------- Reducto path --------
if do_process:
    try:
        # Create client (may fail if API key missing)
        try:
            client = create_client()
        except Exception as e:
            st.error("Reducto is unavailable (configuration or network policy).")
            st.caption("Error details (copyable):")
            st.code(f"{type(e).__name__}: {e}")
            with st.expander("Full traceback"):
                st.exception(traceback.format_exc())
            # Skip Reducto path for this run
            st.stop()

        parsed = None
        reducto_api_secs = 0.0
        with st.status("Parsing with Reducto…", expanded=False) as status:
            try:
                t0 = perf_counter()
                if st.session_state.get("use_reducto_range"):
                    s = int(st.session_state.get("reducto_start_page", int(page_number)))
                    e = int(st.session_state.get("reducto_end_page", int(page_number)))
                    parsed = parse_document_range(client, pdf_path, s, e)
                else:
                    parsed = parse_document(client, pdf_path, int(page_number))
                reducto_api_secs = perf_counter() - t0
                status.update(label="Parsing complete", state="complete")
            except Exception as e:
                status.update(label="Reducto request failed", state="error")
                st.error("Cannot upload to Reducto — likely blocked by corporate policy.")
                st.caption("Error details (copyable):")
                st.code(f"{type(e).__name__}: {e}")
                with st.expander("Full traceback"):
                    st.exception(traceback.format_exc())
                # Skip further Reducto outputs
                st.stop()

        # Outputs (only if parsed OK)
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
        _model = st.session_state.get("azure_default_model", AZURE_CONFIG.get("model_id", "prebuilt-document"))
        _pages = st.session_state.get("azure_default_pages") or None
        if _pages:
            st.warning(f"Using static pages override '{_pages}', ignoring selected page {int(page_number)}.")

        with st.status("Analyzing with Azure Document Intelligence…", expanded=False):
            azure_result, azure_api_secs, model_used, pages_used = run_azure_analysis(
                pdf_path, int(page_number), model_id=_model, pages=_pages, prefer_docint=False
            )
        st.caption(f"Azure analyzed pages: {pages_used}")
        st.caption(f"Model: {model_used} • Pages: {pages_used}")
        st.caption(f"⏱️ Azure timings — API: {_fmt_duration(azure_api_secs)}")
        render_azure_outputs(azure_result, int(page_number), label_prefix="Azure")
    except Exception as e:
        st.error("Azure Doc AI error")
        st.code(f"{type(e).__name__}: {e}")
        with st.expander("Full traceback"):
            st.exception(traceback.format_exc())

# If nothing has been triggered yet, prompt user
if not (do_process or do_process_azure or do_process_pymupdf):
    st.info("Open a tab above, adjust options, and click Run.")

# -------- PyMuPDF (local) path --------
if do_process_pymupdf:
    st.divider()
    st.subheader("PyMuPDF Output (local extraction)")
    try:
        # Scope is controlled from the PyMuPDF tab options (pages spec > all-pages toggle > selected page)
        scope_all = bool(st.session_state.get("pym_scope_all", False))
        pages_spec = (st.session_state.get("pym_pages_spec") or "").strip()
        pages_list = _pages_list_from_spec(pages_spec, page_count) if pages_spec else []
        if pages_list:
            scope_label = f"pages {','.join(map(str, pages_list))}"
        else:
            scope_label = 'all pages' if scope_all else 'selected page'
        st.caption(
            f"PyMuPDF scope: {scope_label} • Selected page: {int(page_number)}"
        )
        # 1) Normalized plain text from PyMuPDF
        pm_t0 = perf_counter()
        if pages_list:
            parts = [pymupdf_extract_text(pdf_path, int(p)) for p in pages_list]
            normalized_text = "\n".join(parts)
        elif scope_all:
            normalized_text = extract_text_pymupdf(pdf_path)
        else:
            normalized_text = pymupdf_extract_text(pdf_path, int(page_number))
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
            st.caption(f"Advanced timings — dict/blocks/words: {_fmt_duration(adv_secs)} • dump: {_fmt_duration(dump_secs)}")
            st.text(dump_txt)
    except Exception:
        st.error("PyMuPDF extraction failed.")
        st.exception(traceback.format_exc())
