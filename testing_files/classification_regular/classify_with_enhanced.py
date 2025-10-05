"""Classify a PDF using the enhanced regex config.

Usage:
    python testing_files/reducto_classifier/classify_with_enhanced.py \
        --pdf testing_files/sample_multiple.pdf \
        --output testing_files/reducto_classifier/sample_multiple_enhanced.json

If the enhanced config is missing, run build_enhanced_config.py first.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.classify_service import load_config, classify_to_result  # noqa: E402

DEFAULT_CONFIG = Path("testing_files/reducto_classifier/forms_config_enhanced.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify PDF pages with enhanced regex config")
    parser.add_argument(
        "--pdf",
        default=str(Path("testing_files/sample_multiple.pdf")),
        help="Path to the PDF to classify (default: testing_files/sample_multiple.pdf)",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to enhanced classifier config (JSON)",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write JSON result. If omitted, prints to stdout.",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Enhanced config not found: {config_path}. Run build_enhanced_config.py first."
        )

    cfg = load_config(config_path)
    result = classify_to_result(pdf_path, cfg)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Classification written to {out_path}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
