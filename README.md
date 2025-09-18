# Reducto + Azure Doc AI GUI

A Streamlit app and test scripts that demonstrate Reducto parsing alongside Azure Document Intelligence. This README covers local setup, environment configuration, running the app, a quick smoke test, and a Docker option for a reproducible run.

## Requirements

- Python 3.10–3.13 (tested with 3.13)
- macOS/Linux/WSL2
- Internet access to Reducto API and Azure Document Intelligence

## Environment Variables

Create a `.env` file in the project root with the following keys:

```
REDUCTO_API_KEY=your_reducto_api_key
AZURE_DOC_AI_ENDPOINT=https://<your-azure-endpoint>.cognitiveservices.azure.com/
AZURE_DOC_AI_KEY=your_azure_key
```

Optional (only if your network requires an outbound proxy):

```
# Example format if needed by your network tooling (NOT required by default)
HTTP_PROXY=http://user:pass@host:port
HTTPS_PROXY=http://user:pass@host:port
NO_PROXY=localhost,127.0.0.1
```

Note: The app’s Reducto client uses an explicit httpx client with `trust_env=False`. The included smoke test can use a hardcoded proxy in-code (see testing section below). If you need full app-wide proxy support, see “Proxy Options”.

## Local Setup

1) Create and activate a virtualenv

```
python -m venv .venv
source .venv/bin/activate
```

2) Install dependencies

```
pip install -r requirements.txt
```

3) Verify environment loads

```
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(bool(os.getenv('REDUCTO_API_KEY')), os.getenv('AZURE_DOC_AI_ENDPOINT'))"
```

## Run the Streamlit App

Recommended (modular entrypoint):

```
streamlit run app/main.py
```

Legacy trampoline (also supported):

```
streamlit run app.py
```

- Open http://localhost:8501
- Upload a PDF and try Reducto and Azure analysis

Required environment variables (names only): `REDUCTO_API_KEY`, `AZURE_DOC_AI_ENDPOINT`, `AZURE_DOC_AI_KEY`.

Notes:
- Keep your `.env` untracked (see `.gitignore`).
- Local development may rely on `python-dotenv` to load environment variables.

## Quick Smoke Test (CLI)

A minimal script that uploads a sample and runs Reducto (schema-based if available; otherwise parse), restricted to pages 18–19 of the provided multi-page sample.

Run:

```
python testing_files/reducto_files/test_reducto.py
```

Notes:
- Uses `.env` for `REDUCTO_API_KEY`.
- Uploads `testing_files/sample_multiple.pdf`.
- If a corporate proxy is set in the script, it will try to use it; if the proxy is not resolvable, it prints a message and falls back to direct with bounded timeouts.


### Rancher Desktop (Windows)

- Install Rancher Desktop (Step-by-Step):
  - https://trten.sharepoint.com/sites/intr-docker/SitePages/Install-Rancher-Desktop(-Step-by-Step.aspx
- If WSL2 is not installed yet (Windows):
  - https://learn.microsoft.com/windows/wsl/install

Notes for Rancher Desktop on Windows:
- Container runtime: Select `dockerd (moby)` in Rancher Desktop → Settings → Container Engine. This ensures the `docker` CLI works as expected. If you previously used `containerd`, switch to `dockerd` and restart Rancher Desktop.
- Kubernetes: Disable Kubernetes in Rancher Desktop unless you specifically need it, to avoid port/resource conflicts.
- WSL2 backend: Ensure WSL2 is installed and enabled; set default with `wsl --set-default-version 2`. A reboot may be required after enabling Windows features.

Troubleshooting (Rancher Desktop + WSL2):
- `docker: command not found` or `Cannot connect to the Docker daemon`:
  - Verify Rancher Desktop is running and the runtime is set to `dockerd (moby)`.
  - Close and reopen your terminal after switching runtimes.
- Images/containers not visible after switching runtime:
  - `containerd` and `dockerd` keep separate stores. Re-pull images after switching, or stick to `dockerd` for this project.
- WSL errors (e.g., 0x80370102):
  - Ensure virtualization is enabled in BIOS, and Windows features "Virtual Machine Platform" and "Windows Subsystem for Linux" are enabled. Then run `wsl --install` and reboot.
- Network/proxy issues:
  - If behind a corporate proxy, configure proxy in Rancher Desktop (Settings → Network) and/or use `.env` as described in Proxy Options.


## Docker (recommended for “it just runs”)

Build the image:

```
docker build -t reducto-azure-ui:latest .
```

Run the app (env vars provided at runtime, not baked into the image):

```
docker run --rm -it \
  --env-file .env \
  -p 8501:8501 \
  reducto-azure-ui:latest
```

Optionally mount local folders for persistence:

```
docker run --rm -it \
  --env-file .env \
  -p 8501:8501 \
  -v "$(pwd)/uploads:/app/uploads" \
  -v "$(pwd)/testing_files:/app/testing_files" \
  reducto-azure-ui:latest
```

Open http://localhost:8501 in your browser.


## Proxy Options

- Test script proxy: `testing_files/reducto_files/test_reducto.py` includes an optional proxy host; set `USE_PROXY = True` and `PROXY_HOST = "host:port"`. It DNS-checks the host and falls back to direct if unresolved.
- App-wide proxy: The Reducto client is created in `app/services/reducto_service.py`. It currently disables environment proxy variables (`trust_env=False`) and only uses a proxy if passed explicitly to `create_client()`. If you need global proxy via env vars, set `trust_env=True` and/or accept a `REDUCTO_PROXY_URL` environment variable and pass it into `httpx.Client(proxy=...)`.

## Troubleshooting

- Missing API key: `reducto.ReductoError: api_key must be set` → Ensure `.env` contains `REDUCTO_API_KEY` and that you run from the project root so `load_dotenv()` finds it.
- Azure credentials not set: `AZURE_DOC_AI_ENDPOINT or AZURE_DOC_AI_KEY not set` → Add both to `.env`.
- Proxy errors / hangs: If you see `httpx.ConnectError` or timeouts and you’re off VPN, disable the proxy in the test script or ensure the proxy hostname resolves. The app path uses bounded timeouts and does not inherit env proxies by default.
- Different behavior across folders: Confirm same venv and package versions (`pip freeze`), same `.env` values, and that you’re importing the intended files (print `module.__file__`).

## Project Layout

- `app/main.py` — Streamlit app orchestrator (upload, layout, tabs, triggers)
- `app.py` — thin trampoline that calls `app/main.py`
- `app/ui/` — tab UIs and helpers
  - `azure_tab.py`, `reducto_tab.py`, `pymupdf_tab.py`
  - `components.py` — small reusable UI utilities
- `app/state/session.py` — typed `AppState` and `get_state()`
- `app/services/reducto_service.py` — Reducto client creation and helpers
- `app/services/azure_service.py` — Azure Document Intelligence helpers
- `app/post_processing.py` — plugin loader + fallback for post-processor
- `testing_files/reducto_files/test_reducto.py` — CLI smoke test
- `requirements.txt` — pinned dependencies
- `Dockerfile`, `.dockerignore` — containerization
 - `.pre-commit-config.yaml`, `pyproject.toml` — lint/type/test tooling config

## License

Proprietary code. Do not redistribute without permission.
