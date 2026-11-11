"""Only server-derived data can obtain generated provenance for LLM demos."""
from dataclasses import replace
import time

from fastapi.testclient import TestClient
import pytest

from copilot.api import create_app
from copilot.data.college_service import CollegeImportService
from copilot.data.importing import XLSX_TYPE
from copilot.data.synthetic_activation import SyntheticActivationService
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook
from copilot.models import DataError
from copilot.storage import Database

PREVIEW = "/api/datasets/synthetic/preview"
COMMIT = "/api/datasets/synthetic/commit"


@pytest.fixture
def options():
    return SyntheticOptions(classes_per_major=2, students_per_class=3, course_count=3)


@pytest.fixture
def demo_api(tmp_path):
    store = Database(tmp_path / "trusted-synthetic.sqlite3")
    with TestClient(create_app(store.path, bootstrap_demo=False)) as client:
        yield client, store


def preview(client, options):
    response = client.post(PREVIEW, json=options.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    return response.json()


def commit(client, options, token, **fields):
    return client.post(COMMIT, json={"options": options.model_dump(mode="json"),
                                    "preview_token": token, "confirmed": True, **fields})


def test_empty_preview_does_not_seed_and_confirmation_activates_only_generated_records(demo_api, options):
    client, store = demo_api
    before = client.get("/health").json()
    result = preview(client, options)
    assert result["can_import"] and result["preview_token"]
    assert result["expected_version"] is None and set(result["before_counts"].values()) == {0}
    assert client.get("/health").json() == before and not store.has_dataset()
    assert not (store.path.parent / ".import-history").exists()
    response = commit(client, options, result["preview_token"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset"]["source"] == "generated" and body["dataset"]["external_llm_allowed"] is True
    assert body["dataset"]["seed"] == options.seed
    assert body["imported_counts"]["students"] == 6 and body["imported_counts"]["grade_records"] == 18
    assert body["retained_course_count"] == 0 and body["archive_id"] is None
    replay = commit(client, options, result["preview_token"])
    assert replay.status_code == 409 and replay.json()["error"]["code"] == "dataset_changed"


@pytest.mark.parametrize("confirmed", [False, "true", 1])
def test_explicit_strict_confirmation_is_required(demo_api, options, confirmed):
    client, store = demo_api
    result = preview(client, options)
    response = commit(client, options, result["preview_token"], confirmed=confirmed)
    assert response.status_code == 422 and not store.has_dataset()


def test_token_rejects_changed_options_and_tampering_without_writes(demo_api, options):
    client, store = demo_api
    result = preview(client, options)
    changed = options.model_copy(update={"seed": options.seed + 1})
    for candidate, token in ((changed, result["preview_token"]), (options, result["preview_token"] + "x")):
        response = commit(client, candidate, token)
        assert response.status_code == 409 and response.json()["error"]["code"] == "preview_invalid"
    assert not store.has_dataset()


def test_expired_or_other_process_token_requires_new_preview(demo_api, options, monkeypatch):
    client, store = demo_api
    result = preview(client, options)
    with pytest.raises(DataError) as error:
        SyntheticActivationService(store).commit(options, result["preview_token"], True)
    assert error.value.code == "preview_invalid"
    future = time.time() + 901
    monkeypatch.setattr("copilot.data.synthetic_activation.time.time", lambda: future)
    response = commit(client, options, result["preview_token"])
    assert response.status_code == 409 and response.json()["error"]["code"] == "preview_expired"
    assert not store.has_dataset()


def test_empty_preview_cannot_overwrite_concurrent_dataset(demo_api, options, tiny_dataset):
    client, store = demo_api
    result = preview(client, options)
    newer = store.replace(tiny_dataset, source="uploaded_local_only")
    response = commit(client, options, result["preview_token"])
    assert response.status_code == 409 and response.json()["error"]["code"] == "dataset_changed"
    assert store.overview() == newer


def test_existing_dataset_change_rejects_stale_preview(demo_api, options, tiny_dataset):
    client, store = demo_api
    store.replace(tiny_dataset, source="uploaded_local_only")
    result = preview(client, options)
    changed = replace(tiny_dataset, grade_records=(tiny_dataset.grade_records[0].model_copy(update={"score_cents": 6100}),
                                                 *tiny_dataset.grade_records[1:]))
    newer = store.replace(changed, source="uploaded_local_only")
    response = commit(client, options, result["preview_token"])
    assert response.status_code == 409 and store.overview() == newer


def test_demo_replaces_prior_private_catalog_instead_of_inheriting_its_labels(demo_api, options, tiny_dataset):
    client, store = demo_api
    private = replace(tiny_dataset, courses=(tiny_dataset.courses[0].model_copy(update={"course_name": "PRIVATE_CATALOG_SENTINEL"}),
                                           *tiny_dataset.courses[1:]))
    store.replace(private, source="uploaded_local_only")
    result = preview(client, options)
    response = commit(client, options, result["preview_token"])
    assert response.status_code == 200
    catalog = client.get("/api/reports/catalog").json()
    assert "PRIVATE_CATALOG_SENTINEL" not in str(catalog)
    assert len(catalog["courses"]) == options.course_count
    assert all(row["course_id"].startswith("I") for row in catalog["courses"])
    assert store.overview().source == "generated"


def test_generated_workbook_uploaded_as_a_file_stays_local_only(demo_api, options):
    client, store = demo_api
    file = generate_college_workbook(options)
    upload = {"workbook": (file.filename, file.content, XLSX_TYPE)}
    parsed = client.post("/api/datasets/college/preview", files=upload).json()
    response = client.post("/api/datasets/college/commit", files=upload,
                           data={"preview_token": parsed["preview_token"], "confirmed": "true"})
    assert response.status_code == 200
    assert store.overview().source == "uploaded_local_only" and store.overview().external_llm_allowed is False
    # This check is independent of configured model credentials.
    blocked = client.post("/api/agent/query", json={"question": "平均分", "execution_mode": "configured"})
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "agent_privacy_block"


def test_client_cannot_supply_bytes_or_provenance_flags_and_tokens_cannot_cross_import_services(demo_api, options):
    client, store = demo_api
    values = options.model_dump(mode="json")
    for extra in ({"source": "generated"}, {"filename": "trusted.xlsx"}, {"content": "PRIVATE_BYTES"}):
        response = client.post(PREVIEW, json={**values, **extra})
        assert response.status_code == 422
    result = preview(client, options)
    assert commit(client, options, result["preview_token"], source="generated").status_code == 422
    file = generate_college_workbook(options)
    raw = CollegeImportService(store).preview(file.filename, XLSX_TYPE, file.content)
    response = commit(client, options, raw.preview_token)
    assert response.status_code == 409 and not store.has_dataset()


def test_blocking_synthetic_scenario_has_no_usable_activation_token(demo_api, options):
    client, store = demo_api
    conflict = options.model_copy(update={"scenario": "conflict"})
    result = preview(client, conflict)
    assert result["can_import"] is False and result["preview_token"] is None
    assert result["issue_counts"]["conflicting_student"] == 1
    assert not store.has_dataset()


def test_synthetic_json_body_and_invalid_options_are_bounded(demo_api, options):
    client, store = demo_api
    assert client.post(PREVIEW, content=b"x" * 8193).status_code == 413
    assert client.post(COMMIT, content=b"x" * 8193).status_code == 413
    values = options.model_dump(mode="json")
    for bad in ({"majors": []}, {"seed": "123"}, {"semester": 3}, {"students_per_class": 10000}):
        assert client.post(PREVIEW, json={**values, **bad}).status_code == 422
    assert not store.has_dataset()
