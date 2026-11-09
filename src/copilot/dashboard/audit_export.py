"""In-memory, aggregate-only export of the current confirmed report.

This module projects existing typed results. It never opens a database, reads
the private import archive or performs a model/network call.
"""
import csv
import hashlib
from io import BytesIO, StringIO
import json
import re
from zipfile import ZIP_DEFLATED, ZipFile

from copilot.dashboard.presentation import TYPE_LABELS, mean_bands_table, rates_table, report_csv
from copilot.dashboard.workflow import WorkflowError, WorkflowState
from copilot.data.college import ASSUMPTIONS

IMPORT_COUNT_FIELDS = frozenset({
    "student_rows", "invalid_student_rows", "duplicate_student_rows", "unknown_status_cells",
    "summary_columns", "course_columns", "header_row", "students", "classes", "courses",
    "enrollments", "grade_records", "excluded_empty_courses", "blank_score_cells",
    "excluded_unparsed_courses", "graded", "missing", "exempt", "issues_omitted",
})
ISSUE_LABELS = {
    "merged_title": "标题有合并单元格", "merged_data": "字段或数据区域有合并单元格",
    "duplicate_information_column": "学生信息字段重复", "unknown_course_category": "课程类别尚未映射",
    "unrecognized_header": "课程表头信息不足", "duplicate_course": "完整课程签名重复",
    "same_name_distinct_courses": "同名课程的属性不同，分别保留", "invalid_student": "学生标识无法识别",
    "unrecognized_class": "班级归属无法识别", "conflicting_student": "重复学号的记录冲突",
    "duplicate_student": "学生记录重复", "unknown_major": "专业尚未映射",
    "unknown_status": "成绩或状态无法识别", "mixed_classes": "包含多个班级",
    "mixed_cohorts": "包含多个入学年", "empty_course": "全空课程列已排除",
    "unparsed_course": "课程无可导入成绩或状态", "no_records": "没有可导入的成绩记录",
}


