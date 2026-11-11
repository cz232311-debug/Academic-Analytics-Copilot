"""Targeted wide-sheet adapter checks using only invented student records."""
from collections import Counter
from decimal import Decimal
import json
from io import BytesIO
import re
import zipfile

import pytest

from copilot.data.college import parse_college_workbook
from copilot.data.importing import XLSX_TYPE
from copilot.models import DataError
from tests.college_fixture import EMPTY, HEADERS, PRACTICE, ROWS, WEB, college_workbook


def parse(**options):
    return parse_college_workbook("synthetic-college.xlsx", XLSX_TYPE, college_workbook(**options))


def issue_text(result):
    return " ".join(issue.code for issue in result.preview.issues).lower()


def test_single_sheet_wide_export_becomes_existing_five_tables():
    result = parse()
    assert result.preview.can_import
    dataset = result.dataset
    assert dataset is not None and dataset.validate() is dataset
    assert len(dataset.students) == 3 and len(dataset.classes) == 2
    assert len(dataset.courses) == 2
    assert len(dataset.enrollments) == len(dataset.grade_records) == 5
    assert {record.term for record in dataset.enrollments} == {"2025-T1"}


def test_summary_columns_are_never_treated_as_course_scores():
    result = parse()
    assert {course.course_name for course in result.dataset.courses} == {"Web技术基础", "工程实践2"}
    scores = [record.score_cents for record in result.dataset.grade_records if record.status == "graded"]
    assert sorted(scores) == [0, 7250, 8000]
    # Deliberately implausible original summaries must not affect recomputation.
    assert 9900 not in scores and 12300 not in scores


def test_all_blank_course_is_excluded_but_zero_and_partial_scores_survive():
    result = parse()
    assert all(course.course_name != "体育 III" for course in result.dataset.courses)
    excluded = [course for course in result.preview.courses if course.excluded]
    assert any(course.name == "体育 III" for course in excluded)
    assert sum(record.score_cents == 0 for record in result.dataset.grade_records) == 1
    counts = Counter(record.status for record in result.dataset.grade_records)
    assert counts == {"graded": 3, "missing": 1, "exempt": 1}


def test_blank_cells_do_not_invent_missing_exempt_or_failing_enrollments():
    rows = [list(row) for row in ROWS]
    for row in rows:
        row[5] = None
    result = parse(rows=rows)
    assert result.preview.can_import
    assert len(result.dataset.enrollments) == 2
    assert {record.status for record in result.dataset.grade_records} == {"graded"}


def test_compound_header_retains_name_metadata_and_fractional_credits():
    result = parse()
    practice = next(course for course in result.dataset.courses if course.course_name == "工程实践2")
    assert practice.credits == Decimal("1.25")
    assert practice.hours == 20
    assert practice.source_header == PRACTICE
    assert practice.course_category == "集中实践教学环节"
    assert practice.study_nature == "必修"
    assert practice.academic_year == "2025-2026"
    assert practice.source_term == "2025-T1"
    assert practice.source_key
    assert practice.course_id.startswith("I") and len(practice.course_id) == 25


@pytest.mark.parametrize("name", ["Web", "工程实践2", "体育 III", "高等数学 (I)-1"])
def test_valid_digits_latin_letters_and_roman_numerals_are_preserved(name):
    header = f"{name}，专业必修课，必修，学分2，学时32，2025-2026-1"
    result = parse(headers=["学号", "姓名", "班级", header],
                   rows=[["FIXTURE240001", "合成甲", "24软工1班", 60]])
    assert result.preview.can_import
    assert result.dataset.courses[0].course_name == name


def test_mapping_uses_each_class_not_filename_and_keeps_academic_year_separate():
    rows = [list(row) for row in ROWS]
    rows[0][2], rows[1][2], rows[2][2] = "2026级软件工程1班", "25计科2班", "23网工1班"
    result = parse(rows=rows)
    assert result.preview.can_import
    assert {row.grade_level for row in result.dataset.classes} == {2023, 2025, 2026}
    assert {row.major for row in result.dataset.classes} == {
        "software_engineering", "computer_science", "network_engineering"}
    assert {row.term for row in result.dataset.enrollments} == {"2025-T1"}
    assert "mixed" in issue_text(result), "Mixed classes/cohorts must be visible in the preview"


