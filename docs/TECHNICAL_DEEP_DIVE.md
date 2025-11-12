# Reducto + Azure Doc AI GUI — Technical Deep Dive

## Module Map & Responsibilities

- Entry
  - `app.py`: Thin trampoline to `app/main.py` run function.
  - `app/main.py`: App bootstrap, layout, upload/save, page preview, sidebar cost estimator, tab registry, trigger handling, and result rendering below tabs.
- UI Tabs (`app/ui/`)
  - `azure_tab.py`: Default Azure and 1040 sub-tabs, model/page inputs, run logic with SDK fallback, cost estimator, JSON conversion, and derived fields view.
  - `reducto_tab.py`: Simple and Schema Extract sub-tabs, page range handling, upload-to-Reducto, resilient block/page mapping, per-block post-processor timings, and single-block reproducer.
  - `pymupdf_tab.py`: Scope selection (selected page, list, or all), text extraction, heuristic KV, advanced structures, and dumps.
  - `classify_tab.py`: Two engines (Regex and ML), config upload/selection, full-document classification, and result download.
  - `components.py`: `fmt_duration`, `file_uploader`, `download_json_button`, `count_pages_from_spec`, `pages_list_from_spec`.
  - `debug.py`: Sidebar diagnostics (env keys set, cert bundle, proxy vars) and post-processor reload.
- State (`app/state/session.py`)
  - `AppState`: typed write-through view over `st.session_state` keys: `pdf_path`, `use_reducto_range`, `reducto_start_page`, `reducto_end_page`, `azure_default_model`, `azure_default_pages`, `azure_1040_model`, `azure_1040_pages`, `pym_scope_all`, `pym_pages_spec`.
  - `get_state()`: populates defaults (with Azure defaults from `app/config.py`).
- Services (`app/services/`)
  - Reducto (`reducto_service.py`): `create_client`, `parse_document`, `parse_document_range`, `extract_with_schema`, `_present_block_pages`, `_resolve_effective_page`, `extract_page_blocks`, `get_blocks_for_page`.
  - Azure (`azure_service.py`): `create_azure_client`, `create_docint_client`, `parse_with_azure`, `parse_with_azure_docint`, `azure_to_dict`, `azure_fields_to_dict`.
  - PyMuPDF (`pymupdf_service.py`): `extract_page_dict`, `extract_text`, `extract_blocks`, `extract_words`, `extract_text_reading_order`, `parse_with_pymupdf`, `simple_page_dump`.
  - Classification (Regex) (`classify_service.py`): config loading, normalization, header/body/footer extraction, rule compilation, weighted scoring, label smoothing, and result assembly.
  - Classification (ML) (`ml_classify_service.py`): wrapper around `app.resources.classifier_module`, top-K suggestions, label normalization, and active FAISS version detection.
- Post-Processing (`app/post_processing.py`)
  - Dynamically loads `post_processors/post_processor.py` and exposes `parse_page3_blocks_resilient` with a safe fallback if the module is missing.
- Config (`app/config.py`)
  - Reducto options (OPTIONS, ADVANCED_OPTIONS, EXPERIMENTAL_OPTIONS), Azure model ids, uploads cleanup policy, and PyMuPDF heuristics control.
- Resources
  - Reducto schemas (`app/resources/reducto_schema/`), regex classifier config (`app/resources/classifyier_regex/forms_config.json`), and ML classifier module (`app/resources/classifier_module/`).

## Key Implementation Details

### Uploads & Preview

- `app/utils/storage.py`: `save_uploaded_file` writes to `uploads/` with sanitized, timestamped filenames. `cleanup_uploads` enforces age/size/count limits and returns a summary displayed in the UI.
- `app/utils/pdf_preview.py`: `render_pdf_page_png_bytes` renders a 1-indexed page to PNG using PyMuPDF, with configurable zoom.

### Reducto Service

- `create_client` builds a client with an explicit `httpx.Client` and timeouts (`connect`, `read`, `write`, and `pool`). By default, it sets `trust_env=False` so `HTTP(S)_PROXY` env vars are ignored unless you pass a `proxy_url` explicitly. This improves determinism in corporate networks.
- `parse_document` and `parse_document_range` upload the file and run `client.parse.run` with dynamic `page_range` in `ADVANCED_OPTIONS`.
- `extract_with_schema` forwards the caller-provided schema and a system prompt to `client.extract.run` and returns a JSON-serializable dict (`model_dump` or `dict` if available).
- Page mapping resilience:
  - `_present_block_pages` collects pages found in block `bbox.page` values.
  - `_resolve_effective_page` maps the requested page to the single available page when Reducto renumbers single-page results.
  - `extract_page_blocks` and `get_blocks_for_page` use that mapping to ensure correct page selection.

### Azure Service

- Two SDKs supported: legacy Form Recognizer (`azure-ai-formrecognizer`) and new Document Intelligence (`azure-ai-documentintelligence`). `run_azure_analysis` prefers one and retries the other; it falls back on model-not-found errors.
- `azure_to_dict` walks SDK objects, attempting `to_dict`/`as_dict`/`to_json`, then a generic deep conversion including `__dict__` and sequences; unknowns fall back to `str(obj)`.
- `azure_fields_to_dict` extracts per-document `fields`; returns a dict for a single document or a list of `{index, docType, fields}` for multiple.

