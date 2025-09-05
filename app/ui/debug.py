from __future__ import annotations

import glob
import os
import sys
import traceback
from pathlib import Path

import streamlit as st

from app.post_processing import POST_PROCESSOR_PATH, reload_post_processor


def debug_panel():
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔧 Debug")
    dbg = st.sidebar.checkbox("Enable debug mode")
    if not dbg:
        return

    st.sidebar.write("**Python**:", sys.version)
    st.sidebar.write("**Interpreter**:", sys.executable)
    st.sidebar.write("**CWD**:", os.getcwd())

    # Show presence of .env without leaking secrets
    env_paths = [Path(".env"), *[Path(p) for p in glob.glob("**/.env", recursive=False)]]
    env_exists = [str(p.resolve()) for p in env_paths if p.exists()]
    st.sidebar.write("**.env found at**:", env_exists or "(none)")

    red_key = os.getenv("REDUCTO_API_KEY")
    st.sidebar.write("**REDUCTO_API_KEY set**:", bool(red_key), "| length:", len(red_key or ""))

    az_ep = os.getenv("AZURE_DOC_AI_ENDPOINT")
    az_key = os.getenv("AZURE_DOC_AI_KEY")
    st.sidebar.write("**AZURE_DOC_AI_ENDPOINT set**:", bool(az_ep))
    st.sidebar.write("**AZURE_DOC_AI_KEY set**:", bool(az_key), "| length:", len(az_key or ""))

    # Network / TLS diagnostics
    st.sidebar.markdown("**Network/TLS diagnostics**")
    try:
        import certifi  # type: ignore
        ca_path = certifi.where() if hasattr(certifi, "where") else "(unknown)"
        st.sidebar.write("certifi CA bundle:", ca_path)
    except Exception:
        st.sidebar.write("certifi CA bundle:", "(certifi not available)")
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
        v = os.getenv(k)
        st.sidebar.write(f"{k}:", v if v else "(unset)")

    if POST_PROCESSOR_PATH:
        st.sidebar.caption(f"post_processor: `{POST_PROCESSOR_PATH}`")
    else:
        st.sidebar.warning("post_processors.post_processor NOT loaded. Using fallback.")

    if st.sidebar.button("Reload post_processor.py"):
        ok, msg = reload_post_processor()
        if ok:
            st.sidebar.success("Reloaded post_processor.py")
        else:
            st.sidebar.error("Reload failed")
            if msg:
                st.sidebar.exception(msg)

    if st.sidebar.button("🔄 Rerun"):
        st.rerun()
