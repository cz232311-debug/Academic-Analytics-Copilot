"""Aggregate audit downloads reuse the current report and exclude private data."""
import csv
import hashlib
from io import BytesIO, StringIO
import json
from zipfile import ZipFile

import pytest

from copilot.dashboard.audit_export import build_analysis_archive
from copilot.dashboard.workflow import WorkflowError, WorkflowState
from copilot.data.college import ImportIssue
from copilot.schemas import ReportScope
from tests.test_workflow_state import confirmed_state, dataset_info, report


def unpack(state):
    with ZipFile(BytesIO(build_analysis_archive(state))) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def read_csv(value):
    return list(csv.DictReader(StringIO(value.decode("utf-8-sig"))))


def test_archive_has_documented_files_and_one_scope_version_for_all_views():
    state = confirmed_state()
    state.set_scope(ReportScope(term="2025-T1", major="software_engineering", course_type="professional_required"))
    state.accept_report(report(state.scope))
    files = unpack(state)
    assert set(files) == {"README.md", "report/scope.json", "report/statistics.json", "report/metrics.json",
                          "tables/courses.csv", "charts/rates.csv", "charts/mean_bands.csv",
                          "cleaning/summary.json", "cleaning/issues.csv", "verification.json"}
    assert not any(name.startswith("/") or ".." in name for name in files)
    scope = json.loads(files["report/scope.json"])
    statistics = json.loads(files["report/statistics.json"])
    verification = json.loads(files["verification.json"])
    assert scope["dataset_version"] == statistics["dataset_version"] == state.confirmed.dataset_version
    assert scope["scope"] == statistics["scope"] == verification["scope"] == state.scope.model_dump(mode="json")
    assert statistics == state.report.model_dump(mode="json")
    courses = read_csv(files["tables/courses.csv"])
    rates = read_csv(files["charts/rates.csv"])
    bands = read_csv(files["charts/mean_bands.csv"])
    assert courses[0]["Average"] == "50.83" and courses[0]["Pass rate"] == "66.67%"
    assert courses[0]["Dataset version"] == scope["dataset_version"]
    assert [row["Rate label"] for row in rates] == ["66.67%", "33.33%"]
    assert [row["Records"] for row in rates] == ["2", "1"]
    assert rates[0]["Graded n"] == courses[0]["Graded n"] == "3"
    assert "50.83" in bands[0]["Professional required"]
    assert json.loads(files["report/metrics.json"])["policy"] == statistics["policy"]
    assert all(verification["checks"].values()) and verification["external_llm_allowed"] is False
    for name, digest in verification["file_sha256"].items():
        assert hashlib.sha256(files[name]).hexdigest() == digest


def test_archive_excludes_raw_upload_preview_identifiers_messages_and_traces(monkeypatch):
    monkeypatch.setenv("COPILOT_LLM_API_KEY", "SECRET_API_KEY_SENTINEL")
    state = confirmed_state()
    issue = ImportIssue(code="unknown_status", row=9, column="F", severity="warning", message="SECRET_NAME_AND_SCORE")
    state.preview = state.preview.model_copy(update={
        "issues": [issue, issue.model_copy(update={"code": "SECRET_CODE", "column": "SECRET_COLUMN", "message": "SECRET_CELL"})],
        "issue_counts": {"unknown_status": 1, "SECRET_COUNT_KEY": 1},
        "counts": {**state.preview.counts, "SECRET_COUNT_KEY": 7, "issues_omitted": 3},
        "assumptions": ["SECRET_ASSUMPTION"],
    })
    state.confirmed = state.confirmed.model_copy(update={"archive_id": "SECRET_ARCHIVE_ID"})
    state.draft = "SECRET_DRAFT"
    state.history.append({"trace": "SECRET_TRACE", "request_id": "SECRET_REQUEST_ID"})
    files = unpack(state)
    text = "\n".join(content.decode("utf-8-sig") for content in files.values())
    for secret in ("PRIVATE_FILE_NAME", "PRIVATE_WORKBOOK_BYTES", "PRIVATE_STUDENT", "PRIVATE_ID", "PRIVATE_PREVIEW_TOKEN",
                   state.confirmed.file_hash, "SECRET_", "87.65"):
        assert secret not in text
    issues = read_csv(files["cleaning/issues.csv"])
    assert issues[0]["code"] == "unknown_status" and issues[0]["row"] == "9" and issues[0]["column"] == "F"
    assert issues[1]["code"] == "unclassified_issue" and issues[1]["column"] == ""
    summary = json.loads(files["cleaning/summary.json"])
    assert summary["coverage"] == "confirmed_import_not_filtered_report"
    assert summary["issue_counts"] == {"unknown_status": 1, "unclassified_issue": 1}
    assert summary["issue_details_omitted"] == 3
    assert summary["source_cell_values_included"] is False
    state.preview = state.preview.model_copy(update={"counts": {**state.preview.counts, "issues_omitted": "SECRET_OMITTED"}})
    safe_files = unpack(state)
    assert b"SECRET_OMITTED" not in b"\n".join(safe_files.values())
    safe_summary = json.loads(safe_files["cleaning/summary.json"])
    assert safe_summary["issue_details_omitted"] == 0 and "issues_omitted" not in safe_summary["counts"]


