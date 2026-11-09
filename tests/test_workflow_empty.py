"""Empty-start workflow and first-import concurrency checks, synthetic data only."""
from dataclasses import replace
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import copilot.api as api_module
from copilot.api import create_app
from copilot.data.importing import XLSX_TYPE
from copilot.models import DataError, TABLE_NAMES
from copilot.storage import Database
from tests.college_fixture import college_workbook


PREVIEW = "/api/datasets/college/preview"
COMMIT = "/api/datasets/college/commit"


def upload(content):
    return {"workbook": ("synthetic-workflow.xlsx", content, XLSX_TYPE)}


def preview(client, content):
    response = client.post(PREVIEW, files=upload(content))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["can_import"] and result["preview_token"]
    return result


def commit(client, content, token):
    return client.post(COMMIT, files=upload(content), data={"preview_token": token, "confirmed": "true"})


def test_production_entrypoint_does_not_activate_demo_by_default(tmp_path, monkeypatch):
    # Execute the production module with a private path; never enter the real
    # module-level app lifespan against a user's existing application database.
    path = tmp_path / "production-empty.sqlite3"
    monkeypatch.setenv("ACADEMIC_DB_PATH", str(path))
    spec = importlib.util.spec_from_file_location("copilot_empty_start_probe", Path(api_module.__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        assert client.get("/health").json()["dataset_active"] is False
        response = client.get("/api/dataset")
        assert response.status_code == 503 and response.json()["error"]["code"] == "no_dataset"
        assert not module.app.state.database.has_dataset()


def test_explicit_factory_demo_fixture_remains_available(tmp_path):
    with TestClient(create_app(tmp_path / "explicit-fixture.sqlite3", bootstrap_demo=True)) as client:
        assert client.get("/api/dataset").json()["counts"]["students"] == 1382


def test_empty_dataset_preview_does_not_activate_or_write_business_rows(tmp_path):
    path = tmp_path / "empty-preview.sqlite3"
    with TestClient(create_app(path, bootstrap_demo=False)) as client:
        result = preview(client, college_workbook())
        assert result["expected_version"] is None
        assert result["before_counts"] == {name: 0 for name in TABLE_NAMES}
        assert client.get("/health").json()["dataset_active"] is False
        store = client.app.state.database
        with store.connect() as conn:
            assert all(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] == 0 for name in TABLE_NAMES)


def test_first_confirmed_import_activates_local_dataset_and_rejects_replay(tmp_path):
    content = college_workbook()
    with TestClient(create_app(tmp_path / "first-import.sqlite3", bootstrap_demo=False)) as client:
        result = preview(client, content)
        response = commit(client, content, result["preview_token"])
        assert response.status_code == 200, response.text
        active = client.get("/api/dataset").json()
        assert active["counts"]["students"] == 3
        assert active["source"] == "uploaded_local_only" and active["external_llm_allowed"] is False
        assert client.get("/health").json()["dataset_active"] is True
        replay = commit(client, content, result["preview_token"])
        assert replay.status_code == 409 and replay.json()["error"]["code"] == "dataset_changed"
        assert client.get("/api/dataset").json() == active


def test_preview_of_empty_database_cannot_overwrite_a_later_activation(tmp_path, tiny_dataset):
    content = college_workbook()
    with TestClient(create_app(tmp_path / "empty-race.sqlite3", bootstrap_demo=False)) as client:
        result = preview(client, content)
        client.app.state.database.replace(tiny_dataset, source="generated", seed=1)
        newer = client.get("/api/dataset").json()
        response = commit(client, content, result["preview_token"])
        assert response.status_code == 409 and response.json()["error"]["code"] == "dataset_changed"
        assert client.get("/api/dataset").json() == newer


def test_existing_dataset_preview_still_requires_same_version(tmp_path, tiny_dataset):
    content = college_workbook()
    store = Database(tmp_path / "existing-race.sqlite3")
    store.initialize()
    before = store.replace(tiny_dataset, source="generated", seed=1)
    with TestClient(create_app(store.path, bootstrap_demo=False)) as client:
        result = preview(client, content)
        assert result["expected_version"] == before.dataset_version
        changed = replace(tiny_dataset, grade_records=(
            tiny_dataset.grade_records[0].model_copy(update={"score_cents": 6100}), *tiny_dataset.grade_records[1:]))
        store.replace(changed, source="generated", seed=2)
        newer = client.get("/api/dataset").json()
        response = commit(client, content, result["preview_token"])
        assert response.status_code == 409 and response.json()["error"]["code"] == "dataset_changed"
        assert client.get("/api/dataset").json() == newer


def test_empty_guard_is_checked_before_any_replacement_or_directory_merge(tmp_path, tiny_dataset):
    store = Database(tmp_path / "guard.sqlite3")
    store.initialize()
    before = store.replace(tiny_dataset, source="generated", seed=1)
    with pytest.raises(DataError) as error:
        store.replace(tiny_dataset, expected_empty=True, preserve_courses=True)
    assert error.value.code == "dataset_changed" and error.value.status == 409
    assert store.overview() == before
    with pytest.raises(ValueError):
        store.replace(tiny_dataset, expected_empty=True, expected_version=before.dataset_version)
