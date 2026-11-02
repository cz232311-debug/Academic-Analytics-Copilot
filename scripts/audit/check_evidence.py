"""Verify audit artifact integrity without importing or executing upstream code."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/audit/evidence"


def read(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def normalized(name):
    return re.sub(r"[-_.]+", "-", name.lower())


def main():
    checks = []
    inventory = read("dependency-inventory.json")["packages"]
    count = 0
    for package in inventory:
        for entry in package["license_files"]:
            path = (ROOT / entry["path"]).resolve()
            assert path.is_relative_to(OUT.resolve())
            assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], entry["path"]
            count += 1
    checks.append({"check": "retained_dependency_notice_hashes", "files": count, "passed": True})
    manifest = read("upstream_manifest.json")
    license_entry = next(x for x in manifest["files"] if x["path"] == "LICENSE")
    assert hashlib.sha256((ROOT / "third_party/student-performance-analyzer/LICENSE").read_bytes()).hexdigest() == license_entry["sha256"]
    checks.append({"check": "unmodified_upstream_license", "passed": True})
    runtime_names = {normalized(p["name"]) for p in inventory if p["name"].lower() != "pip"}
    audited = read("pip-audit.json")["dependencies"]
    assert {normalized(p["name"]) for p in audited} == runtime_names
    assert all("vulns" in p and not p.get("skip_reason") for p in audited)
    installed = read("pip-install-report.json")["install"]
    assert {normalized(p["metadata"]["name"]) for p in installed} == runtime_names
    checks.append({"check": "install_inventory_advisory_package_coverage", "packages": len(runtime_names), "passed": True})
    probes = read("data-probes.json")["cases"]
    assert len(probes) == 6 and all(p["passed"] for p in probes)
    checks.append({"check": "recorded_data_probe_completion", "cases": len(probes), "passed": True})
    cases = re.findall(r"^\| (E\d{2}) \|", (ROOT / "docs/EVALUATION_PLAN.md").read_text(encoding="utf-8"), re.M)
    assert len(cases) == len(set(cases)) == 36
    checks.append({"check": "proposed_evaluation_case_count", "cases": len(cases), "executed": False, "passed": True})
    scripts = list((ROOT / "scripts/audit").glob("*.py"))
    for script in scripts:
        ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    checks.append({"check": "audit_script_syntax_only", "files": len(scripts), "passed": True})
    for document in [ROOT / "README.md", ROOT / "NOTICE.md", *list((ROOT / "docs").rglob("*.md"))]:
        if OUT in document.parents:
            continue
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            assert (document.parent / target.split("#")[0]).exists(), (document, target)
    checks.append({"check": "local_document_links", "passed": True})
    result = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(), "checks": checks}
    (OUT / "integrity-check.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
