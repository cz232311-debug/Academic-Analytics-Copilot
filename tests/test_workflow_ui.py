"""Staff workflow acceptance through Streamlit and the real API, synthetic only."""
from dataclasses import replace
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.college_fixture import ROWS, college_workbook
from tests.test_college_dashboard import button, confirmation, page_backend, uploaded_page

ENTRY = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


def no_upload(monkeypatch):
    monkeypatch.setattr("copilot.dashboard.import_panel.st.file_uploader", lambda *args, **kwargs: None)


def confirmed_page(monkeypatch):
    page = uploaded_page(monkeypatch, college_workbook())
    assert not page.exception
    confirmation(page).check().run()
    button(page, "确认数据并进入分析").click().run()
    assert not page.exception, [item.message for item in page.exception]
    assert page.session_state.workflow.step == "analysis"
    return page


def test_first_open_is_upload_centered_and_does_not_select_existing_demo(page_backend, monkeypatch):
    store, client = page_backend
    before = store.overview()
    no_upload(monkeypatch)
    page = AppTest.from_file(ENTRY, default_timeout=30).run()
    assert not page.exception
    assert page.session_state.workflow.step == "upload"
    assert page.session_state.workflow.confirmed is None
    assert len(page.metric) == len(page.dataframe) == len(page.selectbox) == 0
    assert set(client.request_paths) == {"/health"}
    assert store.overview() == before
    assert button(page, "使用示例数据体验")
    for label in ("3 分析", "4 撰写", "5 导出"):
        assert button(page, label).disabled


def test_example_requires_review_and_confirmation_instead_of_activation(page_backend, monkeypatch):
    store, client = page_backend
    before = store.overview()
    no_upload(monkeypatch)
    page = AppTest.from_file(ENTRY, default_timeout=30).run()
    button(page, "使用示例数据体验").click().run()
    assert not page.exception
    state = page.session_state.workflow
    assert state.step == "review" and state.upload.source == "example"
    assert state.preview and state.preview.can_import and state.confirmed is None
    assert button(page, "确认数据并进入分析").disabled
    assert store.overview() == before
    assert "/api/reports/courses" not in client.request_paths


def test_conflicting_uploaded_identifiers_are_visible_and_block_submission(page_backend, monkeypatch):
    store, _ = page_backend
    before = store.overview()
    conflict = list(ROWS[0])
    conflict[2] = "23软工6班"
    page = uploaded_page(monkeypatch, college_workbook(rows=[*ROWS, conflict]))
    state = page.session_state.workflow
    assert not page.exception and state.step == "review"
    assert not state.preview.can_import and state.preview.issue_counts["conflicting_student"] == 1
    assert button(page, "确认数据并进入分析").disabled
    assert store.overview() == before


def test_confirmed_analysis_builds_one_version_context_with_all_three_views(page_backend, monkeypatch):
    store, client = page_backend
    page = confirmed_page(monkeypatch)
    state = page.session_state.workflow
    assert state.has_current_report and state.has_current_context
    assert state.report.dataset_version == state.confirmed.dataset_version == store.overview().dataset_version
    assert state.context.scope == state.report.scope
    assert set(state.context.selected_views) == {"course_table", "rates_chart", "mean_bands"}
    assert state.report.summary.graded_count == 3 and str(state.report.summary.average_score) == "50.83"
    assert len(page.dataframe) >= 2
    assert all(row.statistics.dataset_version == state.report.dataset_version for row in state.report.rows)
    assert not any(path.startswith("/api/agent") for path in client.request_paths)


def test_filter_change_immediately_invalidates_old_report_before_update(page_backend, monkeypatch):
    _, _client = page_backend
    page = confirmed_page(monkeypatch)
    state = page.session_state.workflow
    state.draft = "STALE_REPORT_DRAFT"
    next(item for item in page.selectbox if item.label == "班级").set_value("C001").run()
    state = page.session_state.workflow
    assert not page.exception
    assert state.scope.class_id == "C001"
    assert state.report is state.context is state.draft is None
    assert not page.dataframe
    button(page, "更新统计预览").click().run()
    state = page.session_state.workflow
    assert not page.exception and state.has_current_context
    assert state.report.scope.class_id == "C001" and state.report.summary.student_count == 2


def test_report_content_and_sections_are_applied_without_changing_statistics(page_backend, monkeypatch):
    page = confirmed_page(monkeypatch)
    original = page.session_state.workflow.report
    page.session_state.workflow.draft = "STALE_REPORT_DRAFT"
    next(item for item in page.multiselect if item.label == "纳入报告的内容").set_value(["course_table"]).run()
    assert not page.exception
    assert page.session_state.workflow.report == original
    assert page.session_state.workflow.context.selected_views == ("course_table",)
    assert page.session_state.workflow.draft is None
    next(item for item in page.multiselect if item.label == "报告章节").set_value(["overview", "methods"]).run()
    assert page.session_state.workflow.context.sections == ("overview", "methods")


