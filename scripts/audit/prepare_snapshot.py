"""Inspect a pinned Git object tree without checking out or loading model files."""
import ast
import csv
import hashlib
import io
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / ".audit/upstream"
PIN = "c691eff6ecc0de351ffcdf26bdce436e5daa64e2"
EVIDENCE = ROOT / "docs/audit/evidence"


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(REPO), *args])


def main() -> None:
    assert git("rev-parse", "HEAD").decode().strip() == PIN
    manifest = {"repository": "https://github.com/CH-Anonymous/student-performance-analyzer",
                "commit": PIN, "files": []}
    allowed = {"app.py", "requirements.txt", "LICENSE", "readme.md", ".streamlit/config.toml"}
    runtime = ROOT / ".audit/runtime"
    for row in git("ls-tree", "-rl", PIN).decode().splitlines():
        attrs, name = row.split("\t", 1)
        mode, kind, oid, size = attrs.split()
        blob = git("cat-file", "blob", oid)
        manifest["files"].append({"path": name, "mode": mode, "git_blob": oid,
                                  "bytes": int(size), "sha256": hashlib.sha256(blob).hexdigest()})
        if name in allowed:
            target = runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        if name == "LICENSE":
            (ROOT / "third_party/student-performance-analyzer/LICENSE").write_bytes(blob)
        if name.endswith(".csv"):
            records = list(csv.DictReader(io.StringIO(blob.decode("utf-8-sig"))))
            manifest["upstream_csv"] = {"rows": len(records), "columns": list(records[0]),
                                        "provenance": "unverified; not used for runtime tests"}
    source = (runtime / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    manifest["python"] = {
        "lines": len(source.splitlines()),
        "functions": sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree)),
        "classes": sum(isinstance(n, ast.ClassDef) for n in ast.walk(tree)),
        "imports": [{"line": n.lineno, "statement": ast.get_source_segment(source, n)}
                    for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))],
        "bare_except_lines": [n.lineno for n in ast.walk(tree)
                              if isinstance(n, ast.ExceptHandler) and n.type is None],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "upstream_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
