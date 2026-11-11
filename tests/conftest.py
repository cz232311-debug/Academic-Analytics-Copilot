import pytest

from copilot.models import (ClassRecord, CourseRecord, Dataset, EnrollmentRecord, GradeRecord, StudentRecord)


@pytest.fixture(autouse=True)
def explicit_offline_provider(monkeypatch):
    """Tests opt into Mock and never inherit a paid production configuration."""
    monkeypatch.setenv("COPILOT_LLM_PROVIDER", "mock")
    monkeypatch.delenv("COPILOT_LLM_API_KEY", raising=False)


@pytest.fixture
def tiny_dataset():
    """Independent hand-calculation fixture; deliberately unequal course loads."""
    classes = tuple(ClassRecord(class_id=f"C{i:03}", grade_level=1, class_label=f"Synthetic Class {i}") for i in (1, 2))
    students = tuple(StudentRecord(student_id=f"S{i:04}", class_id=f"C{1 if i < 3 else 2:03}",
                                   display_name=f"Synthetic Student {i:04}") for i in range(1, 5))
    courses = (CourseRecord(course_id="G1-C01", course_name="Mathematics", grade_level=1, credits=3),
               CourseRecord(course_id="G1-C02", course_name="Programming", grade_level=1, credits=2),
               CourseRecord(course_id="G2-C01", course_name="Statistics", grade_level=2, credits=3))
    specs = [(1, 1, 6000, "graded"), (2, 1, 5999, "graded"), (3, 1, 10000, "graded"),
             (4, 1, None, "missing"), (1, 2, 0, "graded"), (2, 2, None, "exempt")]
    enrollments, grades = [], []
    for number, (student, course, score, status) in enumerate(specs, 1):
        eid = f"E{number:05}"
        enrollments.append(EnrollmentRecord(enrollment_id=eid, student_id=f"S{student:04}",
                                             course_id=f"G1-C{course:02}", term="2026-T1"))
        grades.append(GradeRecord(grade_record_id=f"R{number:05}", enrollment_id=eid, status=status, score_cents=score))
    return Dataset(classes, students, courses, tuple(enrollments), tuple(grades)).validate()
