"""Small catalog-bound academic language compiler, with no model or data I/O.

Only a validated tool intent is sent to a provider. User prose is never copied
to canonical_question. This is a bounded academic grammar, not a general PII
detector or arbitrary natural-language reasoning system.
"""
import json
import re
import unicodedata
from typing import Literal

from pydantic import Field

from copilot.agent.models import AgentError, SessionScope, ToolName
from copilot.agent.privacy import canonical_question as english_gate
from copilot.models import Record
from copilot.schemas import ReportingCatalog

MAJOR_ALIASES = {
    "software_engineering": ("软件工程", "软工", "软件"),
    "computer_science": ("计算机科学与技术", "计算机科学", "计科"),
    "network_engineering": ("网络工程", "网工"),
}
_UNSUPPORTED = re.compile(r"名单|姓名|学号|身份证|手机号|电话|邮箱|个人成绩|某个学生|预测|因果|原因|为什么|中位数|排名|留级|重修|奖学金|处分|党员|干部|心理|性别|宿舍|\bmedian\b|\bpredict\w*\b|\bcaus\w*\b|\brank\w*\b|\bS\d{4}\b", re.I)
_INJECTION = re.compile(r"忽略.{0,8}(?:规则|指令|以上)|密钥|密码|api[ _-]?key|password|https?://|执行\s*(?:SQL|Python|代码)|运行\s*(?:SQL|Python|代码)|\b(?:SELECT|DELETE|DROP|INSERT)\s+", re.I)
_METRICS = re.compile(r"平均|均分|成绩|分数|及格|不及格|缺考|免修|分布|有效成绩|\b(?:average|mean|score|scores|pass|passing|fail|failed|missing|exempt|distribution|summary|statistics|weighted)\b", re.I)
_COMPARISON = re.compile(r"比较|对比|差异|\bcompar(?:e|ing|ison)\b|\bbetween\b|\bagainst\b", re.I)
_CATALOG = re.compile(r"目录|课程列表|班级列表|有哪些(?:课|班)|哪些课程|哪些班级|列出.{0,8}(?:课程|班级)|\bcatalog\b|\blist\b|\bavailable\s+(?:courses|classes)\b", re.I)
_OVERVIEW = re.compile(r"概览|概况|多少(?:名|个|门|条)?(?:学生|班级|课程|成绩)|学生(?:总)?人数|总人数|\boverview\b|\bhow\s+many\b|\bcounts?\b|\btotal\b", re.I)


class ResolvedIntent(Record):
    status: Literal["ready", "needs_clarification", "unsupported"]
    tool: ToolName | None = None
    arguments: dict = Field(default_factory=dict)
    scope: SessionScope = Field(default_factory=SessionScope)
    message: str
    canonical_question: str = ""
    choices: list[dict] = Field(default_factory=list)
    explicit: dict = Field(default_factory=dict)
    dataset_version: str


def _norm(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s()（）·,，.。:：;；_\-–—]", "", value)


def _result(catalog, status, message, *, scope=None, tool=None, arguments=None, choices=None, explicit=None):
    arguments = arguments or {}
    canonical = ("ACADEMIC_INTENT " + json.dumps({"tool": tool, "arguments": arguments}, sort_keys=True,
                    separators=(",", ":"), ensure_ascii=True)) if status == "ready" else ""
    return ResolvedIntent(status=status, tool=tool, arguments=arguments, scope=scope or SessionScope(),
        message=message, canonical_question=canonical, choices=choices or [], explicit=explicit or {},
        dataset_version=catalog.dataset_version)


def _choice(kind, value, label):
    return {"kind": kind, "value": value, "label": label}


def _class_number(record):
    match = re.search(r"(\d+)\s*班?$", record.class_label)
    return int(match[1]) if match else int(record.class_id[1:])


def _chinese_number(text):
    if text.isdigit():
        return int(text)
    digits = dict(zip("一二三四五六七八九", range(1, 10)))
    if text == "十":
        return 10
    if "十" in text:
        left, right = text.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(text)


