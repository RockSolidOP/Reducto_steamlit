from __future__ import annotations

import json
import traceback
from pathlib import Path
from time import perf_counter
from typing import Protocol

import streamlit as st

from app.config import AZURE_CONFIG
from app.state.session import AppState
from app.ui.components import (
    count_pages_from_spec,
    fmt_duration,
)
from app.services.azure_service import (
    parse_with_azure,
    parse_with_azure_docint,
    azure_to_dict,
    azure_fields_to_dict,
)


class Tab(Protocol):
    """Minimal UI tab protocol."""

    name: str

    def render(self, state: AppState) -> None: ...


# Left-column expander removed. All Azure controls live inside the tab now.


def render_cost_estimator(page_count: int, current_page: int, *, per_page_price_default: float = 0.10) -> None:
    """Render the sidebar cost estimator for Azure analysis."""
    st.sidebar.markdown("#### Azure Cost Estimate")
    per_page_price = st.sidebar.number_input(
        "Price per page ($)", min_value=0.0, value=float(per_page_price_default), step=0.01, format="%.2f"
    )

    def _est(label: str, pages_spec: str | None):
        n = count_pages_from_spec(pages_spec or str(int(current_page)), page_count)
        cost = n * per_page_price
        st.sidebar.write(f"{label}: {n} page(s) → ${cost:.2f}")

    ss = st.session_state
    _est("Default Azure", ss.get("azure_default_pages") or str(int(current_page)))
    _est("Azure 1040", ss.get("azure_1040_pages") or str(int(current_page)))


def run_azure_analysis(
    pdf_path: Path,
    page_number: int,
    model_id: str,
    pages: str | None = None,
    *,
    prefer_docint: bool = False,
):
    """Analyze with Azure services using fallbacks; returns (result, secs, model_used, pages_used).

    - prefer_docint: when True, tries Document Intelligence first, falls back to FR.
    """
    pages_used = pages or str(int(page_number))

    def _try_docint():
        return parse_with_azure_docint(pdf_path, int(page_number), model_id=model_id, pages=pages)

    def _try_fr():
        return parse_with_azure(pdf_path, int(page_number), model_id=model_id, pages=pages)

    order = (_try_docint, _try_fr) if prefer_docint else (_try_fr, _try_docint)
    last_exc: Exception | None = None
    for fn in order:
        try:
            t0 = perf_counter()
            res = fn()
            return res, (perf_counter() - t0), model_id, pages_used
        except Exception as e:  # try the alternate client on failure
            last_exc = e
            if "ModelNotFound" not in str(e) and "Resource not found" not in str(e):
                continue
    if last_exc:
        raise last_exc
    raise RuntimeError("Azure analysis failed to return a result")


def render_azure_outputs(result: object, page_number: int, *, label_prefix: str = "Azure") -> None:
    """Render Azure output panels: raw JSON and derived KV/fields with downloads."""
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
        st.markdown(f"#### {label_prefix} Document Fields")
        t0 = perf_counter()
        fields_dict = azure_fields_to_dict(result)
        post_secs = perf_counter() - t0
        st.caption(f"Post-processing time: {fmt_duration(post_secs)}")
        st.json(fields_dict)
        st.download_button(
            f"Download {label_prefix.lower()}_fields.json",
            data=json.dumps(fields_dict, ensure_ascii=False, indent=2),
            file_name=f"{label_prefix.lower()}_fields_page{int(page_number)}.json",
            mime="application/json",
        )


def run_default_results(state: AppState, pdf_path: Path, page_number: int) -> None:
    """Execute and render the default Azure analysis results section (below tabs)."""
    st.divider()
    st.subheader("Azure Document AI Output")
    try:
        model = state.azure_default_model or AZURE_CONFIG.get("model_id", "prebuilt-document")
        pages = state.azure_default_pages or None
        if pages:
            st.warning(
                f"Using static pages override '{pages}', ignoring selected page {int(page_number)}."
            )
        with st.status("Analyzing with Azure Document Intelligence…", expanded=False):
            result, api_secs, model_used, pages_used = run_azure_analysis(
                pdf_path, int(page_number), model_id=model, pages=pages, prefer_docint=False
            )
        st.caption(f"Azure analyzed pages: {pages_used}")
        st.caption(f"Model: {model_used} • Pages: {pages_used}")
        st.caption(f"⏱️ Azure timings — API: {fmt_duration(api_secs)}")
        render_azure_outputs(result, int(page_number), label_prefix="Azure")
    except Exception as e:
        st.error("Azure Doc AI error")
        st.code(f"{type(e).__name__}: {e}")
        with st.expander("Full traceback"):
            st.exception(traceback.format_exc())


