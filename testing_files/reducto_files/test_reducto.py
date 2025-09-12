from pathlib import Path
import json
from typing import Any
import os
import sys
import socket

from dotenv import load_dotenv
from reducto import ReductoError

# Ensure project root is importable for `app.services`
sys.path.append(str(Path(__file__).resolve().parents[2]))
from app.services.reducto_service import create_client
from testing_files.reducto_files.system_prompt import (
    get_irs_1040_2024_extraction_prompt,
)

# Ensure env vars from .env are loaded for local runs
load_dotenv()

# Optional: set corporate proxy for outbound HTTP(S)
# Update the hostname/port if needed or comment out to disable.
PROXY_HOST = "trta-prof-devops-squid.int.thomsonreuters.com:3128"
USE_PROXY = True

# Initialize the client with API key from env
api_key = os.getenv("REDUCTO_API_KEY")
if not api_key:
    raise ReductoError(
        "REDUCTO_API_KEY is not set. Add it to .env or export it before running."
    )

proxy_url = None
if USE_PROXY and PROXY_HOST:
    host, _, port = PROXY_HOST.partition(":")
    try:
        # Basic DNS reachability check; if it fails, skip proxy
        socket.getaddrinfo(host, int(port) if port else 3128)
        proxy_url = f"http://{host}:{port or '3128'}"
    except Exception:
        print(f"Proxy host not resolvable, bypassing proxy: {host}")
        proxy_url = None

print(f"Using proxy: {proxy_url or 'DIRECT'}")
client = create_client(
    use_proxy=bool(proxy_url),
    proxy_url=proxy_url,
    connect_timeout=10.0,
    read_timeout=60.0,
    write_timeout=30.0,
    max_retries=1,
)

"""
This script iterates over JSON schema files in input_json and writes
extraction results to output_json as output{filename}.json.
"""



def _to_printable(obj):
    for m in ("model_dump", "dict"):
        if hasattr(obj, m) and callable(getattr(obj, m)):
            try:
                return getattr(obj, m)()
            except Exception:
                pass
    return obj

# Upload the multi-page sample PDF and use its reducto:// URL
pdf_path = Path("testing_files/reducto_files/sample_1040.pdf")
if not pdf_path.exists():
    raise FileNotFoundError(f"Sample PDF not found at {pdf_path}")

upload_url = client.upload(file=pdf_path)

# Page range: 18-19 (inclusive)
advanced_options = {
    "page_range": {"start": 1, "end": 2}
}

def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


base_dir = Path(__file__).resolve().parent
input_dir = base_dir / "input_json"
output_dir = base_dir / "output_json"
output_dir.mkdir(parents=True, exist_ok=True)

# Iterate through each JSON file in the input folder
for schema_file in sorted(input_dir.glob("*.json")):
    try:
        schema_data = _load_json(schema_file)
    except Exception as e:
        print(f"Skipping {schema_file.name}: failed to load JSON ({e})")
        continue

    # Prefer schema-based extract if available; otherwise fall back to parse
    if hasattr(client, "extract") and hasattr(client.extract, "run"):
        resp = client.extract.run(
            document_url=upload_url,
            schema=schema_data,
            system_prompt=get_irs_1040_2024_extraction_prompt(),
            options={"extraction_mode": "ocr"},
            advanced_options=advanced_options,
        )
    else:
        resp = client.parse.run(
            document_url=upload_url,
            options={
                "ocr_mode": "agentic",
                "extraction_mode": "ocr",
                "chunking": {"chunk_mode": "variable"},
            },
            advanced_options=advanced_options,
        )

    # Name output as output{filename}.json (filename without extension)
    out_name = f"output_{schema_file.stem}.json"
    out_path = output_dir / out_name
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(_to_printable(resp), f, ensure_ascii=False, indent=2)
    print(f"Wrote {out_path}")
