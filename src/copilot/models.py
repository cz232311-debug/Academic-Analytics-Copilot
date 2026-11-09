"""Explicit normalized record types; scores use integer hundredths of a point."""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Annotated, Literal
import unicodedata

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_serializer, model_validator

SCHEMA_VERSION = "1"
TABLE_NAMES = ("classes", "students", "courses", "enrollments", "grade_records")
StudentId = Annotated[str, StringConstraints(pattern=r"^S[0-9]{4}$")]
ClassId = Annotated[str, StringConstraints(pattern=r"^C[0-9]{3}$")]
CourseId = Annotated[str, StringConstraints(pattern=r"^(?:G[1-3]-C[0-9]{2}|I[0-9a-f]{24})$")]
EnrollmentId = Annotated[str, StringConstraints(pattern=r"^E[0-9]{5}$")]
Term = Annotated[str, StringConstraints(pattern=r"^20[0-9]{2}-T[1-3]$")]
GradeLevel = Annotated[int, Field(ge=1, le=3)] | Annotated[int, Field(ge=1900, le=2199)]
Major = Literal["computer_science", "business_administration", "information_systems", "software_engineering", "network_engineering", "unspecified"]
CourseType = Literal["general_required", "general_elective", "professional_required", "professional_elective", "practical", "unspecified"]


def safe_label(value: str) -> str:
    if not value.strip() or any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("Labels must contain visible text without control characters")
    return value


Label = Annotated[str, StringConstraints(min_length=1, max_length=200), AfterValidator(safe_label)]


