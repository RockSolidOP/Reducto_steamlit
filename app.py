from __future__ import annotations
_APP_DOC = """
Streamlit GUI to upload a PDF, run Reducto parsing, and display:
1) "parsed"       – raw Reducto JSON
2) "block"        – concatenated text from the selected page
3) "parsed_info"  – post-processed JSON via your post_processors/post_processor.py
4) Azure Document AI – raw + key-value JSON

Extras:
- PDF page preview (PyMuPDF)
- Debug panel (env/key checks, cwd, files)
- Hot-reload for post_processors.post_processor
- Single-block reproducer to isolate crashes (Reducto)
- Dual-engine buttons to control cost

Setup:
  pip install -U streamlit reductoai python-dotenv pymupdf pillow azure-ai-formrecognizer
  # NOTE: the PyPI package is `reductoai`, but import is `from reducto import Reducto`

Env (.env):
  REDUCTO_API_KEY=...
  AZURE_DOC_AI_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com/
  AZURE_DOC_AI_KEY=...

Run:
  streamlit run app.py
"""

import copy
import glob
import importlib
import io
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Iterable, List, Dict, Any

import streamlit as st
from dotenv import load_dotenv

# PDF rendering
import fitz  # PyMuPDF
from PIL import Image

# Reducto SDK (installed via `pip install reductoai`)
from reducto import Reducto, ReductoError

# Azure Doc AI
from azure.core.credentials import AzureKeyCredential
from azure.ai.formrecognizer import DocumentAnalysisClient

# ---- Options (from your original script) ----
OPTIONS = {
    "ocr_mode": "agentic",
    "extraction_mode": "ocr",
    "chunking": {"chunk_mode": "variable"},
}

ADVANCED_OPTIONS = {
    "ocr_system": "multilingual",
    # page_range is set dynamically to the selected page
    "page_range": {"start": 1, "end": 10},
    "table_output_format": "ai_json",
    "merge_tables": True,
}

EXPERIMENTAL_OPTIONS = {
    "enable_checkboxes": True,
    "return_figure_images": False,
    "rotate_pages": True,
}

# ---- Post-processor import + hot-reload support ----
POST_PROCESSOR_PATH = None
_pp = None  # module handle
def _import_post_processor():
    global _pp, POST_PROCESSOR_PATH, parse_page3_blocks_resilient
    try:
        import post_processors.post_processor as _pp  # your file in a package folder
        parse_page3_blocks_resilient = _pp.parse_page3_blocks_resilient
        POST_PROCESSOR_PATH = getattr(_pp, "__file__", None)
        return True, None
    except Exception as e:
        POST_PROCESSOR_PATH = None
        def parse_page3_blocks_resilient(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
            return {
                "warning": "post_processors.post_processor not found or failed to import; returning blocks summary.",
                "blocks_count": len(blocks),
                "sample_keys": sorted({k for b in blocks for k in b.keys()})[:10],
            }
        return False, e

_ok, _err = _import_post_processor()

# ---- Helpers ----
def create_azure_client() -> DocumentAnalysisClient:
    """Create an Azure Document Analysis client using env vars."""
    load_dotenv()
    endpoint = os.getenv("AZURE_DOC_AI_ENDPOINT")
    key = os.getenv("AZURE_DOC_AI_KEY")
    if not endpoint or not key:
        raise RuntimeError("AZURE_DOC_AI_ENDPOINT or AZURE_DOC_AI_KEY not set in .env")
    return DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))

def create_client() -> Reducto:
    """Create a Reducto client using an API key from .env or environment."""
    load_dotenv()
    api_key = os.getenv("REDUCTO_API_KEY")
    if not api_key:
        raise ReductoError(
            "REDUCTO_API_KEY is not set. Provide it via environment variable or .env file."
        )
    return Reducto(api_key=api_key)

def parse_document(client: Reducto, file_path: Path, page_number: int) -> dict:
    """Upload and parse a document for a specific page."""
    upload_url = client.upload(file=file_path)
    adv = copy.deepcopy(ADVANCED_OPTIONS)
    adv["page_range"] = {"start": page_number, "end": page_number}
    result = client.parse.run(
        document_url=upload_url,
        options=OPTIONS,
        advanced_options=adv,
        experimental_options=EXPERIMENTAL_OPTIONS,
    )
    return result.model_dump()

def _present_block_pages(parsed: dict) -> List[int]:
    pages = set()
    for chunk in parsed.get("result", {}).get("chunks", []) or []:
        for block in chunk.get("blocks", []) or []:
            p = (block.get("bbox") or {}).get("page")
            if isinstance(p, int):
                pages.add(p)
    return sorted(pages)

def _resolve_effective_page(parsed: dict, requested_page: int) -> int:
    """Reducto may renumber pages to 1 when parsing a single page range.
    If the requested page isn't present in block bboxes, fall back to the sole
    page number present (if exactly one), otherwise keep the requested.
    """
    pages = _present_block_pages(parsed)
    if requested_page in pages:
        return requested_page
    if len(pages) == 1:
        return pages[0]
    return requested_page

def extract_page_blocks(parsed: dict, page_number: int) -> Iterable[str]:
    """Yield text content for blocks on a given page, with resilient page mapping."""
    effective_page = _resolve_effective_page(parsed, page_number)
    for chunk in parsed.get("result", {}).get("chunks", []) or []:
        for block in chunk.get("blocks", []) or []:
            if (block.get("bbox") or {}).get("page") == effective_page:
                content = block.get("content")
                if content is not None:
                    yield content

