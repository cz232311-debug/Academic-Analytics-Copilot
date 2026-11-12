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
_CAPABILITY = re.compile(r"你(?:知道|了解)|能否|能不能|可不可以|是否(?:能|可以|支持)|(?:你|系统)?(?:能|会|可以|支持).{0,40}(?:吗|嘛|么|[?？])\s*$|支持(?:哪些|什么)(?:分析|功能)")
_WHOLE_SCOPE = re.compile(r"全体|全部|整体|总体|所有|全数据|当前数据集|整个数据集|所选范围|当前范围")
_ENTIRE_DATASET = re.compile(r"整个数据集|全数据集|全部数据集|所有数据集|全数据范围")
# Interrogative and auxiliary words that ask a question; they are never a
# course subject. Stripped only from the residual text AFTER known catalog
# names have been removed, so they cannot corrupt a real course name.
_QUESTION_WORDS = re.compile(
    r"有没有人|有多少人|有没有|是否有|多少人|几个人|有谁|是不是|怎么样|怎样|"
    r"哪些|什么|谁|怎么|如何|多少|是否|能否|能不能|可不可以|吗|呢|吧|啊|嘛|么|几")
# Review-oriented phrasing ("值得关注的地方", "表现如何") asks for the same
# deterministic score summary as metric words; it must count as an analysis
# request instead of falling through to unsupported.
_REVIEW = re.compile(r"值得关注|值得注意|亮点|异常|特点|强项|薄弱|表现|情况|怎么样|怎样|如何")


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


def _course_choices(rows):
    return [_choice("course_id", row.course_id,
                    f"{row.course_name} · {row.grade_level}级 · {row.source_term or '目录课程'}") for row in rows]


