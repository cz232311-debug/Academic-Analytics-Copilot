"""Phase 3A: catalog-bound semantic intent drives the configured model loop.

All providers here are Mock or a mocked HTTP transport. No live DeepSeek call
is made. The tests cover the trust chain:
Chinese prose -> local ResolvedIntent -> ASCII ACADEMIC_INTENT -> model tool
call -> validate_intent_call -> deterministic tool -> Evidence -> answer.
"""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from copilot.agent.models import AgentError
from copilot.agent.provider import OpenAICompatibleProvider
from copilot.agent.semantics import resolve_question, validate_intent_call
from copilot.agent.service import AgentService
from copilot.agent.tools import ToolRegistry
from copilot.api import create_app
from copilot.data.college import parse_college_workbook
from copilot.data.importing import XLSX_TYPE
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook
from copilot.storage import Database
from tests.test_agent_service import (ScriptedProvider, agent_db, final_turn, tool_turn)
from tests.test_llm_provider import completion, settings

SUMMARY_CN = "分析 G1-C01 的平均分和及格率"
SUMMARY_ARGS = {"term": "2026-T1", "course_id": "G1-C01", "class_id": None, "grade_level": None}
DRIFT_ARGS = {"term": "2026-T1", "course_id": "G1-C02", "class_id": None, "grade_level": None}


@pytest.fixture
def tiny_client(tmp_path, tiny_dataset):
    path = tmp_path / "grounded-tiny.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False)) as client:
        yield client


def _college_dataset():
    options = SyntheticOptions(cohort=2024, academic_year_start=2025, semester=1,
                               majors=("software_engineering",), classes_per_major=2,
                               students_per_class=3, course_count=4)
    workbook = generate_college_workbook(options)
    parsed = parse_college_workbook(workbook.filename, XLSX_TYPE, workbook.content)
    assert parsed.dataset is not None, "the server-generated workbook must be importable"
    return parsed.dataset


@pytest.fixture
def college_client(tmp_path):
    path = tmp_path / "grounded-college.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(_college_dataset(), source="generated")
    with TestClient(create_app(path, bootstrap_demo=False)) as client:
        yield client


@pytest.fixture
def college_db(tmp_path):
    db = Database(tmp_path / "grounded-college-service.sqlite3")
    db.initialize()
    db.replace(_college_dataset(), source="generated")
    return db


# --- 1. Chinese prose is compiled locally; only the verified plan is sent ---

def test_chinese_question_sends_only_verified_intent_to_provider(tmp_path, tiny_dataset):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        message = (tool_turn() if len(seen) == 1 else final_turn()).message.model_dump(mode="json")
        return httpx.Response(200, json=completion(message, finish="tool_calls" if len(seen) == 1 else "stop"))

    provider = OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(handler))
    path = tmp_path / "grounded-transport.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": SUMMARY_CN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed" and body["simulated"] is False
    assert body["tool_calls"][0]["tool"] == "get_score_summary"
    assert body["tool_calls"][0]["arguments"]["course_id"] == "G1-C01"
    assert body["evidence"][0]["data"]["average_score"] == "73.33"
    assert "73.33" in body["answer"] and "99.99" not in body["answer"]
    assert body["resolved_scope"]["course_id"] == "G1-C01"

    user_content = seen[0]["messages"][1]["content"]
    assert user_content.startswith("ACADEMIC_INTENT ")
    plan = json.loads(user_content.removeprefix("ACADEMIC_INTENT "))
    assert plan == {"tool": "get_score_summary", "arguments": SUMMARY_ARGS}
    outbound = json.dumps(seen, ensure_ascii=False)
    for private in ("分析", "平均分", "及格率", "Synthetic Student", "student_id"):
        assert private not in outbound
    assert "fake-test-key" not in response.text


def test_grounded_mode_can_be_selected_explicitly(tiny_client):
    response = tiny_client.post("/api/agent/query", json={"question": SUMMARY_CN,
                                                          "execution_mode": "grounded"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.json()["evidence"][0]["data"]["average_score"] == "73.33"


def test_english_default_path_still_uses_canonical_question(tmp_path, tiny_dataset):
    provider = ScriptedProvider([tool_turn(), final_turn()])
    path = tmp_path / "grounded-legacy.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={
            "question": "What's the average score and pass rate for G1-C01?"})
    assert response.status_code == 200, response.text
    assert provider.requests[0][1]["content"] == "what is the average score and pass rate for g1-c01"


# --- 2. Chinese entities resolve against the active catalog only ---

