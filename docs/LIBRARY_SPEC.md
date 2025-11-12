# Library Specification and Usage

This document summarizes the key third‑party libraries used by the app, what they do, and where they are used in the codebase. Versions are pinned in `requirements.txt` and may change over time; the purpose descriptions here remain valid across minor updates.

## Core UI and State

- `streamlit`
  - Purpose: Drives the entire UI (layout, widgets, session state, status displays, downloads).
  - Used in: `app/main.py:7`, `app/ui/*.py` (tabs, controls), `app/state/session.py:19`.

- `python-dotenv`
  - Purpose: Loads environment variables (API keys, endpoints) from `.env` for local runs.
  - Used in: `app/services/reducto_service.py:8`, `app/services/azure_service.py:7`.

## PDF Processing and Preview

- `PyMuPDF` (import name `fitz`)
  - Purpose: Local PDF parsing and page rendering (text, blocks, words, dict structures) and page counting.
  - Used in: `app/services/pymupdf_service.py:6`, `app/utils/pdf_preview.py:4`, `app/main.py:6`, `app/services/classify_service.py:17`.

- `pypdfium2` + `Pillow`
  - Purpose: Rendering PDF pages to images inside the ML classifier module for CLIP embeddings (CPU‑friendly).
  - Used in: `app/resources/classifier_module/_suggester.py:119`, `app/resources/classifier_module/_suggester.py:120` (imports on demand inside `embed_pdf_page`).

## External Services and Networking

- `reductoai` (import name `reducto`)
  - Purpose: Reducto SDK for document parsing and schema‑based extraction.
  - Used in: `app/services/reducto_service.py:9` (client creation, parse/extract helpers).
  - Notes: The service uses an explicit `httpx.Client` with bounded timeouts and `trust_env=False` by default to avoid inheriting corporate proxy settings implicitly.

- `httpx`
  - Purpose: Underlies the Reducto client for explicit connection/read/write/pool timeouts and optional proxy routing.
  - Used in: `app/services/reducto_service.py:10` (explicit client with timeouts and proxy control).

- Azure SDKs: `azure-ai-formrecognizer`, `azure-ai-documentintelligence`, `azure-core`
  - Purpose: Call Azure Document Intelligence (legacy Form Recognizer and the new DI client). The app prefers one and falls back to the other on model issues.
  - Used in: `app/services/azure_service.py:8` (credentials), `app/services/azure_service.py:9` (Form Recognizer), `app/services/azure_service.py:13` (Document Intelligence; optional).

## Classification Engines

- Regex‑based classification (standard library `re` + PyMuPDF)
  - Purpose: Local, no‑network classifier using regex families/labels configured via bundled JSON.
  - Used in: `app/services/classify_service.py` and tab wiring in `app/ui/classify_tab.py`.

- ML classifier stack: `open-clip-torch` (import `open_clip`), `torch`, `faiss-cpu`, `numpy`, `pypdfium2`, `Pillow`
  - Purpose: Optional CPU‑only pipeline for page embeddings (OpenCLIP) and nearest‑neighbor search (FAISS) against a local index.
  - Used in: `app/resources/classifier_module/_suggester.py` (lazy imports inside helper functions), wrapped by `app/services/ml_classify_service.py` and surfaced in `app/ui/classify_tab.py`.
  - Notes: Controlled by the `CLASSIFIER_FAISS_DIR` env var or bundled assets under `app/resources/classifier_module/data/faiss`. First‑time CLIP weight download may require network access.

## Diagnostics and Utilities

- `certifi`
  - Purpose: CA bundle path reporting for TLS diagnostics in the Debug sidebar.
  - Used in: `app/ui/debug.py:41` (import attempted; app handles absence gracefully).

## Indirect or Not Used Directly by App Code

These packages appear in `requirements.txt` due to Streamlit or SDK dependencies, or for future expansion. The app doesn’t import them directly:

- Visualization/data packages commonly pulled in by Streamlit: `altair`, `pydeck`, `pandas`, `pyarrow`.
- Azure/HTTP stack dependencies: `azure-common`, `msrest`, `httpcore`, `idna`, `urllib3`.
- OpenAI client (`openai`) and helpers (`jiter`): not referenced by app modules; may be used transitively or reserved for future features.
- Git utilities (`GitPython`, `gitdb`, `smmap`): not referenced by app modules.

## How It Fits Together

- Streamlit renders the UI and manages session state and triggers. Users upload a PDF, select a page, then run one of the processors via tabs.
- PyMuPDF powers local page counting, on‑device text extraction, and page preview rendering (PNG) for the main view.
- Reducto and Azure SDKs provide cloud parsing/extraction paths, with results rendered and downloadable from the UI.
- Classification can run fully locally via regex rules, or with the optional ML stack (OpenCLIP + FAISS) if the FAISS assets are available.
- `python-dotenv` helps during local development by loading `.env`; production deployments typically set env vars at the process level.

## Notes on Proxies and Timeouts

- Reducto path uses an explicit `httpx.Client` with `trust_env=False` by default to avoid inheriting `HTTP(S)_PROXY`. Pass a proxy explicitly to `create_client(use_proxy=True, proxy_url=...)` or change the client to honor env vars if needed.
- Azure SDKs use the standard Azure clients; configure network/proxy at the environment or platform level as required.

## Quick File Pointers

- UI entry and layout: `app/main.py:1`.
- Per‑service helpers: `app/services/*.py`.
- Tabs: `app/ui/*.py`.
- Classifier module (ML): `app/resources/classifier_module/`.
