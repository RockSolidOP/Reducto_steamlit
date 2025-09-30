from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from dotenv import load_dotenv
import os
import argparse
import json
from pathlib import Path

# Ensure repo root is importable when running directly
try:
    # Reuse the app's JSON formatting to mirror UI output
    from app.services.azure_service import azure_to_dict, azure_fields_to_dict
except ModuleNotFoundError:
    import sys
    ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(ROOT))
    from app.services.azure_service import azure_to_dict, azure_fields_to_dict


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Test Azure DI prebuilt Schedule C model")
    parser.add_argument(
        "--file",
        default=str(Path(__file__).resolve().parents[2] / "testing_files" / "sample_multiple.pdf"),
        help="Path to PDF/image containing Schedule C",
    )
    parser.add_argument(
        "--pages",
        default="22",  # Schedule C is page 22 in the sample file
        help="Optional comma-separated pages to analyze (e.g., '1' or '2,3'). Default: 22",
    )
    parser.add_argument(
        "--model-id",
        default="prebuilt-tax.us.1040ScheduleC",
        help="Azure model ID to use (default: prebuilt-tax.us.1040ScheduleC)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional path to write extracted fields JSON",
    )
    args = parser.parse_args()

    endpoint = os.getenv("AZURE_DOC_AI_ENDPOINT")
    key = os.getenv("AZURE_DOC_AI_KEY")
    if not endpoint or not key:
        raise RuntimeError("Missing AZURE_DOC_AI_ENDPOINT or AZURE_DOC_AI_KEY in environment/.env")

    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))

    with open(args.file, "rb") as f:
        poller = client.begin_analyze_document(
            args.model_id,
            body=f,
            pages=args.pages if args.pages else None,
        )

    result = poller.result()

    # Mirror the app panels: Raw Azure Output + Document Fields
    raw = azure_to_dict(result)
    fields = azure_fields_to_dict(result)

    header = {
        "modelId": args.model_id,
        "file": str(args.file),
        "pages": args.pages or "",
    }
    print("=== Azure Raw Output ===")
    print(json.dumps({"meta": header, "raw": raw}, indent=2, ensure_ascii=False))
    print("\n=== Azure Document Fields ===")
    print(json.dumps({"meta": header, "fields": fields}, indent=2, ensure_ascii=False))

    # Optionally save fields JSON to file
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({"meta": header, "fields": fields}, indent=2, ensure_ascii=False))
        print(f"\nSaved extracted fields to: {out_path}")


if __name__ == "__main__":
    main()

