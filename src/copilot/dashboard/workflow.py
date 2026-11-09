"""Local workflow state and aggregate-only context for future report authoring.

This module has no UI, database, filesystem or provider calls. Original uploads
stay in the session object and are deliberately absent from ReportContext.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Literal

from pydantic import Field

from copilot.data.college import CollegeCommitResult, CollegePreview, DuplicatePolicy
from copilot.models import Record
from copilot.schemas import CourseReport, DatasetInfo, ReportPolicy, ReportScope

Step = Literal["upload", "review", "analysis", "writing", "export"]
ViewName = Literal["course_table", "rates_chart", "mean_bands"]
SectionName = Literal["overview", "distribution", "rates", "mean_bands", "methods"]
VIEW_LABELS = {"course_table": "课程成绩分布表", "rates_chart": "及格与不及格率图", "mean_bands": "课程均分分层表"}
SECTION_LABELS = {"overview": "数据与范围概况", "distribution": "成绩分布", "rates": "及格情况",
                  "mean_bands": "课程均分分层", "methods": "统计口径与说明"}
STEPS = ("upload", "review", "analysis", "writing", "export")


class WorkflowError(ValueError):
    """A requested transition would reuse missing or stale information."""


@dataclass(frozen=True)
class LocalUpload:
    name: str = field(repr=False)
    content: bytes = field(repr=False)
    sha256: str
    source: Literal["upload", "example"] = "upload"


class ConfirmedDataset(Record):
    dataset_version: str
    source: Literal["generated", "uploaded_local_only"]
    confirmed_at: str
    file_hash: str
    duplicate_policy: DuplicatePolicy
    counts: dict[str, int]
    imported_counts: dict[str, int] = Field(default_factory=dict)
    input_source: Literal["upload", "example"] = "upload"
    selection_revision: int = 0
    archive_id: str | None = None
    archive_status: Literal["complete", "pending"] | None = None


class ReportEvidence(Record):
    dataset_version: str
    scope: ReportScope
    generated_at: str
    endpoint: Literal["/api/reports/courses"] = "/api/reports/courses"
    calculation: Literal["AnalyticsEngine.course_report"] = "AnalyticsEngine.course_report"
    contains_student_rows: Literal[False] = False


class ReportContext(Record):
    """Reusable aggregate context, with no authority to invoke an external LLM."""
    confirmed_dataset_version: str
    source: Literal["generated", "uploaded_local_only"]
    scope: ReportScope
    report: CourseReport
    selected_views: tuple[ViewName, ...]
    sections: tuple[SectionName, ...]
    policy: ReportPolicy
    evidence: ReportEvidence
    confirmation: ConfirmedDataset
    external_llm_allowed: Literal[False] = False
    report_generation_implemented: Literal[False] = False
    docx_export_implemented: Literal[False] = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkflowState:
    step: Step = "upload"
    upload: LocalUpload | None = field(default=None, repr=False)
    preview: CollegePreview | None = field(default=None, repr=False)
    confirmed: ConfirmedDataset | None = None
    scope: ReportScope | None = None
    report: CourseReport | None = field(default=None, repr=False)
    context: ReportContext | None = field(default=None, repr=False)
    draft: str | None = field(default=None, repr=False)
    invalid_reason: str | None = None
    history: list[dict] = field(default_factory=list)
    selection_revision: int = 0
    _observed_dataset: DatasetInfo | None = field(default=None, repr=False)

    @property
    def confirmed_matches_upload(self) -> bool:
        return bool(self.confirmed and self.upload and self.preview and self.preview.can_import
                    and self.confirmed.file_hash == self.upload.sha256
                    and self.confirmed.selection_revision == self.selection_revision
                    and self.confirmed.duplicate_policy == self.preview.duplicate_policy)

    @property
    def has_current_report(self) -> bool:
        try:
            self._check_report(self.report)
        except WorkflowError:
            return False
        return True

    @property
    def has_current_context(self) -> bool:
        return bool(self.context and self.has_current_report
                    and self.context.confirmation == self.confirmed
                    and self.context.scope == self.scope
                    and self.context.report == self.report)

    def _clear_analysis(self, reason: str | None):
        self.report = self.context = self.draft = None
        self.invalid_reason = reason
        if self.step in {"writing", "export"}:
            self.step = "analysis" if self.confirmed_matches_upload else "review" if self.upload else "upload"

    def select_upload(self, name: str, content: bytes, source="upload", *, force=False) -> bool:
        if source not in {"upload", "example"} or not isinstance(content, bytes) or not content:
            raise WorkflowError("请选择有效的工作簿来源。")
        digest = hashlib.sha256(content).hexdigest()
        if not force and self.upload and (self.upload.sha256, self.upload.source, self.upload.name) == (digest, source, name):
            return False
        self.upload = LocalUpload(name=name, content=content, sha256=digest, source=source)
        self.selection_revision += 1
        self.preview = None
        self.scope = None
        self._clear_analysis("数据源已重新选择，请重新解析和确认；此前确认的数据尚未被替换。" if self.confirmed else None)
        self.step = "review"
        return True

    def set_preview(self, preview: CollegePreview, *, file_hash: str | None = None):
        if self.upload is None or file_hash is not None and file_hash != self.upload.sha256:
            raise WorkflowError("解析结果不属于当前工作簿，请重新解析。")
        semantic_fields = {"can_import", "counts", "issue_counts", "courses", "terms", "majors", "classes", "duplicate_policy", "assumptions"}
        changed_cleaning = self.preview is not None and self.preview.model_dump(include=semantic_fields) != preview.model_dump(include=semantic_fields)
        if changed_cleaning:
            self.selection_revision += 1
            self._clear_analysis("清洗结果或重复行处理方式已改变，请重新确认并生成统计。")
        self.preview = preview.model_copy(deep=True)
        self.step = "review"
        self.history.append({"event": "cleaning_preview", "at": _now(), "file_hash": self.upload.sha256,
                             "policy": preview.duplicate_policy, "counts": dict(preview.counts),
                             "issue_counts": dict(preview.issue_counts), "can_import": preview.can_import})

    def confirm(self, result: CollegeCommitResult):
        if self.upload is None or self.preview is None or not self.preview.can_import:
            raise WorkflowError("当前工作簿尚未通过核对，不能进入分析。")
        self.confirmed = ConfirmedDataset(dataset_version=result.dataset.dataset_version, source=result.dataset.source,
            confirmed_at=_now(), file_hash=self.upload.sha256, duplicate_policy=self.preview.duplicate_policy,
            counts=dict(result.dataset.counts), imported_counts=dict(result.imported_counts), input_source=self.upload.source,
            selection_revision=self.selection_revision, archive_id=result.archive_id, archive_status=result.archive_status)
        self._observed_dataset = result.dataset.model_copy(deep=True)
        self.scope = None
        self._clear_analysis(None)
        self.step = "analysis"
        self.history.append({"event": "confirmed", "at": self.confirmed.confirmed_at,
                             "dataset_version": self.confirmed.dataset_version, "source": self.confirmed.source,
                             "file_hash": self.confirmed.file_hash, "policy": self.confirmed.duplicate_policy,
                             "counts": dict(result.imported_counts), "archive_id": self.confirmed.archive_id,
                             "archive_status": self.confirmed.archive_status})

    def observe_dataset(self, info: DatasetInfo | None):
        self._observed_dataset = info.model_copy(deep=True) if info else None
        if self.confirmed and (info is None or (info.dataset_version, info.source) !=
                               (self.confirmed.dataset_version, self.confirmed.source)):
            self.confirmed = None
            self.preview = None
            self.scope = None
            self._clear_analysis("当前数据集或来源已改变，旧统计和报告草稿已失效；请重新解析并确认。")
            self.step = "review" if self.upload else "upload"
            self.history.append({"event": "dataset_invalidated", "at": _now()})

    def set_scope(self, scope: ReportScope) -> bool:
        if self.scope == scope:
            return False
        changed = self.scope is not None
        self.scope = scope.model_copy(deep=True)
        self._clear_analysis("分析范围已改变，请重新生成统计预览。" if changed else None)
        return True

    def _check_report(self, report: CourseReport | None):
        if not self.confirmed_matches_upload or self.scope is None or report is None:
            raise WorkflowError("请先确认当前数据并生成统计预览。")
        if self._observed_dataset is None or (self._observed_dataset.dataset_version, self._observed_dataset.source) != (
                self.confirmed.dataset_version, self.confirmed.source):
            raise WorkflowError("当前数据版本或来源与确认记录不一致。")
        if report.dataset_version != self.confirmed.dataset_version or report.scope != self.scope:
            raise WorkflowError("统计响应的范围或数据版本已失效，请重新生成。")
        if report.summary.dataset_version != report.dataset_version or report.summary.scope != self.scope.base_scope():
            raise WorkflowError("统计汇总的证据版本或范围不一致。")
        for row in report.rows:
            if (row.statistics.dataset_version != report.dataset_version or
                    row.statistics.scope != self.scope.base_scope().model_copy(update={"course_id": row.course_id})):
                raise WorkflowError("课程统计的证据版本或范围不一致。")

    def accept_report(self, report: CourseReport):
        self._check_report(report)
        self.report = report.model_copy(deep=True)
        self.context = self.draft = None
        self.invalid_reason = None

    def navigate(self, step: Step):
        if step not in STEPS:
            raise WorkflowError("无法识别此工作流步骤。")
        if step == "review" and (self.upload is None or self.preview is None):
            raise WorkflowError("请先上传并解析工作簿。")
        if step == "analysis" and not self.confirmed_matches_upload:
            raise WorkflowError("当前上传仍需核对确认，不能复用此前的数据分析。")
        if step in {"writing", "export"} and not self.has_current_context:
            raise WorkflowError("请先生成当前范围的统计并选择报告内容。")
        self.step = step

    def build_context(self, selected_views, sections=None) -> ReportContext:
        self._check_report(self.report)
        views, selected_sections = tuple(selected_views), tuple(SECTION_LABELS if sections is None else sections)
        if (not views or len(set(views)) != len(views) or any(v not in VIEW_LABELS for v in views)
                or not selected_sections or len(set(selected_sections)) != len(selected_sections)
                or any(s not in SECTION_LABELS for s in selected_sections)):
            raise WorkflowError("请至少选择一项有效图表和一个报告章节，且不要重复。")
        context = ReportContext(confirmed_dataset_version=self.confirmed.dataset_version, source=self.confirmed.source,
            scope=self.scope.model_copy(deep=True), report=self.report.model_copy(deep=True), selected_views=views,
            sections=selected_sections, policy=self.report.policy.model_copy(deep=True),
            evidence=ReportEvidence(dataset_version=self.report.dataset_version, scope=self.scope.model_copy(deep=True),
                                    generated_at=self.report.generated_at), confirmation=self.confirmed.model_copy(deep=True))
        if self.context != context:
            self.draft = None
        self.context = context
        return context
