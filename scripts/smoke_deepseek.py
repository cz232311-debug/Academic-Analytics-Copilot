"""Run one synthetic-data Agent acceptance query after explicit live approval."""
import argparse
import json
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from copilot.agent.models import AgentError
from copilot.agent.provider import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, LLMSettings, MockDemoProvider, build_provider
from copilot.agent.service import MAX_MODEL_TURNS, MAX_TOOL_CALLS
from copilot.api import create_app

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "What is the average score and pass rate for G1-C01?"


def passed(body: dict, live: bool) -> bool:
    if body.get("status") != "completed" or body.get("answer_mode") != "grounded_narrative":
        return False
    if live and (body.get("provider") != "deepseek" or body.get("simulated") or not body.get("usage")):
        return False
    if body["model_turns"] < 2:
        return False
    for item in body["evidence"]:
        if item["tool"] != "get_score_summary":
            continue
        data = item["data"]
        if (data["scope"]["course_id"] == "G1-C01" and data["scope"]["term"] == "2026-T1"
                and data["graded_count"] == 469 and data["average_score"] == "69.73"
                and data["pass_rate_percent"] == "67.80" and item["dataset_version"] == body["dataset_version"]
                and f"[{item['evidence_id']}]" in body["answer"] and "69.73" in body["answer"]
                and "67.80%" in body["answer"]):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--offline", action="store_true", help="Use the local mock on the complete generated dataset")
    modes.add_argument("--live", action="store_true", help="Make one billable Agent query after authorization")
    args = parser.parse_args()
    if not (args.offline or args.live):
        print(json.dumps({"mode": "dry_run", "question": QUESTION, "endpoint": DEEPSEEK_BASE_URL + "/chat/completions",
                          "model": DEEPSEEK_MODEL, "thinking": "disabled", "agent_queries": 1,
                          "max_model_turns": MAX_MODEL_TURNS, "max_tool_calls": MAX_TOOL_CALLS,
                          "max_output_tokens_per_turn": 2048, "paid_requests_sent": 0}, indent=2))
        return 0

    timeout = 45.0
    if args.live:
        try:
            settings = LLMSettings.from_env()
        except AgentError:
            print("Live check requires valid DeepSeek environment settings and COPILOT_LLM_API_KEY.")
            return 2
        if (settings.provider, settings.model, settings.base_url) != ("deepseek", DEEPSEEK_MODEL, DEEPSEEK_BASE_URL):
            print("Live check requires the deepseek profile, deepseek-flash and the official DeepSeek base URL.")
            return 2
        provider = build_provider(settings)
        timeout = settings.request_timeout_seconds
    else:
        provider = MockDemoProvider()

    scratch = ROOT / ".audit"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deepseek-check-", dir=scratch) as folder:
        app = create_app(Path(folder) / "synthetic.sqlite3", agent_provider=provider, agent_timeout_seconds=timeout)
        with TestClient(app) as client:
            response = client.post("/api/agent/query", json={"question": QUESTION})
            body = response.json()
    success = response.status_code == 200 and passed(body, args.live)
    report = {"mode": "live_deepseek" if args.live else "offline_mock", "passed": success,
              "question": QUESTION, "http_status": response.status_code, "response": body}
    output = scratch / "deepseek-live.json" if args.live else ROOT / "docs/development/results/14-deepseek-offline-demo.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, indent=2)
    output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
