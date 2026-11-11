"""Scripted/offline proof of the new contract; no live model calls."""
import asyncio
import json
from decimal import Decimal

import pytest

from copilot.agent.analysis import (InvalidAnalysis, analysis_context, analysis_goal, analysis_requirements,
                                    claim_catalog, render_analysis)
from copilot.agent.provider import MockDemoProvider
from copilot.agent.semantics import resolve_question
from copilot.agent.service import AgentService
from copilot.agent.tools import ToolRegistry
from copilot.data.college import parse_college_workbook
from copilot.data.importing import XLSX_TYPE
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook
from copilot.storage import Database
from tests.test_agent_service import ScriptedProvider, agent_db, final_turn, tool_turn


def valid_analysis(*, observations=None, findings=None, suggestions=None):
    """Reusable scripted final-selection body for the single-summary protocol."""
    return {"version": "evidence_analysis_v1", "sections": [
        {"kind": "findings", "claim_ids": findings or ["r1.scope", "r1.sample", "r1.mean", "r1.pass_rate"], "style": "plain"},
        {"kind": "observations", "claim_ids": observations or ["r1.dominant_band", "r1.high_band"], "style": "focus"},
        {"kind": "limitations", "claim_ids": ["r1.limits"], "style": "plain"},
        {"kind": "suggestions", "claim_ids": suggestions or ["r1.review_distribution"], "style": "plain"},
    ]}


@pytest.fixture
def sports_db(tmp_path):
    options = SyntheticOptions(classes_per_major=2, students_per_class=6, course_count=6, seed=20261111)
    workbook = generate_college_workbook(options)
    parsed = parse_college_workbook(workbook.filename, XLSX_TYPE, workbook.content)
    assert parsed.dataset is not None
    db = Database(tmp_path / "sports-evidence.sqlite3")
    db.initialize()
    db.replace(parsed.dataset, source="generated", seed=options.seed)
    return db


def summary_evidence(db, question="分析体育 III 的成绩分布"):
    catalog = db.reporting_catalog()
    intent = resolve_question(question, catalog)
    assert intent.status == "ready", intent.message
    registry = ToolRegistry(db)
    args = registry.validate(intent.tool, json.dumps(intent.arguments))
    return registry.execute(intent.tool, args, version=catalog.dataset_version, evidence_id="r1"), catalog, intent


def run_script(db, analysis=None, *, question="分析体育 III 的成绩分布", extra=None):
    _, _, intent = summary_evidence(db, question)
    provider = ScriptedProvider([tool_turn(intent.tool, intent.arguments), final_turn(analysis=analysis, **(extra or {}))])
    result = asyncio.run(AgentService(db, provider).query(question, intent=intent, execution_mode="grounded"))
    return result, provider


def test_sports_distribution_catalog_derives_hand_checkable_ratios(sports_db):
    evidence, catalog, _ = summary_evidence(sports_db)
    assert evidence.data["graded_count"] == 12
    assert evidence.data["average_score"] == "85.05"
    assert evidence.data["pass_rate_percent"] == "100.00"
    claims = {claim.identifier: claim for claim in claim_catalog([evidence], catalog)}
    # Independent arithmetic using the five observed band counts 0,0,5,3,4.
    dominant, high = claims["r1.dominant_band"], claims["r1.high_band"]
    assert dominant.facts == {"bands": ["[70,80)"], "count_each": 5, "percent_each": "41.67"}
    assert high.facts == {"threshold_cents": 8000, "count": 7, "percent": "58.33"}
    assert Decimal(high.facts["percent"]) == (Decimal(7) / 12 * 100).quantize(Decimal("0.01"))
    assert not any("comparison" in claim.tags for claim in claims.values())


