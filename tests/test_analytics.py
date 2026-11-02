from dataclasses import replace
from decimal import Decimal
import sqlite3

import pandas as pd
import pytest

from copilot.analytics import AnalyticsEngine, ratio, summarize
from copilot.models import DataError
from copilot.schemas import Scope
from copilot.storage import COLUMNS, Database


@pytest.fixture
def database(tiny_dataset, tmp_path):
    store = Database(tmp_path / "academic.sqlite3")
    store.initialize()
    store.replace(tiny_dataset)
    return store


def test_known_totals_denominators_weighting_and_distinct_students(database):
    result = AnalyticsEngine(database).summary(Scope())
    assert (result.enrollment_count, result.student_count, result.graded_count) == (6, 4, 4)
    assert (result.missing_count, result.exempt_count, result.passed_count) == (1, 1, 2)
    assert (result.sum_score_cents, result.weighted_sum_score_cents, result.graded_credits) == (21999, 65997, 11)
    assert result.average_score == Decimal("55.00")
    assert result.weighted_average_score == Decimal("60.00")
    assert result.pass_rate_percent == Decimal("50.00")
    assert result.failed_student_count == result.failed_count == 2
    assert [b.count for b in result.distribution] == [2, 1, 0, 0, 1]


@pytest.mark.parametrize("scope", [Scope(), Scope(course_id="G1-C01"), Scope(class_id="C001"), Scope(class_id="C002"), Scope(course_id="G2-C01")])
def test_independent_sql_and_pandas_counts_agree(database, scope):
    expected = database.sql_counts(scope)
    result = AnalyticsEngine(database).summary(scope).model_dump()
    assert {key: result[key] for key in expected} == expected


def test_course_boundary_and_correct_pass_denominator(database):
    result = AnalyticsEngine(database).summary(Scope(course_id="G1-C01"))
    assert result.average_score == Decimal("73.33")
    assert result.pass_rate_percent == Decimal("66.67")
    assert result.graded_count == 3 and result.enrollment_count == 4
    assert result.passed_count == 2  # Exactly 60 passes; 59.99 fails.


def test_empty_and_no_graded_scopes_have_null_rates(database):
    empty = AnalyticsEngine(database).summary(Scope(course_id="G2-C01"))
    missing = AnalyticsEngine(database).summary(Scope(class_id="C002", course_id="G1-C02"))
    for result in (empty, missing):
        assert result.average_score is None and result.pass_rate_percent is None
        assert result.graded_count == 0 and sum(b.count for b in result.distribution) == 0


def test_comparison_is_same_course_with_explicit_sample_sizes(database):
    result = AnalyticsEngine(database).compare_classes("G1-C01", ["C002", "C001"])
    assert [r.scope.class_id for r in result.results] == ["C001", "C002"]
    assert [r.average_score for r in result.results] == [Decimal("60.00"), Decimal("100.00")]
    assert [r.graded_count for r in result.results] == [2, 1]
    assert result.highest_mean_class_ids == ["C002"]


def test_all_distribution_boundaries_and_exact_half_up_rounding():
    scores = [0, 5999, 6000, 6999, 7000, 7999, 8000, 8999, 9000, 10000]
    frame = pd.DataFrame([(f"S{i:04}", "C001", 1, "G1-C01", "2026-T1", 3, "graded", score)
                          for i, score in enumerate(scores)], columns=COLUMNS)
    result = summarize(frame, "fixture", Scope())
    assert [b.count for b in result.distribution] == [2, 2, 2, 2, 2]
    assert result.passed_count == 8
    assert ratio(1005, 1000) == Decimal("1.01")
    assert ratio(1, 0) is None


def test_failed_students_are_deduplicated(database, tiny_dataset):
    records = list(tiny_dataset.grade_records)
    records[0] = records[0].model_copy(update={"score_cents": 4000})
    database.replace(replace(tiny_dataset, grade_records=tuple(records)))
    result = AnalyticsEngine(database).summary(Scope())
    assert result.failed_count == 3 and result.failed_student_count == 2


def test_atomic_replace_rolls_back_deleted_rows_and_metadata(database, tiny_dataset):
    before = database.overview()
    with database.connect() as conn:
        conn.execute("CREATE TRIGGER reject_student BEFORE INSERT ON students WHEN NEW.student_id='S0002' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    changed = replace(tiny_dataset, grade_records=(tiny_dataset.grade_records[0].model_copy(update={"score_cents": 9000}), *tiny_dataset.grade_records[1:]))
    with pytest.raises(DataError, match="database constraint"):
        database.replace(changed)
    assert database.overview() == before
    assert AnalyticsEngine(database).summary(Scope()).sum_score_cents == 21999


def test_sql_constraints_and_unknown_entities(database):
    with database.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO students VALUES ('S9999','C999','Synthetic Student 9999')")
    with database.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE grade_records SET score_cents=-1 WHERE grade_record_id='R00001'")
    with pytest.raises(DataError, match="Unknown course"):
        AnalyticsEngine(database).summary(Scope(course_id="G1-C99"))
    with pytest.raises(DataError, match="Unknown term"):
        AnalyticsEngine(database).summary(Scope(term="2025-T1"))


def test_uploaded_provenance_cannot_claim_external_llm_permission(database):
    assert database.overview().external_llm_allowed is False


def test_class_ranking_uses_unrounded_values(database, tiny_dataset):
    records = list(tiny_dataset.grade_records)
    records[2] = records[2].model_copy(update={"score_cents": 6000})
    database.replace(replace(tiny_dataset, grade_records=tuple(records)))
    result = AnalyticsEngine(database).compare_classes("G1-C01", ["C001", "C002"])
    assert [r.average_score for r in result.results] == [Decimal("60.00"), Decimal("60.00")]
    assert result.highest_mean_class_ids == ["C002"]  # 60 > 59.995 before display rounding.


def test_all_missing_course_never_becomes_zero_average(database, tiny_dataset):
    records = tuple(r.model_copy(update={"status": "missing", "score_cents": None}) if r.enrollment_id == "E00003" else r
                    for r in tiny_dataset.grade_records)
    database.replace(replace(tiny_dataset, grade_records=records))
    result = AnalyticsEngine(database).summary(Scope(class_id="C002", course_id="G1-C01"))
    assert (result.enrollment_count, result.missing_count, result.graded_count) == (2, 2, 0)
    assert result.average_score is None and result.pass_rate_percent is None
