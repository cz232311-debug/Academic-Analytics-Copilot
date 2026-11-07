"""Independent expected counts, exact mean boundaries and legacy compatibility."""
from dataclasses import replace
from decimal import Decimal
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from copilot.analytics import AnalyticsEngine, course_mean_band
from copilot.api import create_app
from copilot.data.generate import generate_dataset
from copilot.data.importing import import_csv_tables, import_workbook, XLSX_TYPE
from copilot.models import DataError
from copilot.schemas import ReportScope, Scope
from copilot.storage import Database
from tests.test_imports import csv_files
from tests.workbook_fixture import workbook_bytes


@pytest.fixture
def store(tmp_path, tiny_dataset):
    dataset = replace(tiny_dataset,
        classes=tuple(row.model_copy(update={"major": "computer_science" if row.class_id == "C001" else "business_administration"}) for row in tiny_dataset.classes),
        courses=tuple(row.model_copy(update={"course_type": "general_required" if row.course_id.endswith("01") else "professional_elective"}) for row in tiny_dataset.courses))
    db = Database(tmp_path / "report.sqlite3")
    db.initialize()
    db.replace(dataset, source="generated", seed=1)
    return db


def test_course_rows_reuse_existing_summary_and_exact_denominators(store):
    engine = AnalyticsEngine(store)
    report = engine.course_report(ReportScope())
    assert [r.course_id for r in report.rows] == ["G1-C01", "G1-C02"]
    first, second = report.rows
    assert first.statistics == engine.summary(Scope(course_id="G1-C01"))
    assert (first.statistics.graded_count, first.statistics.missing_count, first.statistics.exempt_count) == (3, 1, 0)
    assert (first.statistics.passed_count, first.statistics.failed_count) == (2, 1)
    assert first.statistics.average_score == Decimal("73.33")
    assert first.statistics.pass_rate_percent == Decimal("66.67")
    assert first.fail_rate_percent == first.excellent_rate_percent == Decimal("33.33")
    assert first.excellent_count == 1 and first.average_band == "[70,80)"
    assert second.statistics.graded_count == 1 and second.statistics.exempt_count == 1
    assert second.fail_rate_percent == Decimal("100.00")
    assert second.excellent_rate_percent == Decimal("0.00")
    assert report.summary == engine.summary(Scope())
    assert report.policy.excellent_threshold_cents == 8000
    assert report.generated_at.endswith("+00:00")


@pytest.mark.parametrize("filters,count,score_sum", [
    ({"major": "computer_science"}, 3, 11999),
    ({"major": "business_administration"}, 1, 10000),
    ({"course_type": "general_required"}, 3, 21999),
    ({"course_type": "professional_elective"}, 1, 0),
    ({"major": "computer_science", "course_type": "general_required", "grade_level": 1}, 2, 11999),
    ({"class_id": "C002", "course_type": "professional_elective"}, 0, 0),
])
def test_multidimensional_filters_have_hand_calculated_results(store, filters, count, score_sum):
    report = AnalyticsEngine(store).course_report(ReportScope(**filters))
    assert report.summary.graded_count == count
    assert report.summary.sum_score_cents == score_sum
    for row in report.rows:
        stats = row.statistics
        assert sum(b.count for b in stats.distribution) == stats.graded_count
        assert stats.passed_count + stats.failed_count == stats.graded_count
        assert stats.graded_count + stats.missing_count + stats.exempt_count == stats.enrollment_count


@pytest.mark.parametrize("total,count,band", [
    (0, 1, "[0,60)"), (5999, 1, "[0,60)"), (6000, 1, "[60,70)"),
    (6999, 1, "[60,70)"), (7000, 1, "[70,80)"), (7999, 1, "[70,80)"),
    (8000, 1, "[80,90)"), (8999, 1, "[80,90)"), (9000, 1, "[90,100]"), (10000, 1, "[90,100]"),
    (15999, 2, "[70,80)"), (11999, 2, "[0,60)"), (17999, 2, "[80,90)"), (0, 0, None),
])
def test_exact_mean_band_boundaries(total, count, band):
    assert course_mean_band(total, count) == band


def test_display_rounded_mean_does_not_determine_band(store, tiny_dataset):
    records = tuple(row.model_copy(update={"score_cents": score}) if score is not None else row
                    for row, score in zip(tiny_dataset.grade_records, [8000, 7999, None, None, None, None]))
    store.replace(replace(tiny_dataset, grade_records=records), source="generated")
    row = AnalyticsEngine(store).course_report(ReportScope(class_id="C001", course_id="G1-C01")).rows[0]
    assert row.statistics.average_score == Decimal("80.00")
    assert row.average_band == "[70,80)"
    assert row.excellent_count == 1 and row.excellent_rate_percent == Decimal("50.00")


