"""Phase 3D — offline Agent Evaluation baseline (20 representative cases).

This measures the LOCAL semantic resolver, the evidence-analysis protocol
validator and the server renderer. It does NOT measure a real model: every
"model" reply is a scripted in-process fixture. Consequently these pass rates
must never be reported as DeepSeek accuracy. The baseline exists so that an
authorised live run can be compared against an exactly reproducible offline
reference, case by case.

Zero network calls, zero paid calls, no credentials, synthetic data only.
"""
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

from copilot.agent.models import ModelMessage, ModelTurn
from copilot.agent.semantics import resolve_question
from copilot.agent.service import AgentService
from copilot.data.college import parse_college_workbook
from copilot.data.importing import XLSX_TYPE
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook
from copilot.storage import Database

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEED = 20261111
OPTIONS = SyntheticOptions(classes_per_major=2, students_per_class=6, course_count=6, seed=SEED)
NUM = re.compile(r"\d+\.\d+")


def build(root: Path):
    workbook = generate_college_workbook(OPTIONS)
    parsed = parse_college_workbook(workbook.filename, XLSX_TYPE, workbook.content)
    db = Database(root / "eval.sqlite3")
    db.initialize()
    db.replace(parsed.dataset, source="generated", seed=SEED)
    return db


def selection_from_context(context, *, invalid=None):
    if invalid is not None:
        return invalid
    claims = {item["claim_id"]: item for item in context["claims"]}
    mandatory = list(context["mandatory_claim_ids"])
    sections = {kind: [] for kind in ("findings", "observations", "limitations", "suggestions")}
    for identifier in mandatory:
        sections[claims[identifier]["kind"]].append(identifier)
    covered = {tag for identifier in mandatory for tag in claims[identifier]["tags"]}
    needed = set(context["required_goal_tags"])
    if context["goal"] == "distribution":
        needed |= {"distribution", "distribution_unavailable"}
    if context["goal"] == "comparison":
        needed.add("comparison")
    for item in context["claims"]:
        if not needed - covered:
            break
        if item["claim_id"] in mandatory or not set(item["tags"]) & needed:
            continue
        sections[item["kind"]].append(item["claim_id"])
        covered |= set(item["tags"])
    for kind, identifiers in sections.items():
        if not identifiers:
            fallback = next((item for item in context["claims"] if item["kind"] == kind), None)
            if fallback:
                identifiers.append(fallback["claim_id"])
    return {"version": "evidence_analysis_v1", "sections": [
        {"kind": kind, "claim_ids": identifiers,
         "style": "connected" if kind == "observations" else "plain"}
        for kind, identifiers in sections.items()]}


