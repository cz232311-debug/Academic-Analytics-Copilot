"""Independent workbook -> Adapter -> SQLite/report checks; fabricated data only."""
from collections import Counter
from decimal import Decimal
from io import BytesIO
import json
import re
import zipfile

from openpyxl import load_workbook
from pydantic import ValidationError
import pytest

from copilot.analytics import AnalyticsEngine
from copilot.data.college import parse_college_workbook
from copilot.data.importing import XLSX_TYPE
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook
from copilot.schemas import ReportScope
from copilot.storage import Database


def generate(**changes):
    return generate_college_workbook(SyntheticOptions(**changes))


def raw_sheet(generated):
    book = load_workbook(BytesIO(generated.content), read_only=True, data_only=False)
    try:
        assert len(book.worksheets) == 1
        rows = list(book.worksheets[0].iter_rows(values_only=True))
        assert "合成" in str(rows[0][0])
        headers = list(rows[2])
        positions = {label: index for index, label in enumerate(headers) if label in {"学号", "姓名", "班级"}}
        courses = [index for index, label in enumerate(headers) if isinstance(label, str) and "学分" in label and "学时" in label]
        records = [row for row in rows[3:] if row[positions["学号"]] is not None]
        return headers, positions, courses, records
    finally:
        book.close()


def raw_facts(generated):
    headers, positions, courses, records = raw_sheet(generated)
    unique = {}
    for row in records:
        # Generator counts describe the main student matrix, not deliberately
        # appended duplicate/conflict rows.
        unique.setdefault(row[positions["学号"]], row)
    counts = Counter(graded=0, missing=0, exempt=0, blank=0, unknown=0)
    scores = []
    for row in unique.values():
        for column in courses:
            value = row[column]
            if value is None or isinstance(value, str) and not value.strip():
                counts["blank"] += 1
            elif value == "缺考":
                counts["missing"] += 1
            elif value == "免修":
                counts["exempt"] += 1
            elif isinstance(value, (float, int, Decimal)) and not isinstance(value, bool):
                score = Decimal(str(value))
                assert 0 <= score <= 100 and score * 100 == (score * 100).to_integral_value()
                counts["graded"] += 1
                scores.append(int(score * 100))
            else:
                assert value == "待核验"
                counts["unknown"] += 1
    counts.update(students=len(unique), student_rows=len(records), classes=len({row[positions["班级"]] for row in unique.values()}),
                  course_columns=len(courses), enrollments=counts["graded"] + counts["missing"] + counts["exempt"])
    return headers, positions, courses, records, counts, scores


def parse(generated, *, duplicate_policy="block"):
    return parse_college_workbook(generated.filename, XLSX_TYPE, generated.content, duplicate_policy=duplicate_policy)


def test_same_seed_options_reproduce_workbook_bytes_and_different_seed_changes_scores():
    first = generate(seed=115, students_per_class=8, course_count=5)
    same = generate(seed=115, students_per_class=8, course_count=5)
    different = generate(seed=116, students_per_class=8, course_count=5)
    assert first.content == same.content and first.summary == same.summary
    assert first.filename.endswith(".xlsx") and first.content.startswith(b"PK")
    assert raw_sheet(first) == raw_sheet(same)
    assert raw_facts(first)[-1] != raw_facts(different)[-1]
    with zipfile.ZipFile(BytesIO(first.content)) as archive:
        assert archive.testzip() is None


def test_raw_identities_are_varied_explicitly_synthetic_and_globally_unique():
    generated = generate(majors=("software_engineering", "computer_science", "network_engineering"),
                         classes_per_major=2, students_per_class=7, course_count=4)
    _, positions, _, records = raw_sheet(generated)
    ids = [row[positions["学号"]] for row in records]
    names = [row[positions["姓名"]] for row in records]
    assert len(ids) == len(set(ids)) == 42 and all(identifier.startswith("SIM") for identifier in ids)
    assert all("合成" in name and re.search(r"[\u4e00-\u9fff]{2,}", name) for name in names)
    assert len(set(names)) > 5
    assert len({row[positions["班级"]] for row in records}) == 6


