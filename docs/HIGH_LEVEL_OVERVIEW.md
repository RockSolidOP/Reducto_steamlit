# Reducto + Azure Doc AI GUI — High-Level Overview

## Purpose & Scope

- Provide a simple, local GUI to compare and combine three approaches to PDF analysis:
  - Cloud API: Reducto (schema extract and general parse).
  - Cloud API: Azure Document Intelligence (Form Recognizer + new Document Intelligence SDK).
  - Local-only: PyMuPDF text extraction with heuristic key-value post-processing.
- Add optional whole-document page classification via two engines:
  - Rules-based (regex) using a JSON configuration.
  - ML-based (OpenCLIP + FAISS) using a bundled or external index.

## Key Capabilities

- Upload a PDF and preview a selected page as an image.
- Run processors from tabs and download JSON/text outputs:
  - Reducto: simple page or page-range parse; schema-based extraction; post-processing hook.
  - Azure: default model or 1040-focused models; automatic conversion of SDK objects to JSON.
  - PyMuPDF: normalized text, heuristic key-values, and raw structures per page.
  - Classify: label each page with a family/label using regex or ML.
- Cost awareness: sidebar Azure cost estimator using an Azure-style page spec.
- Local housekeeping: automatic uploads directory cleanup by age/size/file count.

## Architecture Overview

- UI Layer (Streamlit)
  - `app/main.py`: Orchestrates layout, upload, page selection, sidebar, and tab registry; triggers runs and renders results below tabs.
  - Tabs under `app/ui/`: `AzureTab`, `ReductoTab`, `PyMuPDFTab`, `ClassifyTab` encapsulate per-feature options and run controls.
  - Shared UI helpers in `app/ui/components.py` (durations, page spec parsing, downloads, uploader).
  - Debug sidebar in `app/ui/debug.py` surfaces env and post-processor reload.
- Services Layer
  - Reducto (`app/services/reducto_service.py`): client creation, parse/extract helpers, resilient page mapping, block extraction.
  - Azure (`app/services/azure_service.py`): clients for both SDKs, run helpers, robust JSON conversion and field extraction.
  - PyMuPDF (`app/services/pymupdf_service.py`): local text/blocks/words/page-dict extraction and a simple “page dump”.
  - Classification: regex rules (`app/services/classify_service.py`) and ML wrapper (`app/services/ml_classify_service.py`).
- State Layer
  - `app/state/session.py`: typed `AppState` wrapper over `st.session_state` with write-through semantics.
- Post-Processing Plugin
  - `app/post_processing.py`: loads `post_processors/post_processor.py` if present; otherwise provides a safe fallback.
- Resources
  - Schemas in `app/resources/reducto_schema/` and rules in `app/resources/classifyier_regex/forms_config.json`.
  - ML classifier module and bundled FAISS data in `app/resources/classifier_module/`.
- Testing & Samples
  - Smoke tests and samples under `testing_files/` (e.g., Reducto CLI, Azure examples, classification examples).

## Primary Flows

- Upload & Preview
  - Select and persist a PDF to `uploads/` with a sanitized, timestamped name; show a PNG preview of the selected page.
- Reducto
  - Create a client with explicit httpx timeouts and optional explicit proxy.
  - Upload PDF, run parse or schema-based extract against a selected page or range.
  - Display raw parsed output, page text blocks, and post-processed info; allow JSON downloads.
- Azure
  - Instantiate client(s) from env credentials; prefer one SDK and fall back to the other.
  - Run default or 1040-flavored models on a specified page list, convert result to JSON, and extract document fields.
  - Present raw output and derived fields with timing; allow JSON downloads.
- PyMuPDF
  - Extract normalized text from a selected page, a page list, or all pages; run a lightweight heuristic KV post-processor.
  - Provide an advanced view of raw blocks/words/page dict plus a notebook-like page dump.
- Classify (All Pages)
  - Regex engine: classify each page into family/label based on weighted regex matches across header/body/footer; optional smoothing.
  - ML engine: OpenCLIP embedding + FAISS nearest neighbors; show best label or top-K suggestions per page.

## Configuration & Environment

- Required env vars (via `.env`): `REDUCTO_API_KEY`, `AZURE_DOC_AI_ENDPOINT`, `AZURE_DOC_AI_KEY`.
- Proxy: Reducto path uses explicit httpx client with `trust_env=False` by default; pass a proxy explicitly or toggle to honor env vars if needed. The CLI smoke test includes a DNS-checked proxy option.
- Uploads cleanup policy (age, size, count) is configurable in `app/config.py`.
- Optional ML dependencies (for the ML classifier) are pinned in `requirements.txt` and can be omitted if not using that tab.

## Running & Deployment

- Local: `streamlit run app/main.py` (or `streamlit run app.py`).
- Docker: `docker build -t reducto-azure-ui:latest .` then `docker run --rm -it --env-file .env -p 8501:8501 reducto-azure-ui:latest`.
- Windows: Rancher Desktop + WSL2 recommended; see README for details.

## Extensibility

- Add a new tab: create a `Tab`-like class in `app/ui/`, add to the registry in `app/main.py`.
- Add a new Azure model: adjust the model id inputs in tab UI or defaults in `app/config.py`.
- Schema-based extract: drop JSON schemas into `app/resources/reducto_schema/` or upload via the Reducto tab UI.
- Post-processor: implement `parse_page3_blocks_resilient(blocks)` in `post_processors/post_processor.py` and reload from the debug sidebar.
- Classification rules: extend `app/resources/classifyier_regex/forms_config.json` to add families/labels and heuristics.

## Privacy & Storage Notes

- Uploads persist under `uploads/` for re-use and debugging and are subject to periodic cleanup.
- Cloud calls send uploaded content to Reducto and/or Azure; local PyMuPDF and classification do not require network.

