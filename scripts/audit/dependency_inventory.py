"""Record installed versions and wheel license evidence without importing packages."""
import hashlib
import importlib.metadata as md
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/audit/evidence"
DIRECT = set((ROOT / ".audit/runtime/requirements.txt").read_text().split())


def main() -> None:
    entries = []
    for dist in sorted(md.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        license_files = []
        for file in dist.files or []:
            path = str(file).replace("\\", "/")
            if any(t in path.lower() for t in ("license", "licence", "copying", "notice")) and Path(path).suffix.lower() not in {".py", ".pyc", ".so", ".pyd"}:
                data = dist.locate_file(file).read_bytes()
                source = Path(dist.locate_file(file)).resolve()
                if not source.is_relative_to(Path(sys.prefix).resolve()):
                    raise ValueError(f"License path escapes virtual environment: {path}")
                # Wheel .data entries may legitimately use ../../share within the venv.
                relative = Path(*["_parent_" if p == ".." else p for p in Path(path).parts])
                if relative.is_absolute():
                    raise ValueError(f"Absolute wheel license path: {path}")
                target = OUT / "dependency-licenses" / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                # License texts are retained as supplied by the installed wheels.
                target.write_bytes(data)
                license_files.append({"path": str(target.relative_to(ROOT)).replace("\\", "/"),
                                      "sha256": hashlib.sha256(data).hexdigest()})
        entries.append({"name": name, "version": dist.version, "direct": name.lower() in DIRECT,
                        "license_expression": dist.metadata.get("License-Expression"),
                        "license_metadata": dist.metadata.get("License"),
                        "license_classifiers": [c for c in dist.metadata.get_all("Classifier", []) if c.startswith("License")],
                        "project_urls": dist.metadata.get_all("Project-URL", []), "license_files": license_files})
    (OUT / "dependency-inventory.json").write_text(json.dumps({"python": platform.python_version(), "platform": platform.platform(),
                                                           "packages": entries}, indent=2) + "\n", encoding="utf-8")
    resolved = [f'{e["name"]}=={e["version"]}' for e in entries if e["name"].lower() != "pip"]
    (OUT / "resolved-requirements.txt").write_text("\n".join(resolved) + "\n", encoding="utf-8")
    for e in entries:
        print(f'{e["name"]}=={e["version"]}: {e["license_expression"] or str(e["license_metadata"])[:100]} (license files: {len(e["license_files"])})')


if __name__ == "__main__":
    main()
