from __future__ import annotations

"""Extraction Pipeline page

What this page does
- Upload a PDF
- Paste/upload page classifications (from your LLM using the prompts in the repo)
- Plan 1040 routing: pairs Main Pg1/Pg2, runs Schedule 1 individually
- Run Azure 1040 models only on the planned pages
- Produce one JSON output with metadata and Azure document.fields

Notes
- Uses app.services.azure_service helpers and shared UI utilities.
- Does NOT call LLMs for Classification; it expects you to provide classifications.
- Supported labels: "1040_Main_Pg1", "1040_Main_Pg2", and "1040_Schedule_1" (others are skipped).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from time import perf_counter

import fitz  # PyMuPDF
import streamlit as st

# Ensure project root is on sys.path when running this page directly
try:  # noqa: SIM105 - deliberate try/except import shim
    from app.config import AZURE_CONFIG
    from app.services.azure_service import (
        parse_with_azure,
        parse_with_azure_docint,
        azure_fields_to_dict,
    )
    from app.services.classify_service import (
        get_default_config_path,
        load_config,
        classify_document,
    )
except ModuleNotFoundError:  # Running via `streamlit run pages/Extraction_Pipeline.py`
    import sys as _sys
    from pathlib import Path as _Path

    _ROOT = _Path(__file__).resolve().parents[1]
    if str(_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_ROOT))
    from app.config import AZURE_CONFIG
    from app.services.azure_service import (
        parse_with_azure,
        parse_with_azure_docint,
        azure_fields_to_dict,
    )
    from app.services.classify_service import (
        get_default_config_path,
        load_config,
        classify_document,
    )
from app.ui.components import (
    download_json_button,
    file_uploader,
    count_pages_from_spec,
)
from app.utils.storage import save_uploaded_file


# -----------------------------
# Models
# -----------------------------


@dataclass
class ClassifiedPage:
    file_name: str
    page: int
    label: str
    family: str | None = None
    confidence: float | None = None
    reasons: List[str] | None = None


@dataclass
class AzureJob:
    job_id: str
    file_name: str
    model_id: str
    pages: str
    reason: str


@dataclass
class PagePlan:
    page: int
    label: str
    status: str  # "paired" | "single_page_only" | "non_1040"
    pair_number: Optional[int]
    group_id: Optional[str]
    job_id: Optional[str]
    action: str  # "analyze" | "skip"
    model_id: Optional[str] = None  # model selected for this page (if any)




# -----------------------------
# Helpers
# -----------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _infer_family(label: str) -> str:
    if label.startswith("1040"):
        return "1040"
    if label.startswith("Form 5329"):
        return "Form 5329"
    if label.startswith("Form 6251"):
        return "Form 6251"
    if label.startswith("Form 6252"):
        return "Form 6252"
    if label.startswith("Form 8582"):
        return "Form 8582"
    if label.startswith("Form 8801"):
        return "Form 8801"
    if label.startswith("FinCEN 114"):
        return "FinCEN 114"
    if label.startswith("114a"):
        return "114a"
    return "Other"


def _select_model_for_page(label: str, family: str | None) -> Optional[str]:
    """Return the model id to use for a page based on its label/family.

    Extend this mapping as new form types are supported.
    """
    fam = (family or _infer_family(label)).strip()
    if fam == "1040":
        if label in ("1040_Main_Pg1", "1040_Main_Pg2"):
            return AZURE_CONFIG.get("model_id_1040", "prebuilt-tax.us.1040")
        if label == "1040_Schedule_1":
            return AZURE_CONFIG.get("model_id_1040_schedule1", "prebuilt-tax.us.1040Schedule1")
    return None


def _build_plan_1040(classified: List[ClassifiedPage]) -> Tuple[List[AzureJob], List[PagePlan]]:
    file_name = classified[0].file_name if classified else "file.pdf"
    model_id = AZURE_CONFIG.get("model_id_1040", "prebuilt-tax.us.1040")

    # Separate 1040 main pages and Schedule 1 from others
    p1_pages: List[int] = [c.page for c in classified if c.label == "1040_Main_Pg1"]
    p2_pages: List[int] = [c.page for c in classified if c.label == "1040_Main_Pg2"]
    sch1_pages: List[int] = [c.page for c in classified if c.label == "1040_Schedule_1"]
    other_pages: List[ClassifiedPage] = [
        c for c in classified if c.label not in ("1040_Main_Pg1", "1040_Main_Pg2", "1040_Schedule_1")
    ]

    p1_pages_sorted = sorted(p1_pages)
    p2_pages_sorted = sorted(p2_pages)

    azure_jobs: List[AzureJob] = []
    page_plan: List[PagePlan] = []

    # Track unmatched pages
    unmatched_p1: List[int] = []
    unmatched_p2: List[int] = []
    pair_number = 0

    # Walk through all pages in ascending order and greedily pair
    all_main = sorted([(p, 1) for p in p1_pages_sorted] + [(p, 2) for p in p2_pages_sorted])
    for p, kind in all_main:
        if kind == 2:  # Pg2
            if unmatched_p1:
                # Pair with earliest unmatched Pg1
                pg1 = unmatched_p1.pop(0)
                pair_number += 1
                group_id = f"{file_name}#1040#{pair_number}({pg1},{p})"
                job_id = group_id
                # Azure uses ascending page order in pages string
                a, b = (pg1, p) if pg1 <= p else (p, pg1)
                azure_jobs.append(
                    AzureJob(job_id=job_id, file_name=file_name, model_id=model_id, pages=f"{a}-{b}", reason="Paired 1040 main pages"),
                )
                page_plan.append(PagePlan(page=pg1, label="1040_Main_Pg1", status="paired", pair_number=pair_number, group_id=group_id, job_id=job_id, action="analyze", model_id=model_id))
                page_plan.append(PagePlan(page=p, label="1040_Main_Pg2", status="paired", pair_number=pair_number, group_id=group_id, job_id=job_id, action="analyze", model_id=model_id))
            else:
                unmatched_p2.append(p)
        else:  # Pg1
            if unmatched_p2:
                # Pair with earliest unmatched Pg2 (appeared earlier)
                pg2 = unmatched_p2.pop(0)
                pair_number += 1
                group_id = f"{file_name}#1040#{pair_number}({p},{pg2})"
                job_id = group_id
                a, b = (p, pg2) if p <= pg2 else (pg2, p)
                azure_jobs.append(
                    AzureJob(job_id=job_id, file_name=file_name, model_id=model_id, pages=f"{a}-{b}", reason="Matched Pg1 with earlier Pg2"),
                )
                page_plan.append(PagePlan(page=p, label="1040_Main_Pg1", status="paired", pair_number=pair_number, group_id=group_id, job_id=job_id, action="analyze", model_id=model_id))
                page_plan.append(PagePlan(page=pg2, label="1040_Main_Pg2", status="paired", pair_number=pair_number, group_id=group_id, job_id=job_id, action="analyze", model_id=model_id))
            else:
                unmatched_p1.append(p)

    # Single-page jobs for any remaining unmatched (call Azure but mark as single_page_only)
    for p in unmatched_p1:
        pair_number += 1
        group_id = f"{file_name}#1040#{pair_number}({p})"
        job_id = group_id
        azure_jobs.append(
            AzureJob(job_id=job_id, file_name=file_name, model_id=model_id, pages=str(p), reason="Only one 1040 main page present (single_page_only)"),
        )
        page_plan.append(PagePlan(page=p, label="1040_Main_Pg1", status="single_page_only", pair_number=pair_number, group_id=group_id, job_id=job_id, action="analyze", model_id=model_id))

    for p in unmatched_p2:
        pair_number += 1
        group_id = f"{file_name}#1040#{pair_number}({p})"
        job_id = group_id
        azure_jobs.append(
            AzureJob(job_id=job_id, file_name=file_name, model_id=model_id, pages=str(p), reason="Only one 1040 main page present (single_page_only)"),
        )
        page_plan.append(PagePlan(page=p, label="1040_Main_Pg2", status="single_page_only", pair_number=pair_number, group_id=group_id, job_id=job_id, action="analyze", model_id=model_id))

    # 1040 Schedule 1: create single-page jobs using schedule 1 model
    if sch1_pages:
        model_id_sch1 = AZURE_CONFIG.get("model_id_1040_schedule1", "prebuilt-tax.us.1040Schedule1")
        for p in sorted(sch1_pages):
            pair_number += 1
            group_id = f"{file_name}#1040_SCH1#{pair_number}({p})"
            job_id = group_id
            azure_jobs.append(
                AzureJob(job_id=job_id, file_name=file_name, model_id=model_id_sch1, pages=str(p), reason="1040 Schedule 1"),
            )
            page_plan.append(
                PagePlan(page=p, label="1040_Schedule_1", status="schedule1", pair_number=pair_number, group_id=group_id, job_id=job_id, action="analyze", model_id=model_id_sch1)
            )

    # Non‑1040 pages: skip
    for c in other_pages:
        page_plan.append(PagePlan(page=c.page, label=c.label, status="non_1040", pair_number=None, group_id=None, job_id=None, action="skip", model_id=_select_model_for_page(c.label, c.family)))

    # Ensure plan sorted by page
    page_plan.sort(key=lambda x: x.page)
    return azure_jobs, page_plan


 


def _compose_output(
    *,
    file_name: str,
    file_sha256: Optional[str],
    page_count: int,
    classified: List[ClassifiedPage],
    azure_jobs: List[AzureJob],
    page_plan: List[PagePlan],
    azure_results: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    # Index classification per page
    by_page: Dict[int, ClassifiedPage] = {c.page: c for c in classified}

    # Map job_id -> fields (document.fields dict) or error
    job_fields: Dict[str, Dict[str, Any]] = {}
    job_errors: Dict[str, str] = {}
    for jid, payload in azure_results.items():
        ok = bool(payload.get("ok"))
        if ok:
            res = payload.get("result")
            fields_obj = azure_fields_to_dict(res)
            # Ensure a dict for consistency; if list returned (multi-doc), wrap
            if isinstance(fields_obj, list):
                job_fields[jid] = {"documents": fields_obj}
            elif isinstance(fields_obj, dict):
                job_fields[jid] = fields_obj
            else:
                job_fields[jid] = {}
        else:
            job_errors[jid] = str(payload.get("error") or "")

    pages_out: List[Dict[str, Any]] = []
    for pp in page_plan:
        cls = by_page.get(pp.page)
        label = pp.label
        family = (cls.family if cls and cls.family else _infer_family(label))
        paired_with: Optional[int] = None
        if pp.status == "paired":
            # Find sibling page number from the group_id
            # group_id format: <file>#1040#N(p1,p2)
            try:
                inside = pp.group_id.split("(", 1)[1].rstrip(")")
                a = [int(x.strip()) for x in inside.split(",") if x.strip()]
                if len(a) == 2:
                    paired_with = a[1] if pp.page == a[0] else a[0]
            except Exception:
                paired_with = None

        azure_model: Optional[str] = pp.model_id
        azure_fields: Dict[str, Any] = {}
        error: Optional[str] = None
        if pp.action == "analyze" and azure_model:
            if pp.status in ("paired", "schedule1"):
                if pp.job_id in job_fields:
                    azure_fields = job_fields.get(pp.job_id, {})
            if pp.job_id in job_errors:
                error = job_errors.get(pp.job_id)

        pages_out.append(
            {
                "page": pp.page,
                "label": label,
                "family": family,
                "pair_number": pp.pair_number,
                "group_id": pp.group_id,
                "paired_with": paired_with,
                "processed": bool(pp.action == "analyze" and azure_model is not None),
                "status": pp.status,
                "azure_model": azure_model,
                "azure_fields": azure_fields,
                "error": error,
            }
        )

    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out = {
        "run_id": run_id,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "files": [
            {
                "file_name": file_name,
                "file_sha256": file_sha256,
                "page_count": int(page_count),
                "pages": pages_out,
            }
        ],
    }
    return out


def _analyze_with_azure(pdf_path: Path, *, model_id: str, pages: str) -> Any:
    """Try Document Intelligence first; fall back to Form Recognizer client."""
    try:
        return parse_with_azure_docint(pdf_path, page_number=1, model_id=model_id, pages=pages)
    except Exception as e_docint:
        try:
            return parse_with_azure(pdf_path, page_number=1, model_id=model_id, pages=pages)
        except Exception as e_fr:
            raise RuntimeError(f"Azure analyze failed: {e_docint} | {e_fr}")


# -----------------------------
# UI
# -----------------------------


def run() -> None:
    st.set_page_config(page_title="Extraction Pipeline", layout="wide")
    st.title("Extraction Pipeline")
    st.caption("Classification (config) → 1040 routing → Azure extraction → Single JSON output")

    # Upload a single PDF
    uploaded = file_uploader("Upload a PDF (single file)", types=["pdf"])
    if not uploaded:
        st.info("Upload a PDF to start.")
        st.stop()
    pdf_path = save_uploaded_file(uploaded)
    st.caption(f"Saved: {pdf_path}")

    # Page count
    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count

    # Classification config (default to bundled forms_config.json, allow override upload)
    st.markdown("#### Classification Configuration")
    colA, colB = st.columns([2, 1])
    with colA:
        default_cfg_path = get_default_config_path()
        st.text_input(
            "Default config path",
            value=str(default_cfg_path),
            disabled=True,
            key="_pipeline_cfg_path_default",
        )
    with colB:
        cfg_upload = st.file_uploader("Upload alternate config", type=["json"], key="_pipeline_cfg_upload")

    # Load config
    cfg = None
    if cfg_upload is not None:
        try:
            cfg = json.loads(cfg_upload.read().decode("utf-8"))
        except Exception as e:
            st.error(f"Invalid uploaded config JSON: {e}")
            st.stop()
    else:
        try:
            cfg = load_config(default_cfg_path)
        except Exception as e:
            st.error(f"Failed to load default classifier config: {e}")
            st.stop()

    # Run local classifier on all pages using selected config
    try:
        t_clf0 = perf_counter()
        rows = classify_document(pdf_path, cfg)
        t_clf = perf_counter() - t_clf0
        # Convert to ClassifiedPage list
        classified = [
            ClassifiedPage(
                file_name=Path(pdf_path).name,
                page=int(r.get("page")),
                label=str(r.get("predicted_label")),
                family=str(r.get("predicted_family") or _infer_family(str(r.get("predicted_label")))),
            )
            for r in rows
        ]
    except Exception as e:
        st.error("Classification failed.")
        st.code(f"{type(e).__name__}: {e}")
        st.stop()

    # Build 1040 plan
    t_plan0 = perf_counter()
    azure_jobs, page_plan = _build_plan_1040(classified)
    t_plan = perf_counter() - t_plan0

    # Compute total pages to analyze (only 1040 jobs)
    pages_to_analyze = 0
    for job in azure_jobs:
        pages_to_analyze += count_pages_from_spec(job.pages, total_pages=page_count)

    # Show quick plan summary
    st.markdown("#### Plan Summary")
    col_s1, col_s2, col_s3 = st.columns(3)
    col_s1.metric("Classify time", f"{t_clf*1000:.0f} ms")
    col_s2.metric("Plan time", f"{t_plan*1000:.0f} ms")
    col_s3.metric("Pages to analyze", str(pages_to_analyze))

    # Generic job policy (model selection) — extendable ruleset
    job_policy = {
        "rules": [
            {
                "if_family": "1040",
                "labels": ["1040_Main_Pg1", "1040_Main_Pg2"],
                "model": AZURE_CONFIG.get("model_id_1040", "prebuilt-tax.us.1040"),
            },
            {
                "if_family": "1040",
                "labels": ["1040_Schedule_1"],
                "model": AZURE_CONFIG.get("model_id_1040_schedule1", "prebuilt-tax.us.1040Schedule1"),
            },
        ]
    }
    with st.expander("Details: page plan and model policy", expanded=False):
        st.json({
            "file_name": Path(pdf_path).name,
            "job_policy": job_policy,
            "page_plan": [vars(p) for p in page_plan],
        })

    # Run button
    if st.button("Run Extraction", type="primary"):
        # Progress across analyzable pages only
        total_pages = max(0, int(pages_to_analyze))
        done_pages = 0
        prog = st.progress(0.0, text=f"0/{total_pages} Done — waiting to start…")
        status = st.empty()
        job_log_placeholder = st.empty()

        # Execute Azure jobs
        azure_results: Dict[str, Dict[str, Any]] = {}
        model_id = AZURE_CONFIG.get("model_id_1040", "prebuilt-tax.us.1040")
        job_timings: List[Dict[str, Any]] = []
        api_total = 0.0
        for job in azure_jobs:
            label_text = ", ".join(sorted({p.label for p in page_plan if p.job_id == job.job_id})) or "1040"
            # Show job as running before the call
            job_log_placeholder.write({"running": job.job_id, "pages": job.pages, "label": label_text})
            status.text(f"Analyzing {job.pages} — {label_text}")
            t0 = perf_counter()
            try:
                result = _analyze_with_azure(pdf_path, model_id=job.model_id, pages=job.pages)
                secs = perf_counter() - t0
                api_total += secs
                azure_results[job.job_id] = {"ok": True, "result": result}
                job_timings.append({"job_id": job.job_id, "model_id": job.model_id, "pages": job.pages, "secs": secs, "ok": True})
            except Exception as e:
                secs = perf_counter() - t0
                api_total += secs
                azure_results[job.job_id] = {"ok": False, "error": str(e)}
                job_timings.append({"job_id": job.job_id, "model_id": job.model_id, "pages": job.pages, "secs": secs, "ok": False, "error": str(e)})

            # Increment progress by number of pages in this job
            inc = count_pages_from_spec(job.pages, total_pages=page_count)
            done_pages += inc
            frac = 1.0 if total_pages == 0 else min(1.0, max(0.0, done_pages / float(total_pages)))
            prog.progress(frac, text=f"{min(done_pages, total_pages)}/{total_pages} Done — {label_text}")
            # Update job log with latest entry
            job_log_placeholder.json(job_timings)

        # Compose final output (compute hash lazily)
        file_sha = _sha256_file(pdf_path)
        final_json = _compose_output(
            file_name=Path(pdf_path).name,
            file_sha256=file_sha,
            page_count=page_count,
            classified=classified,
            azure_jobs=azure_jobs,
            page_plan=page_plan,
            azure_results=azure_results,
        )

        st.success("Pipeline complete.")
        st.markdown("#### Timings")
        st.json({
            "classify_ms": int(t_clf * 1000),
            "plan_ms": int(t_plan * 1000),
            "azure_api_ms_total": int(api_total * 1000),
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "job_timings": job_timings,
        })

        st.markdown("#### Final Output JSON")
        st.json(final_json)
        download_json_button(
            "Download final_output.json",
            data=final_json,
            filename=f"pipeline_{Path(pdf_path).stem}.json",
        )


if __name__ == "__main__":
    run()
