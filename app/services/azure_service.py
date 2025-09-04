from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.ai.formrecognizer import DocumentAnalysisClient


def create_azure_client() -> DocumentAnalysisClient:
    """Create an Azure Document Analysis client using env vars."""
    load_dotenv()
    endpoint = os.getenv("AZURE_DOC_AI_ENDPOINT")
    key = os.getenv("AZURE_DOC_AI_KEY")
    if not endpoint or not key:
        raise RuntimeError("AZURE_DOC_AI_ENDPOINT or AZURE_DOC_AI_KEY not set in .env")
    return DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))


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
        poller = client.begin_analyze_document(
            "prebuilt-document",
            document=f,
            pages=str(page_number),
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