def test_resolve_endpoint_maps_chinese_entities_to_catalog_ids(college_client):
    payload = {"question": "比较 2024 级软件工程 1 班与 2 班的高等数学 (I)-1 成绩",
               "execution_mode": "grounded"}
    response = college_client.post("/api/agent/resolve", json=payload)
    assert response.status_code == 200, response.text
    intent = response.json()
    assert intent["status"] == "ready" and intent["tool"] == "compare_classes"
    assert intent["arguments"]["class_ids"] == ["C001", "C002"]
    assert intent["arguments"]["term"] == "2025-T1"
    catalog = college_client.get("/api/reports/catalog").json()
    assert intent["arguments"]["course_id"] in {row["course_id"] for row in catalog["courses"]}
    assert "比较" not in intent["canonical_question"] and "软件工程" not in intent["canonical_question"]


@pytest.mark.parametrize("question,expected_tool", [
    ("分析高等数学 (I)-1 的平均分", "get_score_summary"),
    ("2024 级软件工程的成绩总体情况怎么样？", "get_score_summary"),
    ("2024级软工的Web技术基础平均分", "get_score_summary"),
    ("查询当前数据集有哪些课程", "get_catalog"),
    ("数据概览", "get_dataset_overview"),
])
def test_chinese_aliases_and_metrics_select_the_expected_tool(college_db, question, expected_tool):
    catalog = college_db.reporting_catalog()
    intent = resolve_question(question, catalog)
    assert intent.status == "ready", (question, intent.message)
    assert intent.tool == expected_tool
    assert intent.canonical_question.startswith("ACADEMIC_INTENT ")
    assert intent.canonical_question.isascii()


def test_chinese_grade_and_major_are_bound_to_real_catalog_values(college_db):
    intent = resolve_question("2024 级软件工程的成绩总体情况怎么样？", college_db.reporting_catalog())
    assert intent.arguments["grade_level"] == 2024
    assert intent.arguments["major"] == "software_engineering"
    assert intent.explicit["grade_level"] == 2024


def test_grounded_chinese_comparison_executes_real_tool_with_matching_evidence(college_client):
    response = college_client.post("/api/agent/query", json={
        "question": "比较 2024 级软件工程 1 班与 2 班的高等数学 (I)-1 成绩",
        "execution_mode": "grounded"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["tool_calls"][0]["tool"] == "compare_classes"
    assert body["evidence"][0]["tool"] == "compare_classes"
    assert body["evidence"][0]["arguments"] == body["tool_calls"][0]["arguments"]
    assert body["evidence"][0]["dataset_version"] == body["dataset_version"]


# --- 3. A structurally valid but semantically different model call is blocked ---

def test_model_course_drift_is_blocked_before_any_statistic(tmp_path, tiny_dataset):
    provider = ScriptedProvider([tool_turn(args=DRIFT_ARGS)])
    path = tmp_path / "grounded-drift.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": SUMMARY_CN})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "intent_mismatch"
    assert len(provider.requests) == 1  # the model answered, the server refused
    assert response.json()["tool_calls"][0]["status"] == "error"
    assert response.json()["tool_calls"][0]["error_code"] == "intent_mismatch"
    assert "73.33" not in response.text and "evidence" not in response.json()


def test_model_wrong_tool_is_blocked(tmp_path, tiny_dataset):
    provider = ScriptedProvider([tool_turn("get_dataset_overview", {})])
    path = tmp_path / "grounded-wrong-tool.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": SUMMARY_CN})
    assert response.status_code == 422 and response.json()["error"]["code"] == "intent_mismatch"


@pytest.mark.parametrize("args", [
    {"term": "2026-T1", "course_id": "G1-C01", "class_id": "C002", "grade_level": None},
    {"term": "2025-T1", "course_id": "G1-C01", "class_id": None, "grade_level": None},
])
def test_model_may_not_change_class_or_term(tmp_path, tiny_dataset, args):
    provider = ScriptedProvider([tool_turn(args=args)])
    path = tmp_path / "grounded-drift-fields.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": SUMMARY_CN})
    assert response.status_code == 422 and response.json()["error"]["code"] == "intent_mismatch"


# --- 4. Ambiguity, unknown entities and unsupported requests are notices ---

@pytest.mark.parametrize("question,fragment", [
    ("比较 2 班和 3 班的成绩", "班级"),
    ("分析 G1-C99 的平均分", "课程"),
    ("分析不存在的课程的平均分", "课程"),
    ("查一下张三的学号和个人成绩", "支持"),
    ("2024 级网络工程的成绩怎么样", "专业"),
])
def test_unresolvable_chinese_questions_return_notices_without_a_model_call(tmp_path, tiny_dataset, question, fragment):
    provider = ScriptedProvider([])
    path = tmp_path / "grounded-notice.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": question})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] in {"needs_clarification", "unsupported"}
    assert body["answer_mode"] == "notice" and fragment in body["answer"]
    assert body["evidence"] == [] and body["tool_calls"] == [] and body["model_turns"] == 0
    assert body["provider"] == "semantic" and body["simulated"] is False
    assert provider.requests == []