def test_verified_chinese_analysis_reaches_response_with_scope_sample_limits(sports_db):
    result, provider = run_script(sports_db, valid_analysis())
    assert result.analysis_status == "verified" and result.answer_mode == "evidence_analysis"
    assert result.analysis_fallback_reason is None and result.analysis.goal == "distribution"
    assert {section.kind for section in result.analysis.sections} == {"findings", "observations", "limitations", "suggestions"}
    for fact in ("体育 III", "12条", "85.05", "100.00%", "41.67%", "58.33%", "样本较小", "建议"):
        assert fact in result.answer
    assert result.dataset_version not in result.answer
    assert result.evidence[0].arguments["course_id"] not in result.answer
    assert all(section.evidence_ids == ["r1"] for section in result.analysis.sections)
    payload = json.loads(provider.requests[1][-1]["content"])
    assert payload["analysis_context"]["goal"] == "distribution"
    assert "分析体育 III 的成绩分布" not in json.dumps(provider.requests, ensure_ascii=False)
    assert "student_id" not in json.dumps(provider.requests)
    assert "evidence_analysis_v1" in provider.requests[0][0]["content"]


@pytest.mark.parametrize("mutation", [
    lambda value: value["sections"][1].update(text="教师教学好所以及格率100%"),
    lambda value: value["sections"][1].update(claim_ids=["r1.mean_comparison"]),
    lambda value: value["sections"][1].update(claim_ids=["r1.teaching_caused_success"]),
    lambda value: value["sections"][1].update(claim_ids=["r1.mean_99_99"]),
    lambda value: value["sections"][1].update(claim_ids=["r6.dominant_band"]),
    lambda value: value["sections"][0].update(claim_ids=["r1.mean", "r1.pass_rate"]),
    lambda value: value["sections"][2].update(claim_ids=["r1.mean"]),
    lambda value: value["sections"][1].update(claim_ids=["r1.pass_pattern"]),
    lambda value: value["sections"][1].update(claim_ids=["r1.dominant_band", "r1.dominant_band"]),
    lambda value: value.update(new_value=99.99),
])
def test_fabrications_uncomputed_relations_and_missing_required_claims_fall_back(sports_db, mutation):
    value = valid_analysis()
    mutation(value)
    result, _ = run_script(sports_db, value)
    assert result.analysis_status == "fallback" and result.answer_mode == "deterministic_fallback"
    assert result.analysis_fallback_reason == "invalid_analysis" and result.analysis is None
    assert "85.05" in result.answer and "99.99" not in result.answer
    assert "教学好" not in result.answer and "teaching_caused" not in result.answer
    assert result.status == "completed"  # the statistic succeeded; the AI analysis did not


@pytest.mark.parametrize("analysis", [None, "平均分99.99", [], {"version": "other"}])
def test_missing_or_invalid_analysis_is_a_visible_fallback(sports_db, analysis):
    result, _ = run_script(sports_db, analysis)
    assert result.analysis_status == "fallback" and result.analysis is None
    assert result.analysis_fallback_reason == ("missing_analysis" if analysis is None else "invalid_analysis")


def test_default_mock_does_not_impersonate_new_ai_synthesis(sports_db):
    _, _, intent = summary_evidence(sports_db)
    result = asyncio.run(AgentService(sports_db, MockDemoProvider()).query(
        "分析体育 III 的成绩分布", intent=intent, execution_mode="grounded"))
    assert result.simulated is True and result.model_turns == 2
    assert result.analysis_status == "fallback" and result.analysis_fallback_reason == "missing_analysis"
    assert result.answer_mode == "deterministic_fallback"


def test_legacy_configured_english_contract_keeps_narrative(agent_db):
    provider = ScriptedProvider([tool_turn(), final_turn(narrative=["For {{r1.scope}}, {{r1.mean}}."])])
    result = asyncio.run(AgentService(agent_db, provider).query("Average score for G1-C01"))
    assert result.answer_mode == "grounded_narrative" and result.analysis_status == "not_requested"
    assert "English analysis" in provider.requests[0][0]["content"]
    assert "analysis_context" not in json.loads(provider.requests[1][-1]["content"])


