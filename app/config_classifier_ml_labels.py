from __future__ import annotations

"""Mapping from ML classifier output labels (exact strings) to Azure model IDs.

Edit this file to control Azure routing when the Extraction Pipeline uses the
ML (FAISS/CLIP) classifier. Keys are exact labels as produced by the ML
classifier (no normalization). Values are Azure model IDs.

Examples are seeded from `id_map_v1.3.jsonl` under the classifier module.
Extend this list with labels you encounter in your data.
"""

from typing import Dict, Set
from app.config import AZURE_CONFIG


ML_LABEL_MODEL_MAP: Dict[str, str] = {
    # 1040 main
    "Form_1040_P1": AZURE_CONFIG.get("model_id_1040", "prebuilt-tax.us.1040"),
    "Form_1040_P2": AZURE_CONFIG.get("model_id_1040", "prebuilt-tax.us.1040"),

    # 1040 Schedule E
    "Schedule_E_P1": AZURE_CONFIG.get("model_id_1040_schedule_e", "prebuilt-tax.us.1040ScheduleE"),
    "Schedule_E_P2": AZURE_CONFIG.get("model_id_1040_schedule_e", "prebuilt-tax.us.1040ScheduleE"),

    # 1040 Schedule C (single-page mapping; pairs will reuse this)
    "Schedule_C_P1": AZURE_CONFIG.get("model_id_1040_schedule_c", "prebuilt-tax.us.1040ScheduleC"),
    "Schedule_C_P2": AZURE_CONFIG.get("model_id_1040_schedule_c", "prebuilt-tax.us.1040ScheduleC"),

    # 1040 Schedule A / Schedule 1
    "Schedule_A": AZURE_CONFIG.get("model_id_1040_schedule_a", "prebuilt-tax.us.1040ScheduleA"),
    "Schedule_1": AZURE_CONFIG.get("model_id_1040_schedule1", "prebuilt-tax.us.1040Schedule1"),

    # Note: asset/adjustment reports are intentionally not routed here; see SKIP_LABELS below.
}

# Labels that should be skipped (no Azure model selection/job creation)
SKIP_LABELS: Set[str] = {
    "Federal_Asset_Report_Schedule_C_P1",
    "Federal_Asset_Report_Schedule_F_P1",
    "AMT_Asset_Report_Schedule_C_P1",
    "AMT_Asset_Report_Schedule_F_P1",
    "Bonus_Depreciation_Report_Schedule_C_P1",
    "Depreciation_Adjustment_Report_P1",
}
