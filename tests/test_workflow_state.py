"""Pure workflow transitions and aggregate-only context, using invented data."""
import json

import pytest
from pydantic import ValidationError

from copilot.dashboard.workflow import (SECTION_LABELS, VIEW_LABELS, ReportContext, WorkflowError, WorkflowState)
from copilot.data.college import CollegeCommitResult, CollegePreview
from copilot.schemas import CourseReport, DatasetInfo, ReportScope

VERSION = "a" * 64


def dataset_info(version=VERSION, source="uploaded_local_only"):
    return DatasetInfo(dataset_version=version, schema_version="1", source=source, seed=None,
                       external_llm_allowed=source == "generated", counts={"students": 3, "classes": 2, "courses": 2,
                       "enrollments": 5, "grade_records": 5}, students_by_grade={"2024": 3}, terms=["2025-T1"],
                       minimum_courses_per_student=1, maximum_courses_per_student=2)


def preview(*, can_import=True, duplicate_policy="block"):
    return CollegePreview(can_import=can_import, counts={"students": 3, "classes": 2, "courses": 2, "enrollments": 5},
                          terms=["2025-T1"], majors={"software_engineering": 3}, classes=["2024合成班1"], courses=[],
                          issues=[], issue_counts={}, sample=[{"student": "PRIVATE_STUDENT", "id": "PRIVATE_ID", "score": "87.65"}],
                          preview_token="PRIVATE_PREVIEW_TOKEN", duplicate_policy=duplicate_policy)


def report(scope=None, version=VERSION):
    scope = scope or ReportScope(term="2025-T1")
    statistics = {"dataset_version": version, "scope": scope.base_scope().model_dump(), "enrollment_count": 5,
                  "student_count": 3, "graded_count": 3, "missing_count": 1, "exempt_count": 1, "passed_count": 2,
                  "failed_count": 1, "failed_student_count": 1, "sum_score_cents": 15250,
                  "weighted_sum_score_cents": 30500, "graded_credits": 6, "average_score": "50.83",
                  "weighted_average_score": "50.83", "pass_rate_percent": "66.67", "distribution": [
                      {"label": "[0,60)", "lower_cents": 0, "upper_cents": 6000, "upper_inclusive": False, "count": 1},
                      {"label": "[60,70)", "lower_cents": 6000, "upper_cents": 7000, "upper_inclusive": False, "count": 0},
                      {"label": "[70,80)", "lower_cents": 7000, "upper_cents": 8000, "upper_inclusive": False, "count": 1},
                      {"label": "[80,90)", "lower_cents": 8000, "upper_cents": 9000, "upper_inclusive": False, "count": 1},
                      {"label": "[90,100]", "lower_cents": 9000, "upper_cents": 10000, "upper_inclusive": True, "count": 0}]}
    row_statistics = {**statistics, "scope": scope.base_scope().model_copy(update={"course_id": "I" + "1" * 24}).model_dump()}
    body = {"dataset_version": version, "generated_at": "2026-11-09T00:00:00+00:00", "scope": scope.model_dump(),
            "summary": statistics, "rows": [{"course_id": "I" + "1" * 24, "course_name": "合成课程", "course_type": "professional_required",
                "statistics": row_statistics, "fail_rate_percent": "33.33", "excellent_count": 1,
                "excellent_rate_percent": "33.33", "average_band": "[0,60)"}],
            "mean_bands": [{"label": "[0,60)", "course_ids": ["I" + "1" * 24]}],
            "policy": {"score_bins": ["[0,60)", "[60,70)", "[70,80)", "[80,90)", "[90,100]"]}}
    return CourseReport.model_validate_json(json.dumps(body))


def confirmed_state(source="uploaded_local_only"):
    state = WorkflowState()
    state.select_upload("PRIVATE_FILE_NAME.xlsx", b"PRIVATE_WORKBOOK_BYTES")
    state.set_preview(preview())
    state.confirm(CollegeCommitResult(dataset=dataset_info(source=source), imported_counts={"students": 3, "classes": 2,
                                      "courses": 2, "enrollments": 5, "grade_records": 5}, retained_course_count=0))
    state.set_scope(ReportScope(term="2025-T1"))
    state.accept_report(report())
    return state


