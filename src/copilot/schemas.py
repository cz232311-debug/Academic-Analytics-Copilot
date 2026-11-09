"""Public analytical contracts. Decimal display values serialize as JSON strings."""
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from copilot.models import ClassId, ClassRecord, CourseId, CourseRecord, CourseType, GradeLevel, Major, Record, Term


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
    weighted_sum_score_cents: int | Decimal
    graded_credits: int | Decimal
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


class ReportScope(Scope):
    major: Major | None = None
    course_type: CourseType | None = None

    def base_scope(self) -> Scope:
        return Scope(**self.model_dump(include=set(Scope.model_fields)))


class CourseReportQuery(ReportScope):
    expected_version: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None


class ReportingCatalog(Catalog):
    terms: list[str]
    majors: list[str]
    course_types: list[str]


class ReportPolicy(Record):
    excellent_threshold_cents: int = 8000
    pass_threshold_cents: int = 6000
    score_bins: list[str]
    mean_band_rule: str = "Classify the exact unrounded course mean; intervals are lower-inclusive and upper-exclusive, except 100 is included."
    denominator: str = "Graded enrollment records only; missing and exempt are excluded from averages, rates and score bands. No records means null, never zero."
    rounding: str = "Display scores and percentages to 2 decimal places using ROUND_HALF_UP."


class CourseReportRow(Record):
    course_id: CourseId
    course_name: str
    course_type: CourseType
    statistics: Statistics
    fail_rate_percent: Decimal | None
    excellent_count: int
    excellent_rate_percent: Decimal | None
    average_band: str | None


class CourseMeanBand(Record):
    label: str
    course_ids: list[str]


class CourseReport(Record):
    dataset_version: str
    generated_at: str
    scope: ReportScope
    summary: Statistics
    rows: list[CourseReportRow]
    mean_bands: list[CourseMeanBand]
    policy: ReportPolicy


class ImportResult(Record):
    dataset: DatasetInfo
    format: Literal["csv", "xlsx"]
    blank_rows_removed: dict[str, int]


class ErrorDetail(Record):
    code: str
    message: str


class ErrorResponse(Record):
    error: ErrorDetail