def test_relative_term_needs_an_explicit_choice(tiny_client):
    response = tiny_client.post("/api/agent/query", json={"question": "这学期的平均分"})
    assert response.status_code == 200
    assert response.json()["status"] == "needs_clarification"
    assert response.json()["evidence"] == []


def test_confirmed_session_scope_is_reused_and_returned(tiny_client):
    response = tiny_client.post("/api/agent/query", json={
        "question": "分析 G1-C01 的平均分", "execution_mode": "grounded",
        "scope": {"term": "2026-T1", "grade_level": 1}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["resolved_scope"]["term"] == "2026-T1"
    assert body["resolved_scope"]["grade_level"] == 1
    assert body["resolved_scope"]["course_id"] == "G1-C01"


def test_explicit_course_in_question_is_never_silently_replaced(college_db):
    catalog = college_db.reporting_catalog()
    intent = resolve_question("分析高等数学 (I)-1 的平均分", catalog)
    assert intent.status == "ready"
    assert intent.explicit["course_id"] == intent.arguments["course_id"]
    assert intent.arguments["course_id"] in {row.course_id for row in catalog.courses}
    # The user's course is recorded as explicit, so a model proposing any
    # other course fails validate_intent_call even if it is a valid ID.
    other = next(row.course_id for row in catalog.courses if row.course_id != intent.arguments["course_id"])
    with pytest.raises(AgentError) as error:
        args = ToolRegistry(college_db).validate("get_score_summary", json.dumps(
            {**intent.arguments, "course_id": other}))
        validate_intent_call(intent, "get_score_summary", args)
    assert error.value.code == "intent_mismatch"


# --- 5. Version and privacy boundaries ---

def test_stale_expected_version_is_rejected_before_the_model(tiny_client):
    response = tiny_client.post("/api/agent/query", json={"question": SUMMARY_CN,
                                                          "expected_version": "0" * 64})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dataset_changed"


def test_stale_resolved_intent_version_is_rejected(agent_db):
    intent = resolve_question(SUMMARY_CN, agent_db.reporting_catalog())
    assert intent.status == "ready"
    stale = intent.model_copy(update={"dataset_version": "0" * 64})
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, ScriptedProvider([])).query(
            SUMMARY_CN, intent=stale, execution_mode="grounded"))
    assert error.value.code == "dataset_changed" and error.value.status == 409


def test_uploaded_data_never_reaches_the_configured_provider(tmp_path, tiny_dataset):
    path = tmp_path / "grounded-uploaded.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="uploaded_local_only")
    provider = ScriptedProvider([])
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": SUMMARY_CN,
                                                         "execution_mode": "grounded"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "agent_privacy_block"
    assert provider.requests == []


def test_uploaded_data_local_answer_is_not_impersonated_as_deepseek(tmp_path, tiny_dataset):
    path = tmp_path / "grounded-local-label.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="uploaded_local_only")
    with TestClient(create_app(path, bootstrap_demo=False)) as client:
        response = client.post("/api/agent/query", json={"question": SUMMARY_CN,
                                                         "execution_mode": "local"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "local" and body["model"] == "deterministic-tools-v1"
    assert body["answer_mode"] == "verified_localized" and body["model_turns"] == 0
    assert body["status"] == "completed" and body["evidence"][0]["data"]["average_score"] == "73.33"


# --- 6. Model failures and fabricated numbers stay visible and harmless ---

def test_provider_error_is_reported_without_fabricated_statistics(tmp_path, tiny_dataset):
    provider = ScriptedProvider([AgentError("llm_http_error", "LLM provider rejected the request; check server configuration")])
    path = tmp_path / "grounded-provider-error.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": SUMMARY_CN})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_http_error"
    assert "73.33" not in response.text and "66.67" not in response.text


def test_grounded_path_still_rejects_fabricated_narrative_numbers(tmp_path, tiny_dataset):
    provider = ScriptedProvider([tool_turn(), final_turn(narrative=["For {{r1.scope}}, the average was 99.99."])])
    path = tmp_path / "grounded-fabrication.sqlite3"
    db = Database(path)
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": SUMMARY_CN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer_mode"] == "deterministic_fallback"
    assert "73.33" in body["answer"] and "99.99" not in body["answer"]
    assert body["evidence"][0]["dataset_version"] == body["dataset_version"]
