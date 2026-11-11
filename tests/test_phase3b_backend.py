"""Phase 3B backend contracts: client payloads and the extra-tool trust boundary.

All providers are Mock or scripted transports. No live DeepSeek call is made.
"""
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from copilot.agent.models import AgentError
from copilot.agent.semantics import resolve_question, validate_intent_call
from copilot.agent.tools import ToolRegistry
from copilot.api import create_app
from copilot.dashboard.client import DashboardClient, DashboardError
from copilot.data.synthetic_college import SyntheticOptions
from copilot.storage import Database
from tests.college_fixture import college_workbook
from tests.test_agent_service import ScriptedProvider, final_turn, tool_turn

SUMMARY_CN = "分析 G1-C01 的平均分和及格率"
CATALOG_CN = "查询当前数据集有哪些课程"
OPTIONS = SyntheticOptions(classes_per_major=2, students_per_class=3, course_count=4)


@pytest.fixture
def backend(tmp_path):
    """Real FastAPI app reached through a typed client with a Mock transport."""
    store = Database(tmp_path / "phase3b.sqlite3")
    seen = []
    with TestClient(create_app(store.path, bootstrap_demo=False)) as api:
        def transport(request):
            seen.append(request)
            response = api.request(request.method, str(request.url), content=request.content, headers=request.headers)
            return httpx.Response(response.status_code, content=response.content, headers=response.headers)
        client = DashboardClient("http://testserver", transport=httpx.MockTransport(transport))
        yield client, store, seen


def activate_synthetic(client):
    preview = client.synthetic_preview(OPTIONS)
    assert preview.can_import and preview.preview_token
    result = client.synthetic_commit(OPTIONS, preview.preview_token)
    return result.dataset


def payloads(seen, path):
    return [json.loads(request.content) for request in seen if request.url.path == path and request.content]


# --- 1. Server-derived synthetic activation (never a client-faked source) ---

def test_synthetic_activation_is_server_side_and_grants_generated_provenance(backend):
    client, store, seen = backend
    assert not store.has_dataset()
    dataset = activate_synthetic(client)
    assert dataset.source == "generated" and dataset.external_llm_allowed is True
    assert dataset.seed == OPTIONS.seed
    preview_bodies = payloads(seen, "/api/datasets/synthetic/preview")
    commit_bodies = payloads(seen, "/api/datasets/synthetic/commit")
    assert len(preview_bodies) == len(commit_bodies) == 1
    assert preview_bodies[0]["classes_per_major"] == 2
    assert "source" not in preview_bodies[0] and "workbook" not in preview_bodies[0]
    assert "source" not in commit_bodies[0] and commit_bodies[0]["confirmed"] is True
    assert set(commit_bodies[0]) == {"options", "preview_token", "confirmed"}


# --- 2. Chat-first client sends explicit grounded mode, scope and version ---

def test_resolve_forwards_grounded_scope_and_expected_version(backend):
    client, _, seen = backend
    dataset = activate_synthetic(client)
    intent = client.resolve(CATALOG_CN, execution_mode="grounded", expected_version=dataset.dataset_version)
    assert intent.status == "ready" and intent.tool == "get_catalog"
    body = payloads(seen, "/api/agent/resolve")[-1]
    assert body == {"question": CATALOG_CN, "execution_mode": "grounded",
                    "expected_version": dataset.dataset_version}


def test_grounded_query_forwards_mode_and_returns_real_evidence(backend):
    client, _, seen = backend
    dataset = activate_synthetic(client)
    result = client.query(CATALOG_CN, execution_mode="grounded", expected_version=dataset.dataset_version)
    body = payloads(seen, "/api/agent/query")[-1]
    assert body["execution_mode"] == "grounded"
    assert body["expected_version"] == dataset.dataset_version
    assert result.status == "completed" and result.simulated is True
    assert result.evidence and result.evidence[0].tool == "get_catalog"
    assert result.evidence[0].dataset_version == dataset.dataset_version


def test_legacy_query_contract_still_sends_only_question_field(backend):
    client, _, seen = backend
    activate_synthetic(client)
    try:
        client.query("Average score")
    except DashboardError:
        pass
    assert payloads(seen, "/api/agent/query")[-1] == {"question": "Average score"}


# --- 3. Uploaded data must stay on the local deterministic path ---