def test_three_majors_cohort_and_academic_term_map_through_existing_adapter():
    generated = generate(cohort=2026, academic_year_start=2026, semester=2,
                         majors=("software_engineering", "computer_science", "network_engineering"),
                         classes_per_major=1, students_per_class=4, course_count=12)
    parsed = parse(generated)
    assert parsed.preview.can_import and parsed.dataset is not None
    assert len(parsed.dataset.students) == 12 and len(parsed.dataset.classes) == 3
    assert {row.major for row in parsed.dataset.classes} == {"software_engineering", "computer_science", "network_engineering"}
    assert {row.grade_level for row in parsed.dataset.classes} == {2026}
    assert {row.term for row in parsed.dataset.enrollments} == {"2026-T2"}
    assert {row.academic_year for row in parsed.dataset.courses} == {"2026-2027"}
    names = {row.course_name for row in parsed.dataset.courses}
    assert any("Web" in name for name in names)
    assert "工程实践2" in names and "体育 III" in names and "高等数学 (I)-1" in names
    assert any(Decimal(row.credits) != Decimal(row.credits).to_integral_value() for row in parsed.dataset.courses)
    assert all(row.source_header and row.study_nature and row.hours is not None for row in parsed.dataset.courses)


def test_normal_summary_is_supported_by_raw_cells_and_parser_counts():
    generated = generate(students_per_class=6, course_count=7)
    _, _, _, _, facts, scores = raw_facts(generated)
    assert generated.summary["synthetic"] is True
    assert facts["graded"] == facts["students"] * 7 and not any(facts[key] for key in ("missing", "exempt", "blank", "unknown"))
    for key, value in facts.items():
        assert generated.summary["counts"][key] == value
    assert generated.summary["sum_score_cents"] == sum(scores)
    parsed = parse(generated)
    assert parsed.preview.can_import
    for key in ("students", "classes", "course_columns", "graded", "missing", "exempt", "enrollments"):
        assert parsed.preview.counts[key] == facts[key]
    assert parsed.preview.counts["courses"] == generated.summary["counts"]["courses"] == 7


def test_score_profiles_change_performance_without_changing_enrollment_shape():
    average = {}
    shapes = set()
    for profile in ("strong", "balanced", "needs_support"):
        generated = generate(seed=91, score_profile=profile, students_per_class=20, course_count=8)
        *_, facts, scores = raw_facts(generated)
        average[profile] = Decimal(sum(scores)) / len(scores)
        shapes.add((facts["students"], facts["course_columns"], facts["enrollments"]))
    assert average["strong"] > average["balanced"] > average["needs_support"]
    assert len(shapes) == 1


def test_edge_scenario_preserves_boundaries_blanks_states_and_warning_evidence():
    generated = generate(scenario="edge_cases", classes_per_major=1, students_per_class=4, course_count=3)
    _, _, _, _, facts, scores = raw_facts(generated)
    assert {0, 5999, 6000, 7999, 8000, 10000} <= set(scores)
    assert all(facts[key] >= 1 for key in ("missing", "exempt", "blank", "unknown"))
    assert facts["course_columns"] == 4
    for key, value in facts.items():
        assert generated.summary["counts"][key] == value
    parsed = parse(generated)
    assert parsed.preview.can_import and parsed.dataset is not None
    assert parsed.preview.counts["excluded_empty_courses"] == 1
    assert parsed.preview.issue_counts["unknown_status"] == facts["unknown"]
    assert parsed.preview.counts["blank_score_cells"] == facts["blank"]
    assert Counter(row.status for row in parsed.dataset.grade_records) == {key: facts[key] for key in ("graded", "missing", "exempt")}
    assert sorted(row.score_cents for row in parsed.dataset.grade_records if row.status == "graded") == sorted(scores)


def test_small_edge_config_is_rejected_instead_of_silently_omitting_promised_cases():
    for changes in ({"classes_per_major": 1, "students_per_class": 1, "course_count": 12},
                    {"classes_per_major": 1, "students_per_class": 2, "course_count": 3}):
        with pytest.raises(ValidationError):
            SyntheticOptions(scenario="edge_cases", **changes)


