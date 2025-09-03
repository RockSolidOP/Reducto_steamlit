from __future__ import annotations
from pathlib import Path
from typing import Protocol, Dict, Any

class DocumentParser(Protocol):
    name: str
    def parse_document_for_page(self, file_path: Path, page_number: int) -> Dict[str, Any]:
        ...