def test_header_position_and_column_order_are_discovered_from_content():
    positions = [4, 2, 1, 0, 7, 5, 3, 6, 8, 9, 10, 11]
    headers = [HEADERS[index] for index in positions]
    rows = [[row[index] for index in positions] for row in ROWS]
    result = parse(headers=headers, rows=rows, title_rows=5)
    assert result.preview.can_import
    assert len(result.dataset.students) == 3
    assert sorted(record.score_cents for record in result.dataset.grade_records if record.status == "graded") == [0, 7250, 8000]


def test_same_name_with_different_hours_is_not_merged():
    headers = ["学号", "姓名", "班级", WEB, WEB.replace("学时32", "学时48")]
    result = parse(headers=headers, rows=[["FIXTURE240001", "合成甲", "24软工1班", 60, 80]])
    assert result.preview.can_import
    assert len(result.dataset.courses) == len(result.dataset.enrollments) == 2
    assert len({course.source_key for course in result.dataset.courses}) == 2
    assert {course.hours for course in result.dataset.courses} == {32, 48}
    assert any("same_name" in issue.code or "distinct" in issue.code or "duplicate" in issue.code
               for issue in result.preview.issues)


def test_identical_duplicate_course_columns_block_confirmation_instead_of_merging():
    result = parse(headers=["学号", "姓名", "班级", WEB, WEB],
                   rows=[["FIXTURE240001", "合成甲", "24软工1班", 60, 80]])
    assert not result.preview.can_import
    assert "duplicate" in issue_text(result)


def test_unknown_status_is_reported_without_echoing_cell_contents():
    rows = [list(row) for row in ROWS]
    secret_marker = "UNRECOGNIZED_SYNTHETIC_PRIVATE_TEXT"
    rows[0][4] = secret_marker
    result = parse(rows=rows)
    assert result.preview.can_import
    assert len(result.dataset.enrollments) == 4
    assert any("status" in issue.code.lower() or "score" in issue.code.lower() for issue in result.preview.issues)
    assert secret_marker not in json.dumps(result.preview.model_dump(mode="json"), ensure_ascii=False)


def test_identical_student_rows_are_deduplicated_with_a_warning():
    result = parse(rows=[*ROWS, list(ROWS[0])])
    assert result.preview.can_import
    assert len(result.dataset.students) == 3 and len(result.dataset.enrollments) == 5
    assert "duplicate" in issue_text(result)


def test_conflicting_duplicate_student_rows_block_import():
    duplicate = list(ROWS[0])
    duplicate[4] = 88
    result = parse(rows=[*ROWS, duplicate])
    assert not result.preview.can_import
    assert "conflict" in issue_text(result) or "duplicate" in issue_text(result)


def test_unknown_major_is_kept_as_unknown_without_dropping_the_student():
    rows = [list(row) for row in ROWS]
    rows[0][2] = "24合成新专业1班"
    result = parse(rows=rows)
    assert result.preview.can_import
    assert len(result.dataset.students) == 3
    assert any(row.major == "unspecified" for row in result.dataset.classes)
    assert "major" in issue_text(result)


def test_preview_rows_use_original_coordinates_and_put_problem_cells_first():
    rows = [list(row) for row in ROWS]
    # An unrecognised grade in a later physical row must be locatable.
    rows[2][4] = "NOT_A_GRADE"
    result = parse(rows=rows)
    sample = result.preview.sample
    assert sample and all({"row", "column", "course", "value", "status", "issue"} <= set(row) for row in sample)
    assert all("student" not in row for row in sample), "re-numbered aliases must not come back"
    assert sample[0]["issue"] == "unknown_status"
    assert sample[0]["row"] == "6" and sample[0]["column"] == "E"  # 2 title rows, header at row 3
    assert "NOT_A_GRADE" not in sample[0]["value"], "unrecognized text is never echoed"
    assert all(not row["issue"] for row in sample[1:]), "all problem cells are listed before normal cells"
    assert result.preview.issue_cell_total == 1
    assert result.preview.sample_total >= result.preview.issue_cell_total + 1