class Record(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ClassRecord(Record):
    class_id: ClassId
    grade_level: GradeLevel
    class_label: Label
    major: Major = "unspecified"


class StudentRecord(Record):
    student_id: StudentId
    class_id: ClassId
    display_name: str

    @model_validator(mode="after")
    def synthetic_identity(self):
        if self.display_name not in {f"Synthetic Student {self.student_id[1:]}", f"Imported Student {self.student_id[1:]}"}:
            raise ValueError("Only canonical synthetic or imported aliases matching the student ID are allowed")
        return self


class CourseRecord(Record):
    course_id: CourseId
    course_name: Label
    grade_level: GradeLevel
    credits: Annotated[int | Decimal, Field(gt=0, le=100)]
    course_type: CourseType = "unspecified"
    source_header: Annotated[str, Field(max_length=1000)] | None = None
    source_key: Annotated[str, Field(max_length=128)] | None = None
    course_category: Annotated[str, Field(max_length=100)] | None = None
    study_nature: Annotated[str, Field(max_length=40)] | None = None
    hours: Annotated[int, Field(ge=0, le=2000)] | None = None
    academic_year: Annotated[str, Field(pattern=r"^[12][0-9]{3}-[12][0-9]{3}$")] | None = None
    source_term: Term | None = None
    source_code: Annotated[str, Field(max_length=100)] | None = None

    @model_serializer(mode="wrap")
    def serialize_course(self, handler):
        # Absent additive metadata must not change legacy catalog/tool payloads.
        values = handler(self)
        return {key: value for key, value in values.items() if value is not None}

    @field_validator("credits")
    @classmethod
    def exact_credits(cls, value):
        decimal = Decimal(value)
        if not decimal.is_finite() or decimal * 100 != (decimal * 100).to_integral_value():
            raise ValueError("Credits must be finite and have at most two decimal places")
        # Preserve the canonical representation and fingerprints of legacy rows.
        return int(decimal) if decimal == decimal.to_integral_value() else decimal.normalize()

    @field_validator("source_header", "source_key", "course_category", "study_nature", "source_code")
    @classmethod
    def safe_source_metadata(cls, value, info):
        allowed = {"\n", "\r", "\t"} if info.field_name == "source_header" else set()
        if value is not None and any(unicodedata.category(char).startswith("C") and char not in allowed for char in value):
            raise ValueError("Course metadata contains unsupported control characters")
        return value

    @model_validator(mode="after")
    def matching_grade(self):
        if self.course_id.startswith("G") and int(self.course_id[1]) != self.grade_level:
            raise ValueError("Course ID and grade_level disagree")
        return self


class EnrollmentRecord(Record):
    enrollment_id: EnrollmentId
    student_id: StudentId
    course_id: CourseId
    term: Term


class GradeRecord(Record):
    grade_record_id: Annotated[str, StringConstraints(pattern=r"^R[0-9]{5}$")]
    enrollment_id: EnrollmentId
    status: Literal["graded", "missing", "exempt"]
    score_cents: Annotated[int, Field(ge=0, le=10000)] | None

    @model_validator(mode="after")
    def consistent_status(self):
        if (self.status == "graded") != (self.score_cents is not None):
            raise ValueError("Graded records require a score; missing/exempt records require null")
        return self


RECORD_TYPES = dict(zip(TABLE_NAMES, (ClassRecord, StudentRecord, CourseRecord, EnrollmentRecord, GradeRecord)))
PRIMARY_KEYS = {table: next(iter(model.model_fields)) for table, model in RECORD_TYPES.items()}


class DataError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_dataset", status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Dataset:
    classes: tuple[ClassRecord, ...]
    students: tuple[StudentRecord, ...]
    courses: tuple[CourseRecord, ...]
    enrollments: tuple[EnrollmentRecord, ...]
    grade_records: tuple[GradeRecord, ...]

    def validate(self) -> "Dataset":
        indexes = {}
        for table, model in RECORD_TYPES.items():
            records = getattr(self, table)
            if not records:
                raise DataError(f"{table} must contain at least one record")
            if any(not isinstance(row, model) for row in records):
                raise DataError(f"{table} contains an incorrect record type")
            for row in records:
                try:
                    model.model_validate(row.model_dump())
                except ValidationError as exc:
                    raise DataError(f"{table} contains an invalid record") from exc
            key = PRIMARY_KEYS[table]
            index = {getattr(row, key): row for row in records}
            if len(index) != len(records):
                raise DataError(f"Duplicate primary key in {table}")
            indexes[table] = index
        for student in self.students:
            if student.class_id not in indexes["classes"]:
                raise DataError("Student references an unknown class")
        selected = set()
        for enrollment in self.enrollments:
            student = indexes["students"].get(enrollment.student_id)
            course = indexes["courses"].get(enrollment.course_id)
            if student is None or course is None:
                raise DataError("Enrollment references an unknown student or course")
            if indexes["classes"][student.class_id].grade_level != course.grade_level:
                raise DataError("Student is not eligible for the course grade")
            key = (enrollment.student_id, enrollment.course_id, enrollment.term)
            if key in selected:
                raise DataError("Duplicate student/course/term enrollment")
            selected.add(key)
        covered = set()
        for record in self.grade_records:
            if record.enrollment_id not in indexes["enrollments"]:
                raise DataError("Grade references an unknown enrollment")
            if record.enrollment_id in covered:
                raise DataError("An enrollment has more than one final grade record")
            covered.add(record.enrollment_id)
        if covered != set(indexes["enrollments"]):
            raise DataError("Every enrollment needs a graded, missing or exempt record")
        return self

    def payload(self) -> dict:
        # Optional reporting metadata is omitted when unknown. Legacy datasets
        # retain their canonical payload/fingerprint; supplied metadata is hashed.
        payload = {}
        for table in TABLE_NAMES:
            rows = sorted(getattr(self, table), key=lambda r: getattr(r, PRIMARY_KEYS[table]))
            optional = "major" if table == "classes" else "course_type" if table == "courses" else None
            exclude = {optional} if optional and all(getattr(row, optional) == "unspecified" for row in rows) else set()
            payload[table] = [row.model_dump(mode="json", exclude=exclude, exclude_none=table == "courses") for row in rows]
        return payload

    @property
    def version(self) -> str:
        canonical = json.dumps({"schema_version": SCHEMA_VERSION, "tables": self.payload()}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