def test_mixed_legacy_free_text_and_analysis_protocol_is_not_verified(sports_db):
    result, _ = run_script(sports_db, valid_analysis(), extra={"narrative": ["This proves great teaching."]})
    assert result.analysis_status == "fallback" and "great teaching" not in result.answer


def test_empty_effective_sample_does_not_become_a_zero_mean(agent_db):
    evidence, catalog, _ = summary_evidence(agent_db, "分析 G2-C01 的成绩分布")
    selected = valid_analysis(observations=["r1.no_distribution"], findings=["r1.scope", "r1.sample"], suggestions=["r1.review_records"])
    answer, verified = render_analysis(selected, [evidence], catalog, "distribution")
    assert "均分和及格率不可用" in answer and "0.00%" not in answer
    assert verified.goal == "distribution"


def test_comparison_uses_exact_sums_then_rounding_and_only_its_authorized_classes(agent_db):
    evidence, catalog, _ = summary_evidence(agent_db, "比较 C001 和 C002 的 G1-C01 成绩")
    context = analysis_context([evidence], catalog, "comparison")
    relation = next(claim for claim in context["claims"] if claim["claim_id"] == "r1.mean_comparison")
    # C001=(60+59.99)/2=59.995; C002=100. Difference=40.005 -> 40.01,
    # rather than subtracting display-rounded 60.00 from 100.00 (=40.00).
    assert relation["facts"]["difference_points"] == "40.01"
    assert relation["facts"]["highest_class_ids"] == ["C002"]
    assert relation["facts"]["lowest_class_ids"] == ["C001"]
    value = valid_analysis(observations=["r1.mean_comparison"], findings=["r1.scope", "r1.sample"], suggestions=["r1.review_comparison"])
    answer, _ = render_analysis(value, [evidence], catalog, "comparison")
    assert "40.01分" in answer and "Synthetic Class 2" in answer
    assert "C001" not in answer and "显著性" in answer
    value["sections"][1]["claim_ids"] = ["r1.C001.dominant_band"]
    with pytest.raises(InvalidAnalysis, match="missing_comparison"):
        render_analysis(value, [evidence], catalog, "comparison")


@pytest.mark.parametrize("goal", ["mean", "pass", "missing", "summary"])
def test_explicit_metric_cannot_be_replaced_by_an_unrelated_distribution(sports_db, goal):
    evidence, catalog, _ = summary_evidence(sports_db)
    value = valid_analysis(findings=["r1.scope", "r1.sample"])
    with pytest.raises(InvalidAnalysis, match="missing_requested_metric"):
        render_analysis(value, [evidence], catalog, goal)
    target = {"mean": ["r1.mean"], "pass": ["r1.pass_rate"], "missing": ["r1.status"], "summary": ["r1.mean", "r1.pass_rate"]}[goal]
    value["sections"][0]["claim_ids"].extend(target)
    _, result = render_analysis(value, [evidence], catalog, goal)
    assert result.goal == goal


@pytest.mark.parametrize("goal", ["mean", "pass", "summary"])
def test_unavailable_metric_is_explicit_for_zero_effective_sample(agent_db, goal):
    evidence, catalog, _ = summary_evidence(agent_db, "分析 G2-C01 的成绩分布")
    value = valid_analysis(findings=["r1.scope", "r1.sample"], observations=["r1.no_distribution"], suggestions=["r1.review_records"])
    answer, _ = render_analysis(value, [evidence], catalog, goal)
    assert "均分和及格率不可用" in answer


