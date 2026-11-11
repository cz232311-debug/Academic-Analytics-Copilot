"""Synthetic workbook UI uses explicit preview/confirmation, never autoactivation."""
from io import BytesIO
from pathlib import Path
import zipfile

from streamlit.testing.v1 import AppTest

from copilot.dashboard.client import DashboardError
from tests.test_college_dashboard import button, confirmation, page_backend

ENTRY = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


def no_upload(monkeypatch):
    monkeypatch.setattr("copilot.dashboard.import_panel.st.file_uploader", lambda *args, **kwargs: None)


def control(page, key):
    return next(item for group in (page.number_input, page.selectbox, page.multiselect) for item in group if item.key == key)


def open_generator(monkeypatch):
    no_upload(monkeypatch)
    page = AppTest.from_file(ENTRY, default_timeout=30).run()
    button(page, "生成测试成绩单").click().run()
    assert not page.exception
    return page


def generate(page, *, students=4, courses=3, scenario="normal"):
    control(page, "synthetic_classes").set_value(1).run()
    control(page, "synthetic_students").set_value(students).run()
    control(page, "synthetic_courses").set_value(courses).run()
    control(page, "synthetic_scenario").set_value(scenario).run()
    button(page, "生成成绩单").click().run()
    assert not page.exception, [item.message for item in page.exception]
    return page.session_state.synthetic_result


def confirm_generated(page):
    button(page, "使用这份数据进行核对").click().run()
    assert not page.exception and page.session_state.workflow.step == "review"
    confirmation(page).check().run()
    button(page, "确认数据并进入分析").click().run()
    assert not page.exception and page.session_state.workflow.step == "analysis"


def test_generator_is_secondary_and_generation_download_have_no_activation(page_backend, monkeypatch):
    store, client = page_backend
    before = store.overview()
    no_upload(monkeypatch)
    page = AppTest.from_file(ENTRY, default_timeout=30).run()
    assert not page.exception and not page.number_input and not page.selectbox and not page.multiselect
    button(page, "生成测试成绩单").click().run()
    result = generate(page)
    assert zipfile.is_zipfile(BytesIO(result.content))
    assert result.summary["counts"]["students"] == 4
    assert page.session_state.workflow.upload is None and page.session_state.workflow.preview is None
    assert page.session_state.workflow.confirmed is None
    assert store.overview() == before
    assert set(client.request_paths) == {"/health"}


