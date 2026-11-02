"""Public analytical contracts. Decimal display values serialize as JSON strings."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from copilot.models import ClassId, ClassRecord, CourseId, CourseRecord, GradeLevel, Record, Term


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    term: Term = "2026-T1"
    grade_level: GradeLevel | None = None
    class_id: ClassId | None = None
    course_id: CourseId | None = None


class DatasetInfo(Record):
    dataset_version: str
    schema_version: str
    source: Literal["generated", "uploaded_local_only"]
    seed: int | None
    external_llm_allowed: bool
    counts: dict[str, int]
    students_by_grade: dict[str, int]
    terms: list[str]
    minimum_courses_per_student: int
    maximum_courses_per_student: int


class ScoreBin(Record):
    label: str
    lower_cents: int
    upper_cents: int
    upper_inclusive: bool
    count: int


class Statistics(Record):
    dataset_version: str
    scope: Scope
    metric: Literal["enrollment_score_mean"] = "enrollment_score_mean"
    enrollment_count: int
    student_count: int
    graded_count: int
    missing_count: int
    exempt_count: int
    passed_count: int
    failed_count: int
    failed_student_count: int
    sum_score_cents: int
    weighted_sum_score_cents: int
    graded_credits: int
    average_score: Decimal | None
    weighted_average_score: Decimal | None
    pass_rate_percent: Decimal | None
    distribution: list[ScoreBin]


class ClassComparison(Record):
    dataset_version: str
    course_id: CourseId
    term: Term
    results: list[Statistics]
    highest_mean_class_ids: list[str]


class Catalog(Record):
    dataset_version: str
    classes: list[ClassRecord]
    courses: list[CourseRecord]


class ImportResult(Record):
    dataset: DatasetInfo
    format: Literal["csv", "xlsx"]
    blank_rows_removed: dict[str, int]


class ErrorDetail(Record):
    code: str
    message: str


class ErrorResponse(Record):
    error: ErrorDetail