def _unresolved_course_reference(question, matching_names):
    """Find an explicit course subject left after removing known scope terms.

    This deliberately only extracts bounded metric subjects. A partial course
    name supplies clarification choices, never an automatically selected ID.
    """
    text = question
    for name in matching_names:
        separators = r"[\s()（）·,，.。:：;；_\-–—]*"
        text = re.sub(separators.join(re.escape(char) for char in _norm(name)), "", text, flags=re.I)
    text = re.sub(r"(?<![A-Za-z0-9])(?:G\d+-C\d+|I[0-9a-f]{24}|C\d{1,3})(?![A-Za-z0-9])", "", text, flags=re.I)
    text = re.sub(r"20\d{2}\s*[-/至–—]\s*20\d{2}(?:\s*学年)?(?:\s*第?[12一二]学期)?|20\d{2}-T[1-3]|(?:20\d{2})?\s*第?[12一二]学期", "", text, flags=re.I)
    text = re.sub(r"(?<!\d)(?:20\d{2}|\d{2})\s*级?|(?:第)?(?:[0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*班", "", text)
    for aliases in MAJOR_ALIASES.values():
        for alias in sorted(aliases, key=len, reverse=True):
            text = text.replace(alias, "")
    text = _WHOLE_SCOPE.sub("", text)
    text = re.sub(r"^(?:(?:请问|请|麻烦|帮我|给我|我想|想要|希望|看看|了解|知道)\s*)+", "", text)
    text = re.sub(r"(?:这门课|该门课|这门|该课|请|帮我|给我|查看|看看|查询|分析|统计|比较|对比|针对|关于|一下|本学期|这学期|同一学期|学期|数据集|数据|成绩单|范围|班级|班|年级|专业|学生)", "", text)
    text = _QUESTION_WORDS.sub("", text)
    match = re.search(r"平均|均分|成绩|分数|及格|不及格|分布", text)
    if not match:
        return ""
    subject = re.split(r"[，。?？:：]", text[:match.start()])[-1][-40:]
    subject = re.sub(r"^[\s的与和及、在中]+|[\s的与和及、]+$", "", subject)
    subject = re.sub(r"(?:课程|课)$", "", subject).strip()
    return subject if len(_norm(subject)) >= 2 else ""


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

    if _CAPABILITY.search(q):
        return _result(catalog, "needs_clarification",
            "可以进行数据集概览、单课程成绩统计、同课程班级比较，以及课程和班级目录查询。"
            "请先选择目录中的具体课程并说明想看均分、及格率或成绩分布；本次仅介绍能力，尚未执行统计。",
            scope=inherited)

    compare = bool(_COMPARISON.search(q))
    catalog_request = bool(_CATALOG.search(q)) and not compare
    metric_request = bool(_METRICS.search(q)) or bool(_REVIEW.search(q))
    overview_request = bool(_OVERVIEW.search(q)) and not compare and not metric_request and not catalog_request
    if not any((compare, catalog_request, metric_request, overview_request)):
        return _result(catalog, "unsupported", "目前支持数据概览、课程成绩统计、同课程班级比较、课程和班级目录查询。")
    if (metric_request or compare) and _ENTIRE_DATASET.search(q) and any(
            getattr(inherited, key) is not None for key in ("course_id", "class_id", "grade_level", "major")):
        return _result(catalog, "needs_clarification",
            "你要求分析整个数据集，但当前会话已限定课程、班级、年级或专业。请先清除这些筛选并确认全数据范围，再重新分析。",
            scope=inherited)

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
        for identifier in re.findall(r"(?<![A-Za-z0-9])(?:G\d+-C\d+|I[0-9a-f]{24})(?![A-Za-z0-9])", q, re.I)))
    unknown_ids = [identifier for identifier in requested_courses if identifier not in courses]
    if unknown_ids:
        return _result(catalog, "needs_clarification", "当前课程目录中没有这个课程标识，请选择现有课程。")
    if len(requested_courses) > 1 or len(matching_names) > 1:
        return _result(catalog, "unsupported", "一次只支持一门课程的统计或班级比较，请选择一门课程。")
    course_candidates = [courses[identifier] for identifier in requested_courses]
    if not course_candidates and matching_names:
        course_candidates = [row for row in catalog.courses if row.course_name in matching_names]
    explicit_course_aliases = re.findall(r"(?:课程|course)\s*(C\d{1,3})(?![A-Za-z0-9])", q, re.I)
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

    if (metric_request or compare) and not catalog_request:
        unresolved = _unresolved_course_reference(q, matching_names)
        if unresolved:
            candidates = [row for row in catalog.courses if _norm(unresolved) in _norm(row.course_name)
                          and (values["grade_level"] is None or row.grade_level == values["grade_level"])
                          and (not term or row.source_term is None or row.source_term == term)]
            message = ("这个课程说法还不能确定具体课程。请确认目录中的课程全名后再分析。" if candidates else
                       "当前课程目录找不到问题中指定的课程，请核对课程全名或从现有课程中选择。")
            # A new unresolved subject must not be silently replaced by a
            # previously confirmed session course, even with one candidate.
            return _result(catalog, "needs_clarification", message,
                           scope=SessionScope(**{**values, "course_id": None}),
                           choices=_course_choices(candidates or catalog.courses))

    # Cxx after an explicit course label is a course alias; other Cxxx tokens
    # identify classes. No suffix is selected without checking the directory.
    class_text = re.sub(r"(?:课程|course)\s*C\d{1,3}(?![A-Za-z0-9])", "", q, flags=re.I)
    class_text = re.sub(r"G\d+-C\d+", "", class_text, flags=re.I)
    class_ids = list(dict.fromkeys("C" + digits.zfill(3) for digits in re.findall(r"(?<![A-Za-z0-9])C(\d{1,3})(?![A-Za-z0-9])", class_text, re.I)))
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

    scope_filter = any(values[key] is not None for key in ("class_id", "grade_level", "major"))
    if not values["course_id"] and ((re.search(r"课程|这门|该课|\bfor\b", q, re.I) and not _WHOLE_SCOPE.search(q))
            or (not q.isascii() and not scope_filter and not _WHOLE_SCOPE.search(q))):
        return _result(catalog, "needs_clarification", "当前目录找不到唯一匹配课程，请选择课程或补充课程全名与入学年级。", scope=scope,
                       choices=[_choice("course_id", row.course_id, f"{row.course_name} · {row.grade_level}级") for row in catalog.courses])
    arguments = {"term": term, "course_id": values["course_id"], "class_id": values["class_id"], "grade_level": values["grade_level"]}
    if values["major"] is not None:
        arguments["major"] = values["major"]
    return _result(catalog, "ready", "已匹配课程目录和本次统计范围。", tool="get_score_summary", arguments=arguments, scope=scope, explicit=explicit)


def validate_intent_call(intent: ResolvedIntent, name: str, args) -> None:
    """Reject any model call that is not exactly the server-resolved plan.

    Structural validity is not intent correctness: a well-formed G1-C02 call
    for a G1-C01 question must be blocked before any statistic runs. An extra
    get_catalog call is rejected too — the catalog was already resolved on the
    server, so a model-initiated side query would execute a tool the user did
    not request and attach unrequested evidence to the answer.
    """
    if intent.status != "ready":
        raise AgentError("intent_mismatch", "问题尚未明确，不能执行模型提出的统计。", 422)
    values = args.model_dump() if hasattr(args, "model_dump") else dict(args)
    values = {key: value for key, value in values.items() if key != "major" or value is not None}
    if name == intent.tool:
        expected = dict(intent.arguments)
        actual = dict(values)
        if name == "compare_classes":
            expected["class_ids"] = sorted(expected.get("class_ids", []))
            actual["class_ids"] = sorted(actual.get("class_ids", []))
        if actual == expected:
            return
    raise AgentError("intent_mismatch", "模型选择的工具或课程、班级、学期与已确认意图不一致，已阻止执行。", 422)
