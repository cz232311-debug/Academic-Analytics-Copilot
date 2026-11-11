"""Phase 3C semantic regressions, using only a seeded synthetic workbook.

No live provider is used. Ambiguous questions must stop before tools/models;
the explicit course contract remains exactly catalog-bound.
"""
import json

import pytest
from fastapi.testclient import TestClient

from copilot.agent.models import AgentError, SessionScope
from copilot.agent.semantics import resolve_question, validate_intent_call
from copilot.api import create_app
from copilot.data.college import parse_college_workbook
from copilot.data.importing import XLSX_TYPE
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook
from copilot.storage import Database
from tests.test_agent_service import ScriptedProvider


@pytest.fixture
def college_db(tmp_path):
    workbook = generate_college_workbook(SyntheticOptions(
        seed=20261111, cohort=2024, academic_year_start=2025, semester=1,
        majors=("software_engineering",), classes_per_major=2,
        students_per_class=6, course_count=6))
    parsed = parse_college_workbook(workbook.filename, XLSX_TYPE, workbook.content)
    assert parsed.dataset is not None
    db = Database(tmp_path / "phase3c-semantics.sqlite3")
    db.initialize()
    db.replace(parsed.dataset, source="generated", seed=20261111)
    return db


def _sports(catalog):
    return next(row for row in catalog.courses if row.course_name == "体育 III")


@pytest.mark.parametrize("question", [
    "你知道针对体育课的成绩分析嘛",
    "你能分析体育成绩吗？",
    "能否分析体育 III 的成绩分布？",
    "系统支持哪些分析功能？",
])
def test_capability_consultation_explains_scope_without_executing(college_db, question):
    intent = resolve_question(question, college_db.reporting_catalog())
    assert intent.status == "needs_clarification"
    assert "尚未执行统计" in intent.message
    assert intent.tool is None and intent.arguments == {} and intent.canonical_question == ""


@pytest.mark.parametrize("question", ["分析体育 III 的成绩分布", "分析体育III的平均分", "分析体育  III的及格率"])
def test_explicit_sports_course_keeps_exact_catalog_scope(college_db, question):
    catalog = college_db.reporting_catalog()
    intent = resolve_question(question, catalog)
    assert intent.status == "ready", intent.message
    expected = {"term": "2025-T1", "course_id": _sports(catalog).course_id,
                "class_id": None, "grade_level": None}
    assert intent.tool == "get_score_summary" and intent.arguments == expected
    assert intent.explicit["course_id"] == expected["course_id"]
    assert json.loads(intent.canonical_question.removeprefix("ACADEMIC_INTENT ")) == {
        "tool": "get_score_summary", "arguments": expected}


@pytest.mark.parametrize("question", ["分析体育课的成绩", "分析体育成绩", "体育课的成绩分布"])
def test_generic_course_requires_confirmation_even_with_one_candidate(college_db, question):
    catalog = college_db.reporting_catalog()
    intent = resolve_question(question, catalog)
    assert intent.status == "needs_clarification" and intent.tool is None
    assert intent.scope.course_id is None
    assert [choice["value"] for choice in intent.choices] == [_sports(catalog).course_id]
    assert "体育 III" in intent.choices[0]["label"]
    assert _sports(catalog).course_id not in intent.choices[0]["label"]


def test_generic_course_shows_all_catalog_candidates(college_db):
    catalog = college_db.reporting_catalog()
    extra = _sports(catalog).model_copy(update={"course_id": "I" + "a" * 24, "course_name": "体育 II"})
    catalog = catalog.model_copy(update={"courses": [*catalog.courses, extra]})
    intent = resolve_question("分析体育课的成绩分布", catalog)
    assert intent.status == "needs_clarification" and intent.tool is None
    assert {choice["value"] for choice in intent.choices} == {_sports(catalog).course_id, extra.course_id}


