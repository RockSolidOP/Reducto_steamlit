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
