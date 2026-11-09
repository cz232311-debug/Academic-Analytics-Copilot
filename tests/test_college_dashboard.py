"""Upload/preview/commit UI against the actual API with synthetic workbooks."""
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest
from streamlit.testing.v1 import AppTest

from copilot.api import create_app
from copilot.dashboard.client import DashboardClient
from copilot.storage import Database
from tests.college_fixture import ROWS, college_workbook


@pytest.fixture
def page_backend(tmp_path, tiny_dataset, monkeypatch):
    store = Database(tmp_path / "page.sqlite3")
    store.initialize()
    store.replace(tiny_dataset, source="generated", seed=1)
    monkeypatch.setenv("COPILOT_LLM_PROVIDER", "mock")
    with TestClient(create_app(store.path, bootstrap_demo=False)) as api:
        def transport(request):
            response = api.request(request.method, str(request.url), content=request.content, headers=request.headers)
            return httpx.Response(response.status_code, content=response.content, headers=response.headers)
        client = DashboardClient("http://testserver", transport=httpx.MockTransport(transport))
        monkeypatch.setattr("copilot.dashboard.app.DashboardClient", lambda: client)
        yield store, client


def uploaded_page(monkeypatch, content):
    class Upload:
        name = "synthetic-college.xlsx"

        def getvalue(self):
            return content

    monkeypatch.setattr("copilot.dashboard.import_panel.st.file_uploader",
                        lambda *args, **kwargs: Upload() if kwargs["key"] == "college_upload_0" else None)
    return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30).run()


def button(page, label):
    return next(item for item in page.button if item.label == label)


def confirmation(page):
    return next(item for item in page.checkbox if item.label.startswith("我已核对预览"))


def test_upload_and_checking_confirmation_do_not_commit_until_button(page_backend, monkeypatch):
    store, _ = page_backend
    before = store.overview()
    page = uploaded_page(monkeypatch, college_workbook())
    assert not page.exception
    assert page.session_state.college_preview.can_import
    assert store.overview() == before
    assert button(page, "确认导入到当前数据库").disabled
    confirmation(page).check().run()
    assert not page.exception and not button(page, "确认导入到当前数据库").disabled
    assert store.overview() == before


def test_confirmed_upload_refreshes_report_filters_charts_and_disables_agent(page_backend, monkeypatch):
    store, _ = page_backend
    before = store.overview()
    page = uploaded_page(monkeypatch, college_workbook())
    confirmation(page).check().run()
    button(page, "确认导入到当前数据库").click().run()
    assert not page.exception, [item.message for item in page.exception]
    after = store.overview()
    assert after.dataset_version != before.dataset_version
    assert after.source == "uploaded_local_only"
    report = page.session_state.course_report
    assert report.dataset_version == after.dataset_version and report.scope.term == "2025-T1"
    assert report.summary.graded_count == 3 and report.summary.enrollment_count == 5
    assert len(report.rows) == 2 and len(page.dataframe) == 2
    grade = next(item for item in page.selectbox if item.label == "Grade")
    assert "2024级" in grade.options
    grade.set_value(2024)
    button(page, "Load analysis").click().run()
    assert not page.exception and page.session_state.course_report.scope.grade_level == 2024
    assert any("2024级" in item.value for item in page.caption)
    assert button(page, "Ask Copilot").disabled
    assert any("导入已提交" in item.value for item in page.success)
    with pytest.raises(KeyError):
        page.session_state["college_preview"]


def test_duplicate_policy_requires_new_preview_and_new_confirmation(page_backend, monkeypatch):
    store, _ = page_backend
    conflicting = list(ROWS[0])
    conflicting[2] = "23软工6班"
    content = college_workbook(rows=[*ROWS, conflicting])
    before = store.overview()
    page = uploaded_page(monkeypatch, content)
    assert not page.exception and not page.session_state.college_preview.can_import
    assert all(item.label != "确认导入到当前数据库" for item in page.button)
    policy = next(item for item in page.selectbox if item.label.startswith("重复学号"))
    policy.set_value("first").run()
    assert not page.exception and page.session_state.college_preview.can_import
    assert page.session_state.college_preview.duplicate_policy == "first"
    confirmation(page).check().run()
    policy = next(item for item in page.selectbox if item.label.startswith("重复学号"))
    policy.set_value("last").run()
    assert not page.exception and page.session_state.college_preview.duplicate_policy == "last"
    assert not confirmation(page).value and button(page, "确认导入到当前数据库").disabled
    assert store.overview() == before


def test_local_client_uses_only_multipart_http_for_preview_and_commit(page_backend):
    store, client = page_backend
    content = college_workbook()
    preview = client.college_preview("synthetic.xlsx", content)
    assert store.overview().source == "generated"
    result = client.college_commit("synthetic.xlsx", content, preview_token=preview.preview_token)
    assert result.dataset.source == "uploaded_local_only"
    assert result.imported_counts["enrollments"] == 5