def _term(question, catalog, inherited):
    q = re.sub(r"\s+", "", unicodedata.normalize("NFKC", question)).replace("–", "-").replace("—", "-")
    direct = re.findall(r"20\d{2}-[Tt][1-3]", q)
    pairs = list(re.finditer(r"(20\d{2})[-/至](20\d{2})(?:学年)?(?:[-/]|第)?([12一二])?(?:学期)?", q))
    for match in pairs:
        if int(match[2]) != int(match[1]) + 1:
            return None, True, "学年应是连续两年，例如2025–2026学年。"
        if match[3]:
            direct.append(f"{match[1]}-T" + {"一": "1", "二": "2"}.get(match[3], match[3]))
    direct = list(dict.fromkeys(value.upper() for value in direct))
    if len(direct) > 1:
        return None, True, "一次请选择一个学期；跨学期比较尚未支持。"
    if direct:
        return direct[0], True, None if direct[0] in catalog.terms else "当前数据没有这个学期，请从目录中选择。"
    semester_match = re.search(r"第?([12一二])学期", q)
    semester = {"一": "1", "二": "2"}.get(semester_match[1], semester_match[1]) if semester_match else None
    year = pairs[0][1] if pairs else None
    if not year:
        match = re.search(r"(20\d{2})(?:年|学年)?第?[12一二]学期", q)
        year = match[1] if match else None
    if year or semester:
        candidates = [term for term in catalog.terms if (not year or term.startswith(year + "-"))
                      and (not semester or term.endswith("T" + semester))]
        if not year and inherited and inherited in candidates:
            return inherited, True, None
        if len(candidates) == 1:
            return candidates[0], True, None
        return None, True, "请明确学年和学期，例如2025–2026学年第一学期。"
    if re.search(r"上学期|下学期|本学期|这学期|今年", q) and inherited is None:
        return None, False, "相对时间需要先确认会话学期，请选择明确的学年学期。"
    if inherited:
        return inherited, False, None if inherited in catalog.terms else "会话学期已不在当前数据中，请重新选择。"
    if len(catalog.terms) == 1:
        return catalog.terms[0], False, None
    return None, False, "当前包含多个学期，请先选择本次分析的学期。"