def test_preview_rows_keep_canonical_scores_and_never_leak_identity():
    result = parse()
    visible = json.dumps(result.preview.sample, ensure_ascii=False)
    for original in [row[index] for row in ROWS for index in (0, 1)]:
        assert original not in visible
    # The operator can compare a parsed score with a concrete Excel coordinate.
    assert any(row["row"] == "5" and row["column"] == "E" and row["value"] == "72.5"
               for row in result.preview.sample)
    assert result.preview.sample_total == len(result.preview.sample)


def test_preview_and_normalized_records_do_not_contain_raw_student_identifiers():
    result = parse()
    visible = json.dumps(result.preview.model_dump(mode="json"), ensure_ascii=False)
    normalized = json.dumps(result.dataset.payload(), ensure_ascii=False, default=str)
    for original in [row[index] for row in ROWS for index in (0, 1)]:
        assert original not in visible and original not in normalized
    assert all(student.display_name.startswith("Imported Student ") for student in result.dataset.students)


def test_stale_excel_dimensions_do_not_hide_physical_student_rows():
    result = parse(stale_dimensions=True)
    assert result.preview.can_import
    assert len(result.dataset.students) == 3 and len(result.dataset.enrollments) == 5


def test_second_semester_maps_to_academic_start_year_not_cohort_or_calendar_year():
    header = WEB.replace("2025-2026-1", "2025-2026-2")
    result = parse(headers=["学号", "姓名", "班级", header],
                   rows=[["FIXTURE240001", "合成甲", "24软工1班", 60]])
    assert result.preview.can_import
    assert result.dataset.classes[0].grade_level == 2024
    assert result.dataset.courses[0].academic_year == "2025-2026"
    assert result.dataset.courses[0].source_term == "2025-T2"
    assert result.dataset.enrollments[0].term == "2025-T2"


def test_adapter_keeps_external_relationship_and_file_validation_guards():
    with pytest.raises(DataError):
        parse(external=True)
    with pytest.raises(DataError):
        parse_college_workbook("synthetic-college.xlsx", XLSX_TYPE, b"not-a-workbook")
    with pytest.raises(DataError):
        parse_college_workbook("wrong.csv", XLSX_TYPE, college_workbook())


def test_data_merges_are_reported_and_multiple_sheets_are_rejected():
    result = parse(merge_data=True)
    assert not result.preview.can_import
    assert "merge" in issue_text(result)
    with pytest.raises(DataError):
        parse(extra_sheet=True)


def test_course_with_only_unknown_states_is_explicitly_excluded():
    rows = [list(row) for row in ROWS]
    for row in rows:
        row[5] = "合成未知状态"
    result = parse(rows=rows)
    assert result.preview.can_import
    assert result.preview.counts["excluded_unparsed_courses"] == 1
    course = next(c for c in result.preview.courses if c.name == "工程实践2")
    assert course.excluded and course.reason == "无可导入成绩或状态"
    assert all(c.course_name != "工程实践2" for c in result.dataset.courses)


@pytest.mark.parametrize("old,new,rename_part", [(b'r="E4"', b'r="IW4"', False), (b'<row r="4"', b'<row r="99999"', False),
                                                 (b'r="E4"', b'r="IW4"', True)])
def test_actual_coordinate_limits_are_checked_before_openpyxl_allocates_rows(monkeypatch, old, new, rename_part):
    content = college_workbook()
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(content)) as before, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as after:
        for name in before.namelist():
            value = before.read(name)
            if name == "xl/worksheets/sheet1.xml":
                assert old in value
                value = value.replace(old, new, 1)
                if rename_part:
                    name = "xl/custom-grade.xml"
            elif rename_part and name == "xl/_rels/workbook.xml.rels":
                value = value.replace(b"/xl/worksheets/sheet1.xml", b"/xl/custom-grade.xml")
            after.writestr(name, value)
    def must_not_open(*args, **kwargs):
        raise AssertionError("Coordinate validation must precede workbook loading")
    monkeypatch.setattr("copilot.data.college.load_workbook", must_not_open)
    with pytest.raises(DataError) as error:
        parse_college_workbook("synthetic.xlsx", XLSX_TYPE, output.getvalue())
    assert error.value.code == "college_limits" and error.value.status == 413
