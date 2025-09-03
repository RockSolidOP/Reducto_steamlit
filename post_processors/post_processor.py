import re
from typing import Any, Dict, List

EMPTY_TOKENS = {"", "<empty>", "\\u2014", "\u2014", "—", "-"}

def _clean(s: Any) -> str:
    if s is None: return ""
    s = str(s).strip()
    return "" if s in EMPTY_TOKENS else s

def _is_placeholder(s: Any) -> bool:
    if s is None: return True
    return str(s).strip() in EMPTY_TOKENS

def _after_colon(line: str) -> str:
    m = re.search(r":\s*(.*)$", line)
    return _clean(m.group(1)) if m else ""

def _calendar_ended(line: str) -> str:
    m = re.search(r"calendar year ended\s+(\d{1,2}/\d{1,2})/?\s*[:\-]?\s*(\d{4})", line, re.I)
    if m: return f"{m.group(1)}/{m.group(2)}"
    m1 = re.search(r"(\d{1,2}/\d{1,2})", line)
    m2 = re.search(r"(\d{4})(?!.*\d)", line)
    return f"{m1.group(1)}/{m2.group(1)}" if (m1 and m2) else ""

def _parse_table_blob(blob: str) -> Dict[str, Any]:
    out, foreign = {}, {}

    def grab(pat, g=1):
        m = re.search(pat, blob, re.I)
        return _clean(m.group(g)) if m else ""

    out["Type_of_filer"] = grab(r'"Type of filer"\s*,\s*"([^"]*)"')
    out["TIN"]            = grab(r'"U\.S\. Taxpayer Identification Number"\s*,\s*"([^"]*)"')
    out["TIN_TYPE"]       = grab(r'"TIN type"\s*,\s*"([^"]*)"')

    ftype = grab(r'"4a Type\s+([^"]+)"') or grab(r'"4a Type\s*([^"]+)"')
    if ftype: foreign["Type"] = ftype
    fnum  = grab(r'"4b Number"\s*,\s*"([^"]*)"')
    if fnum: foreign["Number"] = fnum
    fctry = grab(r'"4c Country of Issue"\s*,\s*"([^"]*)"')
    if fctry: foreign["Country_of_issue"] = fctry
    if foreign: out["Foreign_identification"] = foreign

    out["DOB"]                       = grab(r"\"Individual's date of birth\"\s*,\s*\"([^\".]*)\"")
    out["Last_name_or_organization"] = grab(r'"Last name or organization"\s*,\s*"([^"]*)"')

    m_fn = re.search(r'First name\s+([^\s\]",]+)', blob, re.I)
    out["First_Name"] = _clean(m_fn.group(1)) if m_fn else ""

    out["Middle_Initial"]  = _clean(grab(r'"Middle initial"\s*,\s*"([^"]*)"'))
    out["Suffix"]          = _clean(grab(r'"Suffix"\s*,\s*"([^"]*)"'))
    out["Mailing_address"] = grab(r'"Mailing address"\s*,\s*"([^"]*)"')
    out["City"]            = grab(r'"City"\s*,\s*"([^"]*)"')
    out["State"]           = grab(r'"State"\s*,\s*"([^"]*)"')
    out["zip_postal_code"] = grab(r'"Zip/postal code"\s*,\s*"([^"]*)"')

    mc = re.search(r'"Country\s+([A-Za-z]{2,})"', blob, re.I)
    out["Country"] = _clean(mc.group(1)) if mc else ""

    # 14a between 14a..14b
    seg_14a = ""
    m14a = re.search(r'(14a[^]]*\][^\[]*)(14b[^]]*)', blob, re.I)
    if m14a: seg_14a = m14a.group(1)
    else:
        m14a_only = re.search(r'(14a[^]]*\][\s\S]*)$', blob, re.I)
        seg_14a = m14a_only.group(1) if m14a_only else ""
    if seg_14a:
        yes_checked = bool(re.search(r'\["Yes",\s*"\s*\[(?:x|X)\]\s*"', seg_14a))
        no_checked  = bool(re.search(r'\["No",\s*"\s*\[(?:x|X)\]\s*"', seg_14a))
        out["FINANCIAL_Interest_accounts"] = True if yes_checked else False
        if no_checked: out["FINANCIAL_Interest_accounts"] = False
    else:
        out["FINANCIAL_Interest_accounts"] = False

    # 14b after 14b
    seg_14b_m = re.search(r'(14b[\s\S]*)$', blob, re.I)
    seg_14b = seg_14b_m.group(1) if seg_14b_m else ""
    if seg_14b:
        yes_b = bool(re.search(r'Yes[^"\]]*["\]]\s*[\[☑✓xX]', seg_14b))
        no_b  = bool(re.search(r'No[^"\]]*[\u2611☑✓xX]', seg_14b))
        out["Sign_Auth_no_interest"] = False if no_b else True if yes_b else False
    else:
        out["Sign_Auth_no_interest"] = False

    # normalize placeholders to empty
    for k in ("Middle_Initial", "Suffix"):
        if _is_placeholder(out.get(k)): out[k] = ""
    return out