class ProtocolFixture:
    """Scripted provider: name/simulated flags are explicit, not a real model."""
    name, model, simulated = "scripted_mock", "phase3d-eval-fixture-v1", True

    def __init__(self, invalid=None):
        self.invalid, self.requests = invalid, []

    async def complete(self, messages, tools):
        self.requests.append(messages)
        last = messages[-1]
        if last["role"] == "tool":
            payload = json.loads(last["content"])
            selection = selection_from_context(payload["analysis_context"], invalid=self.invalid)
            return ModelTurn(message=ModelMessage(role="assistant", content=json.dumps(
                {"status": "completed", "evidence_ids": [payload["evidence_id"]],
                 "analysis": selection})))
        plan = json.loads(last["content"].removeprefix("ACADEMIC_INTENT "))
        return ModelTurn(message=ModelMessage.model_validate({"role": "assistant", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": plan["tool"], "arguments": json.dumps(plan["arguments"])}}]}))


def case_definitions(catalog):
    courses = catalog.courses
    classes = catalog.classes
    first, last = courses[0].course_name, courses[-1].course_name
    c0, c1 = classes[0].class_id, classes[1].class_id
    major = next((row for row in catalog.majors), None)
    from copilot.dashboard.presentation import STAFF_MAJOR_LABELS
    major_label = STAFF_MAJOR_LABELS.get(major, major) if major else "软件工程"
    # (id, category, question, expected_status, expected_tool, invalid_fixture)
    return [
        ("E01", "预设统计问题", "当前数据集的概览是什么？", "ready", "get_dataset_overview", None),
        ("E02", "预设统计问题", "查询当前数据集有哪些课程", "ready", "get_catalog", None),
        ("E03", "预设统计问题", f"分析{first}的平均分和及格率", "ready", "get_score_summary", None),
        ("E04", "预设统计问题", f"查询{major_label}有哪些课程", "ready", "get_catalog", None),
        ("E05", "中文自然表达变体", f"看一下{first}的成绩", "ready", "get_score_summary", None),
        ("E06", "中文自然表达变体", f"帮我看看{first}的均分", "ready", "get_score_summary", None),
        ("E07", "中文自然表达变体", f"{first}有没有人不及格", "ready", "get_score_summary", None),
        ("E08", "模糊课程名称", "分析体育课的成绩", "needs_clarification", None, None),
        ("E09", "模糊课程名称", "分析体育类课程的成绩", "needs_clarification", None, None),
        ("E10", "明确课程成绩分布", f"分析{last}的成绩分布", "ready", "get_score_summary", None),
        ("E11", "明确课程成绩分布", f"{first}的分数都集中在哪一段", "ready", "get_score_summary", None),
        ("E12", "班级比较", f"比较 {c0} 和 {c1} 的{first}成绩", "ready", "compare_classes", None),
        ("E13", "班级比较", f"比较{classes[0].class_label}和{classes[1].class_label}的{first}成绩", "ready",
         "compare_classes", None),
        ("E14", "综合分析请求", f"分析{first}的成绩表现，有哪些值得关注的地方？", "ready",
         "get_score_summary", None),
        ("E15", "综合分析请求", f"{first}的成绩有什么特点", "ready", "get_score_summary", None),
        ("E16", "超出范围的问题", "预测某位同学下学期会不会挂科", "unsupported", None, None),
        ("E17", "超出范围的问题", "列出所有学生的姓名和学号", "unsupported", None, None),
        ("E18", "模型输出不符合协议", f"分析{first}的成绩分布", "ready", "get_score_summary",
         {"version": "evidence_analysis_v1", "sections": [
             {"kind": "findings", "claim_ids": ["r1.scope", "r1.sample"], "style": "plain"},
             {"kind": "observations", "claim_ids": ["r1.dominant_band"], "text": "教学质量好导致均分提升到99.99分",
              "style": "connected"},
             {"kind": "limitations", "claim_ids": ["r1.limits"], "style": "plain"},
             {"kind": "suggestions", "claim_ids": ["r1.review_distribution"], "style": "plain"}]}),
        ("E19", "模型输出不符合协议", f"分析{first}的成绩分布", "ready", "get_score_summary",
         {"version": "evidence_analysis_v1", "sections": [
             {"kind": "findings", "claim_ids": ["r1.scope", "r1.sample", "r1.mean", "r1.pass_rate"], "style": "plain"},
             {"kind": "observations", "claim_ids": ["r1.mean_comparison"], "style": "connected"},
             {"kind": "limitations", "claim_ids": ["r1.limits"], "style": "plain"},
             {"kind": "suggestions", "claim_ids": ["r1.review_distribution"], "style": "plain"}]}),
        ("E20", "数值和分析结论校验", f"分析{first}的平均分、及格率和成绩分布", "ready",
         "get_score_summary", None),
    ]


def evaluate(db, catalog, definition):
    case_id, category, question, expected_status, expected_tool, invalid = definition
    intent = resolve_question(question, catalog)
    row = {"id": case_id, "category": category, "question": question,
           "expected_status": expected_status, "expected_tool": expected_tool,
           "intent_resolution": {"status": intent.status, "tool": intent.tool,
                                 "arguments": intent.arguments, "message": intent.message},
           "tool_correctness": {"matches_expected": intent.tool == expected_tool,
                                "arguments_present": bool(intent.arguments) or expected_tool == "get_dataset_overview"},
           "fact_consistency": {"checked": False, "version_aligned": None,
                                "numbers_traceable": None, "evidence_ids": []},
           "analysis_quality": {"status": "not_applicable", "claim_ids": [], "sections": [],
                                "fallback_reason": None},
           "failure_reason": None}
    if intent.status != "ready":
        row["analysis_quality"]["status"] = "no_model_call_expected"
        row["failure_reason"] = ("expected_clarification" if expected_status == "needs_clarification"
                                 else "expected_unsupported") if intent.status == expected_status \
            else f"unexpected_status:{intent.status}"
        row["passed"] = intent.status == expected_status
        return row

    provider = ProtocolFixture(invalid=invalid)
    result = asyncio.run(AgentService(db, provider).query(
        question, intent=intent, execution_mode="grounded"))
    evidence_blob = json.dumps([item.data for item in result.evidence], ensure_ascii=False, default=str)
    numbers = set(NUM.findall(result.answer))
    blob = evidence_blob + json.dumps(result.analysis.model_dump(mode="json"), ensure_ascii=False) \
        if result.analysis else evidence_blob
    sections = result.analysis.sections if result.analysis else []
    ids = [identifier for section in sections for identifier in section.claim_ids]
    row["execution"] = {"provider": result.provider, "model": result.model,
                        "simulated": result.simulated, "model_turns": result.model_turns,
                        "answer_mode": result.answer_mode, "analysis_status": result.analysis_status,
                        "analysis_fallback_reason": result.analysis_fallback_reason,
                        "fallback_reason": result.fallback_reason}
    row["fact_consistency"] = {
        "checked": True,
        "version_aligned": len({item.dataset_version for item in result.evidence}
                               | {result.dataset_version}) == 1,
        "numbers_traceable": all(number in blob for number in numbers),
        "evidence_ids": [item.evidence_id for item in result.evidence],
        "statistic_values": sorted(numbers),
    }
    row["analysis_quality"] = {
        "status": result.analysis_status,
        "claim_ids": ids,
        "sections": [section.kind for section in sections],
        "fallback_reason": result.analysis_fallback_reason,
        "has_limitations": "limitations" in [section.kind for section in sections],
        "has_suggestions": "suggestions" in [section.kind for section in sections],
    }
    expects_fallback = invalid is not None
    ok_status = ("fallback" if expects_fallback else "verified") == result.analysis_status
    row["failure_reason"] = (None if ok_status else
                             f"expected_{'fallback' if expects_fallback else 'verified'}_got_{result.analysis_status}")
    row["passed"] = (intent.status == expected_status and intent.tool == expected_tool
                     and ok_status and row["fact_consistency"]["version_aligned"]
                     and row["fact_consistency"]["numbers_traceable"])
    if expects_fallback:
        row["fabricated_text_rejected"] = ("99.99" not in result.answer
                                           and "教学质量" not in result.answer)
    return row


def main(output):
    with tempfile.TemporaryDirectory(prefix="phase3d-eval-") as folder:
        db = build(Path(folder))
        catalog = db.reporting_catalog()
        definitions = case_definitions(catalog)
        cases = [evaluate(db, catalog, definition) for definition in definitions]
        by_category = {}
        for row in cases:
            bucket = by_category.setdefault(row["category"], {"total": 0, "passed": 0})
            bucket["total"] += 1
            bucket["passed"] += int(row["passed"])
        record = {
            "schema": "phase3d_agent_eval_baseline_v1",
            "dataset_version": catalog.dataset_version,
            "data_source": "server-generated synthetic only",
            "meta": {
                "provider_kind": "scripted_offline_fixture",
                "provider_name": "scripted_mock",
                "is_real_deepseek_run": False,
                "live_model_calls": 0,
                "what_this_measures": ["local catalog-bound intent resolution",
                                       "tool/argument correctness",
                                       "evidence-analysis protocol validation",
                                       "deterministic Chinese rendering and numeric consistency"],
                "what_this_does_NOT_measure": ["real DeepSeek protocol adherence",
                                               "real model accuracy or claim-selection quality",
                                               "latency, token usage or cost"],
                "warning": "Do NOT report these pass rates as a real-model accuracy figure. "
                           "A scripted provider always replies exactly as programmed.",
            },
            "totals": {"cases": len(cases), "passed": sum(row["passed"] for row in cases),
                       "failed": sum(not row["passed"] for row in cases),
                       "no_model_call_cases": sum(row["intent_resolution"]["status"] != "ready" for row in cases)},
            "by_category": by_category,
            "cases": cases,
        }
        text = json.dumps(record, ensure_ascii=False, indent=2)
        if output:
            Path(output).write_text(text, encoding="utf-8")
            print(f"Wrote {output}")
            print(f"cases={record['totals']['cases']} passed={record['totals']['passed']} "
                  f"failed={record['totals']['failed']}")
        else:
            print(text)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
