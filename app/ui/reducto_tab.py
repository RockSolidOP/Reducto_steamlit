from __future__ import annotations

import json
import traceback
from pathlib import Path
from time import perf_counter
from typing import Protocol

import streamlit as st

from app.post_processing import parse_page3_blocks_resilient
from app.services.reducto_service import (
    create_client,
    parse_document,
    parse_document_range,
    _present_block_pages,
    _resolve_effective_page,
    extract_page_blocks,
    get_blocks_for_page,
    extract_with_schema,
)
from app.state.session import AppState
from app.ui.components import fmt_duration
from testing_files.reducto_files.system_prompt import (
    get_irs_1040_2024_extraction_prompt,
)


class Tab(Protocol):
    """Minimal UI tab protocol."""

    name: str

    def render(self, state: AppState) -> None: ...


def render_options_expander(state: AppState, *, page_count: int, page_number: int) -> bool:
    """Render the Reducto options expander in the left column.

    Returns True when the action button is clicked (trigger run).
    """
    did_trigger = False
    with st.expander("Reducto Options", expanded=False):
        r_col1, r_col2 = st.columns(2)
        with r_col1:
            red_start = st.number_input(
                "Start page",
                min_value=1,
                max_value=max(1, page_count),
                value=int(page_number),
                step=1,
                key="red_start",
            )
        with r_col2:
            red_end = st.number_input(
                "End page",
                min_value=1,
                max_value=max(1, page_count),
                value=int(page_number),
                step=1,
                key="red_end",
            )
        run_reducto_adv = st.button("Run Reducto", type="primary", key="run_reducto_adv")
        if run_reducto_adv:
            state.use_reducto_range = True
            state.reducto_start_page = int(red_start)
            state.reducto_end_page = int(red_end)
            did_trigger = True
    return did_trigger


def run_results(state: AppState, pdf_path: Path, page_number: int) -> None:
    """Execute Reducto parsing and render results below the tabs."""
    try:
        try:
            client = create_client()
        except Exception as e:  # Early stop on client creation failure
            st.error("Reducto is unavailable (configuration or network policy).")
            st.caption("Error details (copyable):")
            st.code(f"{type(e).__name__}: {e}")
            with st.expander("Full traceback"):
                st.exception(traceback.format_exc())
            st.stop()

        parsed: dict | None = None
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
                st.stop()

        # Outputs
        st.divider()
        st.caption(f"⏱️ Reducto timings — API: {fmt_duration(reducto_api_secs)}")
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
        present_pages = _present_block_pages(parsed or {})
        effective_page = _resolve_effective_page(parsed or {}, int(page_number))
        page_text_blocks = list(extract_page_blocks(parsed or {}, int(page_number)))
        page_blocks = get_blocks_for_page(parsed or {}, int(page_number))

        with out2:
            st.markdown("#### 2) block (text of that page)")
            if page_text_blocks:
                st.text("\n\n".join(page_text_blocks))
            else:
                st.info("No text blocks detected on this page.")
            st.markdown("**Block diagnostics**")
            st.write(
                {
                    "requested_page": int(page_number),
                    "effective_page": int(effective_page),
                    "present_pages": present_pages,
                    "num_blocks": len(page_blocks),
                    "text_blocks": len(page_text_blocks),
                    "content_lengths": [len(b.get("content") or "") for b in page_blocks[:20]],
                }
            )
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
                st.caption(f"Post-processing time: {fmt_duration(reducto_post_secs)}")
                st.json(parsed_info)
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
                            f"Per-block post-processing — count: {cnt} • avg: {fmt_duration(avg)} • max: {fmt_duration(mx)}"
                        )
                except Exception:
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

        # Reproducer
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
                st.caption(f"Per-block post-processing time: {fmt_duration(_secs)}")
                st.json(out)
            except Exception:
                st.error("Parser crashed on this block.")
                st.exception(traceback.format_exc())

    except Exception:
        st.exception(traceback.format_exc())