def test_enrolled_all_missing_course_has_no_rates_and_is_unclassified(store):
    report = AnalyticsEngine(store).course_report(ReportScope(class_id="C002", major="business_administration"))
    assert report.summary.graded_count == 1
    # C002/G1-C02 has no enrollments and is omitted; G1-C01 contains one graded plus one missing.
    report = AnalyticsEngine(store).course_report(ReportScope(class_id="C002", course_id="G1-C02"))
    assert report.rows == [] and report.summary.average_score is None
    with store.connect() as conn:
        conn.execute("UPDATE grade_records SET status='missing',score_cents=NULL WHERE enrollment_id='E00003'")
    # Direct mutation is a test fixture only; normal writes always replace atomically.
    report = AnalyticsEngine(store).course_report(ReportScope(class_id="C002", course_id="G1-C01"))
    row = report.rows[0]
    assert row.statistics.graded_count == 0 and row.statistics.missing_count == 2
    assert row.statistics.average_score is row.fail_rate_percent is row.excellent_rate_percent is row.average_band is None
    assert all(not band.course_ids for band in report.mean_bands)


def test_report_uses_one_snapshot_and_expected_version(store, monkeypatch):
    original = store.snapshot
    calls = []
    def snapshot(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "snapshot", snapshot)
    version = store.overview().dataset_version
    report = AnalyticsEngine(store).course_report(ReportScope(), expected_version=version)
    assert len(calls) == 1 and calls[0]["reporting"] is True
    assert {r.statistics.dataset_version for r in report.rows} == {version}
    with pytest.raises(DataError) as exc:
        AnalyticsEngine(store).course_report(ReportScope(), expected_version="0" * 64)
    assert exc.value.code == "dataset_changed"


def test_reporting_api_validation_and_unknown_filters(store):
    with TestClient(create_app(store.path, bootstrap_demo=False)) as client:
        catalog = client.get("/api/reports/catalog").json()
        assert catalog["majors"] == ["business_administration", "computer_science"]
        legacy = client.get("/api/catalog").json()
        assert "major" not in legacy["classes"][0] and "course_type" not in legacy["courses"][0]
        assert client.get("/api/reports/courses", params={"major": "computer_science"}).json()["summary"]["graded_count"] == 3
        for params in ({"grade_level": 4}, {"major": "random"}, {"course_type": "sql"}, {"unknown": "value"},
                       {"expected_version": "not-a-version"}, {"class_id": "C001;DROP TABLE students"}):
            assert client.get("/api/reports/courses", params=params).status_code == 422
        for params in ({"course_id": "G1-C99"}, {"major": "information_systems"}, {"course_type": "practical"}, {"term": "2025-T1"}):
            assert client.get("/api/reports/courses", params=params).status_code == 404
        empty = client.get("/api/reports/courses", params={"course_id": "G2-C01", "grade_level": 1})
        assert empty.status_code == 200 and empty.json()["rows"] == []
        assert client.get("/api/reports/courses", params={"expected_version": "0" * 64}).status_code == 409


def test_migration_keeps_legacy_content_version_and_unknown_metadata(tmp_path, tiny_dataset):
    path = tmp_path / "legacy.sqlite3"
    # Construct the original schema without optional reporting columns.
    import copilot.storage
    schema = Path(copilot.storage.__file__).with_name("schema.sql").read_text()
    schema = schema.replace(",\n    major TEXT NOT NULL DEFAULT 'unspecified' CHECK (major IN ('computer_science','business_administration','information_systems','unspecified'))", "")
    schema = schema.replace(",\n    course_type TEXT NOT NULL DEFAULT 'unspecified' CHECK (course_type IN ('general_required','professional_required','professional_elective','practical','unspecified'))", "")
    with sqlite3.connect(path) as conn:
        conn.executescript(schema)
    store = Database(path)
    store.replace(tiny_dataset)
    before = store.overview()
    store.initialize()
    store.initialize()  # Startup migration is idempotent.
    assert store.overview() == before
    assert before.dataset_version == tiny_dataset.version
    catalog = store.reporting_catalog()
    assert catalog.majors == catalog.course_types == ["unspecified"]
    assert AnalyticsEngine(store).course_report(ReportScope()).summary.sum_score_cents == 21999


def test_optional_metadata_imports_legacy_extended_and_mixed_rows(tiny_dataset, tmp_path):
    enriched = replace(tiny_dataset,
        classes=(tiny_dataset.classes[0].model_copy(update={"major": "computer_science"}), *tiny_dataset.classes[1:]),
        courses=(tiny_dataset.courses[0].model_copy(update={"course_type": "general_required"}), *tiny_dataset.courses[1:]))
    assert enriched.version != tiny_dataset.version
    for dataset in (tiny_dataset, enriched):
        parsed, _ = import_csv_tables(csv_files(dataset))
        assert parsed.version == dataset.version
        parsed, _ = import_workbook("demo.xlsx", XLSX_TYPE, workbook_bytes(dataset.payload()))
        assert parsed.version == dataset.version
    db = Database(tmp_path / "mixed.sqlite3")
    db.initialize()
    db.replace(enriched)
    assert db.reporting_catalog().classes[1].major == "unspecified"
    assert db.reporting_catalog().classes[0].major == "computer_science"


def test_enriched_generator_preserves_scores_and_seed_reproducibility():
    original = generate_dataset()
    enriched = generate_dataset(reporting_metadata=True)
    assert original.grade_records == enriched.grade_records
    assert original.enrollments == enriched.enrollments
    assert enriched.version == generate_dataset(reporting_metadata=True).version != original.version
    assert len({row.major for row in enriched.classes}) == 3
    assert len({row.course_type for row in enriched.courses}) == 4