@pytest.mark.parametrize("where", ["evidence", "data", "catalog"])
def test_stale_evidence_or_catalog_cannot_be_rendered_as_verified(sports_db, where):
    evidence, catalog, _ = summary_evidence(sports_db)
    if where == "evidence":
        evidence = evidence.model_copy(update={"dataset_version": "0" * 64})
    elif where == "data":
        evidence = evidence.model_copy(update={"data": {**evidence.data, "dataset_version": "0" * 64}})
    else:
        catalog = catalog.model_copy(update={"dataset_version": "0" * 64})
    with pytest.raises(InvalidAnalysis, match="evidence_version_mismatch"):
        render_analysis(valid_analysis(), [evidence], catalog, "distribution")


def test_clarification_includes_readable_candidates_without_executing_them(sports_db):
    question = "分析体育课的成绩"
    intent = resolve_question(question, sports_db.reporting_catalog())
    assert intent.status == "needs_clarification"
    provider = ScriptedProvider([])
    result = asyncio.run(AgentService(sports_db, provider).query(question, intent=intent, execution_mode="grounded"))
    assert "体育 III" in result.answer and "可选范围" in result.answer
    assert all(choice["value"] not in result.answer for choice in intent.choices)
    assert not provider.requests and not result.tool_calls


def test_directory_analysis_names_actual_courses_and_classes_without_internal_ids(sports_db):
    question = "当前有哪些课程"
    evidence, catalog, intent = summary_evidence(sports_db, question)
    value = valid_analysis(findings=["r1.scope", "r1.sample"], observations=["r1.catalog_entries"], suggestions=["r1.review_scope"])
    provider = ScriptedProvider([tool_turn(intent.tool, intent.arguments), final_turn(analysis=value)])
    result = asyncio.run(AgentService(sports_db, provider).query(question, intent=intent, execution_mode="grounded"))
    assert result.analysis_status == "verified" and result.analysis.goal == "catalog"
    assert "体育 III" in result.answer and "Web技术基础" in result.answer
    assert catalog.classes[0].class_label in result.answer
    assert all(row.course_id not in result.answer for row in catalog.courses)
    value["sections"][1]["claim_ids"] = ["r1.coverage"]
    with pytest.raises(InvalidAnalysis, match="missing_scope_sample_or_limit"):
        render_analysis(value, [evidence], catalog, "catalog")


def test_explicit_combined_metrics_are_not_lost_behind_distribution_goal(sports_db):
    question = "分析体育 III 的平均分、及格率和成绩分布"
    assert analysis_goal(question, "get_score_summary") == "distribution"
    assert analysis_requirements(question) == ("mean", "pass")
    value = valid_analysis(findings=["r1.scope", "r1.sample"])
    result, provider = run_script(sports_db, value, question=question)
    assert result.analysis_status == "fallback" and result.analysis_fallback_reason == "invalid_analysis"
    context = json.loads(provider.requests[1][-1]["content"])["analysis_context"]
    assert context["required_goal_tags"] == ["mean", "pass"]
    value["sections"][0]["claim_ids"].extend(["r1.mean", "r1.pass_rate"])
    result, _ = run_script(sports_db, value, question=question)
    assert result.analysis_status == "verified"


def test_internal_unknown_required_metric_cannot_relax_the_analysis_contract(sports_db):
    evidence, catalog, _ = summary_evidence(sports_db)
    with pytest.raises(InvalidAnalysis, match="unknown_required_metric"):
        render_analysis(valid_analysis(), [evidence], catalog, "distribution", ("invented_metric",))


@pytest.mark.parametrize("question,tool,expected", [
    ("张三的成绩分布", "get_score_summary", "distribution"),
    ("检查缺考", "get_score_summary", "missing"),
    ("看及格率", "get_score_summary", "pass"),
    ("看平均分", "get_score_summary", "mean"),
    ("看平均分及格率", "get_score_summary", "summary"),
    ("比较分布", "compare_classes", "comparison"),
    ("查询概览", "get_dataset_overview", "overview"),
    ("课程目录", "get_catalog", "catalog"),
])
def test_goal_extractor_emits_only_a_safe_enum(question, tool, expected):
    assert analysis_goal(question, tool) == expected