def get_blocks_for_page(parsed: dict, page_number: int) -> List[Dict[str, Any]]:
    """Return full block dicts for a given page, with resilient page mapping."""
    out: List[Dict[str, Any]] = []
    effective_page = _resolve_effective_page(parsed, page_number)
    for chunk in parsed.get("result", {}).get("chunks", []) or []:
        for block in chunk.get("blocks", []) or []:
            if (block.get("bbox") or {}).get("page") == effective_page:
                out.append(block)
    return out

def render_pdf_page_png_bytes(pdf_path: Path, page_number: int, zoom: float = 2.0) -> bytes:
    """Return PNG bytes for the given PDF page (1-indexed)."""
    with fitz.open(pdf_path) as doc:
        page_index = max(0, min(page_number - 1, doc.page_count - 1))
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")

# ---- Azure helpers ----
def parse_with_azure_old(file_path: Path):
    """Run Azure Doc AI prebuilt-document model and return the result object."""
    client = create_azure_client()
    with open(file_path, "rb") as f:
        poller = client.begin_analyze_document("prebuilt-document", document=f)
    return poller.result()

def parse_with_azure(file_path: Path, page_number: int):
    """Run Azure Doc AI prebuilt-document model on a single page."""
    client = create_azure_client()
    with open(file_path, "rb") as f:
        # Azure accepts page ranges like "1", "1-3", or a list ["1", "3"]
        poller = client.begin_analyze_document(
            "prebuilt-document",
            document=f,
            pages=str(page_number),   # <-- key line
        )
    return poller.result()


def azure_to_dict(result) -> dict:
    """Best-effort convert Azure result to a dict."""
    try:
        return result.to_dict()  # available in recent SDKs
    except AttributeError:
        try:
            return json.loads(result.to_json())
        except Exception:
            # last resort: shallow projection
            return {"documents": [vars(d) for d in getattr(result, "documents", [])]}

def azure_kv_to_dict(result) -> dict:
    kv_dict = {}
    for kv_pair in result.key_value_pairs:
        key_text = kv_pair.key.content if kv_pair.key else None
        value_text = kv_pair.value.content if kv_pair.value else None
        if key_text:
            kv_dict[key_text] = value_text
    return kv_dict

# ---- Debug panel ----
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

    if POST_PROCESSOR_PATH:
        st.sidebar.caption(f"post_processor: `{POST_PROCESSOR_PATH}`")
        if st.sidebar.button("Reload post_processor.py"):
            try:
                mod = importlib.reload(_pp)  # type: ignore
                globals()["parse_page3_blocks_resilient"] = mod.parse_page3_blocks_resilient  # type: ignore
                st.sidebar.success("Reloaded post_processor.py")
            except Exception as e:
                st.sidebar.error(f"Reload failed: {e}")
                st.sidebar.exception(traceback.format_exc())
    else:
        st.sidebar.warning("post_processors.post_processor NOT loaded. Using fallback.")

    if st.sidebar.button("🔄 Rerun"):
        st.rerun()

# ---- UI ----
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

    except ReductoError as e:
        st.error(f"Reducto error: {e}")
    except Exception:
        st.exception(traceback.format_exc())

# -------- Azure Doc AI path --------
# if do_process_azure:
#     st.divider()
#     st.subheader("Azure Document AI Output")

#     try:
#         with st.status("Analyzing with Azure Document Intelligence…", expanded=False) as status:
#             # azure_result = parse_with_azure(tmp_pdf)
#             azure_result = parse_with_azure(tmp_pdf, int(page_number))
#             st.caption(f"Azure analyzed page: {page_number}")

#             status.update(label="Azure analysis complete", state="complete")

#         colA, colB = st.columns(2)

#         with colA:
#             st.markdown("#### Raw Azure Output")
#             az_raw = azure_to_dict(azure_result)
#             st.json(az_raw)
#             st.download_button(
#                 "Download azure_raw.json",
#                 data=json.dumps(az_raw, ensure_ascii=False, indent=2),
#                 file_name="azure_raw.json",
#                 mime="application/json",
#             )

#         with colB:
#             st.markdown("#### Azure Key-Value Pairs (flattened)")
#             kv_dict = azure_kv_to_dict(azure_result)
#             st.json(kv_dict)
#             st.download_button(
#                 "Download azure_kv.json",
#                 data=json.dumps(kv_dict, ensure_ascii=False, indent=2),
#                 file_name="azure_kv.json",
#                 mime="application/json",
#             )

#     except Exception as e:
#         st.error(f"Azure Doc AI error: {e}")
#         st.exception(traceback.format_exc())


if do_process_azure:
    st.divider()
    st.subheader("Azure Document AI Output")

    try:
        with st.status("Analyzing with Azure Document Intelligence…", expanded=False) as status:
            azure_result = parse_with_azure(tmp_pdf, int(page_number))  # <-- pass page
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
                file_name=f"azure_raw_page{int(page_number)}.json",  # unique per page
                mime="application/json",
            )

        with colB:
            st.markdown("#### Azure Key-Value Pairs (flattened)")
            kv_dict = azure_kv_to_dict(azure_result)
            st.json(kv_dict)
            st.download_button(
                "Download azure_kv.json",
                data=json.dumps(kv_dict, ensure_ascii=False, indent=2),
                file_name=f"azure_kv_page{int(page_number)}.json",   # unique per page
                mime="application/json",
            )

    except Exception as e:
        st.error(f"Azure Doc AI error: {e}")
        st.exception(traceback.format_exc())

# If neither button clicked, just show preview + wait for action
if not (do_process or do_process_azure):
    st.info("Select a page and click **Process with Reducto** or **Process with Azure AI**.")