class ReductoTab:
    """Reducto options and triggers UI (tab)."""

    name: str = "Reducto"

    def render(self, state: AppState) -> None:  # noqa: D401 - documented in Protocol
        st.markdown("### Reducto")
        r_col1, r_col2 = st.columns(2)
        with r_col1:
            red_start = st.number_input(
                "Start page",
                min_value=1,
                max_value=max(1, int(st.session_state.get("page_count_ui", 1))),
                value=int(st.session_state.get("page_number_ui", 1)),
                step=1,
                key="red_start_tab",
            )
        with r_col2:
            red_end = st.number_input(
                "End page",
                min_value=1,
                max_value=max(1, int(st.session_state.get("page_count_ui", 1))),
                value=int(st.session_state.get("page_number_ui", 1)),
                step=1,
                key="red_end_tab",
            )
        c1, c2 = st.columns(2)
        with c1:
            run_red_page = st.button("Run Reducto (selected page)", type="primary", key="btn_red_page")
        with c2:
            run_red_range = st.button("Run Reducto (range)", type="primary", key="btn_red_range")
        if run_red_range:
            state.use_reducto_range = True
            state.reducto_start_page = int(red_start)
            state.reducto_end_page = int(red_end)
            st.session_state["trigger_reducto"] = True
        elif run_red_page:
            state.use_reducto_range = False
            st.session_state["trigger_reducto"] = True

        # --- Schema Extract (no preprocessors) ---
        with st.expander("Schema Extract", expanded=False):
            # Discover schemas from both the canonical test folder and a local editable folder
            # test_schema_dir = Path("testing_files/reducto_files/input_json")
            local_schema_dir = Path("reducto_schema")
            # Ensure local schema directory exists
            try:
                local_schema_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            schema_paths = sorted(local_schema_dir.glob("*.json")) if local_schema_dir.exists() else []
            # Build human-friendly labels that include directory context
            labels = [
                (f"{p.parent.name}/{p.name}" if p.parent else p.name)
                for p in schema_paths
            ]
            # Persist selection
            default_idx = 0
            if "selected_schema_path" in st.session_state and st.session_state["selected_schema_path"]:
                try:
                    default_idx = next(
                        (i for i, p in enumerate(schema_paths) if str(p) == st.session_state["selected_schema_path"]),
                        0,
                    )
                except Exception:
                    default_idx = 0
            sel = st.selectbox(
                "Choose schema (from reducto_schema)",
                options=list(range(len(labels))) if labels else [],
                format_func=lambda i: labels[i] if labels else "",
                index=min(default_idx, max(0, len(labels) - 1)) if labels else 0,
            )
            selected_schema_path: Path | None = schema_paths[sel] if labels else None
            if selected_schema_path is not None:
                st.session_state["selected_schema_path"] = str(selected_schema_path)

            schema_obj: dict | None = None
            if selected_schema_path is not None and selected_schema_path.exists():
                try:
                    with selected_schema_path.open("r", encoding="utf-8") as f:
                        schema_obj = json.load(f)
                except Exception as e:
                    st.error(f"Failed to load schema: {e}")
                    schema_obj = None
            else:
                if not labels:
                    st.info("No schemas found. Add JSON files to reducto_schema.")

            if schema_obj is not None:
                with st.expander("Selected schema (preview)", expanded=False):
                    st.json(schema_obj)
            else:
                if labels:
                    st.info("Select a schema to enable extraction.")

            c_run1, c_run2 = st.columns([1, 1])
            with c_run1:
                run_schema = st.button(
                    "Run Schema Extraction",
                    type="primary",
                    disabled=(schema_obj is None),
                    key="btn_run_schema_extract",
                )
            with c_run2:
                st.caption(
                    "Uses current Start/End page above for page_range; passes schema untouched."
                )

            if run_schema and schema_obj is not None:
                try:
                    client = create_client()
                except Exception as e:
                    st.error("Reducto is unavailable (configuration or network policy).")
                    st.code(f"{type(e).__name__}: {e}")
                    with st.expander("Full traceback"):
                        st.exception(traceback.format_exc())
                    st.stop()

                # Resolve page range from the tab inputs
                s_page = int(st.session_state.get("red_start_tab", 1))
                e_page = int(st.session_state.get("red_end_tab", s_page))

                # Resolve current PDF path from state
                pdf_path = st.session_state.get("uploaded_pdf_path") or st.session_state.get("pdf_path")
                if not pdf_path:
                    st.error("No PDF selected or uploaded in the app.")
                else:
                    try:
                        out = extract_with_schema(
                            client,
                            Path(str(pdf_path)),
                            schema=schema_obj,
                            start_page=s_page,
                            end_page=e_page,
                            system_prompt=get_irs_1040_2024_extraction_prompt(),
                        )
                        st.success("Extraction complete.")
                        with st.expander("Schema result (JSON)", expanded=True):
                            st.json(out)
                        # Download
                        st.download_button(
                            "Download extracted.json",
                            data=json.dumps(out, ensure_ascii=False, indent=2),
                            file_name=f"extracted_{selected_schema_path.stem}.json",
                            mime="application/json",
                        )
                        # Optional save to output_json mirroring test script
                        try:
                            out_dir = Path("testing_files/reducto_files/output_json")
                            out_dir.mkdir(parents=True, exist_ok=True)
                            out_path = out_dir / f"output_{selected_schema_path.stem}.json"
                            with out_path.open("w", encoding="utf-8") as f:
                                json.dump(out, f, ensure_ascii=False, indent=2)
                            st.toast(f"Saved {out_path}")
                        except Exception:
                            # Non-fatal if saving fails
                            pass
                    except Exception as e:
                        st.error("Schema extraction failed.")
                        st.code(f"{type(e).__name__}: {e}")
                        with st.expander("Full traceback"):
                            st.exception(traceback.format_exc())
