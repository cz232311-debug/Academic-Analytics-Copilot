"""Local-only adapter for one-sheet college grade matrices.

Raw identities live only while parsing. Results contain internal aliases, course
metadata and coordinate-only issues, never original names or student numbers.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import posixpath
import re
import unicodedata
import warnings
import zipfile
from typing import Literal

from defusedxml import ElementTree as SafeXML
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter, range_boundaries
from openpyxl.xml import DEFUSEDXML
from pydantic import Field

from copilot.data.importing import MAX_ROWS, _validate_xlsx, validate_file
from copilot.models import (ClassRecord, CourseRecord, DataError, Dataset, EnrollmentRecord,
                            GradeRecord, Record, StudentRecord)
from copilot.schemas import DatasetInfo

MAX_COLUMNS, HEADER_SEARCH_ROWS, MAX_ISSUES = 256, 25, 200
# Preview cell review rows are bounded so a large workbook cannot flood the UI.
# The archive never persists these rows (see import_archive.stage).
MAX_REVIEW_CELLS = 200
# Hard ceiling on cells inspected while building the preview, so a workbook
# with thousands of rows and hundreds of course columns cannot exhaust memory.
MAX_REVIEW_COLLECT = 20000
DuplicatePolicy = Literal["block", "first", "last"]
# Aliases are data rather than a sequence of filename-dependent branches.
MAJOR_ALIASES = {
    "软件工程": "software_engineering", "软工": "software_engineering",
    "计算机科学与技术": "computer_science", "计科": "computer_science",
    "网络工程": "network_engineering", "网工": "network_engineering",
}
CATEGORY_ALIASES = {
    "通识必修课": "general_required", "公共必修课": "general_required",
    "通识公共选修课": "general_elective", "通识选修课": "general_elective", "公共选修课": "general_elective",
    "专业必修课": "professional_required", "学科基础必修课": "professional_required", "跨专业必修课": "professional_required",
    "专业选修课": "professional_elective", "集中实践教学环节": "practical", "实践课": "practical",
}
INFO_ALIASES = {
    "学号": "student", "学生学号": "student", "姓名": "name", "学生姓名": "name",
    "班级": "class", "行政班级": "class", "行政班": "class", "年级": "cohort", "入学年级": "cohort",
    "专业": "major", "专业名称": "major", "序号": "ignore", "学院": "ignore", "性别": "ignore",
}
SUMMARY_HEADERS = {"课程门数", "不及格门数", "平均分", "总分", "所得学分", "获得学分", "总学分", "学分",
                   "平均学分绩", "平均学分绩点", "平均绩点", "绩点", "排名", "班级排名", "专业排名", "学分绩点"}
NATURES = {"必修", "选修", "限选", "公选", "任选"}
EXPLICIT_STATES = {"缺考": "missing", "免修": "exempt", "missing": "missing", "exempt": "exempt"}
EXPLICIT_STATE_LABELS = {"missing": "缺考", "exempt": "免修"}
ASSUMPTIONS = [
    "全空课程列仅在本次导入中排除，不删除已有课程目录。",
    "空白单元格不生成选课记录，不推断缺考、免修、不及格或未选课；0分保留。",
    "只导入明确的0–100数值成绩或缺考/免修状态；异常记录列出并排除，需确认。",
    "覆盖率仅针对成功解析记录；原表平均分、学分、绩点、排名等汇总值不导入、不反推。",
    "同源课程按入学年保守分开，不推断跨年级选课或重修规则。",
    "学号冲突默认阻止提交；只有操作者明确选择时才保留第一行或最后一行，其余行列入排除数量。",
    "确认后替换当前学生及选课成绩，保留已有课程目录；上传数据禁止Agent与外部模型访问。",
]


class ImportIssue(Record):
    code: str
    severity: str = "warning"
    row: int | None = None
    column: str | None = None
    message: str


class CourseColumn(Record):
    column: str
    name: str
    term: str | None = None
    credits: str | None = None
    hours: int | None = None
    category: str | None = None
    nature: str | None = None
    excluded: bool = False
    reason: str | None = None


class CollegePreview(Record):
    can_import: bool
    counts: dict[str, int]
    terms: list[str]
    majors: dict[str, int]
    classes: list[str]
    courses: list[CourseColumn]
    issues: list[ImportIssue]
    issue_counts: dict[str, int]
    # Every entry is located by its ORIGINAL workbook coordinates (row + column
    # letter), never by a re-numbered alias. Raw cell values are shown only in
    # this in-memory preview response so an operator can compare with the XLSX;
    # the archive receipt deliberately omits both samples and row previews.
    sample: list[dict[str, str]]
    sample_total: int = 0
    issue_cell_total: int = 0
    duplicate_policy: DuplicatePolicy = "block"
    assumptions: list[str] = Field(default_factory=lambda: list(ASSUMPTIONS))
    preview_token: str | None = None
    expected_version: str | None = None
    before_counts: dict[str, int] = Field(default_factory=dict)
    expires_in_seconds: int = 900


class CollegeCommitResult(Record):
    dataset: DatasetInfo
    imported_counts: dict[str, int]
    retained_course_count: int
    archive_id: str | None = None
    archive_status: Literal["complete", "pending"] | None = None


@dataclass
class CollegeParseResult:
    dataset: Dataset | None
    preview: CollegePreview


def clean_text(value) -> str:
    return unicodedata.normalize("NFC", str(value)).strip() if value is not None else ""


def parse_term(value: str) -> tuple[str, str]:
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    match = re.fullmatch(r"((?:19|20|21)\d{2})[-/]((?:19|20|21)\d{2})(?:学年)?(?:[-/]|第)([12一二])(?:学期)?", text)
    if match and int(match[2]) == int(match[1]) + 1:
        semester = {"一": "1", "二": "2"}.get(match[3], match[3])
        return f"{match[1]}-T{semester}", f"{match[1]}-{match[2]}"
    raise ValueError("Unrecognized academic term")


def parse_class(value: str, cohort_value="", major_value="") -> tuple[tuple, str]:
    if len(value) > 200 or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Invalid class label")
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    match = re.fullmatch(r"((?:19|20|21)\d{2}|\d{2})级?(.+?)[(]?([0-9]+)[)]?班?", text)
    if not match:
        raise ValueError("Unrecognized class")
    cohort = int(match[1]) + (2000 if len(match[1]) == 2 else 0)
    major_name = match[2].strip("-_")
    major = MAJOR_ALIASES.get(major_name, "unspecified")
    if not 1900 <= cohort <= 2199 or not 0 < int(match[3]) <= 999:
        raise ValueError("Unrecognized class")
    if cohort_value:
        explicit = re.fullmatch(r"((?:19|20|21)\d{2}|\d{2})级?", clean_text(cohort_value))
        if not explicit or (int(explicit[1]) + (2000 if len(explicit[1]) == 2 else 0)) != cohort:
            raise ValueError("Conflicting cohort")
    if major_value:
        declared = MAJOR_ALIASES.get(clean_text(major_value), clean_text(major_value))
        if declared != (major if major != "unspecified" else major_name):
            raise ValueError("Conflicting major")
    # Unknown majors remain separated using their source labels.
    return (cohort, major if major != "unspecified" else major_name, int(match[3])), major


def parse_course_header(value: str) -> dict:
    raw = clean_text(value)
    if len(raw) > 1000 or any(unicodedata.category(c).startswith("C") and c not in "\n\r\t" for c in raw):
        raise ValueError("Invalid course header")
    parts = [p.strip() for p in re.split(r"[,，;；\n]+", raw) if p.strip()]
    if len(parts) < 5:
        raise ValueError("Incomplete course metadata")
    # Only split at an explicit metadata category; course punctuation is retained.
    category_index = next((i for i, p in enumerate(parts) if p in CATEGORY_ALIASES or p.endswith("课") and i > 0), None)
    if category_index is None or category_index == 0:
        raise ValueError("Missing course category")
    name, category = re.sub(r"\s+", " ", "，".join(parts[:category_index])), parts[category_index]
    data = {"course_name": name, "course_category": category, "course_type": CATEGORY_ALIASES.get(category, "unspecified"),
            "source_header": raw, "source_code": None}
    for part in parts[category_index + 1:]:
        if part in NATURES:
            field, parsed = "study_nature", part
        elif match := re.fullmatch(r"学分\s*[:：]?\s*(\d+(?:\.\d{1,2})?)", part):
            field, parsed = "credits", Decimal(match[1])
            if not 0 < parsed <= 100:
                raise ValueError("Invalid credits")
        elif match := re.fullmatch(r"学时\s*[:：]?\s*(\d+)", part):
            field, parsed = "hours", int(match[1])
            if not 0 <= parsed <= 2000:
                raise ValueError("Invalid hours")
        elif match := re.fullmatch(r"课程(?:代码|编号)\s*[:：]\s*([A-Za-z0-9._/-]{1,100})", part):
            field, parsed = "source_code", match[1]
        else:
            term, year = parse_term(part)
            field, parsed = "source_term", term
            data["academic_year"] = year
        if field in data and data[field] is not None:
            raise ValueError("Repeated course metadata")
        data[field] = parsed
    if not {"credits", "hours", "study_nature", "source_term"} <= data.keys() or not name or len(name) > 200:
        raise ValueError("Incomplete course metadata")
    if data["credits"] == data["credits"].to_integral_value():
        data["credits"] = int(data["credits"])
    identity = {k: str(v) for k, v in data.items() if k not in {"source_header", "course_type"}}
    data["source_key"] = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return data


def parse_score(value) -> tuple[str, int | None] | None:
    text = clean_text(value)
    if not text:
        return None
    if text.lower() in EXPLICIT_STATES:
        return EXPLICIT_STATES[text.lower()], None
    # Do not assign invented numeric scores to qualitative grades or annotations.
    if not re.fullmatch(r"(?:\d{1,3})(?:\.\d{1,2})?", text):
        raise ValueError("Unknown status or score")
    score = Decimal(text)
    if score < 0 or score > 100:
        raise ValueError("Score outside range")
    return "graded", int(score * 100)


def _workbook_structure(content):
    """Check actual coordinates before openpyxl can allocate sparse row widths."""
    merged = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = SafeXML.fromstring(archive.read("xl/workbook.xml"), forbid_dtd=True)
        sheets = [node for node in root.iter() if node.tag.endswith("}sheet")]
        if len(sheets) != 1:
            raise DataError("学院成绩适配器仅支持一个成绩工作表", code="college_structure")
        relationship_id = next((value for key, value in sheets[0].attrib.items() if key.endswith("}id")), None)
        relationships = SafeXML.fromstring(archive.read("xl/_rels/workbook.xml.rels"), forbid_dtd=True)
        targets = [node.attrib.get("Target", "") for node in relationships
                   if node.attrib.get("Id") == relationship_id and node.attrib.get("Type", "").endswith("/worksheet")]
        if len(targets) != 1:
            raise DataError("无法定位唯一成绩工作表", code="college_structure")
        target = posixpath.normpath(targets[0].lstrip("/") if targets[0].startswith("/") else "xl/" + targets[0])
        if not target.startswith("xl/") or target not in archive.namelist():
            raise DataError("工作表关系无效", code="college_structure")
        for node in SafeXML.fromstring(archive.read(target), forbid_dtd=True).iter():
            if node.tag.endswith("}c"):
                coordinate = node.attrib.get("r", "")
                match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,4})", coordinate)
                if not match or column_index_from_string(match[1]) > MAX_COLUMNS or int(match[2]) > MAX_ROWS["students"] + HEADER_SEARCH_ROWS:
                    raise DataError("实际单元格坐标超出256列或5025行限制", code="college_limits", status=413)
            elif node.tag.endswith("}row"):
                if not re.fullmatch(r"[1-9][0-9]{0,4}", node.attrib.get("r", "")) or int(node.attrib["r"]) > MAX_ROWS["students"] + HEADER_SEARCH_ROWS:
                    raise DataError("实际行号超出成绩表限制", code="college_limits", status=413)
            elif node.tag.endswith("}mergeCell"):
                ref = node.attrib.get("ref", "")
                if len(ref) > 24:
                    raise DataError("合并单元格范围无法识别", code="college_structure")
                left, top, right, bottom = range_boundaries(ref)
                if left is None or right is None or top is None or bottom is None or right > MAX_COLUMNS or bottom > MAX_ROWS["students"] + HEADER_SEARCH_ROWS:
                    raise DataError("合并区域超出成绩表限制", code="college_limits", status=413)
                merged.append(ref)
    return merged


def parse_college_workbook(filename: str, content_type: str, content: bytes, *, duplicate_policy: DuplicatePolicy = "block") -> CollegeParseResult:
    if duplicate_policy not in {"block", "first", "last"}:
        raise DataError("不支持的重复行处理策略", code="invalid_request")
    validate_file(filename, content_type, content, ".xlsx")
    _validate_xlsx(content)
    if not DEFUSEDXML:
        raise RuntimeError("Hardened XML parser is required")
    issues, issue_counts, fatal = [], Counter(), False

    def issue(code, message, *, row=None, col=None, blocking=False):
        nonlocal fatal
        fatal |= blocking
        issue_counts[code] += 1
        if len(issues) < MAX_ISSUES:
            issues.append(ImportIssue(code=code, severity="error" if blocking else "warning", row=row,
                                      column=get_column_letter(col + 1) if col is not None else None, message=message))

    workbook = None
    try:
        merged_ranges = _workbook_structure(content)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
        if len(workbook.worksheets) != 1 or workbook.worksheets[0].sheet_state != "visible":
            raise DataError("学院成绩适配器仅支持一个可见成绩工作表", code="college_structure")
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()  # College exporters may advertise an incorrect dimension.
        rows = sheet.iter_rows(max_col=MAX_COLUMNS)
        header, header_row = None, None
        for number, cells in enumerate(rows, 1):
            if number > HEADER_SEARCH_ROWS:
                break
            if len(cells) > MAX_COLUMNS:
                raise DataError("成绩表列数超过256列", code="college_limits", status=413)
            values = [clean_text(c.value) for c in cells]
            aliases = [INFO_ALIASES.get(re.sub(r"\s+", "", v)) for v in values]
            if "student" in aliases and "class" in aliases:
                header, header_row = values, number
                if any(c.data_type == "f" for c in cells):
                    raise DataError("成绩表头不能含公式", code="college_structure")
                break
        if header is None:
            raise DataError("前25行未识别到学号和班级表头；请使用单行字段表头", code="college_structure")
        for merged in merged_ranges:
            _, top, _, bottom = range_boundaries(merged)
            issue("merged_title" if bottom < header_row else "merged_data", "检测到合并单元格；标题保留，字段或数据区域合并须先拆分。",
                  row=top, blocking=bottom >= header_row)
        info, columns, signatures, ignored = {}, {}, defaultdict(list), 0
        for index, label in enumerate(header):
            normalized = re.sub(r"\s+", "", label)
            role = INFO_ALIASES.get(normalized)
            if role:
                if role != "ignore" and role in info:
                    issue("duplicate_information_column", "学生信息字段重复，无法可靠选择。", col=index, blocking=True)
                info[role] = index
            elif normalized in SUMMARY_HEADERS:
                ignored += 1
            elif label:
                try:
                    data = parse_course_header(label)
                    columns[index] = data
                    signatures[data["source_key"]].append(index)
                    if data["course_type"] == "unspecified":
                        issue("unknown_course_category", "课程类别未映射，保留原类别并标为未提供。", col=index)
                except (ValueError, InvalidOperation):
                    issue("unrecognized_header", "此列不是已识别的汇总字段，且课程元数据不完整；本次排除。", col=index)
        if not columns:
            raise DataError("没有可解析的课程列；课程表头需含名称、类别、性质、学分、学时及学年学期", code="college_structure")
        duplicate_columns = set()
        for indices in signatures.values():
            if len(indices) > 1:
                duplicate_columns.update(indices)
                for index in indices:
                    issue("duplicate_course", "完整课程签名重复；请核对重复列后重新上传，不自动合并。", col=index, blocking=True)
        names = Counter(data["course_name"] for data in columns.values())
        for index, data in columns.items():
            if names[data["course_name"]] > 1 and index not in duplicate_columns:
                issue("same_name_distinct_courses", "同名课程属性不同，分别保留，不合并。", col=index)
        records, by_student, selected_rows, class_info = [], {}, {}, {}
        populated, parsed_columns, blanks = Counter(), Counter(), 0
        counts = Counter(student_rows=0, invalid_student_rows=0, duplicate_student_rows=0, unknown_status_cells=0,
                         summary_columns=ignored, course_columns=len(columns), header_row=header_row)
        # Coordinate-located review rows. Identity columns (学号/姓名/班级) are
        # never included, and unrecognized cell contents are never echoed.
        review_cells = []
        for number, cells in enumerate(rows, header_row + 1):
            if number > MAX_ROWS["students"] + HEADER_SEARCH_ROWS:
                raise DataError("学生记录超过5000行限制", code="college_limits", status=413)
            if len(cells) > MAX_COLUMNS:
                raise DataError("成绩表列数超过256列", code="college_limits", status=413)
            values = [clean_text(c.value) for c in cells]
            values += [""] * (len(header) - len(values))
            if not any(values):
                continue
            counts["student_rows"] += 1
            sid = values[info["student"]]
            # Identifiers may contain a letter (e.g. transfer entry); never log them.
            if not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", sid):
                counts["invalid_student_rows"] += 1
                issue("invalid_student", "学生标识缺失或格式无法可靠识别；整行排除。", row=number)
                continue
            try:
                class_key, major = parse_class(values[info["class"]], values[info["cohort"]] if "cohort" in info else "",
                                               values[info["major"]] if "major" in info else "")
            except ValueError:
                counts["invalid_student_rows"] += 1
                issue("unrecognized_class", "班级归属无法识别或与年级/专业字段冲突；整行排除。", row=number)
                continue
            relevant = (class_key, tuple(values[i] for i in columns))
            if sid in by_student:
                counts["duplicate_student_rows"] += 1
                conflict = by_student[sid][1] != relevant
                issue("conflicting_student" if conflict else "duplicate_student",
                      f"与第{by_student[sid][0]}行的学生标识重复；一致行去重，冲突按已选策略处理（{duplicate_policy}）。",
                      row=number, blocking=conflict and duplicate_policy == "block")
                if not conflict or duplicate_policy != "last":
                    counts["excluded_duplicate_score_cells"] += sum(bool(values[i]) for i in columns)
                    continue
                previous_values = selected_rows[sid][2]
                counts["excluded_duplicate_score_cells"] += sum(bool(previous_values[i]) for i in columns)
            by_student[sid] = (number, relevant)
            selected_rows[sid] = (number, cells, values, class_key, major)
        for sid, (number, cells, values, class_key, major) in selected_rows.items():
            class_info[class_key] = (major, values[info["class"]])
            if major == "unspecified":
                issue("unknown_major", "专业不在当前别名表；保留班级原标签，专业标为未提供。", row=number)
            parsed_cells = []
            for index, data in columns.items():
                if index in duplicate_columns:
                    continue
                value = values[index]
                if value:
                    populated[index] += 1
                else:
                    blanks += 1
                status, score, cell_issue, shown = "", None, "", ""
                try:
                    if index < len(cells) and cells[index].data_type == "f":
                        raise ValueError
                    parsed = parse_score(value)
                    if parsed is not None:
                        status, score = parsed
                        parsed_cells.append((index, *parsed))
                        parsed_columns[index] += 1
                        shown = str(Decimal(score) / 100) if score is not None else EXPLICIT_STATE_LABELS.get(status, status)
                except (ValueError, InvalidOperation):
                    status, cell_issue = "unrecognized", "unknown_status"
                    # Never echo unrecognized cell text: it may contain private
                    # annotations. The coordinate is enough to find it in the XLSX.
                    shown = "—"  # coordinate + status already explain what to fix
                    counts["unknown_status_cells"] += 1
                    issue("unknown_status", "成绩或状态无法识别；请按下方坐标回原表核对，本次不生成记录。", row=number, col=index)
                if value and len(review_cells) < MAX_REVIEW_COLLECT:
                    review_cells.append({"row": str(number), "column": get_column_letter(index + 1),
                                         "course": data["course_name"], "value": shown,
                                         "status": status or "blank", "issue": cell_issue})
            records.append((sid, class_key, parsed_cells))
        if len(records) > MAX_ROWS["students"] or len(class_info) > MAX_ROWS["classes"]:
            raise DataError("学生或班级数超过导入限制", code="college_limits", status=413)
        if len(class_info) > 1:
            issue("mixed_classes", "文件包含多个班级；按每行班级分别识别入学年及专业，不使用文件名推断。")
        if len({k[0] for k in class_info}) > 1:
            issue("mixed_cohorts", "文件包含多个入学年，与课程学年分开保存。")
        excluded = {i for i in columns if not populated[i] and i not in duplicate_columns}
        for index in excluded:
            issue("empty_course", "全部有效学生的此课程单元格为空，本次排除，不删除已有目录。", col=index)
        unparsed = {i for i in columns if populated[i] and not parsed_columns[i] and i not in duplicate_columns}
        for index in unparsed:
            issue("unparsed_course", "此课程没有可导入的成绩或状态，整列排除；异常单元格已列出。", col=index)
        classes = tuple(ClassRecord(class_id=f"C{n:03}", grade_level=key[0], class_label=class_info[key][1], major=class_info[key][0])
                        for n, key in enumerate(sorted(class_info), 1))
        class_ids = {key: row.class_id for key, row in zip(sorted(class_info), classes)}
        courses, students, enrollments, grades = {}, [], [], []
        for n, (sid, key, cells) in enumerate(sorted(records, key=lambda r: r[0]), 1):
            student_id = f"S{n:04}"
            students.append(StudentRecord(student_id=student_id, class_id=class_ids[key], display_name=f"Imported Student {n:04}"))
            for index, status, score in cells:
                data = columns[index]
                course_id = "I" + hashlib.sha256(f"{data['source_key']}:{key[0]}".encode()).hexdigest()[:24]
                courses.setdefault(course_id, CourseRecord(course_id=course_id, grade_level=key[0], **data))
                number = len(enrollments) + 1
                if number > MAX_ROWS["enrollments"]:
                    raise DataError("选课成绩记录超过50000条", code="college_limits", status=413)
                enrollment_id = f"E{number:05}"
                enrollments.append(EnrollmentRecord(enrollment_id=enrollment_id, student_id=student_id, course_id=course_id, term=data["source_term"]))
                grades.append(GradeRecord(grade_record_id=f"R{number:05}", enrollment_id=enrollment_id, status=status, score_cents=score))
        # Problem cells come first, then ordinary rows in ORIGINAL workbook order.
        # Every entry carries its real Excel coordinates so the operator can go
        # straight to that cell in the source file. Identities are never echoed.
        problem_cells = [cell for cell in review_cells if cell["issue"]]
        normal_cells = [cell for cell in review_cells if not cell["issue"]]
        samples = (problem_cells + normal_cells)[:MAX_REVIEW_CELLS]
        sample_total, issue_cell_total = len(review_cells), len(problem_cells)
        dataset = None
        if students and courses and enrollments and not fatal:
            dataset = Dataset(classes, tuple(students), tuple(courses[k] for k in sorted(courses)), tuple(enrollments), tuple(grades)).validate()
        elif not enrollments:
            issue("no_records", "没有成功解析的选课成绩，不能提交空导入。", blocking=True)
        counts.update(students=len(students), classes=len(classes), courses=len(courses), enrollments=len(enrollments),
                      grade_records=len(grades), excluded_empty_courses=len(excluded), blank_score_cells=blanks,
                      excluded_unparsed_courses=len(unparsed),
                      graded=sum(g.status == "graded" for g in grades), missing=sum(g.status == "missing" for g in grades),
                      exempt=sum(g.status == "exempt" for g in grades), issues_omitted=max(0, sum(issue_counts.values()) - len(issues)))
        preview = CollegePreview(can_import=dataset is not None, duplicate_policy=duplicate_policy, counts=dict(counts), terms=sorted({e.term for e in enrollments}),
                                majors=dict(Counter(class_info[key][0] for _, key, _ in records)),
                                classes=[row.class_label for row in classes], issues=issues, issue_counts=dict(issue_counts),
                                sample=samples, sample_total=sample_total, issue_cell_total=issue_cell_total,
                                courses=[CourseColumn(column=get_column_letter(i+1), name=data["course_name"], term=data["source_term"],
                                           credits=str(data["credits"]), hours=data["hours"], category=data["course_category"], nature=data["study_nature"],
                                           excluded=i in excluded or i in duplicate_columns or i in unparsed,
                                           reason="全空" if i in excluded else "重复签名" if i in duplicate_columns else "无可导入成绩或状态" if i in unparsed else None)
                                         for i, data in columns.items()])
        return CollegeParseResult(dataset, preview)
    except DataError:
        raise
    except Exception:
        # Never expose workbook contents through parser exceptions or error logs.
        raise DataError("学院成绩表解析失败；请核对格式与字段元数据", code="college_structure") from None
    finally:
        if workbook is not None:
            workbook.close()
