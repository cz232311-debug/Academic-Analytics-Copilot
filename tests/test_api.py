from fastapi.testclient import TestClient
import pytest

from copilot.api import create_app
from copilot.data.importing import MAX_FILE_BYTES, XLSX_TYPE
from copilot.storage import Database
from tests.test_imports import csv_files
from tests.workbook_fixture import workbook_bytes


def multipart(files):
    # HTTPX uses (filename, bytes, MIME); the importer uses (filename, MIME, bytes).
    return {table: (name, content, mime) for table, (name, mime, content) in files.items()}


@pytest.fixture
def client(tmp_path, tiny_dataset):
    path = tmp_path / "api.sqlite3"
    store = Database(path)
    store.initialize()
    store.replace(tiny_dataset)
    with TestClient(create_app(path, bootstrap_demo=False)) as session:
        yield session


def test_health_openapi_dataset_and_catalog(client):
    assert client.get("/health").json()["dataset_active"] is True
    assert client.get("/docs").status_code == 200
    assert "/api/datasets/import" in client.get("/openapi.json").json()["paths"]
    info = client.get("/api/dataset").json()
    assert info["counts"]["students"] == 4 and info["external_llm_allowed"] is False
    catalog = client.get("/api/catalog").json()
    assert len(catalog["courses"]) == 3 and "students" not in catalog


def test_filtered_statistics_and_decimal_wire_format(client):
    response = client.get("/api/analytics/summary", params={"course_id": "G1-C01", "grade_level": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["average_score"] == "73.33" and body["pass_rate_percent"] == "66.67"
    assert body["graded_count"] == 3 and body["missing_count"] == 1
    assert sum(b["count"] for b in body["distribution"]) == 3


@pytest.mark.parametrize("params", [{"grade_level": 4}, {"course_id": "G1-C01' OR 1=1 --"}, {"class_id": "../../x"}, {"unknown": "value"}])
def test_invalid_queries_are_rejected_and_not_echoed(client, params):
    response = client.get("/api/analytics/summary", params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "OR 1=1" not in response.text


def test_unknown_and_empty_scope(client):
    assert client.get("/api/analytics/summary?class_id=C999").status_code == 404
    assert client.get("/api/analytics/summary?term=2025-T1").status_code == 404
    response = client.get("/api/analytics/summary?course_id=G2-C01")
    assert response.status_code == 200 and response.json()["average_score"] is None


def test_class_comparison_and_bad_class_lists(client):
    params = [("course_id", "G1-C01"), ("class_ids", "C001"), ("class_ids", "C002")]
    response = client.get("/api/analytics/compare-classes", params=params)
    assert response.status_code == 200, response.text
    assert response.json()["highest_mean_class_ids"] == ["C002"]
    assert client.get("/api/analytics/compare-classes", params=params[:-1]).status_code == 422
    params[-1] = ("class_ids", "C001")
    assert client.get("/api/analytics/compare-classes", params=params).status_code == 422


def test_five_csv_uploads_activate_atomically(client, tiny_dataset):
    response = client.post("/api/datasets/import", files=multipart(csv_files(tiny_dataset)))
    assert response.status_code == 200, response.text
    assert response.json()["dataset"]["dataset_version"] == tiny_dataset.version
    assert response.json()["dataset"]["external_llm_allowed"] is False
    assert client.get("/api/analytics/summary").json()["average_score"] == "55.00"


def test_excel_upload_is_followed_by_actual_query(client, tiny_dataset):
    response = client.post("/api/datasets/import", files={"workbook": ("demo.xlsx", workbook_bytes(tiny_dataset.payload()), XLSX_TYPE)})
    assert response.status_code == 200, response.text
    assert response.json()["format"] == "xlsx"
    assert client.get("/api/analytics/summary?course_id=G1-C01").json()["sum_score_cents"] == 21999


def test_invalid_upload_preserves_previous_data_and_sanitizes_error(client, tiny_dataset):
    before = client.get("/api/dataset").json()
    files = csv_files(tiny_dataset)
    name, mime, content = files["students"]
    files["students"] = (name, mime, content.replace(b"Synthetic Student 0001", b"PRIVATE-NAME"))
    response = client.post("/api/datasets/import", files=multipart(files))
    assert response.status_code == 422 and "PRIVATE-NAME" not in response.text
    assert client.get("/api/dataset").json() == before
    assert client.get("/api/analytics/summary").json()["sum_score_cents"] == 21999


def test_wrong_media_size_and_incomplete_uploads(client, tiny_dataset):
    assert client.post("/api/datasets/import").status_code == 422
    files = multipart(csv_files(tiny_dataset))
    files["students"] = ("students.pkl", b"untrusted", "application/octet-stream")
    assert client.post("/api/datasets/import", files=files).status_code == 415
    files["students"] = ("students.csv", b"x" * (MAX_FILE_BYTES + 1), "text/csv")
    assert client.post("/api/datasets/import", files=files).status_code == 413


def test_request_byte_limit_checks_actual_bytes_without_trusting_length(tmp_path):
    with TestClient(create_app(tmp_path / "bounded.sqlite3", bootstrap_demo=False, max_request_bytes=100)) as session:
        assert session.post("/api/datasets/import", content=b"x" * 101).status_code == 413
        assert session.post("/api/datasets/import", content=b"x" * 101, headers={"Content-Length": "1"}).status_code == 413
        assert session.post("/api/datasets/import", content=b"", headers={"Content-Length": "bad"}).status_code == 400


def test_empty_database_and_default_bootstrap_are_real(tmp_path):
    with TestClient(create_app(tmp_path / "empty.sqlite3", bootstrap_demo=False)) as session:
        assert session.get("/api/dataset").status_code == 503
    path = tmp_path / "demo.sqlite3"
    with TestClient(create_app(path)) as session:
        info = session.get("/api/dataset").json()
        assert info["counts"]["students"] == 1382
        assert info["counts"]["classes"] == 40 and info["counts"]["courses"] == 96
        assert info["minimum_courses_per_student"] == 10 and info["maximum_courses_per_student"] == 12
        version = info["dataset_version"]
    with TestClient(create_app(path)) as session:
        assert session.get("/api/dataset").json()["dataset_version"] == version


def test_duplicate_multipart_fields_and_mixed_upload_modes_rejected(client, tiny_dataset):
    files = multipart(csv_files(tiny_dataset))
    duplicates = [*files.items(), ("students", files["students"])]
    assert client.post("/api/datasets/import", files=duplicates).status_code == 422
    files["workbook"] = ("demo.xlsx", workbook_bytes(tiny_dataset.payload()), XLSX_TYPE)
    assert client.post("/api/datasets/import", files=files).status_code == 422


def test_restart_preserves_uploaded_dataset(tmp_path, tiny_dataset):
    path = tmp_path / "persistent.sqlite3"
    with TestClient(create_app(path)) as session:
        assert session.post("/api/datasets/import", files=multipart(csv_files(tiny_dataset))).status_code == 200
    with TestClient(create_app(path)) as session:
        info = session.get("/api/dataset").json()
        assert info["counts"]["students"] == 4
        assert info["source"] == "uploaded_local_only" and info["dataset_version"] == tiny_dataset.version


def test_malformed_multipart_and_unknown_route_have_sanitized_errors(client):
    malformed = client.post("/api/datasets/import", content=b"invalid multipart", headers={"Content-Type": "multipart/form-data; boundary=abc"})
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "http_error"
    assert client.get("/missing-route").json()["error"]["code"] == "http_error"
