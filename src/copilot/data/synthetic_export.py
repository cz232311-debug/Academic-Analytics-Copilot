"""Export the active generated dataset from one SQLite read snapshot.

No random generator, private import archive, provider or student-list API is
used. Uploaded provenance is rejected before any business rows are read.
"""
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from copilot.analytics import BINS
from copilot.models import DataError
from copilot.schemas import ReportPolicy
from copilot.storage import Database

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAJORS = {"software_engineering": "软件工程", "computer_science": "计算机科学与技术",
          "network_engineering": "网络工程", "business_administration": "工商管理",
          "information_systems": "信息系统", "unspecified": "未提供"}
COURSE_TYPES = {"general_required": "通识必修", "general_elective": "通识选修",
                "professional_required": "专业/学科必修", "professional_elective": "专业选修",
                "practical": "实践教学", "unspecified": "未提供"}
STATUSES = {"graded": "有成绩", "missing": "缺考/缺失", "exempt": "免修"}
_TABLES = (("classes", "class_id"), ("students", "student_id"), ("courses", "course_id"),
           ("enrollments", "enrollment_id"), ("grade_records", "grade_record_id"))


def _snapshot(database: Database, expected_version: str):
    if not isinstance(expected_version, str) or re.fullmatch(r"[0-9a-f]{64}", expected_version) is None:
        raise DataError("请提供当前数据版本后再下载。", code="invalid_request", status=422)
    with database.connect() as connection:
        connection.execute("BEGIN")
        metadata = database.metadata(connection)
        database.check_access(metadata, expected_version=expected_version, generated_only=True)
        # Identifiers are fixed module constants, never request parameters.
        tables = {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {key}")]
                  for table, key in _TABLES}
        return metadata, tables


def _sheet(workbook, title, headers, rows):
    sheet = workbook.create_sheet(title)
    for number, values in enumerate((headers, *rows), 1):
        for column, value in enumerate(values, 1):
            cell = sheet.cell(number, column, value)
            # Explicit XLSX string type prevents formula/DDE interpretation
            # while preserving the exact name/ID without an added apostrophe.
            if isinstance(value, str):
                cell.data_type = "s"
                if value.lstrip().startswith(("=", "+", "-", "@")):
                    cell.quotePrefix = True
            if number == 1:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="175CD3")
                cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.row_dimensions[1].height = 34
    for column, header in enumerate(headers, 1):
        sheet.column_dimensions[get_column_letter(column)].width = 30 if ("标识" in header or "课程" in header) else 22
    return sheet


