from pathlib import Path
import os

from dotenv import load_dotenv
import httpx
from reducto import Reducto, ReductoError

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

http_client = None
if USE_PROXY and PROXY_HOST:
    proxy_url = f"http://{PROXY_HOST}"
    # Ensure both http and https requests use the proxy
    proxies = {
        "http://": proxy_url,
        "https://": proxy_url,
    }
    http_client = httpx.Client(proxies=proxies, follow_redirects=True)

client = Reducto(api_key=api_key, http_client=http_client)

schema = {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "US Form 1040 – Taxpayer Header",
  "type": "object",
  "additionalProperties": False,
  "properties": {
    "tax_year": {
      "type": "string",
      "pattern": "^(20\\d\\d)$",
      "description": "4-digit year printed at top of Form 1040 (e.g., 2024). Look near the form title or 'This report is for calendar year ended ...'.",
      "examples": ["2024"]
    },
    "taxpayer_full_name": {
      "type": "string",
      "description": "Primary taxpayer full name as printed on the 1040 name line (Last, First MI on the label). Prefer the exact text next to 'Name' or 'Your first name and initial / Last name'."
    },
    "taxpayer_first_name": {
      "type": "string",
      "description": "First name from the 1040 name block ('Your first name and middle initial')."
    },
    "taxpayer_middle_initial": {
      "type": "string",
      "maxLength": 2,
      "description": "Middle initial if present in the 1040 name block."
    },
    "taxpayer_last_name": {
      "type": "string",
      "description": "Last name from the 1040 name block."
    },
    "ssn_itin": {
      "type": "string",
      "pattern": "^(\\d{3}-?\\d{2}-?\\d{4})$",
      "description": "U.S. Taxpayer Identification Number near 'Your social security number' or 'U.S. Taxpayer Identification Number'. Accepts digits with/without hyphens (e.g., 123-45-6789)."
    },
    "tin_type": {
      "type": "string",
      "enum": ["SSN", "ITIN", "SSN/ITIN", "EIN", "Unknown"],
      "description": "TIN type label near 'TIN type' or similar."
    },
    "date_of_birth": {
      "type": "string",
      "format": "date",
      "description": "Date of birth next to 'Date of birth' for the filer (YYYY-MM-DD preferred; if printed as MM/DD/YYYY, transcribe as such).",
      "examples": ["1979-06-06"]
    },
    "foreign_id": {
      "type": "object",
      "description": "If foreign identification is present on the page.",
      "properties": {
        "id_type": {
          "type": "string",
          "enum": ["Passport", "National ID", "Driver License", "Other"],
          "description": "Label near '4a Type'."
        },
        "id_number": {
          "type": "string",
          "description": "Value near '4b Number' as printed (no normalization)."
        },
        "country_of_issue": {
          "type": "string",
          "description": "Text near '4c Country of Issue' (use country name as printed)."
        }
      },
      "required": []
    },
    "mailing_address": {
      "type": "object",
      "description": "Mailing address block next to 'Home address (number and street)' / 'Mailing address'.",
      "properties": {
        "street_address": { "type": "string", "description": "Street line(s) exactly as printed; include apt/suite." },
        "city": { "type": "string" },
        "state": {
          "type": "string",
          "description": "Two-letter state if present; otherwise full state/province name.",
          "examples": ["MI", "Michigan"]
        },
        "postal_code": {
          "type": "string",
          "pattern": "^[A-Za-z0-9\\-\\s]{3,10}$",
          "description": "ZIP or postal code as printed."
        },
        "country": {
          "type": "string",
          "description": "Country if printed; for domestic 1040 often blank."
        }
      },
      "required": ["street_address", "city"]
    },
    "filing_status": {
      "type": "string",
      "enum": [
        "Single",
        "Married filing jointly",
        "Married filing separately",
        "Head of household",
        "Qualifying surviving spouse"
      ],
      "description": "Checkmark near the 'Filing Status' section (line 1) on Form 1040."
    },
    "has_25_plus_accounts_interest": {
      "type": "boolean",
      "description": "If the page shows the checkbox about having a financial interest in 25+ financial accounts, return true/false (copy the selected state)."
    },
    "has_25_plus_accounts_signature_auth": {
      "type": "boolean",
      "description": "If the page shows the checkbox about signature authority in 25+ accounts, return true/false."
    }
  },
  "required": [
    "tax_year",
    "taxpayer_first_name",
    "taxpayer_last_name",
    "ssn_itin",
    "mailing_address"
  ]
}


def _to_printable(obj):
    for m in ("model_dump", "dict"):
        if hasattr(obj, m) and callable(getattr(obj, m)):
            try:
                return getattr(obj, m)()
            except Exception:
                pass
    return obj

# Upload the multi-page sample PDF and use its reducto:// URL
pdf_path = Path("testing_files/sample_multiple.pdf")
if not pdf_path.exists():
    raise FileNotFoundError(f"Sample PDF not found at {pdf_path}")

upload_url = client.upload(file=pdf_path)

# Page range: 18-19 (inclusive)
advanced_options = {
    "page_range": {"start": 18, "end": 19}
}

# Prefer schema-based extract if available; otherwise fall back to parse
if hasattr(client, "extract") and hasattr(client.extract, "run"):
    resp = client.extract.run(
        document_url=upload_url,
        schema=schema,
        system_prompt=(
            "Extract exactly as printed. "
            "For SSN/ITIN keep digits and hyphens if present. "
            "Use the Filing Status text that is selected/checked on the form."
        ),
        options={"extraction_mode": "ocr"},  # or "hybrid" if your plan allows
        advanced_options=advanced_options,
    )
else:
    # Fallback: run parse without schema (schema is not supported here)
    resp = client.parse.run(
        document_url=upload_url,
        options={
            "ocr_mode": "agentic",
            "extraction_mode": "ocr",
            "chunking": {"chunk_mode": "variable"},
        },
        advanced_options=advanced_options,
    )

print(_to_printable(resp))