@pytest.mark.parametrize("question", ["分析火星考古的平均分", "分析体育课的成绩", "分析不存在的课程的成绩"])
def test_new_unknown_subject_never_reuses_previous_session_course(college_db, question):
    catalog = college_db.reporting_catalog()
    scope = SessionScope(term="2025-T1", course_id=_sports(catalog).course_id)
    intent = resolve_question(question, catalog, scope)
    assert intent.status == "needs_clarification" and intent.tool is None
    assert intent.scope.course_id is None


def test_missing_course_with_valid_cohort_and_major_does_not_become_overall_summary(college_db):
    catalog = college_db.reporting_catalog()
    assert "数据结构与算法" not in {row.course_name for row in catalog.courses}
    intent = resolve_question("2024级软工的数据结构与算法平均分", catalog)
    assert intent.status == "needs_clarification" and intent.tool is None


def test_known_course_plus_unknown_course_is_not_silently_reduced(college_db):
    intent = resolve_question("分析体育 III 和火星考古的成绩", college_db.reporting_catalog())
    assert intent.status == "needs_clarification" and intent.tool is None


def test_implicit_followup_can_use_confirmed_course_scope(college_db):
    catalog = college_db.reporting_catalog()
    scope = SessionScope(term="2025-T1", course_id=_sports(catalog).course_id)
    intent = resolve_question("分析成绩分布", catalog, scope)
    assert intent.status == "ready"
    assert intent.arguments["course_id"] == scope.course_id


@pytest.mark.parametrize("question", ["分析成绩分布", "成绩怎么样", "平均分是多少"])
def test_unscoped_chinese_metric_question_does_not_expand_to_dataset(college_db, question):
    intent = resolve_question(question, college_db.reporting_catalog())
    assert intent.status == "needs_clarification" and intent.tool is None


@pytest.mark.parametrize("question", ["分析整个数据集的成绩分布", "分析全部成绩", "2024级软工的成绩总体情况怎么样？"])
def test_explicit_overall_or_cohort_scope_can_request_dataset_summary(college_db, question):
    intent = resolve_question(question, college_db.reporting_catalog())
    assert intent.status == "ready", intent.message
    assert intent.tool == "get_score_summary" and intent.arguments["course_id"] is None


def test_compact_chinese_class_ids_are_bound_to_directory(college_db):
    catalog = college_db.reporting_catalog()
    intent = resolve_question("比较C001和C002的体育III成绩", catalog)
    assert intent.status == "ready", intent.message
    assert intent.tool == "compare_classes"
    assert intent.arguments["class_ids"] == ["C001", "C002"]
    assert intent.arguments["course_id"] == _sports(catalog).course_id


def test_explicit_course_id_adjacent_to_chinese_is_not_ignored(college_db):
    catalog = college_db.reporting_catalog()
    course_id = _sports(catalog).course_id
    intent = resolve_question(f"分析{course_id}的成绩分布", catalog)
    assert intent.status == "ready", intent.message
    assert intent.arguments["course_id"] == course_id


def test_unsupported_personal_prediction_remains_rejected(college_db):
    intent = resolve_question("预测某位同学下学期会不会挂科", college_db.reporting_catalog())
    assert intent.status == "unsupported" and intent.tool is None


def test_mismatched_course_and_extra_tool_remain_blocked(college_db):
    catalog = college_db.reporting_catalog()
    intent = resolve_question("分析体育 III 的成绩分布", catalog)
    other = next(row.course_id for row in catalog.courses if row.course_id != intent.arguments["course_id"])
    for tool, args in (("get_score_summary", {**intent.arguments, "course_id": other}), ("get_catalog", {})):
        with pytest.raises(AgentError) as error:
            validate_intent_call(intent, tool, args)
        assert error.value.code == "intent_mismatch"


@pytest.mark.parametrize("question", ["你知道针对体育课的成绩分析嘛", "分析体育课的成绩"])
def test_notice_has_no_provider_request_tool_call_or_evidence(college_db, question):
    provider = ScriptedProvider([])
    with TestClient(create_app(college_db.path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": question, "execution_mode": "grounded"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "needs_clarification" and body["answer_mode"] == "notice"
    assert body["model_turns"] == 0 and body["tool_calls"] == [] and body["evidence"] == []
    assert provider.requests == []
