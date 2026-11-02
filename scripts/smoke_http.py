"""Real Uvicorn/HTTP integration check with a temporary local database and cleanup."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

from copilot.models import TABLE_NAMES

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / "data/demo/manifest.json").read_text(encoding="utf-8"))
    checks = []
    (ROOT / ".audit").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="http-smoke-", dir=ROOT / ".audit") as directory:
        env = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP") if k in os.environ}
        env["ACADEMIC_DB_PATH"] = str(Path(directory) / "smoke.sqlite3")
        env["OPENBLAS_NUM_THREADS"] = "1"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        command = [sys.executable, "-m", "uvicorn", "copilot.api:app", "--host", "127.0.0.1", "--port", str(port)]
        with (ROOT / ".audit/product-http-server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
                    deadline = time.monotonic() + 30
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError("Server exited; see .audit/product-http-server.log")
                        try:
                            health = client.get("/health")
                            health.raise_for_status()
                            break
                        except httpx.TransportError:
                            if time.monotonic() > deadline:
                                raise RuntimeError("Server startup timeout")
                            time.sleep(0.2)
                    checks.append({"check": "real_http_health", "status": health.status_code, "passed": True})
                    info = client.get("/api/dataset").json()
                    assert info["counts"] == manifest["counts"]
                    assert info["dataset_version"] == manifest["dataset_version"]
                    checks.append({"check": "generated_dataset_matches_export_manifest", "counts": info["counts"], "passed": True})
                    response = client.get("/api/analytics/summary", params={"course_id": "G1-C01"})
                    response.raise_for_status()
                    summary = response.json()
                    assert summary["graded_count"] == sum(b["count"] for b in summary["distribution"])
                    checks.append({"check": "filtered_statistics_over_http", "average_score": summary["average_score"],
                                   "pass_rate_percent": summary["pass_rate_percent"], "graded_count": summary["graded_count"], "passed": True})
                    comparison = client.get("/api/analytics/compare-classes", params=[("course_id", "G1-C01"),
                                                  ("class_ids", "C001"), ("class_ids", "C002")])
                    comparison.raise_for_status()
                    assert len(comparison.json()["results"]) == 2
                    checks.append({"check": "class_comparison_over_http", "passed": True})
                    files = {table: (f"{table}.csv", (ROOT / f"data/demo/{table}.csv").read_bytes(), "text/csv") for table in TABLE_NAMES}
                    imported = client.post("/api/datasets/import", files=files)
                    imported.raise_for_status()
                    assert imported.json()["dataset"]["dataset_version"] == manifest["dataset_version"]
                    assert imported.json()["dataset"]["external_llm_allowed"] is False
                    checks.append({"check": "full_demo_csv_upload_over_http", "status": imported.status_code, "passed": True})
                    assert client.get("/api/analytics/summary?course_id=G1-C01").json() == summary
                    checks.append({"check": "statistics_unchanged_after_roundtrip", "passed": True})
                    files["students"] = ("students.pkl", b"opaque-not-a-model", "application/octet-stream")
                    rejected = client.post("/api/datasets/import", files=files)
                    assert rejected.status_code == 415
                    assert client.get("/api/dataset").json()["dataset_version"] == manifest["dataset_version"]
                    checks.append({"check": "invalid_upload_rejected_without_data_loss", "status": 415, "passed": True})
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    result = {"transport": "real TCP HTTP to local Uvicorn; no mocked analytics", "checks": checks,
              "server_stopped": True, "dataset_version": manifest["dataset_version"]}
    (ROOT / "docs/development/results/http-smoke.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
