CREATE TABLE IF NOT EXISTS classes (
    class_id TEXT PRIMARY KEY NOT NULL,
    grade_level INTEGER NOT NULL CHECK (typeof(grade_level) = 'integer' AND grade_level BETWEEN 1 AND 3),
    class_label TEXT NOT NULL,
    major TEXT NOT NULL DEFAULT 'unspecified' CHECK (major IN ('computer_science','business_administration','information_systems','unspecified'))
);
CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY NOT NULL,
    class_id TEXT NOT NULL REFERENCES classes(class_id),
    display_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS courses (
    course_id TEXT PRIMARY KEY NOT NULL,
    course_name TEXT NOT NULL,
    grade_level INTEGER NOT NULL CHECK (typeof(grade_level) = 'integer' AND grade_level BETWEEN 1 AND 3),
    credits INTEGER NOT NULL CHECK (typeof(credits) = 'integer' AND credits BETWEEN 2 AND 4),
    course_type TEXT NOT NULL DEFAULT 'unspecified' CHECK (course_type IN ('general_required','professional_required','professional_elective','practical','unspecified'))
);
CREATE TABLE IF NOT EXISTS enrollments (
    enrollment_id TEXT PRIMARY KEY NOT NULL,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    term TEXT NOT NULL,
    UNIQUE (student_id, course_id, term)
);
CREATE TABLE IF NOT EXISTS grade_records (
    grade_record_id TEXT PRIMARY KEY NOT NULL,
    enrollment_id TEXT NOT NULL UNIQUE REFERENCES enrollments(enrollment_id),
    status TEXT NOT NULL CHECK (status IN ('graded', 'missing', 'exempt')),
    score_cents INTEGER,
    CHECK ((status = 'graded' AND score_cents IS NOT NULL AND typeof(score_cents) = 'integer' AND score_cents BETWEEN 0 AND 10000)
        OR (status IN ('missing', 'exempt') AND score_cents IS NULL))
);
CREATE TABLE IF NOT EXISTS dataset_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    dataset_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('generated', 'uploaded_local_only')),
    seed INTEGER
);
CREATE INDEX IF NOT EXISTS enrollment_course_term ON enrollments(course_id, term);
CREATE INDEX IF NOT EXISTS enrollment_student ON enrollments(student_id);
CREATE INDEX IF NOT EXISTS student_class ON students(class_id);
