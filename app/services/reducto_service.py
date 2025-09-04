from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Iterable, List, Dict, Any

from dotenv import load_dotenv
from reducto import Reducto, ReductoError

from app.config import OPTIONS, ADVANCED_OPTIONS, EXPERIMENTAL_OPTIONS


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
    """Resolve actual page number when Reducto renumbers single-page results."""
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

