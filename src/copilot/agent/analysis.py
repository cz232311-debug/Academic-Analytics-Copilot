"""Bounded Chinese synthesis: the model selects verified claims, never writes facts.

AnalyticsEngine remains the source of statistics. This module only derives
display proportions/relations from that evidence and makes an explicit claim
catalog. Free prose is intentionally outside this first, verifiable contract.
"""
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import re

from pydantic import ValidationError

from copilot.agent.models import (AnalysisGoal, AnalysisSelection, Evidence,
                                  VerifiedAnalysis, VerifiedAnalysisSection)
from copilot.analytics import ratio

VERSION = "evidence_analysis_v1"
KINDS = ("findings", "observations", "limitations", "suggestions")
HEADINGS = {"findings": "统计事实", "observations": "分析观察", "limitations": "范围与局限", "suggestions": "后续建议"}
PROMPT = """
After the exact validated tool completes, return only JSON with status,
evidence_ids and analysis. The tool's analysis_context contains a server-built
claim catalog and a safe goal enum, never the original user question.
For completed results use this strict protocol:
{"status":"completed","evidence_ids":["r1"],"analysis":{"version":"evidence_analysis_v1","sections":[
{"kind":"findings","claim_ids":["r1.scope","r1.sample","r1.mean"],"style":"plain"},
{"kind":"observations","claim_ids":["r1.dominant_band","r1.high_band"],"style":"connected"},
{"kind":"limitations","claim_ids":["r1.limits"],"style":"plain"},
{"kind":"suggestions","claim_ids":["r1.review_distribution"],"style":"plain"}]}}
The example is structural; use ONLY IDs that actually appear in analysis_context.
Include each of findings, observations, limitations and suggestions exactly once.
Choose relevant claims, order them for the goal, and choose plain, connected or
focus style. Every mandatory_claim_id must occur, without duplicate claims.
For distribution goals include an observations claim tagged distribution (or
distribution_unavailable if there are no graded records). For comparison goals
include a claim tagged comparison. Mean, pass and missing goals require the
corresponding tag in required_goal_tags (or its unavailable alternative).
General summary goals require mean and pass information. Never replace a distribution observation
with only a mean/pass-rate roster. All claims must match the section's kind.
Do not add text, narrative, guidance, literal numbers, new claim IDs, invented
comparisons, causes, predictions or conclusions. Chinese prose and numbers are
rendered by the server from the selected verified claims. You choose focus and
organization, not new statistical or causal facts. Suggestions remain labeled
suggestions. The server rejects invalid selection and explicitly falls back.
"""


class InvalidAnalysis(ValueError):
    pass


@dataclass(frozen=True)
class Claim:
    identifier: str
    kind: str
    text: str
    evidence_id: str
    tags: tuple[str, ...] = ()
    mandatory: bool = False
    facts: dict = field(default_factory=dict)

    def payload(self):
        return {"claim_id": self.identifier, "kind": self.kind, "text": self.text,
                "evidence_id": self.evidence_id, "tags": list(self.tags), "facts": self.facts}


def analysis_goal(question: str, tool: str) -> AnalysisGoal:
    """Reduce locally checked user prose to a finite, non-personal objective."""
    if tool == "compare_classes":
        return "comparison"
    if tool == "get_dataset_overview":
        return "overview"
    if tool == "get_catalog":
        return "catalog"
    if re.search(r"分布|分段|distribution|score\s*bands?", question, re.I):
        return "distribution"
    if re.search(r"缺考|缺失|免修|missing|exempt", question, re.I):
        return "missing"
    mean = bool(re.search(r"均分|平均|mean|average|weighted", question, re.I))
    passing = bool(re.search(r"及格|pass|fail", question, re.I))
    if mean and passing:
        return "summary"
    if mean:
        return "mean"
    if passing:
        return "pass"
    return "summary"


def analysis_requirements(question: str) -> tuple[str, ...]:
    """Preserve explicit combined metrics without forwarding the question."""
    patterns = {"mean": r"均分|平均|mean|average|weighted", "pass": r"及格|pass|fail",
                "missing": r"缺考|缺失|免修|missing|exempt"}
    return tuple(tag for tag, pattern in patterns.items() if re.search(pattern, question, re.I))