def test_generator_works_offline_and_download_is_xlsx(monkeypatch):
    import copilot.dashboard.app as app
    import copilot.dashboard.synthetic_panel as panel

    class OfflineClient:
        def health(self):
            raise DashboardError("connection", "本机分析服务未开启，请启动后重试。")

    monkeypatch.setattr(app, "DashboardClient", OfflineClient)
    observed = []
    native_download = panel.st.download_button

    def record_download(label, data, **kwargs):
        observed.append((label, data, kwargs))
        return native_download(label, data, **kwargs)

    monkeypatch.setattr(panel.st, "download_button", record_download)
    page = open_generator(monkeypatch)
    result = generate(page)
    label, content, settings = observed[-1]
    assert label == "下载合成成绩单（XLSX）" and content == result.content
    assert settings["mime"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert settings["file_name"].endswith(".xlsx") and not settings["disabled"]
    assert page.session_state.workflow.step == "upload" and page.session_state.workflow.upload is None


def test_changed_options_disable_previous_file_until_regenerated(page_backend, monkeypatch):
    page = open_generator(monkeypatch)
    previous = generate(page)
    old_options = page.session_state.synthetic_generated_options
    control(page, "synthetic_students").set_value(5).run()
    assert page.session_state.synthetic_result == previous
    assert page.session_state.synthetic_generated_options == old_options
    assert button(page, "使用这份数据进行核对").disabled
    assert page.get("download_button")[0].proto.disabled
    assert any("设置已改变" in item.value for item in page.warning)
    button(page, "生成成绩单").click().run()
    assert not page.exception
    assert page.session_state.synthetic_result.summary["counts"]["students"] == 5
    assert not button(page, "使用这份数据进行核对").disabled


def test_next_batch_changes_seed_and_bytes_without_calling_import(page_backend, monkeypatch):
    store, client = page_backend
    page = open_generator(monkeypatch)
    previous = generate(page)
    before = store.overview()
    seed = page.session_state.synthetic_generated_options.seed
    button(page, "换一批数据").click().run()
    assert not page.exception
    assert page.session_state.synthetic_generated_options.seed == seed + 1
    assert control(page, "synthetic_seed").value == seed + 1
    assert page.session_state.synthetic_result.content != previous.content
    assert page.session_state.workflow.upload is None and store.overview() == before
    assert not any("/college/" in path for path in client.request_paths)


def test_explicit_use_requires_review_then_commit_and_reports_same_version(page_backend, monkeypatch):
    store, client = page_backend
    page = open_generator(monkeypatch)
    result = generate(page)
    before = store.overview()
    button(page, "使用这份数据进行核对").click().run()
    state = page.session_state.workflow
    assert not page.exception and state.step == "review" and state.upload.source == "example"
    assert state.preview.can_import and button(page, "确认数据并进入分析").disabled
    assert store.overview() == before
    confirmation(page).check().run()
    assert store.overview() == before
    button(page, "确认数据并进入分析").click().run()
    state = page.session_state.workflow
    assert not page.exception and state.has_current_report
    assert state.confirmed.imported_counts["students"] == result.summary["counts"]["students"] == 4
    assert state.report.summary.graded_count == result.summary["counts"]["graded"] == 12
    assert state.report.summary.sum_score_cents == result.summary["sum_score_cents"]
    assert state.report.dataset_version == state.confirmed.dataset_version == store.overview().dataset_version
    assert state.confirmed.source == "uploaded_local_only" and state.context.external_llm_allowed is False
    assert not any(path.startswith("/api/agent") for path in client.request_paths)


def test_edge_scenario_exposes_empty_course_and_unknown_status_before_commit(page_backend, monkeypatch):
    store, _ = page_backend
    page = open_generator(monkeypatch)
    result = generate(page, scenario="edge_cases")
    before = store.overview()
    button(page, "使用这份数据进行核对").click().run()
    preview = page.session_state.workflow.preview
    assert not page.exception and preview.can_import
    assert preview.counts["excluded_empty_courses"] == 1
    assert preview.issue_counts["unknown_status"] == 1
    assert preview.counts["graded"] == result.summary["counts"]["graded"]
    assert button(page, "确认数据并进入分析").disabled and store.overview() == before


def test_conflict_scenario_blocks_submission_without_overwrite(page_backend, monkeypatch):
    store, _ = page_backend
    page = open_generator(monkeypatch)
    generate(page, scenario="conflict")
    before = store.overview()
    button(page, "使用这份数据进行核对").click().run()
    preview = page.session_state.workflow.preview
    assert not page.exception and not preview.can_import
    assert preview.issue_counts["conflicting_student"] == 1
    assert button(page, "确认数据并进入分析").disabled
    assert store.overview() == before


def test_regeneration_preserves_confirmed_results_until_new_file_is_used_and_return_restores_settings(page_backend, monkeypatch):
    store, _ = page_backend
    page = open_generator(monkeypatch)
    original_file = generate(page, students=5, courses=4)
    confirm_generated(page)
    before = store.overview()
    previous_confirmation = page.session_state.workflow.confirmed
    previous_report = page.session_state.workflow.report
    previous_context = page.session_state.workflow.context
    page.session_state.workflow.draft = "STALE_SYNTHETIC_DRAFT"
    button(page, "1 上传").click().run()
    assert not page.exception and page.session_state.workflow.step == "upload"
    assert not any("default value" in item.value and "Session State" in item.value for item in page.warning)
    assert control(page, "synthetic_students").value == 5
    assert control(page, "synthetic_courses").value == 4
    button(page, "换一批数据").click().run()
    state = page.session_state.workflow
    assert not page.exception and state.confirmed == previous_confirmation
    assert state.report == previous_report and state.context == previous_context
    assert state.draft == "STALE_SYNTHETIC_DRAFT" and store.overview() == before
    button(page, "使用这份数据进行核对").click().run()
    state = page.session_state.workflow
    assert not page.exception and state.step == "review" and not state.confirmed_matches_upload
    assert state.confirmed == previous_confirmation
    assert state.report is state.context is state.draft is None and store.overview() == before
    assert page.session_state.college_upload_epoch == 2

    class StaleUpload:
        name = original_file.filename

        def getvalue(self):
            return original_file.content

    # A previous uploader value must not overwrite the newly selected batch
    # when returning to Upload after generated-data preview.
    monkeypatch.setattr("copilot.dashboard.import_panel.st.file_uploader",
                        lambda *args, **kwargs: StaleUpload() if kwargs["key"] == "college_upload_1" else None)
    expected_bytes = page.session_state.workflow.upload.content
    button(page, "1 上传").click().run()
    assert not page.exception and page.session_state.workflow.step == "upload"
    assert not any("default value" in item.value and "Session State" in item.value for item in page.warning)
    assert page.session_state.workflow.upload.source == "example"
    assert page.session_state.workflow.upload.content == expected_bytes != original_file.content
    assert control(page, "synthetic_students").value == 5 and control(page, "synthetic_courses").value == 4


def test_invalid_configuration_can_be_corrected_without_losing_previous_generated_file(page_backend, monkeypatch):
    page = open_generator(monkeypatch)
    previous = generate(page)
    control(page, "synthetic_majors").set_value([]).run()
    assert not page.exception and button(page, "生成成绩单").disabled
    assert button(page, "使用这份数据进行核对").disabled and page.get("download_button")[0].proto.disabled
    assert page.session_state.synthetic_result == previous
    control(page, "synthetic_majors").set_value(["software_engineering"]).run()
    control(page, "synthetic_year").set_value(2023).run()
    assert not page.exception and button(page, "生成成绩单").disabled
    assert any("学年不能早于入学年" in item.value for item in page.error)
    control(page, "synthetic_year").set_value(2025).run()
    assert not page.exception and not button(page, "生成成绩单").disabled
    assert page.session_state.synthetic_result == previous
