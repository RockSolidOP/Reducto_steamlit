from __future__ import annotations

"""Extraction Pipeline page (ML-only)

What this page does
- Upload a PDF
- Classify all pages with the local ML classifier (FAISS/CLIP)
- Pair P1/P2 within each base_label; extra pages are singles
- Run Azure models on planned pages using ML label → model mapping
- Show a compact combined result JSON (page_plan + Azure runs)

Notes
- No regex classifier here; ML labels are used end-to-end.
- Model mapping and skip list live in app/config_classifier_ml_labels.py.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from time import perf_counter

import fitz  # PyMuPDF
import streamlit as st

# Ensure project root is on sys.path when running this page directly
try:  # noqa: SIM105 - deliberate try/except import shim
    from app.services.azure_service import parse_with_azure_docint, azure_to_dict
    from app.services.ml_classify_service import classify_document_ml
    from app.config_classifier_ml_labels import ML_LABEL_MODEL_MAP, SKIP_LABELS
except ModuleNotFoundError:  # Running via `streamlit run pages/Extraction_Pipeline.py`
    import sys as _sys
    from pathlib import Path as _Path

    _ROOT = _Path(__file__).resolve().parents[1]
    if str(_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_ROOT))
    from app.services.azure_service import parse_with_azure_docint, azure_to_dict
    from app.services.ml_classify_service import classify_document_ml
    from app.config_classifier_ml_labels import ML_LABEL_MODEL_MAP, SKIP_LABELS
from app.ui.components import download_json_button, file_uploader
from app.utils.storage import save_uploaded_file


# -----------------------------
# Models
# -----------------------------


@dataclass
class ClassifiedPage:
    file_name: str
    page: int
    label: str  # ML label used end-to-end
    base_label: Optional[str] = None  # normalized base form (e.g., 1040, Schedule_C)
    page_in_form: Optional[int] = None  # 1/2 when known


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
    status: str  # "paired" | "single"
    group_id: Optional[str]
    job_id: Optional[str]
    action: str  # "analyze" | "skip"
    model_id: Optional[str] = None  # model selected for this page (if any)




# -----------------------------
# Helpers
# -----------------------------

# No fallback inference: base_label is taken from ML output or defaults to "Other".


## (removed) canonical mapping — we use raw ML labels end-to-end now.

def _select_model_for_page(label: str, base_label: Optional[str]) -> Optional[str]:
    """Return model id for a page strictly based on ML label map; else skip."""
    if label in SKIP_LABELS:
        return None
    return ML_LABEL_MODEL_MAP.get(label)


def _build_plan_generic(classified: List[ClassifiedPage]) -> Tuple[List[AzureJob], List[PagePlan]]:
    """Pair P1/P2 within each base_label; extras become singles. Analyze all pages."""
    if not classified:
        return [], []

    file_name = classified[0].file_name
    groups: Dict[str, List[ClassifiedPage]] = {}
    for c in sorted(classified, key=lambda x: int(x.page)):
        bl = c.base_label or "Other"
        groups.setdefault(bl, []).append(c)

    import re
    def _n(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", (s or "").lower())

    def is_p1(c: ClassifiedPage) -> bool:
        if c.page_in_form == 1:
            return True
        n = _n(c.label)
        return n.endswith("_p1") or "page_1" in n or "pg_1" in n

    def is_p2(c: ClassifiedPage) -> bool:
        if c.page_in_form == 2:
            return True
        n = _n(c.label)
        return n.endswith("_p2") or "page_2" in n or "pg_2" in n

    def tag(bl: str) -> str:
        return _n(bl).upper()

    jobs: List[AzureJob] = []
    plan: List[PagePlan] = []
    pair_no = 0

    for bl, pages in groups.items():
        unmatched_p1: List[ClassifiedPage] = []
        unmatched_p2: List[ClassifiedPage] = []
        singles: List[ClassifiedPage] = []

        roles: List[Tuple[str, ClassifiedPage]] = []
        for c in pages:
            if is_p1(c):
                roles.append(("p1", c))
            elif is_p2(c):
                roles.append(("p2", c))
            else:
                roles.append(("single", c))

        for role, c in roles:
            if role == "p2":
                if unmatched_p1:
                    first = unmatched_p1.pop(0)
                    pair_no += 1
                    a, b = (int(first.page), int(c.page))
                    if a > b:
                        a, b = b, a
                    gid = f"{file_name}#{tag(bl)}#{pair_no}({a},{b})"
                    jid = gid
                    mdl = _select_model_for_page(first.label, bl)
                    if mdl is None:
                        plan.append(PagePlan(page=a, label=first.label, status="paired", group_id=gid, job_id=None, action="skip", model_id=None))
                        plan.append(PagePlan(page=b, label=c.label, status="paired", group_id=gid, job_id=None, action="skip", model_id=None))
                    else:
                        jobs.append(AzureJob(job_id=jid, file_name=file_name, model_id=mdl, pages=f"{a}-{b}", reason=f"Paired {bl} pages"))
                        plan.append(PagePlan(page=a, label=first.label, status="paired", group_id=gid, job_id=jid, action="analyze", model_id=mdl))
                        plan.append(PagePlan(page=b, label=c.label, status="paired", group_id=gid, job_id=jid, action="analyze", model_id=mdl))
                else:
                    unmatched_p2.append(c)
            elif role == "p1":
                if unmatched_p2:
                    second = unmatched_p2.pop(0)
                    pair_no += 1
                    a, b = (int(c.page), int(second.page))
                    if a > b:
                        a, b = b, a
                    gid = f"{file_name}#{tag(bl)}#{pair_no}({a},{b})"
                    jid = gid
                    mdl = _select_model_for_page(c.label, bl)
                    if mdl is None:
                        plan.append(PagePlan(page=a, label=c.label, status="paired", group_id=gid, job_id=None, action="skip", model_id=None))
                        plan.append(PagePlan(page=b, label=second.label, status="paired", group_id=gid, job_id=None, action="skip", model_id=None))
                    else:
                        jobs.append(AzureJob(job_id=jid, file_name=file_name, model_id=mdl, pages=f"{a}-{b}", reason=f"Paired {bl} pages"))
                        plan.append(PagePlan(page=a, label=c.label, status="paired", group_id=gid, job_id=jid, action="analyze", model_id=mdl))
                        plan.append(PagePlan(page=b, label=second.label, status="paired", group_id=gid, job_id=jid, action="analyze", model_id=mdl))
                else:
                    unmatched_p1.append(c)
            else:
                singles.append(c)

        for c in unmatched_p1 + unmatched_p2 + singles:
            pair_no += 1
            gid = f"{file_name}#{tag(bl)}#{pair_no}({int(c.page)})"
            jid = gid
            mdl = _select_model_for_page(c.label, bl)
            if mdl is None:
                plan.append(PagePlan(page=int(c.page), label=c.label, status="single", group_id=gid, job_id=None, action="skip", model_id=None))
            else:
                jobs.append(AzureJob(job_id=jid, file_name=file_name, model_id=mdl, pages=str(int(c.page)), reason=f"Single {bl} page"))
                plan.append(PagePlan(page=int(c.page), label=c.label, status="single", group_id=gid, job_id=jid, action="analyze", model_id=mdl))

    plan.sort(key=lambda x: x.page)
    return jobs, plan

    # End of generic planning
    plan.sort(key=lambda x: x.page)
    return jobs, plan


 


def _analyze_with_azure(pdf_path: Path, *, model_id: str, pages: str) -> Any:
    """Run only Document Intelligence client; do not fall back."""
    return parse_with_azure_docint(pdf_path, page_number=1, model_id=model_id, pages=pages)


# -----------------------------
# UI
# -----------------------------


def run() -> None:
    st.set_page_config(page_title="Extraction Pipeline", layout="wide")
    st.title("Extraction Pipeline")
    st.caption("Classification (ML) → 1040 routing → Azure extraction → Single JSON output")

    # Upload a single PDF
    uploaded = file_uploader("Upload a PDF (single file)", types=["pdf"])
    if not uploaded:
        st.info("Upload a PDF to start.")
        st.stop()
    pdf_path = save_uploaded_file(uploaded)
    st.caption(f"Saved: {pdf_path}")

    # Optional: open for validation if needed (currently not used)

    # Run classifier on all pages (ML classifier only)
    try:
        t_clf0 = perf_counter()
        rows = classify_document_ml(Path(pdf_path), topk=1)
        t_clf = perf_counter() - t_clf0
        classified = []
        for r in rows:
            raw_lbl = str(r.get("predicted_label"))
            base_val = str(r.get("predicted_family") or r.get("base_label") or "Other")
            pif = r.get("page_in_form")
            try:
                pif_int = int(pif) if pif is not None else None
            except Exception:
                pif_int = None
            classified.append(
                ClassifiedPage(
                    file_name=Path(pdf_path).name,
                    page=int(r.get("page")),
                    label=raw_lbl,
                    base_label=base_val,
                    page_in_form=pif_int,
                )
            )
    except Exception as e:
        st.error("Classification failed.")
        st.code(f"{type(e).__name__}: {e}")
        st.stop()

    # Show classification summary (ML labels)
    with st.expander("Classification (ML labels)", expanded=False):
        st.json([
            {"page": c.page, "label": c.label, "base_label": c.base_label, "page_in_form": c.page_in_form}
            for c in classified
        ])

    # Build plan (pairing by base_label)
    t_plan0 = perf_counter()
    azure_jobs, page_plan = _build_plan_generic(classified)
    t_plan = perf_counter() - t_plan0

    # Compute total pages to analyze
    def _count_pages(spec: str) -> int:
        try:
            if "-" in spec:
                a, b = spec.split("-", 1)
                return max(0, int(b) - int(a) + 1)
            return 1 if spec.strip() else 0
        except Exception:
            return 0
    pages_to_analyze = sum(_count_pages(job.pages) for job in azure_jobs)

    # Show quick plan summary
    st.markdown("#### Plan Summary")
    col_s1, col_s2, col_s3 = st.columns(3)
    col_s1.metric("Classify time", f"{t_clf*1000:.0f} ms")
    col_s2.metric("Plan time", f"{t_plan*1000:.0f} ms")
    col_s3.metric("Pages to analyze", str(pages_to_analyze))

    # Show page plan only
    with st.expander("Details: page plan", expanded=False):
        st.json({
            "file_name": Path(pdf_path).name,
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
            inc = _count_pages(job.pages)
            done_pages += inc
            frac = 1.0 if total_pages == 0 else min(1.0, max(0.0, done_pages / float(total_pages)))
            prog.progress(frac, text=f"{min(done_pages, total_pages)}/{total_pages} Done — {label_text}")
            # Update job log with latest entry
            job_log_placeholder.json(job_timings)

        # Summarize outputs
        st.success("Pipeline complete.")
        st.markdown("#### Timings")
        st.json({
            "classify_ms": int(t_clf * 1000),
            "plan_ms": int(t_plan * 1000),
            "azure_api_ms_total": int(api_total * 1000),
            "job_timings": job_timings,
        })

        # Build a compact combined result
        combined = {
            "document": Path(pdf_path).name,
            "page_plan": [vars(p) for p in page_plan],
            "runs": [],
        }
        for job in azure_jobs:
            payload = azure_results.get(job.job_id) or {}
            ok = bool(payload.get("ok"))
            entry = {
                "job_id": job.job_id,
                "model_id": job.model_id,
                "pages": job.pages,
                "ok": ok,
            }
            if ok:
                try:
                    entry["result"] = azure_to_dict(payload.get("result"))
                except Exception:
                    entry["result"] = None
            else:
                entry["error"] = payload.get("error")
            combined["runs"].append(entry)

        st.markdown("#### Combined Result (JSON)")
        st.json(combined)
        download_json_button(
            "Download azure_pipeline.json",
            data=combined,
            filename=f"azure_pipeline_{Path(pdf_path).stem}.json",
        )


if __name__ == "__main__":
    run()