@pytest.mark.parametrize("label", ["纳入报告的内容", "报告章节"])
def test_clearing_content_selection_revokes_existing_context_and_draft(page_backend, monkeypatch, label):
    page = confirmed_page(monkeypatch)
    page.session_state.workflow.draft = "STALE_REPORT_DRAFT"
    next(item for item in page.multiselect if item.label == label).set_value([]).run()
    state = page.session_state.workflow
    assert not page.exception and state.report is not None
    assert state.context is state.draft is None
    assert button(page, "4 撰写").disabled and button(page, "5 导出").disabled


def test_backward_navigation_preserves_confirmed_data_and_cleaning_record(page_backend, monkeypatch):
    store, _ = page_backend
    page = confirmed_page(monkeypatch)
    before = store.overview()
    confirmed = page.session_state.workflow.confirmed
    button(page, "2 核对").click().run()
    assert not page.exception and page.session_state.workflow.step == "review"
    assert page.session_state.workflow.preview is not None
    button(page, "1 上传").click().run()
    assert not page.exception and page.session_state.workflow.step == "upload"
    assert page.session_state.workflow.confirmed == confirmed and store.overview() == before
    button(page, "3 分析").click().run()
    assert not page.exception and page.session_state.workflow.step == "analysis"
    assert page.session_state.workflow.confirmed.dataset_version == before.dataset_version
    assert any(record["event"] == "cleaning_preview" for record in page.session_state.workflow.history)
    assert any(record["event"] == "confirmed" for record in page.session_state.workflow.history)


def test_backend_version_change_revokes_old_context_without_auto_reconfirmation(page_backend, monkeypatch, tiny_dataset):
    store, _ = page_backend
    page = confirmed_page(monkeypatch)
    page.session_state.workflow.draft = "STALE_REPORT_DRAFT"
    changed = replace(tiny_dataset, grade_records=(tiny_dataset.grade_records[0].model_copy(update={"score_cents": 6100}),
                                                  *tiny_dataset.grade_records[1:]))
    newer = store.replace(changed, source="generated", seed=1)
    page.run()
    state = page.session_state.workflow
    assert not page.exception and state.step in {"upload", "review"}
    assert state.confirmed is state.report is state.context is state.draft is None
    assert store.overview().dataset_version == newer.dataset_version
    assert any(record["event"] == "dataset_invalidated" for record in state.history)


def test_backend_source_change_invalidates_even_when_hash_is_unchanged(page_backend, monkeypatch):
    store, _ = page_backend
    page = confirmed_page(monkeypatch)
    version = store.overview().dataset_version
    with store.connect() as connection:
        # A provenance-only mutation is deliberately a synthetic test fixture.
        connection.execute("UPDATE dataset_metadata SET source='generated',seed=1 WHERE singleton=1")
    page.run()
    state = page.session_state.workflow
    assert not page.exception and store.overview().dataset_version == version
    assert state.confirmed is state.report is state.context is None


def test_reupload_invalidates_analysis_but_does_not_overwrite_confirmed_database(page_backend, monkeypatch):
    store, _ = page_backend
    page = confirmed_page(monkeypatch)
    before = store.overview()
    previous = page.session_state.workflow.confirmed
    rows = [list(row) for row in ROWS]
    rows[0][4] = 88

    class NewUpload:
        name = "second-synthetic.xlsx"

        def getvalue(self):
            return college_workbook(rows=rows)

    monkeypatch.setattr("copilot.dashboard.import_panel.st.file_uploader", lambda *args, **kwargs: NewUpload())
    button(page, "1 上传").click().run()
    state = page.session_state.workflow
    assert not page.exception and state.step == "review"
    assert state.confirmed == previous and not state.confirmed_matches_upload
    assert state.report is state.context is state.draft is None
    assert store.overview() == before and button(page, "3 分析").disabled


def test_new_browser_session_does_not_implicitly_reuse_active_uploaded_results(page_backend, monkeypatch):
    store, _ = page_backend
    confirmed_page(monkeypatch)
    before = store.overview()
    no_upload(monkeypatch)
    refreshed = AppTest.from_file(ENTRY, default_timeout=30).run()
    assert not refreshed.exception
    assert refreshed.session_state.workflow.step == "upload" and refreshed.session_state.workflow.confirmed is None
    assert refreshed.session_state.workflow.report is None and not refreshed.metric and not refreshed.dataframe
    assert store.overview() == before


def test_writing_and_export_are_navigation_only_and_context_is_deidentified(page_backend, monkeypatch):
    _, client = page_backend
    page = confirmed_page(monkeypatch)
    context = page.session_state.workflow.context
    text = context.model_dump_json()
    for raw in [row[index] for row in ROWS for index in (0, 1)]:
        assert raw not in text
    assert "synthetic-college.xlsx" not in text
    assert context.external_llm_allowed is context.report_generation_implemented is context.docx_export_implemented is False
    button(page, "保存内容选择，进入撰写").click().run()
    assert not page.exception and page.session_state.workflow.step == "writing"
    button(page, "5 导出").click().run()
    assert not page.exception and page.session_state.workflow.step == "export"
    assert not any(path.startswith("/api/agent") for path in client.request_paths)
