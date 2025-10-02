from __future__ import annotations

# ---------------------------
# Reducto Configuration
# ---------------------------
# These options are used by the Reducto service only.

OPTIONS = {
    "ocr_mode": "agentic",
    "extraction_mode": "ocr",
    "chunking": {"chunk_mode": "variable"},
}

ADVANCED_OPTIONS = {
    "ocr_system": "multilingual",
    # page_range is set dynamically to the selected page
    "page_range": {"start": 1, "end": 10},
    "table_output_format": "ai_json",
    "merge_tables": True,
}

EXPERIMENTAL_OPTIONS = {
    "enable_checkboxes": True,
    "return_figure_images": False,
    "rotate_pages": True,
}

# Namespaced view for Reducto consumers (optional, for clarity)
REDUCTO_CONFIG = {
    "OPTIONS": OPTIONS,
    "ADVANCED_OPTIONS": ADVANCED_OPTIONS,
    "EXPERIMENTAL_OPTIONS": EXPERIMENTAL_OPTIONS,
}


# ---------------------------
# Azure Document Intelligence
# ---------------------------
# Settings specific to Azure service usage.
# You may change model IDs or add feature flags here.
AZURE_CONFIG = {
    "model_id": "prebuilt-document",
    # Prebuilt model for US IRS Form 1040 (if available in your region)
    "model_id_1040": "prebuilt-tax.us.1040",
    # Prebuilt model for US IRS Form 1040 Schedule 1
    "model_id_1040_schedule1": "prebuilt-tax.us.1040Schedule1",
    # Prebuilt model for US IRS Form 1040 Schedule A (Itemized Deductions)
    "model_id_1040_schedule_a": "prebuilt-tax.us.1040ScheduleA",
    # Prebuilt model for US IRS Form 1040 Schedule C (Profit or Loss From Business)
    "model_id_1040_schedule_c": "prebuilt-tax.us.1040ScheduleC",
    # Prebuilt model for US IRS Form 1040 Schedule E (Starter placeholder)
    # Note: This model id may not exist in all regions/tenants.
    "model_id_1040_schedule_e": "prebuilt-tax.us.1040ScheduleE",
}


# ---------------------------
# General App Policies
# ---------------------------
# Uploads cleanup policy (used by storage.cleanup_uploads)
UPLOADS_CLEANUP = {
    "enabled": True,
    "max_age_days": 30,        # delete files older than 30 days
    "max_total_size_mb": 512,  # keep uploads dir under 512 MB
    "max_files": 1000,         # and at most 1000 files
}


# ---------------------------
# PyMuPDF (local extraction)
# ---------------------------
PYMUPDF_CONFIG = {
    # Text extraction strategy: "blocks" or "dict"
    "text_mode": "blocks",
    # Heuristic KV extraction toggles
    "kv_enable_colon": True,
    "kv_enable_dot_leader": True,
    # Treat multiple values: "first", "last", or "list"
    "kv_merge_strategy": "first",
}