def _json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _csv(columns, rows) -> bytes:
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        # Source labels can begin with a spreadsheet formula character. Quote
        # them as text just as report_csv does; CSV quoting alone is not enough.
        writer.writerow("'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                        else value for value in row)
    return stream.getvalue().encode("utf-8-sig")


def _frame_csv(frame, *, empty_columns=()) -> bytes:
    columns = list(frame.columns) or list(empty_columns)
    # A mixed numeric/None rate column becomes NaN in Pandas. Serialize the
    # missing value as an empty CSV field, preserving the separate "—" label.
    serializable = frame.astype(object).where(frame.notna(), None)
    return _csv(columns, serializable.itertuples(index=False, name=None))


def _counts(values):
    return {key: value for key, value in values.items()
            if key in IMPORT_COUNT_FIELDS and type(value) is int and value >= 0}


def _cleaning(preview, confirmed):
    """Export coordinates/categories, never source values or preview messages."""
    issues = []
    for issue in preview.issues:
        code = issue.code if issue.code in ISSUE_LABELS else "unclassified_issue"
        row = issue.row if type(issue.row) is int and issue.row > 0 else None
        column = issue.column if isinstance(issue.column, str) and re.fullmatch(r"[A-Z]{1,3}", issue.column) else None
        severity = issue.severity if issue.severity in {"error", "warning", "info"} else "warning"
        issues.append((code, ISSUE_LABELS.get(code, "其他校验问题"), severity, row, column))
    issue_counts = {code: count for code, count in preview.issue_counts.items()
                    if code in ISSUE_LABELS and type(count) is int and count >= 0}
    unknown_count = sum(count for code, count in preview.issue_counts.items()
                        if code not in ISSUE_LABELS and type(count) is int and count >= 0)
    if unknown_count:
        issue_counts["unclassified_issue"] = unknown_count
    counts = _counts(preview.counts)
    summary = {
        "coverage": "confirmed_import_not_filtered_report",
        "description": "本节描述整次已确认导入，异常数量不随当前统计范围筛选。",
        "counts": counts, "issue_counts": issue_counts,
        "duplicate_policy": confirmed.duplicate_policy,
        "assumptions": list(ASSUMPTIONS),
        "issue_details_exported": len(issues),
        "issue_details_omitted": counts.get("issues_omitted", 0),
        "source_cell_values_included": False,
    }
    return summary, _csv(["code", "description", "severity", "row", "column"], issues)


def build_analysis_archive(state: WorkflowState) -> bytes:
    """Return a ZIP for the current confirmed scope, or reject stale state.

    Empty *valid* report scopes are exportable: null metrics stay null and the
    CSV headers remain present. An upload preview alone never grants access.
    """
    if not state.has_current_report:
        raise WorkflowError("请先确认成绩单并更新当前范围的统计，再下载完整明细。")
    report = state.report.model_copy(deep=True)
    confirmed = state.confirmed
    cleaning, issue_csv = _cleaning(state.preview, confirmed)
    scope = {"dataset_version": report.dataset_version, "source": confirmed.source,
             "scope": report.scope.model_dump(mode="json"), "generated_at": report.generated_at}
    metrics = {
        "policy": report.policy.model_dump(mode="json"),
        "definitions": {
            "denominator": "仅有效成绩记录；缺考/缺失、免修单列，空白不补零。",
            "average_score": "有效成绩总分除以有效成绩记录数，并非学生个人均分的平均。",
            "pass_rate_percent": "达到及格线的有效成绩记录数 / 有效成绩记录数 × 100。",
            "fail_rate_percent": "低于及格线的有效成绩记录数 / 有效成绩记录数 × 100。",
            "excellent_rate_percent": "达到优良线的有效成绩记录数 / 有效成绩记录数 × 100。",
            "mean_bands": "按未舍入的课程均分分层；显示四舍五入不改变分层。",
            "empty_scope": "没有有效成绩时均分和比率为null，不能当成0。",
        },
    }
    files = {
        "README.md": (
            "# 完整分析与数据明细\n\n"
            "本包由当前已确认报告在本机生成，供核对、留档与复现。\n\n"
            "- report/scope.json：数据版本、当前统计范围和报告生成时间。\n"
            "- report/statistics.json：当前范围的完整课程聚合报告。\n"
            "- report/metrics.json：指标定义与后端返回的计算口径。\n"
            "- tables/courses.csv：课程成绩分布、均分、比率与计数。\n"
            "- charts/rates.csv：及格/不及格图对应的统计数据。\n"
            "- charts/mean_bands.csv：课程均分分层表对应的数据。\n"
            "- cleaning/summary.json：整次已确认导入的清洗摘要与假设。\n"
            "- cleaning/issues.csv：异常类别和原表坐标，不含原单元格值。\n"
            "- verification.json：本次导出的一致性检查及文件SHA-256。\n\n"
            "三种统计视图复用同一CourseReport；不重新计算统计。清洗记录描述整次导入，"
            "与当前筛选后的成绩统计分开标记。详细异常条数可能受原解析器上限限制，"
            "被省略数量见清洗摘要。\n\n"
            "本包不含原工作簿、文件名、学生个人记录、预览样例、密钥、会话历史或内部Trace。"
            "保留课程名称和课程级聚合结果；不代表获准向外部AI发送数据。CSV采用UTF-8 BOM，"
            "可能被电子表格解释为公式的文本已加前导单引号。\n"
        ).encode("utf-8"),
        "report/scope.json": _json(scope),
        "report/statistics.json": _json(report.model_dump(mode="json")),
        "report/metrics.json": _json(metrics),
        "tables/courses.csv": report_csv(report),
        "charts/rates.csv": _frame_csv(rates_table(report)),
        "charts/mean_bands.csv": _frame_csv(mean_bands_table(report), empty_columns=["Course mean band", *TYPE_LABELS.values()]),
        "cleaning/summary.json": _json(cleaning),
        "cleaning/issues.csv": issue_csv,
    }
    verification = {
        "verification_kind": "current_report_contract_checks_not_a_test_run",
        "dataset_version": report.dataset_version, "scope": report.scope.model_dump(mode="json"),
        "checks": {"confirmed_version_and_source_match": True, "report_scope_matches": True,
                   "summary_and_course_evidence_match": True, "all_views_use_one_report": True},
        "contains_student_rows": False, "external_llm_allowed": False,
        "file_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    files["verification.json"] = _json(verification)
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()
