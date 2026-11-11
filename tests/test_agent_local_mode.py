"""The provider-free path answers local data without any external model call."""
from fastapi.testclient import TestClient

from copilot.api import create_app
from copilot.storage import Database
from tests.test_agent_service import ScriptedProvider


def test_local_mode_answers_uploaded_data_without_calling_provider(tmp_path, tiny_dataset):
    path = tmp_path / "local-uploaded.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="uploaded_local_only")
    provider = ScriptedProvider([])
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={
            "question": "分析 G1-C01 的平均分和及格率", "execution_mode": "local"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "local" and body["answer_mode"] == "verified_localized"
    assert body["status"] == "completed" and body["model_turns"] == 0
    assert len(body["evidence"]) == 1 and body["evidence"][0]["tool"] == "get_score_summary"
    assert body["evidence"][0]["data"]["average_score"] == "73.33"
    assert "73.33" in body["answer"]
    assert "Synthetic Student" not in response.text
    assert provider.requests == []


def test_local_mode_clarifies_without_evidence_or_provider(tmp_path, tiny_dataset):
    path = tmp_path / "local-clarify.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="uploaded_local_only")
    provider = ScriptedProvider([])
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={
            "question": "比较 2 班和 3 班的成绩", "execution_mode": "local"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "needs_clarification" and body["answer_mode"] == "notice"
    assert body["evidence"] == [] and body["tool_calls"] == []
    assert body["answer"] and provider.requests == []


def test_configured_mode_still_refuses_uploaded_data_before_any_provider_call(tmp_path, tiny_dataset):
    path = tmp_path / "configured-uploaded.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="uploaded_local_only")
    provider = ScriptedProvider([])
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={
            "question": "分析 G1-C01 的平均分", "execution_mode": "configured"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "agent_privacy_block"
    assert provider.requests == []
