"""Synthetic-only compatibility checks for normalized college imports."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import re
import sqlite3

import pytest
from pydantic import ValidationError

from copilot.analytics import AnalyticsEngine
from copilot.data.generate import generate_dataset
from copilot.models import ClassRecord, CourseRecord, DataError, StudentRecord
from copilot.schemas import ReportScope, Scope
from copilot.storage import Database


def test_extended_normalized_csv_and_xlsx_reuse_exact_credit_and_metadata_validation(college_dataset):
    from copilot.data.importing import XLSX_TYPE, import_csv_tables, import_workbook
    from tests.test_imports import csv_files
    from tests.workbook_fixture import workbook_bytes

    parsed, _ = import_csv_tables(csv_files(college_dataset))
    assert parsed.version == college_dataset.version
    parsed, _ = import_workbook("synthetic-normalized.xlsx", XLSX_TYPE, workbook_bytes(college_dataset.payload()))
    assert parsed.version == college_dataset.version


@pytest.fixture
def college_dataset(tiny_dataset):
    courses = tuple(CourseRecord(
        course_id="I" + str(index) * 24, course_name=name, grade_level=2024,
        credits=credit, course_type="general_elective", source_header=f"{name} [合成课程]",
        source_key=f"synthetic-{index}", source_code=f"TEST-{index}",
        course_category="通识教育", study_nature="选修", hours=16 * index,
        academic_year="2025-2026", source_term="2025-T1")
        for index, (name, credit) in enumerate([
            ("高等数学 (I)-1", Decimal("0.25")),
            ("Web 工程实践2", Decimal("0.75")),
            ("体育 III", Decimal("1.25")),
        ], 1))
    mapping = {old.course_id: new.course_id for old, new in zip(tiny_dataset.courses, courses, strict=True)}
    return replace(
        tiny_dataset,
        classes=tuple(row.model_copy(update={"grade_level": 2024, "class_label": f"2024软工{index}班",
                                            "major": "software_engineering"})
                      for index, row in enumerate(tiny_dataset.classes, 1)),
        students=tuple(row.model_copy(update={"display_name": f"Imported Student {row.student_id[1:]}"})
                       for row in tiny_dataset.students),
        courses=courses,
        enrollments=tuple(row.model_copy(update={"course_id": mapping[row.course_id]}) for row in tiny_dataset.enrollments),
    ).validate()


def test_legacy_generator_fingerprint_and_integer_wire_values_are_preserved(tiny_dataset, tmp_path):
    assert generate_dataset().version == "a52c8961a7c1da92ab4d7d8cc8af9e9a636fe3e788cbf5bcd24c8eb16607cabc"
    assert tiny_dataset.payload()["courses"][0]["credits"] == 3
    assert "source_header" not in tiny_dataset.payload()["courses"][0]
    assert tiny_dataset.payload()["grade_records"][3]["score_cents"] is None
    db = Database(tmp_path / "legacy-values.sqlite3")
    db.initialize()
    db.replace(tiny_dataset)
    wire = AnalyticsEngine(db).summary(Scope()).model_dump(mode="json")
    assert type(wire["weighted_sum_score_cents"]) is int and wire["weighted_sum_score_cents"] == 65997
    assert type(wire["graded_credits"]) is int and wire["graded_credits"] == 11


def test_unicode_courses_aliases_cohorts_and_metadata_round_trip(college_dataset, tmp_path):
    db = Database(tmp_path / "college.sqlite3")
    db.initialize()
    info = db.replace(college_dataset)
    assert info.dataset_version == college_dataset.version
    assert info.source == "uploaded_local_only" and info.external_llm_allowed is False
    assert db.catalog().courses == list(college_dataset.courses)
    assert db.reporting_catalog().majors == ["software_engineering"]
    assert db.reporting_catalog().classes[0].grade_level == 2024
    assert db.catalog().courses[0].course_name == "高等数学 (I)-1"
    altered = replace(college_dataset, courses=(college_dataset.courses[0].model_copy(update={"hours": 17}), *college_dataset.courses[1:]))
    assert altered.version != college_dataset.version


def test_fractional_credit_aggregation_is_exact_and_does_not_change_score_policy(college_dataset, tmp_path):
    db = Database(tmp_path / "fractional.sqlite3")
    db.initialize()
    db.replace(college_dataset)
    stats = AnalyticsEngine(db).summary(Scope())
    assert stats.sum_score_cents == 21999 and stats.graded_count == 4
    assert stats.average_score == Decimal("55.00")
    assert stats.pass_rate_percent == Decimal("50.00")
    assert stats.weighted_sum_score_cents == Decimal("5499.75")
    assert stats.graded_credits == Decimal("1.50")
    assert stats.weighted_average_score == Decimal("36.67")
    report = AnalyticsEngine(db).course_report(ReportScope(grade_level=2024, major="software_engineering"))
    assert report.summary == stats.model_copy(update={"scope": Scope(grade_level=2024)})


def test_nonbinary_decimal_credits_agree_with_independent_sql_counts(college_dataset, tmp_path):
    changed = replace(college_dataset, courses=tuple(
        course.model_copy(update={"credits": credit}) for course, credit in zip(
            college_dataset.courses, (Decimal("0.1"), Decimal("0.3"), Decimal("1.25")), strict=True)))
    db = Database(tmp_path / "decimal-tenths.sqlite3")
    db.initialize()
    db.replace(changed)
    stats = AnalyticsEngine(db).summary(Scope())
    assert stats.weighted_sum_score_cents == Decimal("2199.90")
    assert stats.graded_credits == Decimal("0.6")
    expected = db.sql_counts(Scope())
    assert {key: stats.model_dump()[key] for key in expected} == expected


@pytest.mark.parametrize("credits", [Decimal("0"), Decimal("-1"), Decimal("100.01"), Decimal("0.125"), Decimal("NaN"), True])
def test_invalid_credit_values_are_rejected(credits):
    with pytest.raises(ValidationError):
        CourseRecord(course_id="I" + "a" * 24, course_name="合成课程", grade_level=2024, credits=credits)


def test_labels_and_identifiers_still_reject_unsafe_or_unsupported_values():
    with pytest.raises(ValidationError):
        ClassRecord(class_id="C001", grade_level=4, class_label="invalid")
    with pytest.raises(ValidationError):
        CourseRecord(course_id="I" + "a" * 24, course_name="课程\x00隐藏", grade_level=2024, credits=1)
    with pytest.raises(ValidationError):
        StudentRecord(student_id="S0001", class_id="C001", display_name="Real Person")
    assert StudentRecord(student_id="S0001", class_id="C001", display_name="Imported Student 0001")


def test_old_check_constraints_migrate_atomically_and_idempotently(tiny_dataset, college_dataset, tmp_path):
    import copilot.storage
    path = tmp_path / "old-layout.sqlite3"
    schema = Path(copilot.storage.__file__).with_name("schema.sql").read_text(encoding="utf-8")
    old_classes = """CREATE TABLE IF NOT EXISTS classes (
        class_id TEXT PRIMARY KEY NOT NULL,
        grade_level INTEGER NOT NULL CHECK (typeof(grade_level)='integer' AND grade_level BETWEEN 1 AND 3),
        class_label TEXT NOT NULL);"""
    old_courses = """CREATE TABLE IF NOT EXISTS courses (
        course_id TEXT PRIMARY KEY NOT NULL,
        course_name TEXT NOT NULL,
        grade_level INTEGER NOT NULL CHECK (typeof(grade_level)='integer' AND grade_level BETWEEN 1 AND 3),
        credits INTEGER NOT NULL CHECK (typeof(credits)='integer' AND credits BETWEEN 2 AND 4));"""
    schema = re.sub(r"CREATE TABLE IF NOT EXISTS classes \(.*?\);", old_classes, schema, flags=re.S)
    schema = re.sub(r"CREATE TABLE IF NOT EXISTS courses \(.*?\);", old_courses, schema, flags=re.S)
    with sqlite3.connect(path) as conn:
        conn.executescript(schema)
    db = Database(path)
    db.replace(tiny_dataset)
    before = db.overview()
    db.initialize()
    db.initialize()
    assert db.overview() == before and db.catalog().courses == list(tiny_dataset.courses)
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.replace(college_dataset).dataset_version == college_dataset.version


def test_migration_integrity_failure_rolls_back_layout_and_preserves_records(tiny_dataset, tmp_path):
    path = tmp_path / "migration-failure.sqlite3"
    db = Database(path)
    db.initialize()
    before = db.replace(tiny_dataset)
    # Construct a damaged legacy fixture without exposing any real records.
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=0")
        conn.execute("UPDATE students SET class_id='C999' WHERE student_id='S0001'")
    with pytest.raises(DataError) as error:
        db.initialize()
    assert error.value.code == "schema_migration_failed"
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT class_id FROM students WHERE student_id='S0001'").fetchone()[0] == "C999"
        assert conn.execute("SELECT dataset_version FROM dataset_metadata").fetchone()[0] == before.dataset_version
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name LIKE '__college_%'").fetchall()


def test_confirmed_import_preserves_existing_catalog_and_optional_metadata(tiny_dataset, college_dataset, tmp_path):
    db = Database(tmp_path / "retained.sqlite3")
    db.initialize()
    original = db.replace(tiny_dataset)
    result = db.replace(college_dataset, expected_version=original.dataset_version, preserve_courses=True)
    assert result.counts["courses"] == len(tiny_dataset.courses) + len(college_dataset.courses)
    assert result.counts["enrollments"] == len(college_dataset.enrollments)
    assert result.counts["students"] == len(college_dataset.students)
    lookup = {row.course_id: row for row in db.catalog().courses}
    assert lookup[tiny_dataset.courses[0].course_id] == tiny_dataset.courses[0]
    assert lookup[college_dataset.courses[0].course_id] == college_dataset.courses[0]
    expected = replace(college_dataset, courses=(*tiny_dataset.courses, *college_dataset.courses))
    assert result.dataset_version == expected.version


def test_stale_confirmation_and_conflicting_courses_do_not_change_database(tiny_dataset, tmp_path):
    db = Database(tmp_path / "conflict.sqlite3")
    db.initialize()
    before = db.replace(tiny_dataset)
    with pytest.raises(DataError) as stale:
        db.replace(tiny_dataset, expected_version="0" * 64, preserve_courses=True)
    assert stale.value.code == "dataset_changed"
    changed = replace(tiny_dataset, courses=(tiny_dataset.courses[0].model_copy(update={"credits": Decimal("2.5")}), *tiny_dataset.courses[1:]))
    with pytest.raises(DataError) as conflict:
        db.replace(changed, expected_version=before.dataset_version, preserve_courses=True)
    assert conflict.value.code == "course_conflict"
    assert db.overview() == before and db.catalog().courses == list(tiny_dataset.courses)


def test_confirmed_import_rolls_back_catalog_and_data_on_insert_failure(tiny_dataset, college_dataset, tmp_path):
    db = Database(tmp_path / "rollback.sqlite3")
    db.initialize()
    before = db.replace(tiny_dataset)
    with db.connect() as conn:
        conn.execute("CREATE TRIGGER reject_student BEFORE INSERT ON students WHEN NEW.student_id='S0002' BEGIN SELECT RAISE(ABORT,'synthetic test failure'); END")
    with pytest.raises(DataError, match="database constraint"):
        db.replace(college_dataset, expected_version=before.dataset_version, preserve_courses=True)
    assert db.overview() == before and db.catalog().courses == list(tiny_dataset.courses)
