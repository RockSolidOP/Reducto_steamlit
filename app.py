from __future__ import annotations
import json, traceback
from pathlib import Path
from typing import Iterable, List, Dict, Any

import streamlit as st

from config.settings import load_settings
from providers import get_provider
from services.pdf_service import get_page_count, render_pdf_page_png_bytes
from services.utils.storage import save_upload, save_parsed, save_preview

# post-processor (unchanged)
try:
    import post_processors.post_processor as post_mod
    parse_page3_blocks_resilient = post_mod.parse_page3_blocks_resilient
except Exception:
    post_mod = None
    def parse_page3_blocks_resilient(blocks): return {"warning": "fallback", "blocks_count": len(blocks)}

def extract_page_blocks(parsed: dict, page_number: int):
    for ch in parsed.get("result", {}).get("chunks", []):
        for b in ch.get("blocks", []):
            if (b.get("bbox") or {}).get("page") == page_number:
                if (t := b.get("content")) is not None:
                    yield t

def get_blocks_for_page(parsed: dict, page_number: int) -> List[Dict[str, Any]]:
    out = []
    for ch in parsed.get("result", {}).get("chunks", []):
        for b in ch.get("blocks", []) or []:
            if (b.get("bbox") or {}).get("page") == page_number:
                out.append(b)
    return out

# ---- UI ----
st.set_page_config(page_title="Reducto PDF GUI", layout="wide", initial_sidebar_state="collapsed")
st.title("Document Parser GUI")

cfg = load_settings()

# choose provider (from config, with an override dropdown)
providers = ["reducto"]  # extend as you add adapters
prov_name = st.sidebar.selectbox("Provider", providers, index=providers.index(cfg.default_provider) if cfg.default_provider in providers else 0)
st.sidebar.caption(f"Using provider: {prov_name}")

uploaded = st.file_uploader("Upload a PDF", type=["pdf"])
if not uploaded:
    st.info("👆 Upload a PDF to get started.")
    st.stop()

pdf_path, sha = save_upload(uploaded.name or "upload.pdf", uploaded.read())
page_count = get_page_count(pdf_path)

col_left, col_right = st.columns([1, 2], gap="large")
with col_left:
    page_number = st.number_input("Page number", 1, page_count, 1)
    run = st.button("Process", type="primary")

if not run:
    st.stop()

# build provider (factory)
prov = get_provider(
    prov_name,
    reducto_api_key=cfg.reducto_api_key,  # pass all keys; factory uses what's needed
)

# parse
with st.status("Parsing…", expanded=False):
    parsed = prov.parse_document_for_page(pdf_path, int(page_number))

# preview
png_bytes = render_pdf_page_png_bytes(pdf_path, int(page_number))
with col_right:
    st.subheader(f"PDF Preview — Page {int(page_number)}")
    st.image(png_bytes, caption=f"Page {int(page_number)}", use_container_width=True)

# save artifacts (unchanged)
parsed_path = save_parsed(sha, int(page_number), parsed)
save_preview(sha, int(page_number), png_bytes)

# outputs (unchanged)
st.divider()
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("#### 1) parsed (raw)")
    st.json(parsed)
    st.download_button("Download parsed.json", data=Path(parsed_path).read_text("utf-8"),
                       file_name=parsed_path.name, mime="application/json",
                       key=f"dl-parsed-{sha}-{int(page_number)}")

page_text_blocks = list(extract_page_blocks(parsed, int(page_number)))
page_blocks = get_blocks_for_page(parsed, int(page_number))

with c2:
    st.markdown("#### 2) block (text of that page)")
    st.text("\n\n".join(page_text_blocks) if page_text_blocks else "(no text blocks)")

with c3:
    st.markdown("#### 3) parsed_info (post-processed)")
    try:
        parsed_info = parse_page3_blocks_resilient(page_blocks)
        st.json(parsed_info)
    except Exception:
        st.error("post_processor crashed.")
        st.exception(traceback.format_exc())
