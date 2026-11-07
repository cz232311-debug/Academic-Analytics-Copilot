"""Integration tests use actual loopback TCP, with an isolated offline backend."""
import httpx
import pytest

from copilot.dashboard.client import DashboardClient
from copilot.schemas import ReportScope
from scripts.smoke_dashboard import (assert_report_invariants, check_rendered_dashboard,
                                     check_unavailable_dashboard, local_services)


@pytest.fixture(scope="module")
def live_backend():
    # No existing service/database is reused, and no LLM API key is inherited.
    with local_services() as services:
        yield services["api_url"]


def test_http_dashboard_client_gets_actual_seeded_dataset(live_backend):
    client = DashboardClient(live_backend)
    info = client.dataset()
    catalog = client.catalog()
    assert info.counts["students"] == 1382
    assert info.counts["classes"] == 40 and info.counts["courses"] == 96
    assert info.dataset_version == catalog.dataset_version
    assert client.agent_status().simulated is True


@pytest.mark.parametrize("filters", [
    {}, {"grade_level": 1}, {"class_id": "C001"}, {"major": "computer_science"},
    {"course_type": "professional_required"},
    {"grade_level": 1, "major": "computer_science", "course_type": "professional_required"},
])
def test_actual_http_filtered_course_results_balance(live_backend, filters):
    client = DashboardClient(live_backend)
    report = client.report(ReportScope(term="2026-T1", **filters))
    assert_report_invariants(report)
    assert report.summary.graded_count > 0
    if filters.get("course_type"):
        assert {row.course_type for row in report.rows} == {filters["course_type"]}


def test_actual_http_known_course_and_mock_agent_share_evidence(live_backend):
    client = DashboardClient(live_backend)
    report = client.report(ReportScope(course_id="G1-C01"))
    answer = client.query("What is the average score and pass rate for G1-C01?")
    assert answer.simulated is True and answer.model_turns == 2
    assert answer.dataset_version == report.dataset_version
    assert (report.summary.graded_count, report.summary.passed_count, report.summary.failed_count) == (469, 318, 151)
    assert str(report.summary.average_score) == "69.73"
    assert answer.evidence[0].data["average_score"] == "69.73"
    assert "69.73" in answer.answer and "67.80" in answer.answer


def test_actual_http_empty_scope_and_invalid_version(live_backend):
    report = DashboardClient(live_backend).report(ReportScope(grade_level=1, course_id="G2-C01"))
    assert report.rows == [] and report.summary.graded_count == 0
    assert report.summary.average_score is None
    with httpx.Client(base_url=live_backend, trust_env=False, timeout=10) as client:
        assert client.get("/api/reports/courses", params={"grade_level": 4}).status_code == 422
        assert client.get("/api/reports/courses", params={"course_id": "G1-C99"}).status_code == 404
        changed = client.get("/api/reports/courses", params={"expected_version": "0" * 64})
        assert changed.status_code == 409 and changed.json()["error"]["code"] == "dataset_changed"


def test_actual_streamlit_page_filters_and_agent_form_use_http(live_backend):
    rendered = check_rendered_dashboard(live_backend)
    assert rendered["rendered_tables"] >= 2 and rendered["rendered_metrics"] >= 3


def test_actual_streamlit_page_shows_backend_unavailable_error():
    check_unavailable_dashboard()