def test_initial_state_does_not_select_or_analyze_an_existing_dataset():
    state = WorkflowState()
    state.observe_dataset(dataset_info(source="generated"))
    assert state.step == "upload" and state.confirmed is None
    assert state.preview is state.report is state.context is None
    with pytest.raises(WorkflowError):
        state.navigate("analysis")


def test_uploaded_bytes_and_preview_are_separate_from_confirmation():
    state = WorkflowState()
    state.select_upload("synthetic.xlsx", b"synthetic workbook")
    assert state.step == "review" and state.preview is None and state.confirmed is None
    state.set_preview(preview())
    assert state.step == "review" and state.confirmed is None and not state.confirmed_matches_upload
    with pytest.raises(WorkflowError):
        state.navigate("analysis")


def test_blocked_preview_cannot_be_confirmed():
    state = WorkflowState()
    state.select_upload("synthetic.xlsx", b"synthetic")
    state.set_preview(preview(can_import=False))
    with pytest.raises(WorkflowError):
        state.confirm(CollegeCommitResult(dataset=dataset_info(), imported_counts={}, retained_course_count=0))
    assert state.confirmed is None


def test_confirmed_data_can_return_to_prior_steps_without_being_destroyed():
    state = confirmed_state()
    confirmed, original, original_report = state.confirmed, state.upload, state.report
    state.navigate("review")
    state.navigate("upload")
    assert state.confirmed == confirmed and state.upload == original and state.report == original_report
    state.navigate("analysis")
    assert state.has_current_report


def test_selecting_new_upload_preserves_database_confirmation_but_blocks_old_analysis():
    state = confirmed_state()
    old_confirmation = state.confirmed
    state.build_context(VIEW_LABELS)
    state.draft = "old draft"
    state.select_upload("second.xlsx", b"different workbook")
    assert state.confirmed == old_confirmation and state.step == "review"
    assert state.report is state.context is state.draft is None
    state.set_preview(preview())
    with pytest.raises(WorkflowError):
        state.navigate("analysis")


def test_same_upload_rerun_is_a_noop_but_explicit_reupload_invalidates():
    state = confirmed_state()
    assert state.select_upload("PRIVATE_FILE_NAME.xlsx", b"PRIVATE_WORKBOOK_BYTES") is False
    assert state.has_current_report
    assert state.select_upload("PRIVATE_FILE_NAME.xlsx", b"PRIVATE_WORKBOOK_BYTES", force=True) is True
    assert not state.confirmed_matches_upload and state.report is None


@pytest.mark.parametrize("changed", [dataset_info("b" * 64), dataset_info(source="generated"), None])
def test_dataset_version_source_or_disappearance_invalidates_report_and_draft(changed):
    state = confirmed_state()
    state.build_context(VIEW_LABELS)
    state.draft = "stale draft"
    state.navigate("writing")
    state.observe_dataset(changed)
    assert state.confirmed is state.preview is state.report is state.context is state.draft is None
    assert state.step == "review" and state.invalid_reason


def test_changing_full_scope_immediately_clears_report_context_and_draft():
    state = confirmed_state()
    state.build_context(VIEW_LABELS)
    state.draft = "stale draft"
    state.set_scope(ReportScope(term="2025-T1", major="software_engineering"))
    assert state.report is state.context is state.draft is None
    with pytest.raises(WorkflowError):
        state.accept_report(report())
    state.accept_report(report(state.scope))
    assert state.has_current_report


