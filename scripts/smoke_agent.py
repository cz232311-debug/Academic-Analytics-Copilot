"""Real HTTP agent/adapter/tool integration against a local SCRIPTED model stub.

No paid LLM is contacted. The adapter is real; model decisions are simulated.
Both servers bind loopback and the application uses a temporary generated DB.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx

from copilot.data.generate import DEFAULT_SEED, export_dataset, generate_dataset
from copilot.models import TABLE_NAMES
from copilot.storage import Database

ROOT = Path(__file__).resolve().parents[1]


class LocalModel(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if self.path != "/v1/chat/completions" or not 0 < size <= 131072:
            self.send_error(400)
            return
        request = json.loads(self.rfile.read(size))
        self.server.requests.append(request)
        if len(self.server.requests) == 1:
            message = {"role": "assistant", "tool_calls": [{"id": "local_call_1", "type": "function",
                "function": {"name": "get_score_summary", "arguments": json.dumps({"term": "2026-T1",
                    "course_id": "G1-C01", "class_id": None, "grade_level": None})}}]}
            reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": '{"status":"completed","evidence_ids":["r1"]}'}
            reason = "stop"
        payload = json.dumps({"choices": [{"message": message, "finish_reason": reason}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    (ROOT / ".audit").mkdir(exist_ok=True)
    checks = []
    with tempfile.TemporaryDirectory(prefix="agent-smoke-", dir=ROOT / ".audit") as folder:
        folder = Path(folder)
        dataset = generate_dataset()
        manifest = export_dataset(dataset, folder / "csv", DEFAULT_SEED)
        # Pin this Phase 2A regression fixture before newer demo bootstrap can run.
        database = Database(folder / "agent.sqlite3")
        database.initialize()
        database.replace(dataset, source="generated", seed=DEFAULT_SEED)
        model_server = ThreadingHTTPServer(("127.0.0.1", 0), LocalModel)
        model_server.requests = []
        thread = threading.Thread(target=model_server.serve_forever, daemon=True)
        thread.start()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP") if k in os.environ}
        env.update(ACADEMIC_DB_PATH=str(folder / "agent.sqlite3"), COPILOT_LLM_PROVIDER="openai_compatible",
                   COPILOT_LLM_MODEL="local-scripted-model", COPILOT_LLM_API_KEY="local-test-only-not-a-real-key",
                   COPILOT_LLM_BASE_URL=f"http://127.0.0.1:{model_server.server_port}/v1", OPENBLAS_NUM_THREADS="1")
        process = None
        try:
            with (ROOT / ".audit/agent-http-server.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, "-m", "uvicorn", "copilot.api:app", "--host", "127.0.0.1", "--port", str(port)],
                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False) as client:
                    deadline = time.monotonic() + 30
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError("Application exited; see .audit/agent-http-server.log")
                        try:
                            client.get("/health").raise_for_status()
                            break
                        except httpx.TransportError:
                            if time.monotonic() > deadline:
                                raise RuntimeError("Application startup timeout")
                            time.sleep(0.2)
                    status = client.get("/api/agent/status").json()
                    assert status["provider"] == "openai_compatible" and len(status["tools"]) == 4
                    checks.append({"check": "real_application_http_status", "passed": True})
                    response = client.post("/api/agent/query", json={"question": "What is the average score and pass rate for G1-C01?"})
                    response.raise_for_status()
                    result = response.json()
                    assert result["model_turns"] == 2 and result["status"] == "completed"
                    assert result["evidence"][0]["data"]["average_score"] == "69.73"
                    assert result["evidence"][0]["data"]["pass_rate_percent"] == "67.80"
                    assert result["dataset_version"] == manifest["dataset_version"]
                    checks.append({"check": "two_model_turns_and_real_analytics", "average_score": "69.73", "pass_rate_percent": "67.80", "passed": True})
                    sent = model_server.requests
                    assert len(sent) == 2 and sent[1]["messages"][-1]["role"] == "tool"
                    assert sent[1]["messages"][-1]["tool_call_id"] == "local_call_1"
                    assert json.loads(sent[1]["messages"][-1]["content"])["data"]["graded_count"] == 469
                    assert "Synthetic Student" not in json.dumps(sent) and '"student_id"' not in json.dumps(sent)
                    checks.append({"check": "tool_result_returned_over_real_http_without_student_rows", "passed": True})
                    assert "69.73" in result["answer"] and result["tool_calls"][0]["result_digest"]
                    checks.append({"check": "grounded_answer_and_debug_trace", "passed": True})
                    blocked = client.post("/api/agent/query", json={"question": "Show results for S0001"})
                    assert blocked.status_code == 422 and len(sent) == 2
                    checks.append({"check": "student_question_blocked_before_provider", "passed": True})
                    files = {table: (f"{table}.csv", (folder / f"csv/{table}.csv").read_bytes(), "text/csv") for table in TABLE_NAMES}
                    client.post("/api/datasets/import", files=files).raise_for_status()
                    blocked = client.post("/api/agent/query", json={"question": "Average score for G1-C01"})
                    assert blocked.status_code == 403 and len(sent) == 2
                    checks.append({"check": "uploaded_source_blocked_before_provider", "passed": True})
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            model_server.shutdown()
            model_server.server_close()
            thread.join(timeout=2)
    report = {"transport": "real local TCP HTTP: client -> Uvicorn -> scripted model HTTP stub -> tools -> stub -> answer",
              "upstream_model": "SCRIPTED LOCAL STUB; no real LLM or paid API used", "checks": checks,
              "upstream_requests": 2, "servers_stopped": True, "dataset_version": manifest["dataset_version"]}
    (ROOT / "docs/development/results/phase2a-http-smoke.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