def parse_page3_blocks_resilient(page3_blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    expect_name = False
    accumulating = False
    table_buf: List[str] = []

    for blk in page3_blocks:
        content = blk.get("content")
        if not isinstance(content, str):
            # if you sometimes get an actual list-of-rows instead of a string blob, handle here if needed
            continue

        raw = content
        line = raw.strip()
        if not line: continue

        # already accumulating table?
        if accumulating:
            table_buf.append(raw)
            joined = "\n".join(table_buf)
            if joined.count("[") - joined.count("]") <= 0:
                result["Filer_Information"] = _parse_table_blob(joined)
                accumulating = False
            continue

        # split if header is glued
        if "Part I - Filer Information" in line:
            before, after = line.split("Part I - Filer Information", 1)
            before = before.strip()
            if before:
                if expect_name:
                    result["Name"] = _clean(before); expect_name = False
                elif before.lower().startswith("taxpayer identification number"):
                    result["Taxpayer Identification Number"] = _after_colon(before)
                elif "calendar year ended" in before.lower():
                    result["calendar_year_ended"] = _calendar_ended(before)
                elif before.lower().startswith("amended"):
                    result["Amended"] = bool(re.search(r"\[(x|X|☑|✓)\]", before))
                elif before.lower().startswith("prior report bsa identifier"):
                    v = _after_colon(before); result["BSA_identifier"] = "" if _is_placeholder(v) else v
                elif before.lower().startswith("reason if filing late"):
                    v = _after_colon(before); result["Reason_if_filing_late"] = "" if _is_placeholder(v) else v
                elif re.match(r"^Form\s+(\d+)\b", before, re.I):
                    result["Form"] = re.match(r"^Form\s+(\d+)\b", before, re.I).group(1)
                elif re.fullmatch(r"\d{4}", before):
                    result["year"] = before
                elif before.lower() == "name":
                    expect_name = True

            trailing = after.strip()
            if trailing.startswith("[["):
                accumulating = True
                table_buf = [trailing]
                if trailing.count("[") - trailing.count("]") <= 0:
                    result["Filer_Information"] = _parse_table_blob(trailing)
                    accumulating = False
            else:
                accumulating = True
                table_buf = []
            continue

        # normal lines
        if expect_name:
            result["Name"] = _clean(line); expect_name = False; continue
        m = re.match(r"^Form\s+(\d+)\b", line, re.I)
        if m: result["Form"] = m.group(1); continue
        if re.fullmatch(r"\d{4}", line): result["year"] = line; continue
        if line.lower() == "name": expect_name = True; continue
        if line.lower().startswith("taxpayer identification number"):
            result["Taxpayer Identification Number"] = _after_colon(line); continue
        if "calendar year ended" in line.lower():
            result["calendar_year_ended"] = _calendar_ended(line); continue
        if line.lower().startswith("amended"):
            result["Amended"] = bool(re.search(r"\[(x|X|☑|✓)\]", line)); continue
        if line.lower().startswith("prior report bsa identifier"):
            v = _after_colon(line); result["BSA_identifier"] = "" if _is_placeholder(v) else v; continue
        if line.lower().startswith("reason if filing late"):
            v = _after_colon(line); result["Reason_if_filing_late"] = "" if _is_placeholder(v) else v; continue
        if line.startswith("[["):
            accumulating = True; table_buf = [raw]
            if raw.count("[") - raw.count("]") <= 0:
                result["Filer_Information"] = _parse_table_blob(raw); accumulating = False
            continue

    # defaults if truly missing
    result.setdefault("Amended", False)
    result.setdefault("BSA_identifier", "")
    result.setdefault("Reason_if_filing_late", "")

    # normalize em-dash placeholders in nested fields
    fi = result.get("Filer_Information", {})
    for k in ("Middle_Initial", "Suffix"):
        if _is_placeholder(fi.get(k)): fi[k] = ""

    return result
