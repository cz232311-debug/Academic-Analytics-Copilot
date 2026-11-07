"""Workbench contract and display checks, with no network or paid model calls."""
import csv
from io import StringIO
import json

import httpx
import pytest

from copilot.dashboard.client import DashboardClient, DashboardError
from copilot.dashboard.presentation import course_table, mean_bands_table, rates_chart, rates_table, report_csv
from copilot.schemas import CourseReport, ReportScope


@pytest.fixture
def report():
    def statistics(course=None, *, graded=3, mean="73.33", passed=2):
        return {"dataset_version": "a" * 64, "scope": {"term": "2026-T1", "course_id": course},
                "enrollment_count": 4, "student_count": 4, "graded_count": graded,
                "missing_count": 1 if graded else 2, "exempt_count": 0 if graded else 2,
                "passed_count": passed, "failed_count": graded - passed, "failed_student_count": graded - passed,
                "sum_score_cents": 21999 if graded else 0, "weighted_sum_score_cents": 65997 if graded else 0,
                "graded_credits": 9 if graded else 0, "average_score": mean, "weighted_average_score": mean,
                "pass_rate_percent": "66.67" if graded else None,
                "distribution": [{"label": label, "lower_cents": low, "upper_cents": high,
                                  "upper_inclusive": inclusive, "count": count if graded else 0}
                                 for label, low, high, inclusive, count in (
                                     ("[0,60)", 0, 6000, False, 1), ("[60,70)", 6000, 7000, False, 1),
                                     ("[70,80)", 7000, 8000, False, 0), ("[80,90)", 8000, 9000, False, 0),
                                     ("[90,100]", 9000, 10000, True, 1))]}
    payload = {"dataset_version": "a" * 64, "generated_at": "2026-11-08T01:00:00+00:00",
               "scope": {"term": "2026-T1", "grade_level": 1}, "summary": statistics(),
               "rows": [{"course_id": "G1-C01", "course_name": "Mathematics", "course_type": "general_required",
                         "statistics": statistics("G1-C01"), "fail_rate_percent": "33.33", "excellent_count": 1,
                         "excellent_rate_percent": "33.33", "average_band": "[70,80)"},
                        {"course_id": "G1-C02", "course_name": "Programming", "course_type": "professional_required",
                         "statistics": statistics("G1-C02", graded=0, mean=None, passed=0), "fail_rate_percent": None,
                         "excellent_count": 0, "excellent_rate_percent": None, "average_band": None}],
               "mean_bands": [{"label": label, "course_ids": ["G1-C01"] if label == "[70,80)" else []}
                              for label in ("[90,100]", "[80,90)", "[70,80)", "[60,70)", "[0,60)")],
               "policy": {"score_bins": ["[0,60)", "[60,70)", "[70,80)", "[80,90)", "[90,100]"]}}
    payload["summary"].update(enrollment_count=8, missing_count=3, exempt_count=2)
    return CourseReport.model_validate_json(json.dumps(payload))


def test_table_chart_and_csv_share_backend_rates_and_version(report):
    table, chart_data = course_table(report), rates_table(report)
    assert table.iloc[0]["Pass rate"] == "66.67%"
    assert table.iloc[0]["Fail rate"] == "33.33%"
    assert table.iloc[0]["Excellent ≥80"] == "33.33%"
    assert chart_data.iloc[0]["Rate"] == 66.67 and chart_data.iloc[1]["Rate"] == 33.33
    assert chart_data.iloc[0]["Graded n"] == 3
    rows = list(csv.DictReader(StringIO(report_csv(report).decode("utf-8-sig"))))
    assert rows[0]["Pass rate"] == table.iloc[0]["Pass rate"]
    assert rows[0]["Fail rate"] == table.iloc[0]["Fail rate"]
    assert rows[0]["Dataset version"] == report.dataset_version
    assert rows[0]["Generated at (UTC)"] == report.generated_at
    assert "Grade 1" in rows[0]["Scope"]


