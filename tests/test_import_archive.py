"""Original retention uses synthetic uploads only and never leaks identities to JSON."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from copilot.data.college_service import CollegeImportService
from copilot.data.import_archive import ImportArchive, StagedImport
from copilot.data.importing import XLSX_TYPE
from copilot.models import DataError
from copilot.storage import Database
from tests.college_fixture import ROWS, college_workbook


@pytest.fixture
def service(tmp_path, tiny_dataset):
    store = Database(tmp_path / "local.sqlite3")
    store.initialize()
    store.replace(tiny_dataset, source="generated", seed=1)
    return CollegeImportService(store)


def prepare(service):
    content = college_workbook()
    return content, service.preview("synthetic-private.xlsx", XLSX_TYPE, content)


def commit(service, content, preview):
    return service.commit("synthetic-private.xlsx", XLSX_TYPE, content,
                          preview_token=preview.preview_token, confirmed=True)


def test_preview_creates_no_private_archive_or_original(service):
    before = service.database.overview()
    _, preview = prepare(service)
    assert preview.can_import
    assert not service.archive.root.exists()
    assert service.database.overview() == before


def test_confirmed_import_retains_exact_original_cleaning_and_confirmation(service):
    content, preview = prepare(service)
    result = commit(service, content, preview)
    assert result.archive_status == "complete" and len(result.archive_id) == 32
    folder = service.archive.root / result.archive_id
    assert (folder / "original.xlsx").read_bytes() == content
    cleaning = json.loads((folder / "cleaning.json").read_text(encoding="utf-8"))
    confirmation = json.loads((folder / "confirmation.json").read_text(encoding="utf-8"))
    assert cleaning["original_sha256"] == hashlib.sha256(content).hexdigest()
    assert cleaning["counts"] == preview.counts
    assert cleaning["assumptions"] == preview.assumptions
    assert cleaning["duplicate_policy"] == "block"
    assert confirmation["confirmed"] and confirmation["status"] == "committed"
    assert confirmation["dataset_version"] == result.dataset.dataset_version
    assert not list(service.archive.root.glob(".pending-*"))
    # A restarted service neither deletes nor re-identifies prior archives.
    restarted = CollegeImportService(service.database)
    assert (restarted.archive.root / result.archive_id / "original.xlsx").read_bytes() == content


def test_archive_receipts_never_contain_original_identity_sample_filename_or_token(service):
    content, preview = prepare(service)
    result = commit(service, content, preview)
    folder = service.archive.root / result.archive_id
    text = "\n".join(path.read_text(encoding="utf-8") for path in folder.glob("*.json"))
    for raw in [row[index] for row in ROWS for index in (0, 1)]:
        assert raw not in text
    assert "synthetic-private.xlsx" not in text
    assert preview.preview_token not in text
    assert '"sample"' not in text
    assert '"filename"' not in text
    assert '"preview_token"' not in text


def test_failed_database_version_guard_discards_only_its_staging_original(service, tiny_dataset):
    content, preview = prepare(service)
    changed = replace(tiny_dataset, grade_records=(
        tiny_dataset.grade_records[0].model_copy(update={"score_cents": 6100}), *tiny_dataset.grade_records[1:]))
    newer = service.database.replace(changed, source="generated", seed=2)
    with pytest.raises(DataError) as error:
        commit(service, content, preview)
    assert error.value.code == "dataset_changed"
    assert service.database.overview() == newer
    assert list(service.archive.root.iterdir()) == []


def test_archive_write_failure_prevents_database_commit(service, monkeypatch):
    content, preview = prepare(service)
    before = service.database.overview()
    def fail_write(*args, **kwargs):
        raise OSError("synthetic local disk failure")
    monkeypatch.setattr(service.archive, "_write_file", fail_write)
    with pytest.raises(DataError) as error:
        commit(service, content, preview)
    assert error.value.code == "archive_unavailable"
    assert service.database.overview() == before
    assert list(service.archive.root.iterdir()) == []


def test_finalize_failure_reports_pending_without_claiming_rollback(service, monkeypatch):
    content, preview = prepare(service)
    before = service.database.overview()
    def fail_finalize(*args, **kwargs):
        raise OSError("synthetic local receipt failure")
    monkeypatch.setattr(service.archive, "finalize", fail_finalize)
    result = commit(service, content, preview)
    assert result.archive_status == "pending"
    assert result.dataset.dataset_version != before.dataset_version
    assert service.database.overview().dataset_version == result.dataset.dataset_version
    assert service.database.overview().source == "uploaded_local_only"
    pending = service.archive.root / (".pending-" + result.archive_id)
    assert (pending / "original.xlsx").read_bytes() == content
    assert (pending / "cleaning.json").is_file()


def test_discard_refuses_a_target_outside_the_exact_private_staging_directory(service, tmp_path):
    external = tmp_path / "owner-data"
    external.mkdir()
    (external / "keep.txt").write_text("synthetic owner file", encoding="utf-8")
    with pytest.raises(ValueError):
        service.archive.discard(StagedImport("a" * 32, external))
    assert (external / "keep.txt").is_file()
