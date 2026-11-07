"""Actual local HTTP + Streamlit rendering smoke check with an offline Mock Agent.

Starts isolated processes on unused loopback ports and a temporary synthetic DB.
No credentials are inherited and no external LLM endpoint is contacted.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import csv
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
RESULT_PATH = ROOT / "docs/development/results/phase2b-http-smoke.json"


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop_process(process):
    """Stop only the exact child process created by this check."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wait_ready(process, url, log_path):
    deadline = time.monotonic() + 45
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Local server exited; see {log_path.name}")
            try:
                response = client.get(url)
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.15)
    raise RuntimeError(f"Local server startup timed out; see {log_path.name}")


@contextmanager
def local_services(*, dashboard=False):
    """The child environment is deliberately restricted to offline test settings."""
    audit = ROOT / ".audit"
    audit.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dashboard-http-", dir=audit) as directory:
        isolated_home = Path(directory) / "streamlit-home"
        isolated_home.mkdir()
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP")
               if key in os.environ}
        env.update(ACADEMIC_DB_PATH=str(Path(directory) / "demo.sqlite3"),
                   COPILOT_LLM_PROVIDER="mock", OPENBLAS_NUM_THREADS="1",
                   STREAMLIT_BROWSER_GATHER_USAGE_STATS="false",
                   USERPROFILE=str(isolated_home), HOME=str(isolated_home))
        api_port = unused_port()
        api_url = f"http://127.0.0.1:{api_port}"
        env["COPILOT_API_BASE_URL"] = api_url
        api_log_path = audit / f"dashboard-api-{api_port}.log"
        dashboard_log_path = audit / f"dashboard-ui-{api_port}.log"
        processes = []
        with api_log_path.open("w", encoding="utf-8") as api_log, dashboard_log_path.open("w", encoding="utf-8") as ui_log:
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "copilot.api:app", "--host", "127.0.0.1", "--port", str(api_port)],
                    cwd=ROOT, env=env, stdout=api_log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                processes.append(process)
                wait_ready(process, api_url + "/health", api_log_path)
                ui_url = None
                if dashboard:
                    ui_port = unused_port()
                    ui_url = f"http://127.0.0.1:{ui_port}"
                    ui_process = subprocess.Popen(
                        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.headless=true",
                         "--server.address=127.0.0.1", f"--server.port={ui_port}", "--browser.gatherUsageStats=false"],
                        cwd=ROOT, env=env, stdout=ui_log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    processes.append(ui_process)
                    wait_ready(ui_process, ui_url + "/_stcore/health", dashboard_log_path)
                yield {"api_url": api_url, "dashboard_url": ui_url}
            finally:
                for child in reversed(processes):
                    stop_process(child)


@contextmanager
def dashboard_environment(api_url):
    previous = os.environ.get("COPILOT_API_BASE_URL")
    os.environ["COPILOT_API_BASE_URL"] = api_url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("COPILOT_API_BASE_URL", None)
        else:
            os.environ["COPILOT_API_BASE_URL"] = previous


def assert_report_invariants(report):
    for row in report.rows:
        stats = row.statistics
        assert sum(item.count for item in stats.distribution) == stats.graded_count
        assert stats.passed_count + stats.failed_count == stats.graded_count
        assert stats.enrollment_count == stats.graded_count + stats.missing_count + stats.exempt_count
        assert stats.dataset_version == report.dataset_version
    assert sum(row.statistics.graded_count for row in report.rows) == report.summary.graded_count
    classified = [course_id for band in report.mean_bands for course_id in band.course_ids]
    expected = [row.course_id for row in report.rows if row.statistics.graded_count]
    assert sorted(classified) == sorted(expected)


def check_rendered_dashboard(api_url):
    """AppTest executes page Python against real TCP APIs, including form submits."""
    from streamlit.testing.v1 import AppTest

    with dashboard_environment(api_url):
        page = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=40).run()
        assert not page.exception, [error.message for error in page.exception]
        assert len(page.dataframe) >= 2
        assert len(page.metric) >= 3
        grade = next(item for item in page.selectbox if item.label == "Grade")
        grade.set_value(1)
        next(button for button in page.button if button.label == "Load analysis").click()
        page.run()
        assert not page.exception, [error.message for error in page.exception]
        next(button for button in page.button if button.label == "Ask Copilot").click()
        page.run()
        assert not page.exception, [error.message for error in page.exception]
        markdown = "\n".join(item.value for item in page.markdown)
        assert "69.73" in markdown and "67.80" in markdown
        assert any("evidence" in expander.label.lower() for expander in page.expander)
        assert page.session_state.course_report.scope.grade_level == 1
        assert page.session_state.agent_response.evidence[0].evidence_id == "r1"
        return {"rendered_tables": len(page.dataframe), "rendered_metrics": len(page.metric),
                "agent_answer_contains": ["69.73", "67.80"], "filter_submitted": "Grade 1"}


def check_unavailable_dashboard():
    from streamlit.testing.v1 import AppTest

    with dashboard_environment(f"http://127.0.0.1:{unused_port()}"):
        page = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30).run()
        assert not page.exception
        assert page.error, "Unavailable API must produce a visible page error"


