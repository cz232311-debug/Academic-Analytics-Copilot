"""Phase 3D semantic regressions: interrogative phrasing is never a course.

Found by the full catalog x pattern audit (scripts/phase3d_semantic_audit.py):
question words such as 有没有人 / 有多少人 / 谁 were treated as unresolved
course subjects, and review phrasing (有哪些值得关注的地方) fell through to
unsupported — for EVERY catalog course, not just one example. These tests pin
the generic fix. No live provider is used.
"""
import pytest

from copilot.agent.semantics import resolve_question
from tests.test_phase3c_semantics import college_db  # noqa: F401


@pytest.mark.parametrize("template", [
    "{course}有没有人不及格",
    "{course}有没有人及格",
    "{course}有多少人不及格",
    "{course}谁不及格",
    "{course}的成绩怎么样",
    "{course}有哪些值得关注的地方",
    "{course}表现如何",
    "{course}的情况怎么样",
])
def test_explicit_course_with_interrogative_phrasing_resolves(college_db, template):
    catalog = college_db.reporting_catalog()
    for course in catalog.courses:
        intent = resolve_question(template.format(course=course.course_name), catalog)
        assert intent.status == "ready", f"{template} @ {course.course_name}: {intent.status}"
        assert intent.tool == "get_score_summary"
        assert intent.arguments["course_id"] == course.course_id


def test_class_scoped_interrogative_keeps_class_and_course(college_db):
    catalog = college_db.reporting_catalog()
    course = next(row for row in catalog.courses if row.course_name == "工程实践2")
    klass = catalog.classes[0]
    intent = resolve_question(f"{klass.class_label}的{course.course_name}有没有人不及格", catalog)
    assert intent.status == "ready"
    assert intent.tool == "get_score_summary"
    assert intent.arguments["course_id"] == course.course_id
    assert intent.arguments["class_id"] == klass.class_id


@pytest.mark.parametrize("question", [
    "分析体育课的成绩。",          # generic family reference still clarifies
    "分析火星课的成绩。",          # unknown subject still clarifies
    "分析体育 IIIII 的成绩。",     # near-miss name still clarifies
])
def test_ambiguous_and_unknown_subjects_still_clarify(college_db, question):
    catalog = college_db.reporting_catalog()
    intent = resolve_question(question, catalog)
    assert intent.status == "needs_clarification"
    assert intent.tool is None


@pytest.mark.parametrize("question", [
    "你知道针对体育课的成绩分析嘛",
    "你能分析体育成绩吗？",
])
def test_capability_questions_still_describe_scope(college_db, question):
    catalog = college_db.reporting_catalog()
    intent = resolve_question(question, catalog)
    assert intent.status == "needs_clarification"
    assert intent.tool is None
    assert "可以进行" in intent.message


def test_unsupported_requests_still_rejected(college_db):
    catalog = college_db.reporting_catalog()
    for question in ("预测某位同学下学期会不会挂科",
                     "体育 III 为什么考得这么好",
                     "给我看看体育 III 的成绩排名"):
        intent = resolve_question(question, catalog)
        assert intent.status == "unsupported", question