def _term(value):
    if value and re.fullmatch(r"20\d{2}-T[1-3]", value):
        year, semester = value.split("-T")
        return f"{year}–{int(year) + 1}学年 第{semester}学期"
    return value or "当前数据集"


def _labels(catalog):
    return ({row.course_id: row.course_name for row in catalog.courses},
            {row.class_id: row.class_label for row in catalog.classes})


def _scope(scope, courses, classes, arguments=None):
    parts = [_term(scope.get("term"))]
    if scope.get("course_id"):
        parts.append("课程“" + courses[scope["course_id"]] + "”")
    else:
        parts.append("当前确认范围内的课程")
    if scope.get("class_id"):
        parts.append(classes[scope["class_id"]])
    if scope.get("grade_level") is not None:
        grade = scope["grade_level"]
        parts.append(f"{grade}级" if grade >= 1900 else f"年级{grade}")
    # Major is an additive tool filter; legacy Statistics.scope has no major.
    major = (arguments or {}).get("major")
    major_names = {"software_engineering": "软件工程", "computer_science": "计算机科学与技术",
                   "network_engineering": "网络工程", "business_administration": "工商管理",
                   "information_systems": "信息系统", "unspecified": "未分类专业"}
    if major:
        parts.append(major_names[major])
    return "，".join(parts)


def _band(row):
    low, high = row["lower_cents"] // 100, row["upper_cents"] // 100
    return f"{low}–{high}分" + ("（含100分）" if row["upper_inclusive"] else "（不含上界）")


def _stat_claims(data, prefix, evidence_id, courses, classes, arguments, *, mandatory=False):
    def claim(suffix, kind, text, *, tags=(), required=False, facts=None):
        return Claim(f"{prefix}.{suffix}", kind, text, evidence_id, tags, required, facts or {})

    n = data["graded_count"]
    claims = [
        claim("scope", "findings", "本次分析范围为" + _scope(data["scope"], courses, classes, arguments), required=mandatory),
        claim("sample", "findings", f"范围内有{data['student_count']}名学生、{data['enrollment_count']}条成绩记录，其中{n}条为有效成绩；均分、及格率和分布均以有效成绩记录为统计对象",
              required=mandatory, facts={key: data[key] for key in ("student_count", "enrollment_count", "graded_count")}),
        claim("status", "findings", f"缺考或缺失{data['missing_count']}条、免修{data['exempt_count']}条；两类记录均不进入均分、及格率和分布的分母", tags=("missing",),
              facts={"missing_count": data["missing_count"], "exempt_count": data["exempt_count"]}),
    ]
    limitation = "这些结果仅描述本次已确认范围和已导入记录，不能据此推断其他学期、未记录学生或教学原因"
    if 0 < n < 30:
        limitation = f"本次仅有{n}条有效成绩，样本较小；" + limitation
    if n == 0:
        limitation = "本次没有有效成绩，均分和及格率不可用；" + limitation
    claims.append(claim("limits", "limitations", limitation, required=mandatory))
    if not n:
        claims.extend([
            claim("no_distribution", "observations", "没有有效成绩可用于识别分数段集中情况，均分和及格率不可用，不能把空结果当作零分或零及格率", tags=("distribution_unavailable", "mean_unavailable", "pass_unavailable")),
            claim("review_records", "suggestions", "建议先核对导入覆盖范围和明确的成绩状态，再重新生成统计"),
        ])
        return claims
    claims.extend([
        claim("mean", "findings", f"有效成绩均分为{data['average_score']}分，学分加权均分为{data['weighted_average_score']}分", tags=("mean",),
              facts={"average_score": str(data["average_score"]), "weighted_average_score": str(data["weighted_average_score"])}),
        claim("pass_rate", "findings", f"及格率为{data['pass_rate_percent']}%（{data['passed_count']}/{n}），不及格成绩{data['failed_count']}条", tags=("pass",),
              facts={"pass_rate_percent": str(data["pass_rate_percent"]), "passed_count": data["passed_count"], "graded_count": n}),
    ])
    bins = data["distribution"]
    largest = max(row["count"] for row in bins)
    leaders = [row for row in bins if row["count"] == largest]
    share = ratio(largest * 100, n)
    band_names = "、".join(_band(row) for row in leaders)
    text = (f"记录最多的分数段为{band_names}，共{largest}条，占有效成绩的{share}%"
            if len(leaders) == 1 else f"{band_names}并列为记录最多的分数段，各{largest}条，各占有效成绩的{share}%")
    claims.append(claim("dominant_band", "observations", text, tags=("distribution",),
                        facts={"bands": [row["label"] for row in leaders], "count_each": largest, "percent_each": str(share)}))
    high = sum(row["count"] for row in bins if row["lower_cents"] >= 8000)
    high_share = ratio(high * 100, n)
    claims.append(claim("high_band", "observations", f"80分及以上共有{high}条，占有效成绩的{high_share}%；这说明高分段在本样本中的实际占比，并不表示教学效果的因果结论",
                        tags=("distribution",), facts={"threshold_cents": 8000, "count": high, "percent": str(high_share)}))
    if data["passed_count"] == n:
        pass_text = f"本样本的{n}条有效成绩全部及格；及格率达到100.00%仍不能代替对具体分布的检查"
    else:
        failed_share = ratio(data["failed_count"] * 100, n)
        pass_text = f"本样本有{data['failed_count']}条不及格成绩，占有效成绩的{failed_share}%；这是成绩记录比例，不是全体学生的风险预测"
    claims.extend([
        claim("pass_pattern", "observations", pass_text, tags=("pass_observation",)),
        claim("record_coverage", "observations", f"共有{data['missing_count'] + data['exempt_count']}条缺考、缺失或免修记录未进入成绩分布，不能据此补推空白单元格的选课情况", tags=("coverage",)),
        claim("review_distribution", "suggestions", "建议结合各分数段占比检查集中区间；如需了解班级差异，请另行确认同课程、同学期的班级比较范围"),
        claim("review_sample", "suggestions", "建议先核对各组样本量和记录覆盖情况，再开展进一步比较；不要把当前描述性统计当作个人预测或因果判断"),
    ])
    return claims


