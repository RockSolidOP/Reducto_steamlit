from __future__ import annotations

from app.services.pymupdf_kv import postprocess_pymupdf


def test_postprocess_pymupdf_simple_extraction() -> None:
    text = (
        "2023\n"
        "Type: Individual\n"
        "SSN/ITIN 123456789\n"
        "DOB 01/31/1990\n"
        "123 Main St\n"
        "Anytown\n"
        "CA 90210\n"
        "United States\n"
    )
    out = postprocess_pymupdf(text)

    # Deterministic subset assertions
    assert out.get("report_year") == "2023"
    assert out.get("type_of_filer") == "Individual"
    assert out.get("taxpayer_identification_number") == "123456789"
    assert out.get("tin_type") == "SSN/ITIN"
    assert out.get("dob") == "01/31/1990"
    assert out.get("mailing_address", "").startswith("123 Main St")
    assert out.get("city") == "Anytown"
    assert out.get("state") == "CA"
    assert out.get("zip") == "90210"
    assert out.get("country") == "US"