def run_smoke():
    from copilot.dashboard.client import DashboardClient
    from copilot.dashboard.presentation import course_table, mean_bands_table, rates_chart, rates_table, report_csv
    from copilot.schemas import ReportScope

    checks = []
    samples = {}
    with local_services(dashboard=True) as urls:
        api = DashboardClient(urls["api_url"])
        info = api.dataset()
        catalog = api.catalog()
        assert info.counts["students"] == 1382 and info.counts["classes"] == 40 and info.counts["courses"] == 96
        assert info.dataset_version == catalog.dataset_version
        checks.append({"check": "dashboard_client_to_real_uvicorn", "passed": True})
        report = api.report(ReportScope(term="2026-T1"), expected_version=catalog.dataset_version)
        assert_report_invariants(report)
        assert len(report.rows) == 96
        checks.append({"check": "all_courses_one_snapshot_and_count_invariants", "passed": True, "courses": len(report.rows)})
        filters = [
            {"grade_level": 1}, {"class_id": "C001"}, {"course_id": "G1-C01"},
            {"major": "computer_science"}, {"course_type": "professional_required"},
            {"grade_level": 1, "major": "computer_science", "course_type": "professional_required"},
        ]
        for values in filters:
            filtered = api.report(ReportScope(term="2026-T1", **values), expected_version=report.dataset_version)
            assert_report_invariants(filtered)
            assert filtered.summary.graded_count > 0
            checks.append({"check": "filtered_report", "filters": values, "passed": True,
                           "courses": len(filtered.rows), "graded_count": filtered.summary.graded_count})
        single = api.report(ReportScope(term="2026-T1", course_id="G1-C01"), expected_version=report.dataset_version)
        assert single.summary.average_score == report.rows[0].statistics.average_score
        assert str(single.summary.average_score) == "69.73"
        assert str(single.summary.pass_rate_percent) == "67.80"
        samples["course_analysis"] = single.rows[0].model_dump(mode="json")
        samples["course_mean_bands"] = [band.model_dump(mode="json") for band in report.mean_bands]
        table, chart_rows = course_table(report), rates_table(report)
        downloaded = list(csv.DictReader(io.StringIO(report_csv(report).decode("utf-8-sig"))))
        assert len(downloaded) == len(table) == len(report.rows)
        chart_spec = rates_chart(report).to_dict()
        plotted = chart_spec["datasets"][chart_spec["data"]["name"]]
        assert plotted == chart_rows.to_dict(orient="records")
        for row, exported in zip(report.rows, downloaded, strict=True):
            stats = row.statistics
            plotted_row = chart_rows[chart_rows["Course ID"] == row.course_id]
            pass_rate = plotted_row[plotted_row["Series"] == "Pass rate"].iloc[0]
            fail_rate = plotted_row[plotted_row["Series"] == "Fail rate"].iloc[0]
            assert Decimal(str(pass_rate["Rate"])) == stats.pass_rate_percent
            assert Decimal(str(fail_rate["Rate"])) == row.fail_rate_percent
            assert exported["Pass rate"] == pass_rate["Rate label"]
            assert exported["Fail rate"] == fail_rate["Rate label"]
            assert exported["Dataset version"] == report.dataset_version
        assert len(mean_bands_table(report)) == 5
        samples["pass_fail_rates"] = chart_rows.head(6).to_dict(orient="records")
        checks.append({"check": "table_chart_csv_use_identical_backend_rates_and_version", "passed": True})
        empty = api.report(ReportScope(term="2026-T1", grade_level=1, course_id="G2-C01"))
        assert not empty.rows and empty.summary.graded_count == 0
        checks.append({"check": "empty_filter_is_not_zero_mean", "passed": True})
        agent_status = api.agent_status()
        assert agent_status.simulated is True
        answer = api.query("What is the average score and pass rate for G1-C01?")
        assert answer.simulated and answer.model_turns == 2
        assert answer.dataset_version == report.dataset_version
        assert "69.73" in answer.answer and "67.80" in answer.answer
        assert answer.evidence[0].data["average_score"] == "69.73"
        samples["agent"] = {"simulated": answer.simulated, "answer": answer.answer,
                            "answer_mode": answer.answer_mode, "request_id": answer.request_id,
                            "evidence_ids": [item.evidence_id for item in answer.evidence]}
        checks.append({"check": "mock_agent_over_real_http_with_evidence", "passed": True})
        with httpx.Client(timeout=10, trust_env=False) as http:
            assert http.get(urls["api_url"] + "/api/reports/courses", params={"grade_level": 4}).status_code == 422
            assert http.get(urls["api_url"] + "/api/reports/courses", params={"expected_version": "0" * 64}).status_code == 409
            assert http.get(urls["dashboard_url"] + "/_stcore/health").status_code == 200
            assert http.get(urls["dashboard_url"]).status_code == 200
        checks.append({"check": "streamlit_server_and_api_error_statuses", "passed": True})
        samples["rendered_page"] = check_rendered_dashboard(urls["api_url"])
        checks.append({"check": "actual_streamlit_page_filters_and_agent_form_over_tcp", "passed": True})
        check_unavailable_dashboard()
        checks.append({"check": "unavailable_api_visible_error_without_page_exception", "passed": True})
    with httpx.Client(timeout=2, trust_env=False) as http:
        for url in urls.values():
            try:
                http.get(url)
            except httpx.TransportError:
                continue
            raise AssertionError("A smoke-test service remained reachable after cleanup")
    checks.append({"check": "own_servers_unreachable_after_cleanup", "passed": True})
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "transport": "Dashboard HTTP client and Streamlit AppTest -> actual TCP -> isolated Uvicorn -> SQLite/Pandas",
            "llm": "offline mock provider only; no paid API requests", "passed": True,
            "dataset_version": report.dataset_version, "checks": checks, "samples": samples,
            "servers_stopped": True}


def main():
    result = run_smoke()
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