def _comparison_claims(result, courses, classes):
    data, eid = result.data, result.evidence_id
    rows = data["results"]
    scope = {"term": data["term"], "course_id": data["course_id"]}
    claims = [
        Claim(eid + ".scope", "findings", "本次比较范围为" + _scope(scope, courses, classes) + "；班级为" + "、".join(classes[row["scope"]["class_id"]] for row in rows), eid, mandatory=True),
        Claim(eid + ".sample", "findings", "各班有效成绩记录数为" + "、".join(f"{classes[row['scope']['class_id']]} {row['graded_count']}条" for row in rows) + "；比较使用同一课程、同一学期和同一数据版本", eid, mandatory=True),
        Claim(eid + ".limits", "limitations", "均分差值属于本次样本的描述性比较，不能据此判断统计显著性、学生能力或教学因果；各班样本量与记录覆盖可能不同", eid, mandatory=True),
        Claim(eid + ".review_comparison", "suggestions", "建议同时查看各班样本量、缺失记录和分布，再决定是否需要补充数据或开展进一步比较", eid),
    ]
    graded = [row for row in rows if row["graded_count"]]
    if len(graded) < 2:
        claims.append(Claim(eid + ".comparison_unavailable", "observations", "不足两个班级具有有效成绩，目前无法形成可靠的班级均分差值", eid, ("comparison",)))
    else:
        means = {row["scope"]["class_id"]: Fraction(row["sum_score_cents"], row["graded_count"] * 100) for row in graded}
        high, low = max(means.values()), min(means.values())
        high_ids = sorted(cid for cid, value in means.items() if value == high)
        low_ids = sorted(cid for cid, value in means.items() if value == low)
        delta = high - low
        difference = (Decimal(delta.numerator) / Decimal(delta.denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not delta:
            text = "有有效成绩的各班原始均分相同，显示差值为0.00分"
        elif difference == 0:
            text = "各班原始均分存在差异，但最高与最低的差值小于0.01分的显示精度；不作显著性结论"
        else:
            text = ("、".join(classes[cid] for cid in high_ids) + "的原始均分高于" + "、".join(classes[cid] for cid in low_ids)
                    + f"，最高与最低的均分差为{difference}分；差值先由精确总分和记录数计算，再作显示舍入")
        claims.append(Claim(eid + ".mean_comparison", "observations", text, eid, ("comparison", "mean"),
                            facts={"highest_class_ids": high_ids, "lowest_class_ids": low_ids,
                                   "difference_points": str(difference), "exact_difference_numerator": delta.numerator,
                                   "exact_difference_denominator": delta.denominator}))
    for row in rows:
        claims.extend(_stat_claims(row, eid + "." + row["scope"]["class_id"], eid, courses, classes, result.arguments))
    return claims


def claim_catalog(evidence: list[Evidence], catalog) -> list[Claim]:
    if any(result.dataset_version != catalog.dataset_version or result.data.get("dataset_version") != result.dataset_version for result in evidence):
        raise InvalidAnalysis("evidence_version_mismatch")
    courses, classes = _labels(catalog)
    claims = []
    for result in evidence:
        eid, data = result.evidence_id, result.data
        if result.tool == "get_score_summary":
            claims.extend(_stat_claims(data, eid, eid, courses, classes, result.arguments, mandatory=True))
        elif result.tool == "compare_classes":
            claims.extend(_comparison_claims(result, courses, classes))
        elif result.tool == "get_dataset_overview":
            c = data["counts"]
            claims.extend([
                Claim(eid + ".scope", "findings", "本次查看当前已确认数据集的整体概览", eid, mandatory=True),
                Claim(eid + ".sample", "findings", f"共有{c['students']}名学生、{c['classes']}个班级、{c['courses']}门目录课程、{c['enrollments']}条成绩记录", eid, mandatory=True),
                Claim(eid + ".coverage", "observations", f"每名学生每学期的记录课程数范围为{data['minimum_courses_per_student']}–{data['maximum_courses_per_student']}门；这描述已有记录的覆盖，不是个人实际应修规则", eid),
                Claim(eid + ".limits", "limitations", "概览统计数量，不包含具体课程均分、班级差异或个人成绩结论", eid, mandatory=True),
                Claim(eid + ".review_scope", "suggestions", "建议选择一门明确课程和学期，进一步查看成绩分布或比较班级", eid),
            ])
        else:
            course_names = [row["course_name"] for row in data["courses"]]
            class_names = [row["class_label"] for row in data["classes"]]
            entries = "课程：" + ("、".join(course_names[:12]) or "无") + "；班级：" + ("、".join(class_names[:12]) or "无")
            if len(course_names) > 12 or len(class_names) > 12:
                entries += "；此处分别展示前12项，其余请在技术详情的完整目录或数据导出中查看"
            claims.extend([
                Claim(eid + ".scope", "findings", "本次查询已确认范围内的课程和班级目录", eid, mandatory=True),
                Claim(eid + ".sample", "findings", f"目录中有{len(data['courses'])}门课程、{len(data['classes'])}个班级", eid, mandatory=True),
                Claim(eid + ".coverage", "observations", "目录条目说明当前可选择的课程和班级，不代表这些条目都有有效成绩", eid),
                Claim(eid + ".catalog_entries", "observations", entries, eid, mandatory=True),
                Claim(eid + ".limits", "limitations", "目录查询不提供均分、及格率或班级成绩差异，不能由目录数量推断成绩水平", eid, mandatory=True),
                Claim(eid + ".review_scope", "suggestions", "建议从目录选择可读的课程与班级名称，再明确需要的统计指标", eid),
            ])
    return claims


def analysis_context(evidence: list[Evidence], catalog, goal: AnalysisGoal, required_metrics=()) -> dict:
    claims = claim_catalog(evidence, catalog)
    return {"version": VERSION, "goal": goal, "required_goal_tags": _required_tags(goal, required_metrics), "dataset_version": catalog.dataset_version,
            "indicator_definitions": {
                "denominator": "有效成绩记录；缺考、缺失和免修不进入均分、及格率和分布分母；空结果为不可用",
                "pass_threshold": "60分及以上", "high_band_threshold": "80分及以上",
                "rounding": "百分比和均分显示两位小数，ROUND_HALF_UP；比较差值使用精确总分和记录数",
                "small_sample_note": "不足30条有效记录时提示样本较小；这不是统计显著性检验"},
            "mandatory_claim_ids": [claim.identifier for claim in claims if claim.mandatory],
            "claims": [claim.payload() for claim in claims]}


def _required_tags(goal, required_metrics=()):
    if any(tag not in {"mean", "pass", "missing"} for tag in required_metrics):
        raise InvalidAnalysis("unknown_required_metric")
    defaults = {"mean": ["mean"], "pass": ["pass"], "missing": ["missing"], "summary": ["mean", "pass"]}.get(goal, [])
    return sorted(set(defaults).union(required_metrics))


def render_analysis(raw, selected: list[Evidence], catalog, goal: AnalysisGoal, required_metrics=()) -> tuple[str, VerifiedAnalysis]:
    try:
        selection = AnalysisSelection.model_validate(raw)
    except (ValidationError, ValueError, TypeError):
        raise InvalidAnalysis("invalid_structure") from None
    claims = claim_catalog(selected, catalog)
    by_id = {claim.identifier: claim for claim in claims}
    if (len({section.kind for section in selection.sections}) != len(KINDS)
            or {section.kind for section in selection.sections} != set(KINDS)):
        raise InvalidAnalysis("missing_section")
    identifiers = [identifier for section in selection.sections for identifier in section.claim_ids]
    if len(identifiers) != len(set(identifiers)) or any(identifier not in by_id for identifier in identifiers):
        raise InvalidAnalysis("unknown_or_duplicate_claim")
    if not {claim.identifier for claim in claims if claim.mandatory}.issubset(identifiers):
        raise InvalidAnalysis("missing_scope_sample_or_limit")
    observed_tags, all_tags = set(), set()
    for section in selection.sections:
        if any(by_id[identifier].kind != section.kind for identifier in section.claim_ids):
            raise InvalidAnalysis("claim_kind_mismatch")
        all_tags.update(tag for identifier in section.claim_ids for tag in by_id[identifier].tags)
        if section.kind == "observations":
            observed_tags.update(tag for identifier in section.claim_ids for tag in by_id[identifier].tags)
    if goal == "distribution" and not observed_tags.intersection({"distribution", "distribution_unavailable"}):
        raise InvalidAnalysis("missing_distribution_observation")
    if goal == "comparison" and "comparison" not in observed_tags:
        raise InvalidAnalysis("missing_comparison_observation")
    if any(not all_tags.intersection({tag, tag + "_unavailable"}) for tag in _required_tags(goal, required_metrics)):
        raise InvalidAnalysis("missing_requested_metric")
    sections = []
    for section in selection.sections:
        chosen = [by_id[identifier] for identifier in section.claim_ids]
        texts = [claim.text for claim in chosen]
        if section.style == "connected" and len(texts) > 1:
            body = texts[0] + "。另外，" + "；".join(texts[1:]) + "。"
        else:
            body = "；".join(texts) + "。"
        if section.style == "focus" and section.kind == "observations":
            body = "值得关注的是，" + body
        refs = sorted({claim.evidence_id for claim in chosen})
        sections.append(VerifiedAnalysisSection(kind=section.kind, claim_ids=section.claim_ids,
                         text=body, evidence_ids=refs))
    verified = VerifiedAnalysis(goal=goal, sections=sections)
    answer = "\n\n".join(f"**{HEADINGS[section.kind]}**\n\n{section.text} [" + ", ".join(section.evidence_ids) + "]" for section in sections)
    return answer, verified