### PyMuPDF Service

- `extract_text_reading_order` reconstructs line ordering by bucketing words on y and sorting by x to stabilize output versus producer quirks.
- `parse_with_pymupdf` collects `text`, `text_reading_order`, `blocks` (from `get_text("blocks")`), `words`, and `dict`.
- The UI tab provides both normalized text and heuristic key-values (`postprocess_pymupdf`) along with an advanced raw-structures expander.

### Classification Engines

- Regex Rules (`classify_service.py`)
  - Config structure: `weights` per section; `families` list with `{name, patterns, must, avoid, boost}`; `labels` with `{label, family, patterns, must, avoid, boost}`.
  - `classify_document` iterates all pages with PyMuPDF, extracts header/body/footer (`_extract_sections`) using page y-thresholds, normalizes text, compiles rules, scores matches, respects `must`/`avoid`, applies boosts and section weights, and picks the top family/label. Smoothing renames a few common multi-page labels (e.g., `1040_Main_Pg1/Pg2`).
  - `classify_to_result` assembles a concise JSON with `pages:[{page,family,label}]` and metadata.
- ML (FAISS + OpenCLIP) (`ml_classify_service.py` + `app/resources/classifier_module/`)
  - Uses a module-level suggester: render the page (pypdfium2) → OpenCLIP embed (CPU) → FAISS search. Returns top-k neighbors with `{label, score, base_label, page_in_form}`.
  - `CLASSIFIER_FAISS_DIR` env var or bundled data under `app/resources/classifier_module/data/faiss` determine index resolution. `ACTIVE_VERSION.txt` selects the versioned index and id-map naming.

### Post-Processing Plugin

- `app/post_processing.py` exposes a single callable `parse_page3_blocks_resilient(blocks: List[Dict[str, Any]]) -> Dict[str, Any]`.
- If `post_processors/post_processor.py` is present, it is imported (and can be reloaded from the Debug sidebar). If not, a safe fallback returns a simple summary.
- Example implementation provided in `post_processors/post_processor.py` shows how to normalize and parse a specific form’s blocks.

## UI Triggers & Data Flow

- Main page sets `st.session_state["page_number_ui"]` and `st.session_state["page_count_ui"]` and registers tabs.
- Each tab writes run triggers into session state:
  - Reducto: `trigger_reducto` with optional `use_reducto_range`, `reducto_start_page`, `reducto_end_page`.
  - Azure: `trigger_azure_default` and persisted `azure_default_model`/`azure_default_pages`.
  - PyMuPDF: `trigger_pymupdf` and persisted `pym_scope_all`/`pym_pages_spec`.
- After tab rendering, `app/main.py` executes the triggered runs with context (paths, page) and renders results beneath the tabs.

## Error Handling & Diagnostics

- Reducto client creation and API calls are wrapped with early exits, friendly error messages, and an optional traceback expander.
- Azure analysis catches model-not-found errors to attempt a configured fallback model.
- The Debug sidebar reports interpreter info, `.env` presence, key lengths (without values), CA bundle path, proxy environment variables, and a hot-reload button for the post-processor.

## Performance Considerations

- Timeouts are bounded for Reducto httpx client; retry count is configurable.
- UI shows durations with `fmt_duration` for API and post-processing steps.
- PyMuPDF extraction provides both bulk and page-specific views to balance thoroughness and responsiveness.

## Extending & Integrating

- New tabs can follow the `Tab` Protocol (`name`, `render(state)`) and be appended to the registry in `app/main.py`.
- To add a new Azure model shortcut, extend `AZURE_CONFIG` and wire the option into `AzureTab`.
- To enable environment-proxy handling globally for Reducto, set `trust_env=True` in its httpx client and/or pass an env-driven `proxy=`.
- To expand regex classification, update `forms_config.json` with new families/labels and adjust `weights/must/avoid/boost`.
- To change ML classifier data, replace bundled FAISS assets or point `CLASSIFIER_FAISS_DIR` to your dataset path.

## What You Need to Run Locally

- Python 3.10–3.13 with `pip install -r requirements.txt`.
- `.env` with `REDUCTO_API_KEY`, `AZURE_DOC_AI_ENDPOINT`, `AZURE_DOC_AI_KEY`.
- For ML classification: ensure `open-clip-torch`, `torch`, `faiss-cpu`, and `pypdfium2` install successfully; first-time CLIP weight download may need network access.
- Optional corporate proxy: pass explicitly to Reducto via `create_client(use_proxy=True, proxy_url=...)` or adjust `trust_env`.

## Known Limitations

- Reducto path avoids environment proxies by default (safer), which may require explicit proxy configuration in locked-down networks.
- Azure model IDs vary by region/tenant; 1040 variants may not be available everywhere; the tab falls back to `prebuilt-document` when possible.
- ML classifier requires CPU-friendly wheels; some environments (e.g., constrained corporate Macs) may require custom installation steps for `faiss-cpu`/`torch`.

