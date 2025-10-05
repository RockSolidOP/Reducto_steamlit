"""Build an enhanced regex classifier config based on the app's default config.

This keeps all existing labels/families but adds broader patterns and tweaked
weights for better robustness against layout variations.

Run:
    python testing_files/reducto_classifier/build_enhanced_config.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

BASE_CONFIG_PATH = Path("app/resources/classifyier_regex/forms_config.json")
OUTPUT_CONFIG_PATH = Path("testing_files/reducto_classifier/forms_config_enhanced.json")


def _ensure_lists(obj: Dict, key: str) -> List:
    value = obj.setdefault(key, [])
    if not isinstance(value, list):
        raise TypeError(f"Expected list at key '{key}', found {type(value)}")
    return value


def enhance_config(config: Dict) -> Dict:
    config = dict(config)  # shallow copy
    config["name"] = "forms_config_enhanced"
    config["description"] = (
        "Expanded regex coverage for IRS 1040 forms and FinCEN packets."
    )

    # Slightly boost header weight; headers carry strongest signals.
    weights = config.get("weights") or {}
    weights.update({
        "header": 1.6,
        "body": weights.get("body", 1.0),
        "footer": weights.get("footer", 1.1),
    })
    config["weights"] = weights

    # Helper to add extra regex patterns.
    additions: Dict[str, Dict[str, List[str]]] = {
        "1040_Main_Pg1": {
            "patterns": [
                r"(?is)form\s*1040(?:-sr)?\b.*?page\s*1",
                r"(?is)u\.?s\.?\s+individual\s+income\s+tax\s+return.*?filing\s+status",
                r"(?is)standard\s+deduction|dependents\s+if\s+checked",
            ],
        },
        "1040_Main_Pg2": {
            "patterns": [
                r"(?is)form\s*1040(?:-sr)?\b.*?page\s*2",
                r"(?is)(amount\s+you\s+owe|direct\s+deposit|sign\s+here|paid\s+preparer)",
            ],
        },
        "1040_Schedule_E": {
            "patterns": [
                r"(?is)schedule\s*e\s*\(\s*form\s*1040\s*\).*?supplemental\s+income\s+and\s+loss",
                r"(?is)part\s*i\s*income\s+or\s+loss\s+from\s+rental",
            ],
            "avoid": [
                r"(?is)page\s*2",
                r"(?is)part\s*iv|part\s*v",
            ],
        },
        "1040_Schedule_E_PG_2": {
            "patterns": [
                r"(?is)schedule\s*e\s*\(\s*form\s*1040\s*\).*?(page\s*2|part\s*iv|part\s*v)",
                r"(?is)summary\s*of\s*income\s+or\s+loss",
            ],
        },
        "FinCEN 114 - Report of Foreign Bank and Financial Accounts, Pg 1": {
            "patterns": [
                r"(?is)u\.?s\.\s+department\s+of\s+the\s+treasury.*?finCEN\s*114",
                r"(?is)report\s+of\s+foreign\s+bank\s+and\s+financial\s+accounts",
            ],
        },
        "FinCEN 114 - Report of Foreign Bank and Financial Accounts, Pg 2": {
            "patterns": [
                r"(?is)finCEN\s*114\b.*?(page\s*2|part\s*ii)",
            ],
        },
        "FinCEN 114 - Report of Foreign Bank and Financial Accounts, Pg 3": {
            "patterns": [
                r"(?is)finCEN\s*114\b.*?(page\s*3|part\s*iii)",
            ],
        },
        "FinCEN 114 - Report of Foreign Bank and Financial Accounts, Pg 5": {
            "patterns": [
                r"(?is)finCEN\s*114\b.*?(page\s*5|third\s+party\s+preparer)",
            ],
        },
        "Filing Instructions": {
            "patterns": [
                r"(?is)filing\s+instructions",
                r"(?is)must\s+be\s+electronically\s+filed",
                r"(?is)do\s+not\s+mail\s+the\s+attached",
            ],
        },
    }

    labels: List[Dict] = config.get("labels", [])
    label_index = {entry.get("label"): entry for entry in labels}

    for label, tweaks in additions.items():
        entry = label_index.get(label)
        if not entry:
            continue
        for key, values in tweaks.items():
            if not isinstance(values, list):
                continue
            lst = _ensure_lists(entry, key)
            for value in values:
                if value not in lst:
                    lst.append(value)

    return config


def main() -> None:
    base = json.loads(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
    enhanced = enhance_config(base)
    OUTPUT_CONFIG_PATH.write_text(
        json.dumps(enhanced, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Enhanced config written to {OUTPUT_CONFIG_PATH}")


if __name__ == "__main__":
    main()