def test_unavailable_rates_and_mean_bands_are_not_zero(report):
    table = course_table(report)
    assert table.iloc[1]["Average"] == table.iloc[1]["Pass rate"] == table.iloc[1]["Fail rate"] == "—"
    assert rates_table(report).iloc[2:]["Rate"].isna().all()
    bands = mean_bands_table(report)
    assert "Mathematics" in bands.iloc[2]["General required"]
    assert bands.iloc[-1]["Course mean band"] == "No graded records"
    assert "Programming" in bands.iloc[-1]["Professional required"]


def test_chart_contract_uses_fixed_percentage_axis_and_sample_tooltip(report):
    spec = rates_chart(report).to_dict()
    encoding = spec["layer"][0]["encoding"]
    assert encoding["x"]["scale"]["domain"] == [0, 100]
    assert any(item["field"] == "Graded n" for item in encoding["tooltip"])
    assert encoding["y"]["axis"]["labelLimit"] >= 300


def test_client_validates_report_and_sends_typed_filters(report):
    def respond(request):
        assert request.url.path == "/api/reports/courses"
        assert request.url.params["grade_level"] == "1"
        assert request.url.params["major"] == "computer_science"
        assert request.url.params["expected_version"] == "a" * 64
        assert "class_id" not in request.url.params
        return httpx.Response(200, content=report.model_dump_json(), headers={"Content-Type": "application/json"})
    client = DashboardClient("http://127.0.0.1:8000", transport=httpx.MockTransport(respond))
    assert client.report(ReportScope(grade_level=1, major="computer_science"), expected_version="a" * 64) == report


@pytest.mark.parametrize("exception,code", [(httpx.ConnectError("private connection data"), "connection"),
                                             (httpx.ReadTimeout("private timeout data"), "timeout")])
def test_client_transport_failures_have_actionable_safe_messages(exception, code):
    def respond(request):
        raise exception
    with pytest.raises(DashboardError) as error:
        DashboardClient(transport=httpx.MockTransport(respond)).dataset()
    assert error.value.code == code
    assert "private" not in str(error.value)


@pytest.mark.parametrize("code,status", [("unknown_entity", 404), ("dataset_changed", 409),
                                          ("question_privacy_block", 422), ("agent_privacy_block", 403),
                                          ("invalid_model_response", 502)])
def test_client_safe_api_errors_preserve_trace_id(code, status):
    client = DashboardClient(transport=httpx.MockTransport(lambda request: httpx.Response(status,
        json={"error": {"code": code, "message": "private body must not be echoed"}, "request_id": "safe-request-1"})))
    with pytest.raises(DashboardError) as error:
        client.query("Average score for G1-C01")
    assert error.value.code == code and error.value.request_id == "safe-request-1"
    assert "private" not in str(error.value)


@pytest.mark.parametrize("body", [b"not JSON", b"{}", b'{"dataset_version": 123}'])
def test_client_rejects_success_with_invalid_response_contract(body):
    with pytest.raises(DashboardError, match="expected contract"):
        DashboardClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))).dataset()


def test_client_query_only_sends_question_field():
    def respond(request):
        assert request.url.path == "/api/agent/query"
        assert request.extensions["timeout"]["read"] > 120
        assert json.loads(request.content) == {"question": "Average score for G1-C01"}
        return httpx.Response(422, json={"error": {"code": "question_privacy_block"}})
    with pytest.raises(DashboardError):
        DashboardClient(transport=httpx.MockTransport(respond)).query("Average score for G1-C01")


def test_export_sanitizes_spreadsheet_formula_text(report):
    row = report.rows[0].model_copy(update={"course_name": "=1+1"})
    changed = report.model_copy(update={"rows": [row]})
    rows = list(csv.DictReader(StringIO(report_csv(changed).decode("utf-8-sig"))))
    assert rows[0]["Course"] == "'=1+1"


def test_empty_csv_keeps_header_and_chart_data_has_contract(report):
    empty = report.model_copy(update={"rows": []})
    assert "Dataset version" in report_csv(empty).decode("utf-8-sig")
    assert rates_table(empty).empty


