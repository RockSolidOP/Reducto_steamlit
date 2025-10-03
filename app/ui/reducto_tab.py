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


# Left-column expander removed. All Reducto controls live inside the tab now.


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
        tab_simple, tab_schema = st.tabs(["Simple Extract", "Schema Extract"])

        # --- Simple Extract tab ---
        with tab_simple:
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

        # --- Schema Extract tab (no preprocessors) ---
        with tab_schema:
            # Optional: Page range controls local to Schema Extract
            st.markdown("#### Page Range (Schema Extract)")
            s_col, e_col = st.columns(2)
            with s_col:
                schema_start = st.number_input(
                    "Start page",
                    min_value=1,
                    max_value=max(1, int(st.session_state.get("page_count_ui", 1))),
                    value=int(
                        st.session_state.get("schema_start_tab")
                        or st.session_state.get("red_start_tab")
                        or st.session_state.get("page_number_ui", 1)
                    ),
                    step=1,
                    key="schema_start_tab",
                )
            with e_col:
                schema_end = st.number_input(
                    "End page",
                    min_value=1,
                    max_value=max(1, int(st.session_state.get("page_count_ui", 1))),
                    value=int(
                        st.session_state.get("schema_end_tab")
                        or st.session_state.get("red_end_tab")
                        or st.session_state.get("page_number_ui", 1)
                    ),
                    step=1,
                    key="schema_end_tab",
                )
            # Discover schemas from app-bundled resources folder
            resources_dir = Path(__file__).resolve().parents[1] / "resources" / "reducto_schema"
            try:
                resources_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            # Upload new schema
            st.markdown("#### Manage Schemas (resources/reducto_schema)")
            up_col1, up_col2 = st.columns([2, 1])
            with up_col1:
                uploaded_schema = st.file_uploader("Upload JSON schema", type=["json"], key="schema_uploader")
            with up_col2:
                overwrite_upload = st.checkbox("Overwrite if exists", value=False, key="schema_upload_overwrite")
            if uploaded_schema is not None:
                try:
                    raw = uploaded_schema.read().decode("utf-8")
                    obj = json.loads(raw)  # validate JSON
                    target_name = Path(uploaded_schema.name).name
                    if not target_name.lower().endswith(".json"):
                        target_name += ".json"
                    target_path = resources_dir / target_name
                    if target_path.exists() and not overwrite_upload:
                        base = target_path.stem
                        suffix = target_path.suffix
                        i = 1
                        while True:
                            alt = resources_dir / f"{base}_{i}{suffix}"
                            if not alt.exists():
                                target_path = alt
                                break
                            i += 1
                    with target_path.open("w", encoding="utf-8") as f:
                        json.dump(obj, f, ensure_ascii=False, indent=2)
                    st.success(f"Uploaded schema saved to {target_path}")
                    st.session_state["selected_schema_path"] = str(target_path)
                except Exception as e:
                    st.error(f"Upload failed: {e}")

            # Refresh list after possible upload
            schema_paths = sorted(resources_dir.glob("*.json")) if resources_dir.exists() else []
            labels = [f"{p.name}" for p in schema_paths]

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
                "Choose schema (from resources)",
                options=list(range(len(labels))) if labels else [],
                format_func=lambda i: labels[i] if labels else "",
                index=min(default_idx, max(0, len(labels) - 1)) if labels else 0,
                key="schema_select_index",
            )
            # Robust selection: if sel is None or out-of-range, fall back to 0
            if labels:
                try:
                    sel_idx = int(sel) if sel is not None else 0
                    if not (0 <= sel_idx < len(schema_paths)):
                        sel_idx = 0
                    selected_schema_path: Path | None = schema_paths[sel_idx]
                except Exception:
                    selected_schema_path = schema_paths[0]
            else:
                selected_schema_path = None
            if selected_schema_path is not None:
                st.session_state["selected_schema_path"] = str(selected_schema_path)

            # Viewer + editor for selected schema (with Edit toggle on top-right)
            schema_text_key = "schema_editor_text"
            edit_mode_key = "schema_edit_mode"
            st.session_state.setdefault(edit_mode_key, False)

            def _load_editor_buffer_from_file(p: Path) -> None:
                try:
                    with p.open("r", encoding="utf-8") as f:
                        st.session_state[schema_text_key] = f.read()
                except Exception:
                    st.session_state[schema_text_key] = ""

            # Load current file into editor buffer when switching files
            if selected_schema_path and selected_schema_path.exists():
                try:
                    prev_sel = st.session_state.get("selected_schema_path_prev")
                    if (schema_text_key not in st.session_state) or (prev_sel != str(selected_schema_path)):
                        _load_editor_buffer_from_file(selected_schema_path)
                        st.session_state["selected_schema_path_prev"] = str(selected_schema_path)
                except Exception as e:
                    st.error(f"Failed to read schema: {e}")
                # Ensure buffer is populated even if key existed but was empty
                if not st.session_state.get(schema_text_key):
                    _load_editor_buffer_from_file(selected_schema_path)
            else:
                if not labels:
                    st.info("No schemas found. Upload a JSON schema to begin.")

            # Header row with Edit or Save/Cancel actions on the right
            if labels and selected_schema_path is not None:
                hdr_l, hdr_r = st.columns([4, 2])
                with hdr_l:
                    st.markdown("#### Selected Schema")
                with hdr_r:
                    if not st.session_state[edit_mode_key]:
                        if st.button("Edit", key="schema_btn_edit"):
                            # Enter edit mode and ensure editor buffer is populated
                            if selected_schema_path and selected_schema_path.exists():
                                _load_editor_buffer_from_file(selected_schema_path)
                            st.session_state[edit_mode_key] = True
                    else:
                        r1, r2, r3 = st.columns([1, 1, 1])
                        with r1:
                            save_btn = st.button("Save", key="schema_btn_save")
                        with r2:
                            save_as_btn = st.button("Save As", key="schema_btn_save_as")
                        with r3:
                            cancel_btn = st.button("Cancel", key="schema_btn_cancel")

                        if cancel_btn:
                            # Reset editor buffer to file content
                            try:
                                with selected_schema_path.open("r", encoding="utf-8") as f:
                                    st.session_state[schema_text_key] = f.read()
                            except Exception:
                                pass
                            st.session_state[edit_mode_key] = False

                # Editor/viewer area
                if st.session_state[edit_mode_key]:
                    st.text_area(
                        "",
                        key=schema_text_key,
                        height=300,
                        label_visibility="collapsed",
                    )
                    # Save-As filename field below editor
                    save_as_name = st.text_input(
                        "Filename (.json)",
                        value=selected_schema_path.name,
                        key="schema_save_as_name",
                    )

                    def _write_schema(target: Path, content: str) -> bool:
                        try:
                            obj = json.loads(content)
                        except Exception as e:
                            st.error(f"Invalid JSON: {e}")
                            return False
                        try:
                            with target.open("w", encoding="utf-8") as f:
                                json.dump(obj, f, ensure_ascii=False, indent=2)
                            st.success(f"Saved {target}")
                            return True
                        except Exception as e:
                            st.error(f"Save failed: {e}")
                            return False

                    if 'save_btn' in locals() and save_btn:
                        if _write_schema(selected_schema_path, st.session_state.get(schema_text_key, "")):
                            st.session_state[edit_mode_key] = False

                    if 'save_as_btn' in locals() and save_as_btn:
                        name = (save_as_name or selected_schema_path.name).strip()
                        if not name.lower().endswith(".json"):
                            name += ".json"
                        target = local_schema_dir / name
                        if _write_schema(target, st.session_state.get(schema_text_key, "")):
                            st.session_state["selected_schema_path"] = str(target)
                            st.session_state[edit_mode_key] = False
                else:
                    # Viewer mode
                    try:
                        file_obj = None
                        with selected_schema_path.open("r", encoding="utf-8") as f:
                            file_obj = json.load(f)
                        st.json(file_obj)
                    except Exception as e:
                        st.error(f"Failed to load schema: {e}")

            # Non-blocking validation preview of current schema content
            st.markdown("#### Schema Validation Preview")
            try:
                current_schema_text: str | None = None
                if st.session_state.get(edit_mode_key):
                    current_schema_text = st.session_state.get(schema_text_key)
                elif selected_schema_path and selected_schema_path.exists():
                    current_schema_text = selected_schema_path.read_text(encoding="utf-8")
                if current_schema_text:
                    parsed_any = json.loads(current_schema_text)
                    looks_like_json_schema = (
                        isinstance(parsed_any, dict)
                        and parsed_any.get("type") == "object"
                        and isinstance(parsed_any.get("properties"), dict)
                    )
                    if looks_like_json_schema:
                        st.success("Looks like a valid JSON Schema (type: object with properties).")
                    else:
                        st.warning(
                            "Selected JSON may not meet the API's JSON Schema requirements. "
                            "We will still send it as-is; the API may return 422/400."
                        )
                        with st.expander("What the API expects"):
                            st.code(
                                json.dumps(
                                    {
                                        "title": "Example",
                                        "type": "object",
                                        "properties": {
                                            "fieldA": {"type": "string"},
                                            "fieldB": {"type": "number"},
                                            "flag": {"type": "boolean"}
                                        },
                                        "additionalProperties": False
                                    },
                                    indent=2,
                                ),
                                language="json",
                            )
                            st.caption(
                                "Root must be an object with explicit property types: "
                                "string, number, integer, boolean, object, or array."
                            )
                    st.caption(f"Parsed JSON type: {type(parsed_any).__name__}")
            except Exception:
                # Silent preview errors to avoid blocking flow
                pass

            st.divider()
            # Run extraction using the editor content (even if not saved)
            run_col1, run_col2 = st.columns([1, 2])
            with run_col1:
                run_schema = st.button("Run Schema Extraction", type="primary", key="btn_run_schema_extract")
            with run_col2:
                st.caption("Uses Start/End set in this tab.")

            if run_schema:
                try:
                    client = create_client()
                except Exception as e:
                    st.error("Reducto is unavailable (configuration or network policy).")
                    st.code(f"{type(e).__name__}: {e}")
                    with st.expander("Full traceback"):
                        st.exception(traceback.format_exc())
                    st.stop()

                # Resolve page range: prefer Schema tab inputs; fall back to Simple Extract or current page
                s_page = int(
                    st.session_state.get("schema_start_tab")
                    or st.session_state.get("red_start_tab")
                    or st.session_state.get("page_number_ui", 1)
                )
                e_page = int(
                    st.session_state.get("schema_end_tab")
                    or st.session_state.get("red_end_tab")
                    or s_page
                )

                # Resolve current PDF path from state
                pdf_path = st.session_state.get("uploaded_pdf_path") or st.session_state.get("pdf_path")
                if not pdf_path:
                    st.error("No PDF selected or uploaded in the app.")
                else:
                    try:
                        # Use editor content if in edit mode; else load from selected file
                        schema_text = None
                        if st.session_state.get(edit_mode_key):
                            schema_text = st.session_state.get(schema_text_key)
                        if not schema_text and selected_schema_path and selected_schema_path.exists():
                            schema_text = selected_schema_path.read_text(encoding="utf-8")
                        schema_obj_any = json.loads(schema_text) if schema_text else None
                        if not isinstance(schema_obj_any, dict):
                            st.warning(
                                "Selected content is not a JSON object. The API expects a JSON Schema object; "
                                "sending as-is will likely fail."
                            )
                        schema_api_secs = 0.0
                        with st.status("Extracting with Reducto (schema)…", expanded=False) as status:
                            try:
                                t0 = perf_counter()
                                out = extract_with_schema(
                                    client,
                                    Path(str(pdf_path)),
                                    schema=schema_obj_any,
                                    start_page=s_page,
                                    end_page=e_page,
                                    system_prompt=get_irs_1040_2024_extraction_prompt(),
                                )
                                schema_api_secs = perf_counter() - t0
                                status.update(label="Extraction complete", state="complete")
                            except Exception:
                                status.update(label="Schema extraction failed", state="error")
                                raise

                        st.caption(f"⏱️ Reducto (schema) timings — API: {fmt_duration(schema_api_secs)}")
                        st.success("Extraction complete.")
                        with st.expander("Schema result (JSON)", expanded=True):
                            st.json(out)
                        st.download_button(
                            "Download extracted.json",
                            data=json.dumps(out, ensure_ascii=False, indent=2),
                            file_name=(
                                f"extracted_{selected_schema_path.stem}.json"
                                if selected_schema_path is not None else "extracted.json"
                            ),
                            mime="application/json",
                        )
                        # Intentionally do not write results to disk; rely on on-screen view and manual download.
                    except Exception as e:
                        st.error("Schema extraction failed.")
                        st.code(f"{type(e).__name__}: {e}")
                        with st.expander("Full traceback"):
                            st.exception(traceback.format_exc())
