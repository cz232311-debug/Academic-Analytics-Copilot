"""Current generated SQLite snapshot exports; no live models or real records."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from copilot.api import create_app
from copilot.data.synthetic_export import build_synthetic_export
from copilot.models import DataError
from copilot.storage import Database

ROUTE = "/api/datasets/synthetic/export"


@pytest.fixture
def generated_api(tmp_path, tiny_dataset):
    database = Database(tmp_path / "generated-only.sqlite3")
    database.initialize()
    info = database.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(database.path, bootstrap_demo=False)) as client:
        yield client, database, info


def read_workbook(content):
    return load_workbook(BytesIO(content), data_only=False)


def records(sheet):
    rows = list(sheet.iter_rows(values_only=True))
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def metadata(book):
    return {row[0]: row[1] for row in book["数据说明"].iter_rows(min_row=2, values_only=True)}


def test_download_exports_all_five_table_counts_and_current_page_version(generated_api):
    client, database, info = generated_api
    response = client.get(ROUTE, params={"expected_version": info.dataset_version})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["x-dataset-version"] == info.dataset_version
    assert response.headers["cache-control"] == "no-store"
    assert "attachment" in response.headers["content-disposition"]
    book = read_workbook(response.content)
    assert book.sheetnames == ["成绩明细", "课程目录", "班级目录", "学生目录", "数据说明"]
    page = client.get("/api/dataset").json()
    exported = metadata(book)
    assert exported["数据版本（dataset_version）"] == page["dataset_version"] == info.dataset_version
    assert exported["数据来源"] == "generated"
    counts = {"学生数": "students", "班级数": "classes", "课程数": "courses",
              "选课记录数": "enrollments", "成绩记录数": "grade_records"}
    assert {key: exported[key] for key in counts} == {key: page["counts"][table] for key, table in counts.items()}
    assert book["学生目录"].max_row - 1 == page["counts"]["students"]
    assert book["课程目录"].max_row - 1 == page["counts"]["courses"] == 3  # Includes a catalog course with no grades.
    assert book["班级目录"].max_row - 1 == page["counts"]["classes"]
    assert book["成绩明细"].max_row - 1 == page["counts"]["grade_records"]
    assert database.overview() == info  # No write or reactivation side effect.
    book.close()


def test_grade_rows_keep_ids_labels_zero_nulls_and_exact_hundredths(generated_api, tiny_dataset):
    _, database, info = generated_api
    book = read_workbook(build_synthetic_export(database, info.dataset_version))
    rows = records(book["成绩明细"])
    assert len(rows) == len(tiny_dataset.grade_records)
    by_grade = {row["成绩记录标识"]: row for row in rows}
    enrollments = {row.enrollment_id: row for row in tiny_dataset.enrollments}
    students = {row.student_id: row for row in tiny_dataset.students}
    classes = {row.class_id: row for row in tiny_dataset.classes}
    courses = {row.course_id: row for row in tiny_dataset.courses}
    for grade in tiny_dataset.grade_records:
        actual = by_grade[grade.grade_record_id]
        enrollment = enrollments[grade.enrollment_id]
        student = students[enrollment.student_id]
        classroom = classes[student.class_id]
        assert actual["选课记录标识"] == enrollment.enrollment_id
        assert actual["学生标识"] == student.student_id and actual["学生别名"] == student.display_name
        assert actual["班级标识"] == classroom.class_id and actual["班级"] == classroom.class_label
        assert actual["课程标识（course_id）"] == enrollment.course_id
        assert actual["课程"] == courses[enrollment.course_id].course_name
        assert actual["原始学期标识"] == enrollment.term and actual["状态标识"] == grade.status
        assert actual["成绩（百分之一分）"] == grade.score_cents
        if grade.score_cents is None:
            assert actual["成绩"] is None
        else:
            assert Decimal(str(actual["成绩"])) * 100 == grade.score_cents
    assert by_grade["R00005"]["成绩"] == 0
    assert by_grade["R00002"]["成绩"] == 59.99
    assert by_grade["R00004"]["成绩状态"] == "缺考/缺失"
    assert by_grade["R00006"]["成绩状态"] == "免修"
    assert metadata(book)["及格线"] == "60分" and metadata(book)["优良线"] == "80分"
    book.close()


def test_current_database_edits_export_without_regenerating_from_seed(generated_api, tiny_dataset, monkeypatch):
    client, database, old = generated_api
    changed = replace(tiny_dataset, grade_records=(
        tiny_dataset.grade_records[0].model_copy(update={"score_cents": 6137}), *tiny_dataset.grade_records[1:]))
    current = database.replace(changed, source="generated", seed=1)

    def must_not_generate(*args, **kwargs):
        raise AssertionError("Snapshot export may not regenerate data")

    monkeypatch.setattr("copilot.data.synthetic_college.generate_college_workbook", must_not_generate)
    response = client.get(ROUTE, params={"expected_version": current.dataset_version})
    assert response.status_code == 200 and current.dataset_version != old.dataset_version
    book = read_workbook(response.content)
    assert records(book["成绩明细"])[0]["成绩"] == 61.37
    assert metadata(book)["生成数据编号"] == 1
    book.close()


def test_stale_version_cannot_download_a_switched_dataset(generated_api, tiny_dataset):
    client, database, old = generated_api
    changed = replace(tiny_dataset, grade_records=(
        tiny_dataset.grade_records[0].model_copy(update={"score_cents": 6200}), *tiny_dataset.grade_records[1:]))
    database.replace(changed, source="generated", seed=2)
    response = client.get(ROUTE, params={"expected_version": old.dataset_version})
    assert response.status_code == 409 and response.json()["error"]["code"] == "dataset_changed"
    assert "sheet" not in response.text.lower()


def test_uploaded_source_is_rejected_even_if_hash_matches_generated_snapshot(generated_api, tiny_dataset):
    client, database, old = generated_api
    current = database.replace(tiny_dataset, source="uploaded_local_only")
    assert current.dataset_version == old.dataset_version
    response = client.get(ROUTE, params={"expected_version": old.dataset_version})
    assert response.status_code == 403 and response.json()["error"]["code"] == "agent_privacy_block"
    with pytest.raises(DataError) as error:
        build_synthetic_export(database, old.dataset_version)
    assert error.value.code == "agent_privacy_block"


def test_empty_database_and_invalid_or_missing_versions_do_not_export(tmp_path):
    with TestClient(create_app(tmp_path / "empty.sqlite3", bootstrap_demo=False)) as client:
        assert client.get(ROUTE).status_code == 422
        assert client.get(ROUTE, params={"expected_version": "not-a-hash"}).status_code == 422
        result = client.get(ROUTE, params={"expected_version": "a" * 64})
        assert result.status_code == 503 and result.json()["error"]["code"] == "no_dataset"


def test_formula_like_course_and_class_names_are_literal_cells(generated_api, tiny_dataset, monkeypatch):
    client, database, _ = generated_api
    payload = '=HYPERLINK("https://example.invalid","not executed")'
    changed = replace(tiny_dataset,
        courses=(tiny_dataset.courses[0].model_copy(update={"course_name": payload, "source_header": "=1+1"}), *tiny_dataset.courses[1:]),
        classes=(tiny_dataset.classes[0].model_copy(update={"class_label": "+SUM(1,2)"}), *tiny_dataset.classes[1:]))
    info = database.replace(changed, source="generated", seed=1)
    monkeypatch.setenv("COPILOT_LLM_API_KEY", "SECRET_KEY_NOT_FOR_EXPORT")
    response = client.get(ROUTE, params={"expected_version": info.dataset_version})
    assert response.status_code == 200
    book = read_workbook(response.content)
    assert records(book["课程目录"])[0]["课程"] == payload
    assert records(book["课程目录"])[0]["原始课程表头"] == "=1+1"
    assert records(book["班级目录"])[0]["班级"] == "+SUM(1,2)"
    for sheet in book:
        for row in sheet:
            for cell in row:
                assert cell.data_type != "f" and cell.hyperlink is None
                assert "SECRET_KEY_NOT_FOR_EXPORT" not in str(cell.value)
    book.close()


def test_students_without_enrollments_remain_in_snapshot_directory(generated_api, tiny_dataset):
    _, database, _ = generated_api
    # The independent fixture's fourth student has one missing record. Remove
    # that enrollment/grade pair and retain the student in the validated data.
    enrollment_id = next(row.enrollment_id for row in tiny_dataset.enrollments if row.student_id == "S0004")
    changed = replace(tiny_dataset,
        enrollments=tuple(row for row in tiny_dataset.enrollments if row.enrollment_id != enrollment_id),
        grade_records=tuple(row for row in tiny_dataset.grade_records if row.enrollment_id != enrollment_id))
    info = database.replace(changed, source="generated", seed=1)
    book = read_workbook(build_synthetic_export(database, info.dataset_version))
    assert len(records(book["学生目录"])) == 4 and metadata(book)["学生数"] == 4
    assert len(records(book["成绩明细"])) == 5
    assert "S0004" not in {row["学生标识"] for row in records(book["成绩明细"])}
    book.close()


def test_source_guard_and_all_tables_share_one_read_snapshot(generated_api, tiny_dataset, monkeypatch):
    _, database, old = generated_api
    with database.connect() as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    original_connect = database.connect
    writer = Database(database.path)
    changed = replace(tiny_dataset, grade_records=(
        tiny_dataset.grade_records[0].model_copy(update={"score_cents": 9200}), *tiny_dataset.grade_records[1:]))
    switched = []

    class ConcurrentSwitch:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            # The reader has already selected metadata and checked generated
            # provenance. A separate connection commits an uploaded dataset
            # immediately before its first dimension-table read.
            if sql.startswith("SELECT * FROM classes") and not switched:
                switched.append(writer.replace(changed, source="uploaded_local_only"))
            return self.connection.execute(sql, *args)

    @contextmanager
    def connected():
        with original_connect() as connection:
            yield ConcurrentSwitch(connection)

    monkeypatch.setattr(database, "connect", connected)
    book = read_workbook(build_synthetic_export(database, old.dataset_version))
    assert metadata(book)["数据版本（dataset_version）"] == old.dataset_version
    assert metadata(book)["数据来源"] == "generated"
    assert records(book["成绩明细"])[0]["成绩（百分之一分）"] == tiny_dataset.grade_records[0].score_cents
    assert switched[0].dataset_version != old.dataset_version
    book.close()
    # Subsequent exports see the newly committed source and reject it.
    with pytest.raises(DataError) as error:
        build_synthetic_export(database, old.dataset_version)
    assert error.value.code == "agent_privacy_block"
