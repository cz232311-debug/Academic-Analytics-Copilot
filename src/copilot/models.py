"""Explicit normalized record types; scores use integer hundredths of a point."""
from dataclasses import dataclass
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

SCHEMA_VERSION = "1"
TABLE_NAMES = ("classes", "students", "courses", "enrollments", "grade_records")
StudentId = Annotated[str, StringConstraints(pattern=r"^S[0-9]{4}$")]
ClassId = Annotated[str, StringConstraints(pattern=r"^C[0-9]{3}$")]
CourseId = Annotated[str, StringConstraints(pattern=r"^G[1-3]-C[0-9]{2}$")]
EnrollmentId = Annotated[str, StringConstraints(pattern=r"^E[0-9]{5}$")]
Term = Annotated[str, StringConstraints(pattern=r"^20[0-9]{2}-T[1-3]$")]
GradeLevel = Annotated[int, Field(ge=1, le=3)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ()&.,/-]*$")]


class Record(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ClassRecord(Record):
    class_id: ClassId
    grade_level: GradeLevel
    class_label: Label


class StudentRecord(Record):
    student_id: StudentId
    class_id: ClassId
    display_name: str

    @model_validator(mode="after")
    def synthetic_identity(self):
        if self.display_name != f"Synthetic Student {self.student_id[1:]}":
            raise ValueError("Only synthetic display names matching the student ID are allowed")
        return self


class CourseRecord(Record):
    course_id: CourseId
    course_name: Label
    grade_level: GradeLevel
    credits: Annotated[int, Field(ge=2, le=4)]

    @model_validator(mode="after")
    def matching_grade(self):
        if int(self.course_id[1]) != self.grade_level:
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
        return {table: [row.model_dump() for row in sorted(getattr(self, table), key=lambda r: getattr(r, PRIMARY_KEYS[table]))]
                for table in TABLE_NAMES}

    @property
    def version(self) -> str:
        canonical = json.dumps({"schema_version": SCHEMA_VERSION, "tables": self.payload()}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