@pytest.mark.parametrize("transition", [
    lambda state: state.select_upload("new.xlsx", b"new source"),
    lambda state: state.set_scope(ReportScope(term="2025-T1", class_id="C001")),
    lambda state: state.observe_dataset(dataset_info("b" * 64)),
    lambda state: state.observe_dataset(dataset_info(source="generated")),
    lambda state: state.invalidate_analysis(),
])
def test_stale_data_or_scope_cannot_export_old_analysis(transition):
    state = confirmed_state()
    transition(state)
    with pytest.raises(WorkflowError, match="更新当前范围"):
        build_analysis_archive(state)


def test_unconfirmed_preview_cannot_export():
    state = WorkflowState()
    with pytest.raises(WorkflowError):
        build_analysis_archive(state)


def test_mixed_evidence_is_rejected_even_if_a_report_object_is_present():
    state = confirmed_state()
    state.report = state.report.model_copy(update={"summary": state.report.summary.model_copy(update={"dataset_version": "b" * 64})})
    with pytest.raises(WorkflowError):
        build_analysis_archive(state)


def test_formula_like_course_names_are_exported_as_text_in_each_csv():
    state = confirmed_state()
    dangerous = "=HYPERLINK(\"https://example.invalid\",\"合成课\")"
    state.report = state.report.model_copy(update={"rows": [state.report.rows[0].model_copy(update={"course_name": dangerous})]})
    files = unpack(state)
    assert read_csv(files["tables/courses.csv"])[0]["Course"] == "'" + dangerous
    # Chart rate rows begin with a typed course ID, so the formula text cannot
    # become the cell's formula; the mean-band text starts with the course name.
    assert read_csv(files["charts/rates.csv"])[0]["Course"].startswith(state.report.rows[0].course_id + " · =")
    assert read_csv(files["charts/mean_bands.csv"])[0]["Professional required"].startswith("'=")


def test_valid_empty_scope_exports_nulls_and_headers_instead_of_old_rows():
    state = confirmed_state()
    empty = state.report.summary.model_copy(update={"enrollment_count": 0, "student_count": 0, "graded_count": 0,
        "missing_count": 0, "exempt_count": 0, "passed_count": 0, "failed_count": 0, "failed_student_count": 0,
        "sum_score_cents": 0, "weighted_sum_score_cents": 0, "graded_credits": 0, "average_score": None,
        "weighted_average_score": None, "pass_rate_percent": None,
        "distribution": [bucket.model_copy(update={"count": 0}) for bucket in state.report.summary.distribution]})
    state.report = state.report.model_copy(update={"summary": empty, "rows": [], "mean_bands": []})
    files = unpack(state)
    exported = json.loads(files["report/statistics.json"])
    assert exported["summary"]["average_score"] is None and exported["rows"] == []
    assert read_csv(files["tables/courses.csv"]) == []
    assert read_csv(files["charts/rates.csv"]) == []
    assert b"Dataset version" in files["tables/courses.csv"]
    assert b"Rate label" in files["charts/rates.csv"]
    assert b"Course mean band" in files["charts/mean_bands.csv"]


def test_course_without_graded_scores_exports_empty_numeric_rates_and_keeps_null_labels():
    state = confirmed_state()
    original = state.report
    course_id = "I" + "2" * 24
    no_scores = original.rows[0].statistics.model_copy(update={
        "scope": original.scope.base_scope().model_copy(update={"course_id": course_id}),
        "enrollment_count": 2, "student_count": 2, "graded_count": 0, "missing_count": 1, "exempt_count": 1,
        "passed_count": 0, "failed_count": 0, "failed_student_count": 0, "sum_score_cents": 0,
        "weighted_sum_score_cents": 0, "graded_credits": 0, "average_score": None,
        "weighted_average_score": None, "pass_rate_percent": None,
        "distribution": [bucket.model_copy(update={"count": 0}) for bucket in original.summary.distribution],
    })
    row = original.rows[0].model_copy(update={"course_id": course_id, "course_name": "无有效成绩合成课",
        "statistics": no_scores, "fail_rate_percent": None, "excellent_count": 0,
        "excellent_rate_percent": None, "average_band": None})
    state.report = original.model_copy(update={
        "summary": original.summary.model_copy(update={"enrollment_count": 7, "missing_count": 2, "exempt_count": 2}),
        "rows": [*original.rows, row],
    })
    files = unpack(state)
    rates = read_csv(files["charts/rates.csv"])
    assert rates[0]["Rate"] == "66.67"
    without_scores = [rate for rate in rates if rate["Course ID"] == course_id]
    assert len(without_scores) == 2
    assert all(rate["Rate"] == "" and rate["Rate label"] == "—" and rate["Graded n"] == "0" for rate in without_scores)
    assert "nan" not in files["charts/rates.csv"].decode("utf-8-sig").lower()
    exported = json.loads(files["report/statistics.json"])["rows"][1]
    assert exported["statistics"]["pass_rate_percent"] is None and exported["fail_rate_percent"] is None


def test_export_does_not_mutate_workflow_or_require_future_report_context():
    state = confirmed_state()
    state.navigate("review")
    original = state.report.model_dump_json()
    history = list(state.history)
    assert state.context is None
    unpack(state)
    assert state.step == "review" and state.context is None and state.history == history
    assert state.report.model_dump_json() == original
