from __future__ import annotations

"""Classification UI tab: classify all pages into family + label.

Uses the local classification service and a bundled JSON config under
app/resources/classifyier_regex/forms_config.json by default. Allows
overriding the config via file upload.
"""

import json
from pathlib import Path

import streamlit as st

from app.services.classify_service import (
    get_default_config_path,
    load_config,
    classify_to_result,
)
from app.state.session import AppState


class ClassifyTab:
    name: str = "Classify"

    def render(self, state: AppState) -> None:
        st.markdown("### Classify Document (All Pages)")

        # Resolve current PDF from session (set by main uploader)
        pdf_path = st.session_state.get("pdf_path")
        if not pdf_path:
            st.info("Upload a PDF first in the main page area.")
            return

        # Config selector
        st.caption("Classifier configuration (JSON with families/labels/regex)")
        colA, colB = st.columns([2, 1])
        with colA:
            default_cfg_path = get_default_config_path()
            st.text_input(
                "Config path (default)",
                value=str(default_cfg_path),
                disabled=True,
            )
        with colB:
            uploaded = st.file_uploader("Upload alternate config", type=["json"], key="clf_cfg_upload")

        # Load config (uploaded takes precedence for this run only)
        cfg = None
        if uploaded is not None:
            try:
                cfg = json.loads(uploaded.read().decode("utf-8"))
            except Exception as e:
                st.error(f"Invalid JSON: {e}")
                return
        else:
            try:
                cfg = load_config(default_cfg_path)
            except Exception as e:
                st.error(f"Failed to load default config: {e}")
                return

        if st.button("Classify All Pages", type="primary", key="btn_classify_all"):
            try:
                with st.status("Classifying all pages…", expanded=False):
                    result = classify_to_result(Path(pdf_path), cfg)
                st.success("Classification complete.")
                st.markdown("#### Classification Result (JSON)")
                st.json(result)
                st.download_button(
                    "Download classify_result.json",
                    data=json.dumps(result, ensure_ascii=False, indent=2),
                    file_name=f"classify_{Path(pdf_path).stem}.json",
                    mime="application/json",
                )
            except Exception as e:
                st.error("Classification failed.")
                st.code(f"{type(e).__name__}: {e}")

