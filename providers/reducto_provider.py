from __future__ import annotations
from pathlib import Path
from typing import Dict, Any
import copy
import os

from reducto import Reducto, ReductoError

OPTIONS = {"ocr_mode": "agentic", "extraction_mode": "ocr", "chunking": {"chunk_mode":"variable"}}
ADVANCED_OPTIONS = {"ocr_system":"multilingual","page_range":{"start":1,"end":10},"table_output_format":"ai_json","merge_tables":True}
EXPERIMENTAL_OPTIONS = {"enable_checkboxes":True,"return_figure_images":False,"rotate_pages":True}

class ReductoProvider:
    name = "reducto"
    def __init__(self, api_key: str | None):
        api_key = api_key or os.getenv("REDUCTO_API_KEY")  # 👈 fallback
        if not api_key:
            raise ReductoError("REDUCTO_API_KEY missing")
        self.client = Reducto(api_key=api_key)

    def parse_document_for_page(self, file_path: Path, page_number: int) -> Dict[str, Any]:
        upload_url = self.client.upload(file=file_path)
        adv = copy.deepcopy(ADVANCED_OPTIONS)
        adv["page_range"] = {"start": page_number, "end": page_number}
        res = self.client.parse.run(
            document_url=upload_url,
            options=OPTIONS,
            advanced_options=adv,
            experimental_options=EXPERIMENTAL_OPTIONS,
        )
        return res.model_dump()