def resolve_question(question: str, catalog: ReportingCatalog, session_scope: SessionScope | None = None) -> ResolvedIntent:
    """Resolve only supported academic intents against the current directory."""
    inherited = session_scope or SessionScope()
    q = unicodedata.normalize("NFKC", question).strip()
    if not q or len(q) > 1000 or any(unicodedata.category(char).startswith("C") for char in q):
        return _result(catalog, "unsupported", "请输入简短的课程统计、班级比较、数据概览或目录问题。")
    if _UNSUPPORTED.search(q) or _INJECTION.search(q) or re.search(r"[<>`{}\\]", q):
        return _result(catalog, "unsupported", "仅支持聚合学业统计，不支持个人名单、代码执行、预测、排名或因果分析。")
    classes = {row.class_id: row for row in catalog.classes}
    courses = {row.course_id: row for row in catalog.courses}
    values = inherited.model_dump()
    explicit = {}
    if (values.get("class_id") and values["class_id"] not in classes or
        values.get("course_id") and values["course_id"] not in courses or
        values.get("grade_level") is not None and values["grade_level"] not in {row.grade_level for row in catalog.classes} or
        values.get("major") and values["major"] not in catalog.majors):
        return _result(catalog, "needs_clarification", "已确认范围中的实体已不在当前目录，请重新选择分析范围。")

    compare = bool(_COMPARISON.search(q))
    catalog_request = bool(_CATALOG.search(q)) and not compare
    metric_request = bool(_METRICS.search(q))
    overview_request = bool(_OVERVIEW.search(q)) and not compare and not metric_request and not catalog_request
    if not any((compare, catalog_request, metric_request, overview_request)):
        return _result(catalog, "unsupported", "目前支持数据概览、课程成绩统计、同课程班级比较、课程和班级目录查询。")

    # Longest known course names win over contained shorter catalog names.
    normalized = _norm(q)
    matching_names = {row.course_name for row in catalog.courses if _norm(row.course_name) in normalized}
    matching_names = {name for name in matching_names if not any(_norm(name) != _norm(other) and _norm(name) in _norm(other) for other in matching_names)}
    major_text = normalized
    for name in matching_names:
        major_text = major_text.replace(_norm(name), "")
    major_matches = {code for code, aliases in MAJOR_ALIASES.items() if any(_norm(alias) in major_text for alias in aliases)}
    if len(major_matches) > 1:
        return _result(catalog, "needs_clarification", "一次请选择一个专业；也可以用明确班级标识比较两个班。")
    if major_matches:
        major = next(iter(major_matches))
        if major not in catalog.majors:
            return _result(catalog, "needs_clarification", "当前目录没有这个专业，请选择现有专业。")
        values["major"] = explicit["major"] = major
    compact = re.sub(r"\s+", "", q)
    grades = re.findall(r"(?<!\d)(20\d{2}|\d{2})(?=级|软工|计科|网工|软件工程|计算机科学与技术|网络工程)", compact)
    grades += re.findall(r"\bgrade\s+([1-3]|20\d{2})\b", q, re.I)
    grade_values = {int(g) + (2000 if len(g) == 2 else 0) for g in grades}
    if len(grade_values) > 1:
        return _result(catalog, "needs_clarification", "一次请选择一个入学年级，跨年级比较暂未支持。")
    if grade_values:
        grade = next(iter(grade_values))
        if grade not in {row.grade_level for row in catalog.classes}:
            return _result(catalog, "needs_clarification", "当前目录没有这个入学年级，请重新选择。")
        values["grade_level"] = explicit["grade_level"] = grade

    term, term_explicit, term_error = _term(q, catalog, inherited.term)
    if term_error and not (overview_request or catalog_request):
        return _result(catalog, "needs_clarification", term_error, choices=[_choice("term", t, t) for t in catalog.terms])
    values["term"] = term
    if term_explicit and term:
        explicit["term"] = term

    requested_courses = list(dict.fromkeys(identifier.upper() if identifier.upper().startswith("G") else "I" + identifier[1:].lower()
        for identifier in re.findall(r"\b(?:G\d+-C\d+|I[0-9a-f]{24})\b", q, re.I)))
    unknown_ids = [identifier for identifier in requested_courses if identifier not in courses]
    if unknown_ids:
        return _result(catalog, "needs_clarification", "当前课程目录中没有这个课程标识，请选择现有课程。")
    if len(requested_courses) > 1 or len(matching_names) > 1:
        return _result(catalog, "unsupported", "一次只支持一门课程的统计或班级比较，请选择一门课程。")
    course_candidates = [courses[identifier] for identifier in requested_courses]
    if not course_candidates and matching_names:
        course_candidates = [row for row in catalog.courses if row.course_name in matching_names]
    explicit_course_aliases = re.findall(r"(?:课程|course)\s*(C\d{1,3})\b", q, re.I)
    if explicit_course_aliases:
        if len(set(alias.upper() for alias in explicit_course_aliases)) != 1:
            return _result(catalog, "unsupported", "一次只支持一门课程，请明确课程标识。")
        suffix = "C" + explicit_course_aliases[0][1:].zfill(2)
        course_candidates = [row for row in catalog.courses if row.course_id.upper().endswith("-" + suffix)
                             or (row.source_code or "").upper() == explicit_course_aliases[0].upper()]
        if not course_candidates:
            return _result(catalog, "needs_clarification", "当前课程目录没有这个简称，请选择现有课程。")
    if course_candidates:
        course_candidates = [row for row in course_candidates if values["grade_level"] is None or row.grade_level == values["grade_level"]]
        if term:
            course_candidates = [row for row in course_candidates if row.source_term is None or row.source_term == term]
        if len(course_candidates) != 1:
            return _result(catalog, "needs_clarification", "课程名称或简称不能唯一对应当前范围，请确认入学年级、学期或完整课程标识。",
                choices=[_choice("course_id", row.course_id, f"{row.course_name} · {row.grade_level}级 · {row.source_term or '目录课程'}") for row in course_candidates])
        values["course_id"] = explicit["course_id"] = course_candidates[0].course_id

    # Cxx after an explicit course label is a course alias; other Cxxx tokens
    # identify classes. No suffix is selected without checking the directory.
    class_text = re.sub(r"(?:课程|course)\s*C\d{1,3}\b", "", q, flags=re.I)
    class_text = re.sub(r"G\d+-C\d+", "", class_text, flags=re.I)
    class_ids = list(dict.fromkeys("C" + digits.zfill(3) for digits in re.findall(r"\bC(\d{1,3})\b", class_text, re.I)))
    if any(identifier not in classes for identifier in class_ids):
        return _result(catalog, "needs_clarification", "当前班级目录中没有这个班级标识，请重新选择。")
    numbers = [_chinese_number(number) for number in re.findall(r"(?:第)?([0-9]{1,3}|[一二三四五六七八九十]{1,3})班", compact)]
    numbers += [int(number) for number in re.findall(r"\bclass\s+(\d{1,3})\b", q, re.I)]
    numbers = list(dict.fromkeys(numbers))
    if numbers and not class_ids:
        for number in numbers:
            candidates = [row for row in catalog.classes if _class_number(row) == number
                          and (values["grade_level"] is None or row.grade_level == values["grade_level"])
                          and (values["major"] is None or row.major == values["major"])]
            if len(candidates) != 1:
                return _result(catalog, "needs_clarification", "这个班级简称对应多个或不存在的班级，请补充入学年级、专业或完整班级标识。",
                    choices=[_choice("class_id", row.class_id, row.class_label) for row in candidates])
            class_ids.append(candidates[0].class_id)
    if class_ids:
        if len(class_ids) > 1 and not compare:
            return _result(catalog, "needs_clarification", "检测到多个班级。如需比较，请明确提出同课程班级比较。")
        if any(values["grade_level"] is not None and classes[identifier].grade_level != values["grade_level"]
               or values["major"] is not None and classes[identifier].major != values["major"] for identifier in class_ids):
            return _result(catalog, "needs_clarification", "班级与已确认的年级或专业不一致，请调整范围后再试。")
        explicit["class_ids" if compare else "class_id"] = class_ids if compare else class_ids[0]
        values["class_id"] = None if compare else class_ids[0]

    # A course and a class must belong to the same entry cohort. Never issue a
    # seemingly successful empty answer for a contradictory selection.
    if values["course_id"]:
        course = courses[values["course_id"]]
        selected_classes = class_ids or ([values["class_id"]] if values["class_id"] else [])
        if any(classes[identifier].grade_level != course.grade_level for identifier in selected_classes):
            return _result(catalog, "needs_clarification", "所选课程与班级的入学年级不一致，请选择匹配的课程和班级。")

    if q.isascii():
        scrubbed = q
        for identifier in (*requested_courses, *class_ids, *explicit_course_aliases):
            scrubbed = re.sub(re.escape(identifier), "course" if identifier in requested_courses or identifier in explicit_course_aliases else "class", scrubbed, flags=re.I)
        scrubbed = re.sub(r"\bgrade\s+20\d{2}\b", "grade", scrubbed, flags=re.I)
        try:
            english_gate(scrubbed, catalog)
        except AgentError:
            return _result(catalog, "unsupported", "问题包含当前受控学业查询不支持的内容，请使用目录中的课程、班级和统计指标。")

    scope = SessionScope(**values)
    if overview_request:
        if explicit:
            return _result(catalog, "unsupported", "数据集概览目前只支持全数据范围；筛选后的成绩人数请使用课程统计。")
        return _result(catalog, "ready", "将查询当前数据集的整体概览。", tool="get_dataset_overview", scope=SessionScope())
    if catalog_request:
        arguments = {"grade_level": values["grade_level"]}
        if values["major"] is not None:
            arguments["major"] = values["major"]
        return _result(catalog, "ready", "将查询当前目录中匹配的课程和班级。", tool="get_catalog", arguments=arguments,
                       scope=scope, explicit=explicit)
    if compare:
        if not values["course_id"] or len(class_ids) < 2:
            return _result(catalog, "needs_clarification", "班级比较需要一门明确课程和至少两个明确班级，请补充或从目录选择。", scope=scope,
                           choices=[_choice("class_id", row.class_id, row.class_label) for row in catalog.classes])
        if len(class_ids) > 10:
            return _result(catalog, "unsupported", "一次最多比较10个班级。")
        arguments = {"term": term, "course_id": values["course_id"], "class_ids": class_ids}
        return _result(catalog, "ready", "将比较所选班级同一课程、同一学期的成绩。", tool="compare_classes", arguments=arguments, scope=scope, explicit=explicit)

    if not values["course_id"] and (re.search(r"课程|这门|该课|\bfor\b", q, re.I) or re.search(r"[\u4e00-\u9fffA-Za-z][^，。?？]{0,30}的(?:平均|均分|及格)", q)):
        return _result(catalog, "needs_clarification", "当前目录找不到唯一匹配课程，请选择课程或补充课程全名与入学年级。", scope=scope,
                       choices=[_choice("course_id", row.course_id, f"{row.course_name} · {row.grade_level}级") for row in catalog.courses])
    arguments = {"term": term, "course_id": values["course_id"], "class_id": values["class_id"], "grade_level": values["grade_level"]}
    if values["major"] is not None:
        arguments["major"] = values["major"]
    return _result(catalog, "ready", "已匹配课程目录和本次统计范围。", tool="get_score_summary", arguments=arguments, scope=scope, explicit=explicit)


def validate_intent_call(intent: ResolvedIntent, name: str, args) -> None:
    """Reject a model's structurally valid but semantically different call."""
    if intent.status != "ready":
        raise AgentError("intent_mismatch", "问题尚未明确，不能执行模型提出的统计。", 422)
    values = args.model_dump() if hasattr(args, "model_dump") else dict(args)
    values = {key: value for key, value in values.items() if key != "major" or value is not None}
    if name == "get_catalog" and intent.tool != "get_catalog":
        expected = {"grade_level": intent.scope.grade_level}
        if intent.scope.major is not None:
            expected["major"] = intent.scope.major
        if values == expected:
            return
    elif name == intent.tool:
        expected = dict(intent.arguments)
        actual = dict(values)
        if name == "compare_classes":
            expected["class_ids"] = sorted(expected.get("class_ids", []))
            actual["class_ids"] = sorted(actual.get("class_ids", []))
        if actual == expected:
            return
    raise AgentError("intent_mismatch", "模型选择的工具或课程、班级、学期与已确认意图不一致，已阻止执行。", 422)