def test_uploaded_dataset_only_answers_locally_and_blocks_grounded(backend):
    client, store, seen = backend
    activate_synthetic(client)
    content = college_workbook()
    preview = client.college_preview("upload.xlsx", content)
    result = client.college_commit("upload.xlsx", content, preview_token=preview.preview_token)
    assert result.dataset.source == "uploaded_local_only"
    version = result.dataset.dataset_version
    answer = client.query(CATALOG_CN, execution_mode="local", expected_version=version)
    assert answer.status == "completed" and answer.provider == "local"
    assert answer.model_turns == 0 and answer.answer_mode == "verified_localized"
    assert answer.evidence[0].dataset_version == version
    with pytest.raises(DashboardError) as error:
        client.query(CATALOG_CN, execution_mode="grounded", expected_version=version)
    assert error.value.code == "agent_privacy_block"
    bodies = payloads(seen, "/api/agent/query")
    assert bodies[-2]["execution_mode"] == "local" and bodies[-2]["expected_version"] == version
    assert bodies[-1]["execution_mode"] == "grounded"


# --- 4. Errors are Chinese, actionable and never echo internals ---

@pytest.mark.parametrize("code,status", [("intent_mismatch", 422), ("dataset_changed", 409),
                                          ("unknown_entity", 404), ("agent_privacy_block", 403),
                                          ("llm_configuration_error", 503), ("llm_timeout", 504),
                                          ("invalid_final_selection", 502), ("model_refused", 422)])
def test_agent_errors_have_chinese_actionable_messages(code, status):
    def respond(request):
        return httpx.Response(status, json={"error": {"code": code, "message": "private server detail"},
                                            "request_id": "safe-request-1"})
    client = DashboardClient(transport=httpx.MockTransport(respond))
    with pytest.raises(DashboardError) as error:
        client.query(CATALOG_CN, execution_mode="grounded")
    message = str(error.value)
    assert error.value.code == code and error.value.request_id == "safe-request-1"
    assert re.search(r"[\u4e00-\u9fff]", message)
    assert "private" not in message and "Traceback" not in message and "private server detail" not in message


def test_transport_and_timeout_errors_stay_actionable():
    for exception in (httpx.ConnectError("private host"), httpx.ReadTimeout("private deadline")):
        with pytest.raises(DashboardError) as error:
            DashboardClient(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(exception))).health()
        assert "private" not in str(error.value) and re.search(r"[\u4e00-\u9fff]", str(error.value))


# --- 5. validate_intent_call must reject extra tool calls, not only wrong IDs ---

def test_extra_get_catalog_call_is_rejected_even_when_arguments_match_scope(tmp_path, tiny_dataset):
    db = Database(tmp_path / "catalog-guard.sqlite3")
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    catalog = db.reporting_catalog()
    intent = resolve_question(SUMMARY_CN, catalog, None)
    assert intent.status == "ready" and intent.tool == "get_score_summary"
    side_args = ToolRegistry(db).validate("get_catalog", json.dumps({"grade_level": None, "major": None}))
    with pytest.raises(AgentError) as error:
        validate_intent_call(intent, "get_catalog", side_args)
    assert error.value.code == "intent_mismatch"
    # The exact resolved plan still passes; the fix must not block the real call.
    validate_intent_call(intent, "get_score_summary",
                         ToolRegistry(db).validate("get_score_summary", json.dumps(intent.arguments)))


def test_catalog_intent_still_allows_its_own_catalog_call(tmp_path, tiny_dataset):
    db = Database(tmp_path / "catalog-intent.sqlite3")
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    intent = resolve_question(CATALOG_CN, db.reporting_catalog(), None)
    assert intent.status == "ready" and intent.tool == "get_catalog"
    args = ToolRegistry(db).validate("get_catalog", json.dumps(intent.arguments))
    validate_intent_call(intent, "get_catalog", args)


def test_grounded_loop_blocks_a_model_initiated_catalog_side_query(tmp_path, tiny_dataset):
    """End-to-end: a tool the user never requested must not execute."""
    provider = ScriptedProvider([tool_turn("get_catalog", args={"grade_level": None, "major": None},
                                           call_id="side_1"), tool_turn(), final_turn()])
    db = Database(tmp_path / "side-query.sqlite3")
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(db.path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": SUMMARY_CN, "execution_mode": "grounded"})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "intent_mismatch"
    assert body["tool_calls"][0]["tool"] == "get_catalog"
    assert body["tool_calls"][0]["status"] == "error"
    assert body["tool_calls"][0]["error_code"] == "intent_mismatch"
    assert body["tool_calls"][0]["evidence_id"] is None
