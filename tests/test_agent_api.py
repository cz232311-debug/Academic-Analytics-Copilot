import asyncio
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from copilot.agent.models import AgentError
from copilot.agent.provider import OpenAICompatibleProvider
from copilot.api import create_app
from copilot.storage import Database
from tests.test_agent_service import ScriptedProvider, final_turn, tool_turn
from tests.test_llm_provider import completion, settings


@pytest.fixture
def agent_client(tmp_path, tiny_dataset, monkeypatch):
    monkeypatch.setenv("COPILOT_LLM_PROVIDER", "mock")
    path = tmp_path / "api-agent.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False)) as client:
        yield client


def test_debug_status_and_natural_language_endpoint(agent_client):
    status = agent_client.get("/api/agent/status").json()
    assert status["simulated"] is True and len(status["tools"]) == 4
    response = agent_client.post("/api/agent/query", json={"question": "What is the average score and pass rate for G1-C01?"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulated"] is True and body["model_turns"] == 2
    assert body["evidence"][0]["data"]["sum_score_cents"] == 21999
    assert "73.33" in body["answer"] and "66.67%" in body["answer"]
    assert len(body["request_id"]) == 32 and len(body["tool_calls"]) == 1
    assert "Synthetic Student" not in response.text
    assert "/api/agent/query" in agent_client.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": 123}, {"question": "x" * 1001},
                                     {"question": "Average score", "provider": "attacker"}, {"question": "Average score for John Smith"}])
def test_bad_query_schema_and_privacy_errors(agent_client, payload):
    response = agent_client.post("/api/agent/query", json=payload)
    assert response.status_code == 422 and "John Smith" not in response.text


def test_agent_request_size_is_bounded_before_json_parsing(agent_client):
    response = agent_client.post("/api/agent/query", content=b"x" * 8193)
    assert response.status_code == 413 and "error" in response.json()


def test_upload_source_is_blocked_before_any_provider_call(tmp_path, tiny_dataset):
    path = tmp_path / "uploaded.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset)
    provider = ScriptedProvider([])
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": "Average score for G1-C01"})
    assert response.status_code == 403 and provider.requests == []
    assert response.json()["error"]["code"] == "agent_privacy_block"


def test_configuration_failure_does_not_break_deterministic_apis(agent_client, monkeypatch):
    monkeypatch.setenv("COPILOT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("COPILOT_LLM_API_KEY", "")
    monkeypatch.setenv("COPILOT_LLM_MODEL", "test-model")
    response = agent_client.post("/api/agent/query", json={"question": "Average score"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_configuration_error"
    assert agent_client.get("/api/analytics/summary").status_code == 200


def test_entire_api_to_http_adapter_to_tools_loop_with_mock_transport(tmp_path, tiny_dataset, caplog):
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        message = (tool_turn() if len(seen) == 1 else final_turn()).message.model_dump(mode="json")
        return httpx.Response(200, json=completion(message, finish="tool_calls" if len(seen) == 1 else "stop"))
    provider = OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(handler))
    path = tmp_path / "http-adapter.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": "Average score for G1-C01"})
    assert response.status_code == 200, response.text
    assert response.json()["simulated"] is False  # Real adapter, mocked HTTP transport; not a live model test.
    assert len(seen) == 2 and seen[1]["messages"][-1]["role"] == "tool"
    assert "fake-test-key" not in response.text + caplog.text
    assert "Synthetic Student" not in json.dumps(seen)


def test_model_error_preserves_safe_trace_and_request_id(tmp_path, tiny_dataset):
    path = tmp_path / "bad-model.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    provider = ScriptedProvider([tool_turn("execute_sql", {"sql": "PRIVATE-CODE"})])
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": "Average score"})
    assert response.status_code == 502 and "PRIVATE-CODE" not in response.text
    assert response.json()["tool_calls"][0]["status"] == "error"
    assert response.json()["error"]["code"] == "unknown_tool"


def test_no_dataset_and_global_timeout(tmp_path):
    with TestClient(create_app(tmp_path / "empty.sqlite3", bootstrap_demo=False, agent_provider=ScriptedProvider([]))) as client:
        response = client.post("/api/agent/query", json={"question": "Average score"})
        assert response.status_code == 503 and response.json()["error"]["code"] == "no_dataset"


def test_provider_timeout_is_a_debuggable_http_error(tmp_path, tiny_dataset):
    path = tmp_path / "timeout.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    class Slow(ScriptedProvider):
        async def complete(self, messages, tools):
            await asyncio.sleep(1)
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=Slow([]), agent_timeout_seconds=0.05)) as client:
        response = client.post("/api/agent/query", json={"question": "Average score"})
    assert response.status_code == 504 and response.json()["error"]["code"] == "agent_timeout"


def test_unknown_entity_retains_the_analysis_error_status(agent_client):
    response = agent_client.post("/api/agent/query", json={"question": "Average score for G1-C99"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_entity"
