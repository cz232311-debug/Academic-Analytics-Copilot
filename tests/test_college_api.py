"""Local API acceptance for preview/confirmation; every workbook is synthetic."""
from dataclasses import replace
import time

from fastapi.testclient import TestClient
import pytest

from copilot.agent.provider import MockDemoProvider
from copilot.api import create_app
from copilot.data.college import parse_college_workbook
from copilot.data.importing import XLSX_TYPE
from copilot.storage import Database
from tests.college_fixture import EMPTY, ROWS, college_workbook


PREVIEW = "/api/datasets/college/preview"
COMMIT = "/api/datasets/college/commit"


class CountingMockProvider(MockDemoProvider):
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, tools):
        self.calls += 1
        return await super().complete(messages, tools)


@pytest.fixture
def college_client(tmp_path, tiny_dataset):
    store = Database(tmp_path / "college.sqlite3")
    store.initialize()
    store.replace(tiny_dataset, source="generated", seed=1)
    provider = CountingMockProvider()
    with TestClient(create_app(store.path, bootstrap_demo=False, agent_provider=provider)) as client:
        yield client, store, provider


def upload(content=None):
    return {"workbook": ("synthetic-college.xlsx", college_workbook() if content is None else content, XLSX_TYPE)}


def preview(client, content=None):
    response = client.post(PREVIEW, files=upload(content))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_import"] and body["preview_token"]
    return body


def commit(client, token, content=None, **fields):
    return client.post(COMMIT, files=upload(content),
                       data={"preview_token": token, "confirmed": "true", **fields})


def test_preview_only_identifies_and_validates_without_changing_active_data(college_client, tmp_path):
    client, store, provider = college_client
    before = client.get("/api/dataset").json()
    result = preview(client)
    assert result["before_counts"] == before["counts"]
    assert result["expected_version"] == before["dataset_version"]
    assert result["expires_in_seconds"] == 900
    assert result["terms"] == ["2025-T1"]
    assert client.get("/api/dataset").json() == before
    assert client.get("/api/analytics/summary").json()["sum_score_cents"] == 21999
    assert not list(tmp_path.glob("*.xlsx"))
    assert provider.calls == 0


@pytest.mark.parametrize("confirmed", ["false", "0"])
def test_confirmation_must_be_explicit_and_rejections_preserve_dataset(college_client, confirmed):
    client, _, _ = college_client
    before = client.get("/api/dataset").json()
    result = preview(client)
    response = commit(client, result["preview_token"], confirmed=confirmed)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "confirmation_required"
    assert client.get("/api/dataset").json() == before


