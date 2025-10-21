from __future__ import annotations

"""Classification UI tab: classify all pages into family + label.

Uses the local classification service and a bundled JSON config under
app/resources/classifyier_regex/forms_config.json by default. Allows
overriding the config via file upload.
"""

import json
import os
from pathlib import Path

import streamlit as st

from app.services.classify_service import (
    get_default_config_path,
    load_config,
    classify_to_result,
)
from app.services.ml_classify_service import classify_to_result_ml
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

        # Engine chooser
        engine = st.radio(
            "Engine",
            options=["Regex (rules)", "ML (FAISS/CLIP)"],
            index=0,
            horizontal=True,
            key="classify_engine",
        )

        if engine == "Regex (rules)":
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

            if st.button("Classify All Pages", type="primary", key="btn_classify_all_regex"):
                try:
                    with st.status("Classifying all pages (Regex)…", expanded=False):
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
        else:
            # ML (FAISS/CLIP) classifier
            st.caption("Uses bundled FAISS data in classifier_module; set CLASSIFIER_FAISS_DIR to override.")
            c1, c2 = st.columns([1, 2])
            with c1:
                topk = st.slider("Top-K", min_value=1, max_value=5, value=1, step=1, key="ml_topk")
            with c2:
                st.text_input("FAISS dir (env: CLASSIFIER_FAISS_DIR)", value=os.getenv("CLASSIFIER_FAISS_DIR", ""), disabled=True)

            if st.button("Classify All Pages (ML)", type="primary", key="btn_classify_all_ml"):
                try:
                    with st.status("Classifying all pages (ML)…", expanded=False):
                        result = classify_to_result_ml(Path(pdf_path), topk=int(topk))
                    st.success("Classification complete.")
                    st.markdown("#### Classification Result (JSON)")
                    st.json(result)
                    st.download_button(
                        "Download classify_result_ml.json",
                        data=json.dumps(result, ensure_ascii=False, indent=2),
                        file_name=f"classify_ml_{Path(pdf_path).stem}.json",
                        mime="application/json",
                    )
                except Exception as e:
                    st.error("Classification failed (ML engine).")
                    st.code(f"{type(e).__name__}: {e}")
