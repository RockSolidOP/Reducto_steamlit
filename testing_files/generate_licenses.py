#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

try:
    import httpx  # optional, for --pypi fallback
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

try:
    from importlib.metadata import PackageNotFoundError, metadata, version
except Exception:  # pragma: no cover
    # Python <3.8 shim
    from importlib_metadata import PackageNotFoundError, metadata, version  # type: ignore


@dataclass
class LicenseInfo:
    name: str
    req_version: str | None
    installed_version: str | None
    license: str | None
    classifiers: str | None
    home_page: str | None
    summary: str | None
    source: str  # installed|pypi|unknown


def parse_requirements(req_path: Path) -> List[tuple[str, Optional[str]]]:
    items: List[tuple[str, Optional[str]]] = []
    for line in req_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # simple pin parser: name==ver or name
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:==([^;]+))?", line)
        if not m:
            continue
        name = m.group(1)
        ver = (m.group(2) or "").strip() or None
        items.append((name, ver))
    return items


def get_installed_license(pkg: str) -> LicenseInfo | None:
    try:
        md = metadata(pkg)
    except PackageNotFoundError:
        # Try normalized
        norm = pkg.replace("-", "_")
        try:
            md = metadata(norm)
        except PackageNotFoundError:
            return None
    lic = md.get("License")
    classifiers = " | ".join(md.get_all("Classifier") or []) or None
    home = md.get("Home-page") or md.get("Project-URL")
    summ = md.get("Summary")
    try:
        ver = version(md["Name"]) if md.get("Name") else version(pkg)
    except PackageNotFoundError:
        ver = None
    # Some packages leave License blank but specify via classifiers
    if (not lic) and classifiers:
        # Attempt to extract "License :: ... :: X"
        parts = [c for c in (md.get_all("Classifier") or []) if c.startswith("License :: ")]
        lic = parts[-1].split("::")[-1].strip() if parts else None
    return LicenseInfo(
        name=md.get("Name") or pkg,
        req_version=None,
        installed_version=ver,
        license=lic,
        classifiers=classifiers,
        home_page=home,
        summary=summ,
        source="installed",
    )


def get_pypi_license(pkg: str) -> LicenseInfo | None:
    if httpx is None:
        return None
    url = f"https://pypi.org/pypi/{pkg}/json"
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception:
        return None
    info = data.get("info", {})
    lic = info.get("license") or None
    classifiers = " | ".join(info.get("classifiers") or []) or None
    home = info.get("home_page") or info.get("project_url") or None
    summ = info.get("summary") or None
    ver = info.get("version") or None
    # Prefer license from classifiers if textual license is generic like "UNKNOWN"
    if lic and lic.strip().upper() in {"UNKNOWN", "", "SEE LICENSE"}:
        lic = None
    if (not lic) and classifiers:
        parts = [c for c in (info.get("classifiers") or []) if c.startswith("License :: ")]
        lic = parts[-1].split("::")[-1].strip() if parts else None
    return LicenseInfo(
        name=info.get("name") or pkg,
        req_version=None,
        installed_version=ver,
        license=lic,
        classifiers=classifiers,
        home_page=home,
        summary=summ,
        source="pypi",
    )


def main():
    ap = argparse.ArgumentParser(description="Generate a licenses CSV from requirements.txt")
    here = Path(__file__).resolve().parent
    root = here.parent.parent  # project root
    ap.add_argument("--requirements", default=str(root / "requirements.txt"))
    ap.add_argument("--out", default=str(here / "licenses.csv"))
    ap.add_argument("--pypi", action="store_true", help="Query PyPI for packages not installed locally")
    args = ap.parse_args()

    req_path = Path(args.requirements)
    if not req_path.exists():
        raise FileNotFoundError(req_path)
    items = parse_requirements(req_path)

    rows: List[LicenseInfo] = []
    for name, pin in items:
        info = get_installed_license(name)
        if info is None and args.pypi:
            info = get_pypi_license(name)
        if info is None:
            info = LicenseInfo(
                name=name,
                req_version=pin,
                installed_version=None,
                license=None,
                classifiers=None,
                home_page=None,
                summary=None,
                source="unknown",
            )
        else:
            info.req_version = pin
        rows.append(info)

    out_path = Path(args.out)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "req_version", "installed_version", "license", "classifiers", "home_page", "summary", "source"])
        for r in rows:
            w.writerow([
                r.name,
                r.req_version or "",
                r.installed_version or "",
                r.license or "",
                r.classifiers or "",
                r.home_page or "",
                r.summary or "",
                r.source,
            ])
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()