class AzureTab:
    """Azure UI container: default + 1040 sub-tabs and triggers."""

    name: str = "Azure"

    def render(self, state: AppState) -> None:  # noqa: D401 - documented in Protocol
        tab_default, tab_1040 = st.tabs(["Azure AI", "Azure 1040"])

        with tab_default:
            st.markdown("### Azure Document AI (default)")
            col1, col2 = st.columns(2)
            with col1:
                az_model_opt = st.text_input(
                    "Model id",
                    value=state.azure_default_model,
                    help="e.g., prebuilt-document, prebuilt-invoice, or a custom model id",
                    key="az_model_tab",
                )
            with col2:
                az_pages_opt = st.text_input(
                    "Pages (optional)",
                    value=state.azure_default_pages,
                    help="Azure pages string like 6 or 18-19 or 1,3,5-7",
                    key="az_pages_tab",
                )
            if st.button("Run Azure AI", type="primary", key="btn_az_default"):
                state.azure_default_model = az_model_opt.strip()
                state.azure_default_pages = az_pages_opt.strip()
                st.session_state["trigger_azure_default"] = True

        with tab_1040:
            st.markdown("### Azure 1040 (prebuilt)")
            default_model_1040 = AZURE_CONFIG.get("model_id_1040", "prebuilt-tax.us.1040")
            # Include Schedule 1 as a selectable built-in option
            known_tax_models = [
                "prebuilt-tax.us.1040",  # Main 1040
                AZURE_CONFIG.get("model_id_1040_schedule1", "prebuilt-tax.us.1040Schedule1"),  # Schedule 1
                AZURE_CONFIG.get("model_id_1040_schedule_a", "prebuilt-tax.us.1040ScheduleA"),  # Schedule A
                AZURE_CONFIG.get("model_id_1040_schedule_c", "prebuilt-tax.us.1040ScheduleC"),  # Schedule C
                "Custom…",
            ]
            prev_model = state.azure_1040_model or default_model_1040
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
                st.info(
                    "'prebuilt-document' usually does not populate document.fields; check Key-Value Pairs instead."
                )
            pages_1040 = st.text_input(
                "1040 pages (optional)",
                value=state.azure_1040_pages,
                help=(
                    "Azure pages string like 18-19 or 1,3,5-7. If empty, uses the selected page above."
                ),
                key="input_pages_1040_tab",
            )
            if st.button("Run Azure 1040", type="primary", key="btn_az_1040"):
                # Persist
                state.azure_1040_model = model_1040
                state.azure_1040_pages = pages_1040
                try:
                    # Validate pages spec against document bounds (no fallback)
                    page_count = int(st.session_state.get("page_count_ui", 1))
                    pages_for_azure: str | None = None
                    if pages_1040.strip():
                        spec = pages_1040.strip()
                        # Remove whitespace around commas
                        cleaned = ",".join(part.strip() for part in spec.split(",") if part.strip())
                        pages_set: set[int] = set()
                        invalid = False
                        for part in cleaned.split(","):
                            if not part:
                                continue
                            if "-" in part:
                                a, b = part.split("-", 1)
                                try:
                                    start = int(a)
                                    end = int(b)
                                except ValueError:
                                    invalid = True
                                    break
                                # Normalize start<=end for validation
                                if end < start:
                                    start, end = end, start
                                for i in range(start, end + 1):
                                    pages_set.add(i)
                            else:
                                try:
                                    pages_set.add(int(part))
                                except ValueError:
                                    invalid = True
                                    break
                        # Range check
                        if invalid or not pages_set:
                            st.error("Invalid pages specification.")
                            return
                        if min(pages_set) < 1 or max(pages_set) > page_count:
                            st.error("Pages out of range for this document.")
                            return
                        pages_for_azure = cleaned
                    with st.status("Analyzing with Azure 1040 prebuilt model…", expanded=False):
                        try:
                            result, secs, model_used, pages_used = run_azure_analysis(
                                Path(st.session_state.get("pdf_path")),
                                int(st.session_state.get("page_number_ui", 1)),
                                model_id=model_1040,
                                pages=pages_for_azure,
                                prefer_docint=True,
                            )
                        except Exception as e:
                            if "ModelNotFound" in str(e) or "Resource not found" in str(e):
                                fallback_model = AZURE_CONFIG.get("model_id", "prebuilt-document")
                                result, secs, model_used, pages_used = run_azure_analysis(
                                    Path(st.session_state.get("pdf_path")),
                                    int(st.session_state.get("page_number_ui", 1)),
                                    model_id=fallback_model,
                                    pages=pages_for_azure,
                                    prefer_docint=True,
                                )
                                st.info(f"Fell back to model: {fallback_model}")
                            else:
                                raise

                    st.caption(f"Azure analyzed pages: {pages_used}")
                    st.caption(f"Model: {model_used} • Pages: {pages_used}")
                    st.caption(f"⏱️ Azure 1040 timings — API: {fmt_duration(secs)}")
                    render_azure_outputs(result, int(st.session_state.get("page_number_ui", 1)), label_prefix="Azure 1040")
                except Exception as e:
                    st.error("Azure 1040 extraction failed.")
                    st.code(f"{type(e).__name__}: {e}")
                    with st.expander("Full traceback"):
                        st.exception(traceback.format_exc())
