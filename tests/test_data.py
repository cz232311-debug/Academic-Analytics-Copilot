from collections import Counter
from dataclasses import replace

from pydantic import ValidationError
import pytest

from copilot.data.generate import export_dataset, generate_dataset
from copilot.models import DataError, GradeRecord, StudentRecord


@pytest.fixture(scope="module")
def demo():
    return generate_dataset()


def test_demo_shape_and_normalized_enrollments(demo):
    assert (len(demo.students), len(demo.classes), len(demo.courses)) == (1382, 40, 96)
    classes = {c.class_id: c.grade_level for c in demo.classes}
    assert Counter(classes[s.class_id] for s in demo.students) == {1: 484, 2: 449, 3: 449}
    assert set(Counter(s.class_id for s in demo.students).values()) == {34, 35}
    loads = Counter(e.student_id for e in demo.enrollments)
    assert len(loads) == 1382
    assert set(loads.values()) == {10, 11, 12}
    assert len({e.course_id for e in demo.enrollments}) == 96
    assert len(demo.enrollments) == len(demo.grade_records) < 1382 * 96
    assert {r.status for r in demo.grade_records} == {"graded", "missing", "exempt"}


def test_seed_and_exports_are_reproducible(demo, tmp_path):
    repeated = generate_dataset()
    assert repeated.version == demo.version
    # Frozen after the generator actually ran; change only with a generator revision.
    assert demo.version == "a52c8961a7c1da92ab4d7d8cc8af9e9a636fe3e788cbf5bcd24c8eb16607cabc"
    assert generate_dataset(7).version != demo.version
    first = export_dataset(demo, tmp_path / "first", 20261103)
    second = export_dataset(repeated, tmp_path / "second", 20261103)
    assert first == second
    assert (tmp_path / "first/manifest.json").read_bytes() == (tmp_path / "second/manifest.json").read_bytes()
    for name in first["file_sha256"]:
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_fingerprint_does_not_depend_on_input_row_order(tiny_dataset):
    assert replace(tiny_dataset, students=tuple(reversed(tiny_dataset.students))).version == tiny_dataset.version


@pytest.mark.parametrize("score,status", [(-1, "graded"), (10001, "graded"), (None, "graded"), (6000, "missing"), (True, "graded"), (60.1, "graded")])
def test_invalid_scores_and_status_are_rejected(score, status):
    with pytest.raises(ValidationError):
        GradeRecord(grade_record_id="R00001", enrollment_id="E00001", status=status, score_cents=score)


def test_real_names_and_numeric_identifiers_are_rejected():
    with pytest.raises(ValidationError):
        StudentRecord(student_id="S0001", class_id="C001", display_name="Real Person")
    with pytest.raises(ValidationError):
        StudentRecord(student_id=1, class_id="C001", display_name="Synthetic Student 0001")


def test_duplicate_and_orphan_records_are_rejected(tiny_dataset):
    with pytest.raises(DataError, match="Duplicate primary"):
        replace(tiny_dataset, students=(*tiny_dataset.students, tiny_dataset.students[0])).validate()
    orphan = tiny_dataset.students[0].model_copy(update={"class_id": "C999"})
    with pytest.raises(DataError, match="unknown class"):
        replace(tiny_dataset, students=(orphan, *tiny_dataset.students[1:])).validate()


def test_eligibility_duplicate_enrollment_and_missing_final_are_rejected(tiny_dataset):
    wrong_grade = tiny_dataset.enrollments[0].model_copy(update={"course_id": "G2-C01"})
    with pytest.raises(DataError, match="eligible"):
        replace(tiny_dataset, enrollments=(wrong_grade, *tiny_dataset.enrollments[1:])).validate()
    duplicate = tiny_dataset.enrollments[0].model_copy(update={"enrollment_id": "E99999"})
    with pytest.raises(DataError, match="Duplicate student"):
        replace(tiny_dataset, enrollments=(*tiny_dataset.enrollments, duplicate)).validate()
    with pytest.raises(DataError, match="Every enrollment"):
        replace(tiny_dataset, grade_records=tiny_dataset.grade_records[:-1]).validate()


@pytest.mark.parametrize("seed", [-1, 2**32, True, "20261103"])
def test_seed_is_bounded(seed):
    with pytest.raises(ValueError):
        generate_dataset(seed)


def test_dataset_boundary_revalidates_constructed_records(tiny_dataset):
    invalid = tiny_dataset.grade_records[0].model_copy(update={"score_cents": -1})
    with pytest.raises(DataError, match="invalid record"):
        replace(tiny_dataset, grade_records=(invalid, *tiny_dataset.grade_records[1:])).validate()