@pytest.mark.parametrize("url", ["file:///private", "https://user:secret@example.com", "not-a-url", "http://localhost/?key=secret"])
def test_client_disallows_credentials_and_invalid_service_urls(url):
    with pytest.raises(DashboardError, match="without credentials"):
        DashboardClient(url)


def test_streamlit_api_failure_is_actionable_and_does_not_crash(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import copilot.dashboard.app as dashboard_app

    class OfflineClient:
        def dataset(self):
            raise DashboardError("connection", "Cannot reach FastAPI. Start the backend on port 8000 and keep its terminal open.")

    monkeypatch.setattr(dashboard_app, "DashboardClient", OfflineClient)
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run()
    assert not page.exception
    assert any("Start the backend" in item.value for item in page.error)
    assert len(page.code) == 1


def test_streamlit_live_submit_requires_authorization_and_displays_evidence(monkeypatch, report):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import copilot.dashboard.app as dashboard_app
    from copilot.agent.models import AgentResponse
    from copilot.dashboard.client import AgentStatus
    from copilot.schemas import DatasetInfo, ReportingCatalog

    class FakeClient:
        base_url = "http://127.0.0.1:8000"
        calls = 0

        def dataset(self):
            return DatasetInfo(dataset_version=report.dataset_version, schema_version="1", source="generated", seed=20261103,
                               external_llm_allowed=True, counts={"students": 4, "classes": 1, "courses": 2,
                               "enrollments": 8, "grade_records": 8}, students_by_grade={"1": 4}, terms=["2026-T1"],
                               minimum_courses_per_student=2, maximum_courses_per_student=2)

        def catalog(self):
            return ReportingCatalog.model_validate_json(json.dumps({"dataset_version": report.dataset_version,
                "classes": [{"class_id": "C001", "grade_level": 1, "class_label": "Synthetic Class 1", "major": "computer_science"}],
                "courses": [{"course_id": row.course_id, "course_name": row.course_name, "course_type": row.course_type,
                             "grade_level": 1, "credits": 3} for row in report.rows],
                "terms": ["2026-T1"], "majors": ["computer_science"], "course_types": ["general_required", "professional_required"]}))

        def report(self, scope, *, expected_version=None):
            assert expected_version == report.dataset_version
            return report

        def agent_status(self):
            return AgentStatus(provider="deepseek", model="deepseek-flash", simulated=False,
                               tools=["get_score_summary"], max_model_turns=4, max_tool_calls=6,
                               request_timeout_seconds=45.0, question_policy="restricted_english_aggregate_queries", streaming=False)

        def query(self, question):
            self.calls += 1
            return AgentResponse.model_validate_json(json.dumps({"request_id": "unit-test-only", "provider": "deepseek",
                "model": "deepseek-flash", "simulated": False, "dataset_version": report.dataset_version, "status": "completed",
                "answer": "The average score is 73.33 points. [r1]", "model_turns": 2, "tool_calls": [],
                "evidence": [{"evidence_id": "r1", "tool": "get_score_summary", "dataset_version": report.dataset_version,
                              "arguments": {"course_id": "G1-C01", "term": "2026-T1"}, "data": report.rows[0].statistics.model_dump(mode="json")}],
                "usage": None, "answer_mode": "grounded_narrative", "fallback_reason": None}))

    fake = FakeClient()
    monkeypatch.setattr(dashboard_app, "DashboardClient", lambda: fake)
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run()
    assert not page.exception and fake.calls == 0
    next(item for item in page.button if item.label == "Ask Copilot").click().run()
    assert not page.exception and fake.calls == 0
    assert any("Confirm the external API call" in item.value for item in page.info)
    page.checkbox[0].check()
    next(item for item in page.button if item.label == "Ask Copilot").click().run()
    assert not page.exception and fake.calls == 1
    assert any("73.33 points" in item.value for item in page.markdown)
    assert any("Statistical evidence" in item.label for item in page.expander)
    assert len(page.dataframe) == 2
