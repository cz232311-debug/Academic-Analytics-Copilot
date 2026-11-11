"""Phase 3C UI/source-version boundaries, using only synthetic fixtures."""
import json
from io import BytesIO

from openpyxl import load_workbook
import pytest

from copilot.agent.tools import ToolRegistry
from copilot.dashboard.client import DashboardError
from copilot.dashboard.chat import _evidence_cn
from copilot.models import DataError
from tests.test_chat_app import chat_env, activate_synthetic, submit


def test_download_fetches_current_snapshot_only_when_clicked(chat_env, monkeypatch, tiny_dataset):
    controls = []
    monkeypatch.setattr("copilot.dashboard.chat.st.download_button",
                        lambda label, **kwargs: controls.append((label, kwargs)))
    page = activate_synthetic(chat_env.page())
    assert not page.exception
    assert "/api/datasets/synthetic/export" not in chat_env.paths()
    label, button = controls[-1]
    assert label == "下载当前合成成绩单（XLSX）" and callable(button["data"])
    version = page.session_state.chat["dataset"].dataset_version
    book = load_workbook(BytesIO(button["data"]()))
    meta = dict(book["数据说明"].iter_rows(min_row=2, values_only=True))
    assert meta["数据版本（dataset_version）"] == version
    assert meta["学生数"] == 12 and meta["课程数"] == 6 and meta["成绩记录数"] == 72
    book.close()
    chat_env.store.replace(tiny_dataset, source="generated", seed=99)
    with pytest.raises(DashboardError, match="数据") as error:
        button["data"]()  # Old rendered button must not serve cached old bytes.
    assert error.value.code == "dataset_changed"


def test_upload_has_no_synthetic_download(chat_env, monkeypatch, tiny_dataset):
    controls = []
    monkeypatch.setattr("copilot.dashboard.chat.st.download_button",
                        lambda *args, **kwargs: controls.append(kwargs))
    chat_env.store.replace(tiny_dataset, source="uploaded_local_only")
    page = chat_env.page()
    assert not page.exception and controls == []
    assert "/api/datasets/synthetic/export" not in chat_env.paths()


def test_same_hash_source_flip_invalidates_history_and_old_download(chat_env, monkeypatch):
    controls = []
    monkeypatch.setattr("copilot.dashboard.chat.st.download_button",
                        lambda *args, **kwargs: controls.append(kwargs))
    page = activate_synthetic(chat_env.page())
    old_button = controls[-1]["data"]
    version = chat_env.store.overview().dataset_version
    # Same content/hash; provenance is an independent privacy boundary.
    with chat_env.store.connect() as connection:
        connection.execute("UPDATE dataset_metadata SET source='uploaded_local_only'")
    assert chat_env.store.overview().dataset_version == version
    with pytest.raises(DashboardError) as error:
        old_button()
    assert error.value.code == "agent_privacy_block"
    page.run()
    assert not page.exception
    assert page.session_state.chat["dataset"] is None
    assert page.session_state.chat["messages"] == []


def test_readable_catalog_and_comparison_keep_full_ids_in_details(chat_env):
    page = activate_synthetic(chat_env.page())
    course = chat_env.client.catalog().courses[0]
    submit(page, "查询当前数据集有哪些课程")
    result = page.session_state.chat["messages"][-1]["response"]
    assert result.status == "completed"
    # Body text is readable; full IDs remain in JSON technical evidence/export.
    assert course.course_id not in "\n".join(item.value for item in page.markdown)
    assert course.course_name in "\n".join(item.value for item in page.markdown)
    assert any(item.value == result.dataset_version for item in page.code)
    submit(page, f"比较 C001 和 C002 的{course.course_name}成绩")
    assert not page.exception
    text = _evidence_cn(page.session_state.chat["messages"][-1]["response"], chat_env.client.catalog())
    assert course.course_id not in text and "C001" not in text
    assert chat_env.client.catalog().classes[0].class_label in text


def test_major_filtered_summary_reuses_report_engine_and_privacy_guard(chat_env):
    page = activate_synthetic(chat_env.page())
    version = chat_env.store.overview().dataset_version
    response = chat_env.client.query("2024级软工Web技术基础的平均分",
                                     execution_mode="local", expected_version=version)
    assert response.status == "completed"
    evidence = response.evidence[0]
    assert evidence.arguments["major"] == "software_engineering"
    assert evidence.data["graded_count"] == 12
    assert evidence.data["report_scope"]["major"] == "software_engineering"
    # Same engine path used by grounded mode must retain generated-only guard.
    with chat_env.store.connect() as connection:
        connection.execute("UPDATE dataset_metadata SET source='uploaded_local_only'")
    registry = ToolRegistry(chat_env.store)
    arguments = registry.validate("get_score_summary", json.dumps(evidence.arguments))
    with pytest.raises(DataError) as error:
        registry.execute("get_score_summary", arguments, evidence_id="r1", version=version, generated_only=True)
    assert error.value.code == "agent_privacy_block"
