"""Fixed-seed, single-term synthetic data; never draws on upstream student files."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random

from copilot.models import (TABLE_NAMES, ClassRecord, CourseRecord, Dataset, EnrollmentRecord,
                            GradeRecord, StudentRecord)

DEFAULT_SEED = 20261103
DEFAULT_TERM = "2026-T1"
COURSE_NAMES = (
    "Mathematics", "Academic Writing", "Programming", "Statistics", "English", "Physics", "Ethics", "Data Literacy",
    "Databases", "Economics", "Chemistry", "Biology", "History", "Geography", "Art", "Music", "Design", "Robotics",
    "Networks", "Accounting", "Business", "Psychology", "Sociology", "Literature", "Astronomy", "Environmental Science",
    "Public Speaking", "Project Management", "Algorithms", "Digital Media", "Entrepreneurship", "Research Methods",
)


def generate_dataset(seed: int = DEFAULT_SEED) -> Dataset:
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an integer from 0 to 2**32 - 1")
    rng = random.Random(seed)
    classes, students, courses, enrollments, grades = [], [], [], [], []
    for level, class_count, large_count in ((1, 14, 8), (2, 13, 7), (3, 13, 7)):
        for index in range(class_count):
            class_id = f"C{len(classes) + 1:03}"
            classes.append(ClassRecord(class_id=class_id, grade_level=level, class_label=f"Synthetic Class {class_id}"))
            for _ in range(35 if index < large_count else 34):
                student_id = f"S{len(students) + 1:04}"
                students.append(StudentRecord(student_id=student_id, class_id=class_id,
                                              display_name=f"Synthetic Student {student_id[1:]}"))
        for index, name in enumerate(COURSE_NAMES, 1):
            courses.append(CourseRecord(course_id=f"G{level}-C{index:02}", course_name=name,
                                        grade_level=level, credits=2 + (index % 3)))
    levels = {c.class_id: c.grade_level for c in classes}
    elective_cursors = {1: 0, 2: 0, 3: 0}
    for student in students:
        level = levels[student.class_id]
        count = rng.randint(2, 4)
        cursor = elective_cursors[level]
        electives = [9 + ((cursor + i) % 24) for i in range(count)]
        elective_cursors[level] += count
        ability = rng.randint(-1200, 1200)
        for index in [*range(1, 9), *electives]:
            number = len(enrollments) + 1
            enrollment_id = f"E{number:05}"
            enrollments.append(EnrollmentRecord(enrollment_id=enrollment_id, student_id=student.student_id,
                                                 course_id=f"G{level}-C{index:02}", term=DEFAULT_TERM))
            # Explicit small rates: 2% missing, 1% exempt; realized counts are in the manifest.
            draw = rng.randrange(100)
            status = "missing" if draw < 2 else "exempt" if draw == 2 else "graded"
            score = max(0, min(10000, 7300 + ability + rng.randint(-2600, 2200) - (index % 5) * 120))
            grades.append(GradeRecord(grade_record_id=f"R{number:05}", enrollment_id=enrollment_id,
                                      status=status, score_cents=score if status == "graded" else None))
    return Dataset(tuple(classes), tuple(students), tuple(courses), tuple(enrollments), tuple(grades)).validate()


def export_dataset(dataset: Dataset, directory: Path, seed: int) -> dict:
    dataset.validate()
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for table, records in dataset.payload().items():
        path = directory / f"{table}.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"schema_version": "1", "generator_version": "1", "seed": seed, "synthetic": True,
                "dataset_version": dataset.version, "term": DEFAULT_TERM,
                "counts": {table: len(getattr(dataset, table)) for table in TABLE_NAMES},
                "grade_status_counts": {s: sum(r.status == s for r in dataset.grade_records) for s in ("graded", "missing", "exempt")},
                "file_sha256": hashes}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate the entirely synthetic academic demo")
    parser.add_argument("--output", type=Path, default=Path("data/demo"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    print(json.dumps(export_dataset(generate_dataset(args.seed), args.output, args.seed), indent=2))


if __name__ == "__main__":
    main()
