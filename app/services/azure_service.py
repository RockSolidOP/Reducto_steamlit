from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.ai.formrecognizer import DocumentAnalysisClient

try:
    # Newer SDK (matches testing_files/azureAI/azuretest.py)
    from azure.ai.documentintelligence import DocumentIntelligenceClient  # type: ignore
    HAS_DOCINTEL = True
except Exception:  # ImportError or others if not installed
    HAS_DOCINTEL = False
from app.config import AZURE_CONFIG


def create_azure_client() -> DocumentAnalysisClient:
    """Create an Azure Document Analysis client using env vars."""
    load_dotenv()
    endpoint = os.getenv("AZURE_DOC_AI_ENDPOINT")
    key = os.getenv("AZURE_DOC_AI_KEY")
    if not endpoint or not key:
        raise RuntimeError("AZURE_DOC_AI_ENDPOINT or AZURE_DOC_AI_KEY not set in .env")
    return DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))


def create_docint_client() -> DocumentIntelligenceClient:
    """Create a Document Intelligence client (new SDK)."""
    if not HAS_DOCINTEL:
        raise RuntimeError(
            "azure-ai-documentintelligence is not installed. Add it to requirements and pip install."
        )
    load_dotenv()
    endpoint = os.getenv("AZURE_DOC_AI_ENDPOINT")
    key = os.getenv("AZURE_DOC_AI_KEY")
    if not endpoint or not key:
        raise RuntimeError("AZURE_DOC_AI_ENDPOINT or AZURE_DOC_AI_KEY not set in .env")
    return DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))

def parse_with_azure(
    file_path: Path,
    page_number: int | None = None,
    model_id: str | None = None,
    pages: str | None = None,
):
    """Run Azure Doc AI model.

    - ``pages``: Azure pages string like "1", "18-19", or "1,3,5-7".
    - If ``pages`` is not provided, uses ``page_number`` when given; otherwise analyzes all pages.
    - If ``model_id`` is not provided, falls back to ``AZURE_CONFIG['model_id']``.
    """
    client = create_azure_client()
    _model_id = model_id or AZURE_CONFIG.get("model_id", "prebuilt-document")
    _pages = pages or (str(page_number) if page_number is not None else None)
    with open(file_path, "rb") as f:
        if _pages:
            poller = client.begin_analyze_document(
                _model_id,
                document=f,
                pages=_pages,
            )
        else:
            poller = client.begin_analyze_document(
                _model_id,
                document=f,
            )
    return poller.result()


def parse_with_azure_docint(
    file_path: Path,
    page_number: int | None = None,
    model_id: str | None = None,
    pages: str | None = None,
):
    """Run Document Intelligence begin_analyze_document (new SDK).

    Mirrors parse_with_azure signature for convenience.
    """
    client = create_docint_client()
    _model_id = model_id or AZURE_CONFIG.get("model_id", "prebuilt-document")
    _pages = pages or (str(page_number) if page_number is not None else None)
    with open(file_path, "rb") as f:
        if _pages:
            poller = client.begin_analyze_document(_model_id, body=f, pages=_pages)
        else:
            poller = client.begin_analyze_document(_model_id, body=f)
    return poller.result()


# Removed convenience wrapper analyze_1040_schedule1; use run_azure_analysis via UI instead.


def _to_native(obj):
    """Recursively convert Azure SDK objects to JSON-serializable primitives.

    Handles mappings, sequences, and objects exposing to_dict()/as_dict()/__dict__.
    Unknown objects fall back to str(obj).
    """
    # Primitives
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    # Bytes -> base64 string to preserve content
    if isinstance(obj, (bytes, bytearray)):
        try:
            import base64
            return base64.b64encode(obj).decode("ascii")
        except Exception:
            return str(obj)
    # Mapping
    if isinstance(obj, dict):
        return {str(k): _to_native(v) for k, v in obj.items()}
    # Sequence (list/tuple/set)
    if isinstance(obj, (list, tuple, set)):
        return [_to_native(v) for v in obj]
    # Azure model style: to_dict / as_dict
    for meth in ("to_dict", "as_dict"):
        if hasattr(obj, meth) and callable(getattr(obj, meth)):
            try:
                return _to_native(getattr(obj, meth)())
            except Exception:
                pass
    # Fallback to __dict__ (public attrs)
    if hasattr(obj, "__dict__"):
        try:
            data = {k: v for k, v in vars(obj).items() if not k.startswith("_")}
            return _to_native(data)
        except Exception:
            return str(obj)
    # Last resort
    return str(obj)


def azure_to_dict(result) -> dict:
    """Best-effort convert Azure result to a JSON-serializable dict."""
    # Try native conversions first
    for meth in ("to_dict", "as_dict"):
        if hasattr(result, meth) and callable(getattr(result, meth)):
            try:
                out = getattr(result, meth)()
                return _to_native(out)
            except Exception:
                pass
    # Try to_json (DocumentIntelligence models sometimes support this)
    if hasattr(result, "to_json") and callable(getattr(result, "to_json")):
        try:
            return json.loads(result.to_json())
        except Exception:
            pass
    # Generic deep conversion
    return _to_native(result)


# Removed KV helpers now that UI/pipeline only use document fields.


def azure_fields_to_dict(result):
    """Extract raw document.fields across all documents.

    - If there is a single document, returns its fields dict directly.
    - If multiple documents are present, returns a list of {index, docType, fields}.
    - Returns {} if no documents/fields.
    """
    try:
        d = azure_to_dict(result)
        docs = d.get("documents") or []
        if not docs:
            return {}
        if len(docs) == 1:
            return docs[0].get("fields") or {}
        out = []
        for i, doc in enumerate(docs):
            out.append(
                {
                    "index": i,
                    "docType": doc.get("doc_type") or doc.get("docType"),
                    "fields": doc.get("fields") or {},
                }
            )
        return out
    except Exception:
        # Fallback: attempt to access attributes directly
        try:
            docs = getattr(result, "documents", []) or []
            if not docs:
                return {}
            if len(docs) == 1:
                return getattr(docs[0], "fields", {}) or {}
            out = []
            for i, doc in enumerate(docs):
                out.append(
                    {
                        "index": i,
                        "docType": getattr(doc, "doc_type", None),
                        "fields": getattr(doc, "fields", {}) or {},
                    }
                )
            return out
        except Exception:
            return {}