def test_missing_confirmation_and_missing_token_never_commit(college_client):
    client, _, _ = college_client
    before = client.get("/api/dataset").json()
    result = preview(client)
    response = client.post(COMMIT, files=upload(), data={"preview_token": result["preview_token"]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "confirmation_required"
    assert client.post(COMMIT, files=upload(), data={"confirmed": "true"}).status_code == 422
    assert client.get("/api/dataset").json() == before


def test_tampered_preview_token_is_rejected_without_writes(college_client):
    client, _, _ = college_client
    before = client.get("/api/dataset").json()
    result = preview(client)
    token = result["preview_token"]
    response = commit(client, token[:-1] + "x")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "preview_invalid"
    assert client.get("/api/dataset").json() == before


def test_preview_token_is_bound_to_the_exact_uploaded_workbook(college_client):
    client, _, _ = college_client
    original = college_workbook()
    before = client.get("/api/dataset").json()
    result = preview(client, original)
    rows = [list(row) for row in ROWS]
    rows[0][4] = 88
    changed = college_workbook(rows=rows)
    response = commit(client, result["preview_token"], changed)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "preview_invalid"
    assert client.get("/api/dataset").json() == before


def test_expired_preview_requires_repreview_instead_of_overwriting(college_client, monkeypatch):
    client, _, _ = college_client
    before = client.get("/api/dataset").json()
    result = preview(client)
    future = time.time() + 901
    monkeypatch.setattr("copilot.data.college_service.time.time", lambda: future)
    response = commit(client, result["preview_token"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "preview_expired"
    assert client.get("/api/dataset").json() == before


def test_preview_version_conflict_preserves_the_newer_dataset(college_client, tiny_dataset):
    client, store, _ = college_client
    result = preview(client)
    changed_grade = tiny_dataset.grade_records[0].model_copy(update={"score_cents": 6100})
    changed = replace(tiny_dataset, grade_records=(changed_grade, *tiny_dataset.grade_records[1:]))
    store.replace(changed, source="generated", seed=2)
    newer = client.get("/api/dataset").json()
    response = commit(client, result["preview_token"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dataset_changed"
    assert client.get("/api/dataset").json() == newer


def test_confirmed_import_updates_version_catalog_and_existing_course_reports(college_client):
    client, _, _ = college_client
    content = college_workbook()
    before = client.get("/api/dataset").json()
    result = preview(client, content)
    response = commit(client, result["preview_token"], content)
    assert response.status_code == 200, response.text
    committed = response.json()
    info = committed["dataset"]
    assert info["dataset_version"] != before["dataset_version"]
    assert info["source"] == "uploaded_local_only" and info["external_llm_allowed"] is False
    assert committed["imported_counts"] == {
        "classes": 2, "students": 3, "courses": 2, "enrollments": 5, "grade_records": 5}
    assert committed["retained_course_count"] == 3
    assert info["counts"]["courses"] == 5 and info["counts"]["students"] == 3
    catalog = client.get("/api/reports/catalog").json()
    assert catalog["dataset_version"] == info["dataset_version"]
    assert catalog["terms"] == ["2025-T1"]
    assert {course["course_id"] for course in catalog["courses"]}.issuperset({"G1-C01", "G1-C02", "G2-C01"})
    response = client.get("/api/reports/courses", params={
        "term": "2025-T1", "grade_level": 2024, "expected_version": info["dataset_version"]})
    assert response.status_code == 200, response.text
    report = response.json()
    assert len(report["rows"]) == 2
    assert {row["course_name"] for row in report["rows"]} == {"Web技术基础", "工程实践2"}
    stats = report["summary"]
    assert (stats["enrollment_count"], stats["graded_count"], stats["missing_count"], stats["exempt_count"]) == (5, 3, 1, 1)
    assert stats["sum_score_cents"] == 15250
    assert stats["average_score"] == "50.83" and stats["pass_rate_percent"] == "66.67"
    assert sum(band["count"] for band in stats["distribution"]) == 3


def test_all_blank_course_exclusion_does_not_delete_an_existing_directory_entry(college_client):
    client, store, _ = college_client
    prior_content = college_workbook(headers=["学号", "姓名", "班级", EMPTY],
                                     rows=[["FIXTURE240001", "合成甲", "24软工1班", 60]])
    previous = parse_college_workbook("previous-synthetic.xlsx", XLSX_TYPE, prior_content).dataset
    old_course_id = previous.courses[0].course_id
    store.replace(previous, source="uploaded_local_only")
    content = college_workbook()
    result = preview(client, content)
    assert any(course["name"] == "体育 III" and course["excluded"] for course in result["courses"])
    response = commit(client, result["preview_token"], content)
    assert response.status_code == 200, response.text
    assert response.json()["retained_course_count"] == 1
    catalog = client.get("/api/reports/catalog").json()
    assert any(course["course_id"] == old_course_id for course in catalog["courses"])
    report = client.get("/api/reports/courses", params={"term": "2025-T1"}).json()
    assert all(row["course_id"] != old_course_id for row in report["rows"])


def test_uploaded_dataset_is_blocked_before_agent_provider_and_raw_data_never_logged(college_client, caplog):
    client, _, provider = college_client
    content = college_workbook()
    result = preview(client, content)
    assert commit(client, result["preview_token"], content).status_code == 200
    response = client.post("/api/agent/query", json={"question": "What is the average score?"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "agent_privacy_block"
    assert provider.calls == 0
    for raw in [row[index] for row in ROWS for index in (0, 1)]:
        assert raw not in caplog.text and raw not in response.text


def test_extra_multipart_fields_do_not_bypass_preview_or_confirmation(college_client):
    client, _, _ = college_client
    before = client.get("/api/dataset").json()
    response = client.post(PREVIEW, files=upload(), data={"unexpected": "not-a-command"})
    assert response.status_code == 422
    result = preview(client)
    response = commit(client, result["preview_token"], unexpected="not-a-command")
    assert response.status_code == 422
    assert client.get("/api/dataset").json() == before


@pytest.mark.parametrize("policy,cohorts", [("first", {2024}), ("last", {2023, 2024})])
def test_explicit_duplicate_resolution_is_bound_to_preview_and_commit(college_client, policy, cohorts):
    client, _, _ = college_client
    duplicate = list(ROWS[0])
    duplicate[2] = "23软工6班"
    content = college_workbook(rows=[*ROWS, duplicate])
    blocked = client.post(PREVIEW, files=upload(content)).json()
    assert not blocked["can_import"] and blocked["preview_token"] is None
    chosen = client.post(PREVIEW, files=upload(content), data={"duplicate_policy": policy}).json()
    assert chosen["can_import"] and chosen["duplicate_policy"] == policy
    assert chosen["counts"]["duplicate_student_rows"] == 1
    assert chosen["issue_counts"]["conflicting_student"] == 1
    response = commit(client, chosen["preview_token"], content)
    assert response.status_code == 200
    catalog = client.get("/api/reports/catalog").json()
    assert {row["grade_level"] for row in catalog["classes"]} == cohorts