def build_synthetic_export(database: Database, expected_version: str) -> bytes:
    """Return an XLSX matching the current confirmed generated snapshot.

    ``expected_version`` is mandatory so a rendered download control cannot
    silently return a newly switched dataset. The source/version check and all
    five reads share the same SQLite transaction.
    """
    metadata, tables = _snapshot(database, expected_version)
    classes = {row["class_id"]: row for row in tables["classes"]}
    students = {row["student_id"]: row for row in tables["students"]}
    courses = {row["course_id"]: row for row in tables["courses"]}
    enrollments = {row["enrollment_id"]: row for row in tables["enrollments"]}
    details = []
    try:
        for grade in tables["grade_records"]:
            enrollment = enrollments[grade["enrollment_id"]]
            student = students[enrollment["student_id"]]
            classroom = classes[student["class_id"]]
            course = courses[enrollment["course_id"]]
            score = Decimal(grade["score_cents"]) / 100 if grade["score_cents"] is not None else None
            details.append((grade["grade_record_id"], enrollment["enrollment_id"], student["student_id"],
                            student["display_name"], classroom["class_id"], classroom["class_label"], classroom["grade_level"],
                            MAJORS[classroom["major"]], course["course_id"], course["course_name"], enrollment["term"],
                            STATUSES[grade["status"]], grade["status"], score, grade["score_cents"]))
    except KeyError:
        raise DataError("当前数据关联不完整，无法生成可核对的成绩单。", code="export_snapshot_invalid", status=500) from None
    if len(tables["grade_records"]) != len(tables["enrollments"]):
        raise DataError("当前选课与成绩数量不一致，无法生成可核对的成绩单。", code="export_snapshot_invalid", status=500)

    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.creator = "Academic Analytics Copilot"
    workbook.properties.title = "当前已激活合成成绩数据快照"
    grade_sheet = _sheet(workbook, "成绩明细",
        ("成绩记录标识", "选课记录标识", "学生标识", "学生别名", "班级标识", "班级", "入学年级", "专业",
         "课程标识（course_id）", "课程", "原始学期标识", "成绩状态", "状态标识", "成绩", "成绩（百分之一分）"), details)
    for row in grade_sheet.iter_rows(min_row=2, min_col=14, max_col=14):
        row[0].number_format = "0.00"
    _sheet(workbook, "课程目录",
        ("课程标识（course_id）", "课程", "入学年级", "课程类别", "课程类别标识", "学分", "学时", "原始类别",
         "修读性质", "学年", "原始学期标识", "课程代码", "课程源标识", "原始课程表头"),
        [(row["course_id"], row["course_name"], row["grade_level"], COURSE_TYPES[row["course_type"]], row["course_type"],
          Decimal(str(row["credits"])), row["hours"], row["course_category"], row["study_nature"], row["academic_year"],
          row["source_term"], row["source_code"], row["source_key"], row["source_header"]) for row in tables["courses"]])
    _sheet(workbook, "班级目录", ("班级标识", "班级", "入学年级", "专业", "专业标识"),
        [(row["class_id"], row["class_label"], row["grade_level"], MAJORS[row["major"]], row["major"]) for row in tables["classes"]])
    _sheet(workbook, "学生目录", ("学生标识", "学生别名", "班级标识", "班级"),
        [(row["student_id"], row["display_name"], row["class_id"], classes[row["class_id"]]["class_label"])
         for row in tables["students"]])
    policy = ReportPolicy(score_bins=[row[0] for row in BINS])
    status_counts = {status: sum(row["status"] == status for row in tables["grade_records"]) for status in STATUSES}
    _sheet(workbook, "数据说明", ("项目", "内容"), [
        ("数据版本（dataset_version）", metadata["dataset_version"]), ("数据来源", metadata["source"]),
        ("数据库契约版本", metadata["schema_version"]), ("生成数据编号", metadata["seed"]),
        ("导出范围", "当前已激活数据的完整SQLite只读快照，未重新随机生成，未按某条聊天问题筛选。"),
        ("学生数", len(tables["students"])), ("班级数", len(tables["classes"])), ("课程数", len(tables["courses"])),
        ("选课记录数", len(tables["enrollments"])), ("成绩记录数", len(tables["grade_records"])),
        ("有成绩记录数", status_counts["graded"]), ("缺考/缺失记录数", status_counts["missing"]), ("免修记录数", status_counts["exempt"]),
        ("学生标识说明", "导出数据库中已保存的内部学生标识和系统别名，不重建生成前的原始姓名或学号。"),
        ("空值说明", "空白原单元格不生成选课成绩；缺考/免修分数为空，0为有效成绩。"),
        ("平均分口径", "有效成绩总分 / 有效成绩记录数；不是学生个人均分的平均。"),
        ("学分加权口径", "有效成绩×对应学分之和 / 有效成绩学分之和。"),
        ("及格线", f"{policy.pass_threshold_cents / 100:g}分"), ("优良线", f"{policy.excellent_threshold_cents / 100:g}分"),
        ("成绩分段", "、".join(policy.score_bins)), ("分母", policy.denominator),
        ("课程均分分层", policy.mean_band_rule), ("显示舍入", policy.rounding),
        ("导出时间（UTC）", datetime.now(timezone.utc).isoformat()),
        ("使用边界", "只允许generated来源；所有记录为合成数据。文件不含密钥、模型消息或内部Trace。"),
    ])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