def test_conflict_scenario_blocks_until_explicit_first_or_last_policy():
    generated = generate(scenario="conflict", classes_per_major=1, students_per_class=3, course_count=3)
    _, _, _, _, facts, _ = raw_facts(generated)
    assert facts["student_rows"] == facts["students"] + 1
    blocked = parse(generated)
    assert not blocked.preview.can_import and blocked.dataset is None
    assert blocked.preview.issue_counts["conflicting_student"] == 1
    first, last = parse(generated, duplicate_policy="first"), parse(generated, duplicate_policy="last")
    assert first.preview.can_import and last.preview.can_import
    assert len(first.dataset.students) == len(last.dataset.students) == facts["students"]
    assert first.dataset.version != last.dataset.version


def test_invalid_options_reject_unsafe_sizes_years_and_noninteger_seeds():
    invalid = ({"seed": -1}, {"seed": 2**32}, {"seed": True}, {"seed": "1"},
               {"academic_year_start": 1999}, {"academic_year_start": 2100},
               {"cohort": 2026, "academic_year_start": 2025}, {"cohort": 1899}, {"cohort": 2200},
               {"semester": 0}, {"semester": 3}, {"majors": ()},
               {"majors": ("software_engineering", "software_engineering")}, {"majors": ("unmapped",)},
               {"classes_per_major": 0}, {"classes_per_major": 5}, {"students_per_class": 0}, {"students_per_class": 51},
               {"course_count": 2}, {"course_count": 13}, {"scenario": "invented"}, {"score_profile": "invented"})
    for changes in invalid:
        with pytest.raises(ValidationError):
            SyntheticOptions(**changes)
    accepted = SyntheticOptions(cohort=2099, academic_year_start=2099, semester=2)
    assert accepted.academic_year_start == 2099


def test_maximum_supported_workbook_stays_within_upload_limit_and_parses():
    generated = generate(majors=("software_engineering", "computer_science", "network_engineering"),
                         classes_per_major=4, students_per_class=50, course_count=12)
    assert len(generated.content) < 5 * 1024 * 1024
    parsed = parse(generated)
    assert parsed.preview.can_import
    assert parsed.preview.counts["students"] == 600 and parsed.preview.counts["classes"] == 12
    assert parsed.preview.counts["graded"] == parsed.preview.counts["grade_records"] == 7200


def test_generated_data_reaches_sqlite_and_existing_report_with_independent_expected_counts(tmp_path):
    generated = generate(scenario="edge_cases", classes_per_major=2, students_per_class=6, course_count=8)
    *_, facts, scores = raw_facts(generated)
    parsed = parse(generated)
    database = Database(tmp_path / "synthetic-only.sqlite3")
    database.initialize()
    info = database.replace(parsed.dataset, source="uploaded_local_only")
    scope = ReportScope(term="2025-T1")
    report = AnalyticsEngine(database).course_report(scope, expected_version=info.dataset_version)
    assert info.external_llm_allowed is False
    assert report.summary.student_count == facts["students"]
    assert report.summary.graded_count == facts["graded"]
    assert report.summary.enrollment_count == facts["enrollments"]
    assert report.summary.sum_score_cents == generated.summary["sum_score_cents"] == sum(scores)
    assert report.summary.passed_count == sum(score >= 6000 for score in scores)
    assert report.summary.missing_count == facts["missing"] and report.summary.exempt_count == facts["exempt"]
    intervals = ((0, 6000), (6000, 7000), (7000, 8000), (8000, 9000), (9000, 10001))
    assert [bucket.count for bucket in report.summary.distribution] == [sum(low <= score < high for score in scores) for low, high in intervals]
    assert sum(row.statistics.graded_count for row in report.rows) == facts["graded"]


def test_normalized_aliases_do_not_retain_synthetic_source_names_or_student_numbers():
    generated = generate(students_per_class=4, course_count=3)
    _, positions, _, records = raw_sheet(generated)
    parsed = parse(generated)
    payload = json.dumps(parsed.dataset.payload(), ensure_ascii=False)
    for row in records:
        assert row[positions["学号"]] not in payload
        assert row[positions["姓名"]] not in payload
    assert all(student.display_name.startswith("Imported Student ") for student in parsed.dataset.students)
