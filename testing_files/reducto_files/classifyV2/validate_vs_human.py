#!/usr/bin/env python3
from __future__ import annotations
import csv, argparse, re
from pathlib import Path


def load_predictions(path: Path) -> dict[int, dict]:
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                p = int(row.get("page") or 0)
            except Exception:
                continue
            out[p] = row
    return out


def load_human(path: Path) -> dict[int, dict]:
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                p = int(row.get("page") or 0)
            except Exception:
                continue
            out[p] = row
    return out


def main():
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--pred", default=str(here / "predictions.csv"))
    ap.add_argument("--human", default=str(here.parent / "perdiction_human_test.csv"))
    ap.add_argument("--show-corrected", action="store_true", help="Also list human false positives that match the note")
    args = ap.parse_args()

    pred_path = Path(args.pred)
    human_path = Path(args.human)
    if not pred_path.exists():
        raise FileNotFoundError(f"Predictions file not found: {pred_path}. Pass --pred <path>.")
    if not human_path.exists():
        raise FileNotFoundError(f"Human review file not found: {human_path}. Pass --human <path>.")
    preds = load_predictions(pred_path)
    hum = load_human(human_path)

    def derive_expected(note: str) -> tuple[str | None, str | None]:
        """Infer expected label/family from human 'description'."""
        if not note:
            return None, None
        n = note.lower()
        # Neutral/other
        if re.search(r"filing\s+instruction", n):
            return "Filing Instructions", "Other"
        if re.search(r"letter|not a form|notice", n):
            return "Other", "Other"
        # 1040 schedules
        if re.search(r"schedule\s*1", n):
            return "1040_Schedule_1", "1040"
        if re.search(r"schedule\s*c", n):
            return "1040_Schedule_C", "1040"
        if re.search(r"schedule\s*e\s*pg\s*1|schedule\s*e[^\d]*page\s*1", n):
            return "1040_Schedule_E", "1040"
        if re.search(r"schedule\s*e\s*pg\s*2|schedule\s*e[^\d]*page\s*2", n):
            return "1040_Schedule_E_PG_2", "1040"
        if re.search(r"schedule\s*f", n):
            return "1040_Schedule_F", "1040"
        # Specific forms
        if re.search(r"8801", n):
            return "Form 8801", "Form 8801"
        if re.search(r"6251", n):
            return "Form 6251", "Form 6251"
        if ("8582" in n) and (re.search(r"pg\s*1", n) or re.search(r"page\s*1", n)):
            return "Form 8582 Pg 1", "Form 8582"
        if ("8582" in n) and (re.search(r"pg\s*2", n) or re.search(r"page\s*2", n)):
            return "Form 8582 Pg 2", "Form 8582"
        if ("5329" in n) and (re.search(r"pg\s*1", n) or re.search(r"page\s*1", n)):
            return "Form 5329 Pg 1", "Form 5329"
        if ("5329" in n) and (re.search(r"pg\s*2", n) or re.search(r"page\s*2", n)):
            return "Form 5329 Pg 2", "Form 5329"
        if ("5329" in n) and (re.search(r"pg\s*3", n) or re.search(r"page\s*3", n) or re.search(r"pg\s*unknown", n)):
            return "Form 5329 Pg 3", "Form 5329"
        if re.search(r"6252\s*line\s*29e|line\s*29e", n):
            return "Federal Statements - Form 6252 Line 29e", "Form 6252"
        # FinCEN/114a instruction mentions
        if re.search(r"fincen\s*114.*instruction", n):
            return "Filing Instructions", "Other"
        return None, None

    total = len(hum)
    mismatches = 0
    corrected = 0
    rows_to_print: list[tuple[int, str, str, str]] = []
    for p, h in sorted(hum.items()):
        pr = preds.get(p, {})
        fam = (pr.get("predicted_family") or "").strip() or "?"
        lab_raw = (pr.get("predicted_label") or "").strip() or "?"
        # Accept normalized labels with underscores or spaces
        lab = lab_raw.replace("_", " ")
        res = (h.get("Result") or "").strip().lower() in {"true", "1", "yes"}
        note = (h.get("description") or "").strip() or "(no note)"

        exp_label, exp_fam = derive_expected(note)
        match_expected = False
        if exp_label and lab.lower() == exp_label.lower():
            match_expected = True
        elif exp_fam and fam.lower() == exp_fam.lower():
            match_expected = True

        if res:
            # Human already marked as correct
            continue
        if match_expected:
            corrected += 1
            if args.show_corrected:
                rows_to_print.append((p, fam, lab, f"{note} This is correct (false positive in human file)"))
        else:
            mismatches += 1
            rows_to_print.append((p, fam, lab_raw, f"{note} Mismatch"))

    if rows_to_print:
        print("Page | Family | Label | Human note")
        print("-----+--------+-------+-----------")
        for p, fam, lab, line in rows_to_print:
            print(f"{p:>4} | {fam} | {lab} | {line}")
        print()
    print(f"Auto-corrected (human false but prediction matches note): {corrected}")
    print(f"Remaining mismatches: {mismatches}/{total}")


if __name__ == "__main__":
    main()