@pytest.mark.parametrize("mutate", [
    lambda r: r.model_copy(update={"dataset_version": "b" * 64}),
    lambda r: r.model_copy(update={"scope": ReportScope(term="2025-T1", class_id="C001")}),
    lambda r: r.model_copy(update={"summary": r.summary.model_copy(update={"dataset_version": "b" * 64})}),
    lambda r: r.model_copy(update={"rows": [r.rows[0].model_copy(update={"statistics": r.rows[0].statistics.model_copy(update={"dataset_version": "b" * 64})})]}),
])
def test_stale_or_mixed_report_is_rejected_without_replacing_a_current_one(mutate):
    state = confirmed_state()
    current = state.report
    with pytest.raises(WorkflowError):
        state.accept_report(mutate(current))
    assert state.report == current and state.has_current_report


def test_context_only_contains_aggregate_evidence_and_never_authorizes_external_llm():
    state = confirmed_state()
    context = state.build_context(VIEW_LABELS, SECTION_LABELS)
    serialized = context.model_dump_json()
    for private in ("PRIVATE_FILE_NAME", "PRIVATE_WORKBOOK_BYTES", "PRIVATE_STUDENT", "PRIVATE_ID", "PRIVATE_PREVIEW_TOKEN", "87.65"):
        assert private not in serialized
        assert private not in json.dumps(state.history)
    assert context.scope == state.scope and context.policy == state.report.policy
    assert context.evidence.dataset_version == context.confirmed_dataset_version == VERSION
    assert context.confirmation.source == "uploaded_local_only" and context.external_llm_allowed is False
    assert context.evidence.contains_student_rows is False
    assert context.report_generation_implemented is context.docx_export_implemented is False
    with pytest.raises(ValidationError):
        ReportContext.model_validate({**context.model_dump(), "external_llm_allowed": True})


def test_selections_and_chapter_configuration_invalidate_draft_but_not_statistics():
    state = confirmed_state()
    context = state.build_context(VIEW_LABELS)
    state.draft = "draft"
    assert state.build_context(VIEW_LABELS) == context and state.draft == "draft"
    revised = state.build_context(["course_table"], ["overview", "methods"])
    assert revised.selected_views == ("course_table",) and revised.sections == ("overview", "methods")
    assert state.draft is None and state.has_current_report


@pytest.mark.parametrize("views,sections", [([], ["overview"]), (["unknown"], ["overview"]),
                                          (["course_table", "course_table"], ["overview"]), (["course_table"], [])])
def test_invalid_report_selection_cannot_create_context(views, sections):
    state = confirmed_state()
    with pytest.raises(WorkflowError):
        state.build_context(views, sections)


def test_future_steps_require_current_context_and_never_call_a_provider():
    state = confirmed_state(source="generated")
    with pytest.raises(WorkflowError):
        state.navigate("writing")
    context = state.build_context(VIEW_LABELS)
    assert context.external_llm_allowed is False
    state.navigate("writing")
    state.navigate("export")
    state.set_scope(ReportScope(term="2025-T1", class_id="C001"))
    assert state.step == "analysis" and state.context is None


def test_changed_duplicate_policy_requires_new_confirmation():
    state = confirmed_state()
    state.set_preview(preview(duplicate_policy="first"))
    assert not state.confirmed_matches_upload and state.report is None
    with pytest.raises(WorkflowError):
        state.navigate("analysis")


def test_stale_preview_response_cannot_be_attached_to_new_workbook():
    state = WorkflowState()
    state.select_upload("first.xlsx", b"first")
    old_hash = state.upload.sha256
    state.select_upload("second.xlsx", b"second")
    with pytest.raises(WorkflowError):
        state.set_preview(preview(), file_hash=old_hash)
    assert state.preview is None


def test_changed_cleaning_result_requires_confirmation_even_when_file_and_policy_match():
    state = confirmed_state()
    original = preview()
    state.set_preview(original.model_copy(update={"counts": {**original.counts, "enrollments": 4}}))
    assert not state.confirmed_matches_upload and state.report is None
    with pytest.raises(WorkflowError):
        state.navigate("analysis")


def test_renewing_only_preview_token_preserves_confirmed_analysis():
    state = confirmed_state()
    state.set_preview(preview().model_copy(update={"preview_token": "RENEWED_TOKEN"}))
    assert state.confirmed_matches_upload and state.has_current_report
