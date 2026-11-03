"""Small SQLite repository. All user filters are values in fixed, bound queries."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

import pandas as pd

from copilot.models import ClassRecord, CourseRecord, DataError, Dataset, SCHEMA_VERSION, TABLE_NAMES
from copilot.schemas import Catalog, DatasetInfo, Scope

JOIN = """FROM enrollments e JOIN students s ON s.student_id=e.student_id
          JOIN classes c ON c.class_id=s.class_id JOIN courses cr ON cr.course_id=e.course_id
          JOIN grade_records g ON g.enrollment_id=e.enrollment_id"""
WHERE = """WHERE e.term=:term AND (:grade_level IS NULL OR c.grade_level=:grade_level)
           AND (:class_id IS NULL OR c.class_id=:class_id)
           AND (:course_id IS NULL OR e.course_id=:course_id)"""
COLUMNS = ("student_id", "class_id", "grade_level", "course_id", "term", "credits", "status", "score_cents")
COUNTS_SQL = """SELECT COUNT(*) enrollment_count, COUNT(DISTINCT s.student_id) student_count,
    SUM(CASE WHEN g.status='graded' THEN 1 ELSE 0 END) graded_count,
    SUM(CASE WHEN g.status='missing' THEN 1 ELSE 0 END) missing_count,
    SUM(CASE WHEN g.status='exempt' THEN 1 ELSE 0 END) exempt_count,
    SUM(CASE WHEN g.score_cents>=6000 THEN 1 ELSE 0 END) passed_count,
    SUM(CASE WHEN g.score_cents<6000 THEN 1 ELSE 0 END) failed_count,
    COUNT(DISTINCT CASE WHEN g.score_cents<6000 THEN s.student_id END) failed_student_count,
    COALESCE(SUM(g.score_cents),0) sum_score_cents,
    COALESCE(SUM(g.score_cents*cr.credits),0) weighted_sum_score_cents,
    COALESCE(SUM(CASE WHEN g.status='graded' THEN cr.credits ELSE 0 END),0) graded_credits """


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))

    def has_dataset(self):
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM dataset_metadata").fetchone() is not None

    def replace(self, dataset: Dataset, *, source="uploaded_local_only", seed=None):
        dataset.validate()
        if source not in {"generated", "uploaded_local_only"}:
            raise ValueError("Unknown dataset source")
        if source == "uploaded_local_only" and seed is not None:
            raise ValueError("Imported datasets cannot claim a generator seed")
        version = dataset.version
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for table in reversed(TABLE_NAMES):
                    conn.execute(f"DELETE FROM {table}")  # Identifiers come only from program constants.
                for table, rows in dataset.payload().items():
                    columns = list(rows[0])
                    placeholders = ",".join("?" for _ in columns)
                    conn.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                                     [tuple(row[c] for c in columns) for row in rows])
                conn.execute("INSERT OR REPLACE INTO dataset_metadata VALUES (1,?,?,?,?)",
                             (version, SCHEMA_VERSION, source, seed))
                info = self._overview(conn)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                if isinstance(exc, sqlite3.IntegrityError):
                    raise DataError("Dataset violates a database constraint") from exc
                raise
        return info

    @staticmethod
    def metadata(conn):
        row = conn.execute("SELECT * FROM dataset_metadata WHERE singleton=1").fetchone()
        if row is None:
            raise DataError("No dataset has been activated", code="no_dataset", status=503)
        return dict(row)

    @staticmethod
    def check_access(metadata, *, expected_version=None, generated_only=False):
        if generated_only and metadata["source"] != "generated":
            raise DataError("Agent queries are restricted to generated synthetic data", code="agent_privacy_block", status=403)
        if expected_version is not None and metadata["dataset_version"] != expected_version:
            raise DataError("Dataset changed during this query; please retry", code="dataset_changed", status=409)

    @staticmethod
    def check_scope(conn, scope: Scope):
        for value, query, label in (
            (scope.course_id, "SELECT 1 FROM courses WHERE course_id=?", "course"),
            (scope.class_id, "SELECT 1 FROM classes WHERE class_id=?", "class"),
            (scope.grade_level, "SELECT 1 FROM classes WHERE grade_level=?", "grade"),
            (scope.term, "SELECT 1 FROM enrollments WHERE term=? LIMIT 1", "term"),
        ):
            if value is not None and conn.execute(query, (value,)).fetchone() is None:
                raise DataError(f"Unknown {label} in the active dataset", code="unknown_entity", status=404)

    def snapshot(self, scope: Scope, *, class_ids=(), expected_version=None, generated_only=False):
        with self.connect() as conn:
            conn.execute("BEGIN")
            metadata = self.metadata(conn)
            self.check_access(metadata, expected_version=expected_version, generated_only=generated_only)
            self.check_scope(conn, scope)
            for class_id in class_ids:
                if conn.execute("SELECT 1 FROM classes WHERE class_id=?", (class_id,)).fetchone() is None:
                    raise DataError("Unknown class in the active dataset", code="unknown_entity", status=404)
            rows = conn.execute("SELECT s.student_id,c.class_id,c.grade_level,e.course_id,e.term,cr.credits,g.status,g.score_cents "
                                + JOIN + " " + WHERE + " ORDER BY e.enrollment_id", scope.model_dump()).fetchall()
            frame = pd.DataFrame.from_records([tuple(r) for r in rows], columns=COLUMNS)
            frame["score_cents"] = frame["score_cents"].astype("Int64")
            frame["credits"] = frame["credits"].astype("int64")
            return metadata, frame

    def sql_counts(self, scope: Scope) -> dict:
        with self.connect() as conn:
            conn.execute("BEGIN")
            self.metadata(conn)
            self.check_scope(conn, scope)
            row = conn.execute(COUNTS_SQL + JOIN + " " + WHERE, scope.model_dump()).fetchone()
            return {key: int(row[key] or 0) for key in row.keys()}

    def overview(self, *, expected_version=None, generated_only=False):
        with self.connect() as conn:
            conn.execute("BEGIN")
            self.check_access(self.metadata(conn), expected_version=expected_version, generated_only=generated_only)
            return self._overview(conn)

    def _overview(self, conn):
        meta = self.metadata(conn)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLE_NAMES}
        by_grade = conn.execute("SELECT c.grade_level,COUNT(*) FROM students s JOIN classes c ON s.class_id=c.class_id GROUP BY c.grade_level").fetchall()
        loads = conn.execute("SELECT COUNT(e.enrollment_id) FROM students s LEFT JOIN enrollments e ON s.student_id=e.student_id GROUP BY s.student_id,e.term").fetchall()
        return DatasetInfo(dataset_version=meta["dataset_version"], schema_version=meta["schema_version"],
                           source=meta["source"], seed=meta["seed"], external_llm_allowed=meta["source"] == "generated",
                           counts=counts, students_by_grade={str(r[0]): r[1] for r in by_grade},
                           terms=[r[0] for r in conn.execute("SELECT DISTINCT term FROM enrollments ORDER BY term")],
                           minimum_courses_per_student=min((r[0] for r in loads), default=0),
                           maximum_courses_per_student=max((r[0] for r in loads), default=0))

    def catalog(self, *, expected_version=None, generated_only=False):
        with self.connect() as conn:
            conn.execute("BEGIN")
            meta = self.metadata(conn)
            self.check_access(meta, expected_version=expected_version, generated_only=generated_only)
            return Catalog(dataset_version=meta["dataset_version"],
                           classes=[ClassRecord(**dict(r)) for r in conn.execute("SELECT * FROM classes ORDER BY class_id")],
                           courses=[CourseRecord(**dict(r)) for r in conn.execute("SELECT * FROM courses ORDER BY course_id")])
