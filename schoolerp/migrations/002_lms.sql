-- v0.2: LMS + student portal accounts.  Runs once, inside a transaction.
PRAGMA foreign_keys = OFF;
BEGIN;

-- 1) allow role 'student' (SQLite can't alter a CHECK, so rebuild the table)
CREATE TABLE users_new (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  full_name TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK (role IN ('admin','teacher','student')),
  gender TEXT NOT NULL DEFAULT '' CHECK (gender IN ('','male','female')),
  password_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  must_change_password INTEGER NOT NULL DEFAULT 1,
  token_version INTEGER NOT NULL DEFAULT 0,
  last_login TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO users_new (id, username, full_name, email, role, gender, password_hash, is_active, must_change_password, token_version, last_login, created_at)
  SELECT id, username, full_name, email, role, gender, password_hash, is_active, must_change_password, token_version, last_login, created_at FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

-- 2) link a student record to its portal login
ALTER TABLE students ADD COLUMN user_id INTEGER REFERENCES users(id);
CREATE UNIQUE INDEX idx_students_user ON students(user_id) WHERE user_id IS NOT NULL;

-- 3) LMS
CREATE TABLE lms_courses (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  level TEXT NOT NULL DEFAULT 'beginner' CHECK (level IN ('beginner','intermediate','advanced')),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  owner_id INTEGER NOT NULL REFERENCES users(id),
  catalog_course_id INTEGER REFERENCES courses(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_lms_courses_owner ON lms_courses(owner_id);

CREATE TABLE lms_modules (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES lms_courses(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_lms_modules_course ON lms_modules(course_id, position);

CREATE TABLE lms_lessons (
  id INTEGER PRIMARY KEY,
  module_id INTEGER NOT NULL REFERENCES lms_modules(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'text' CHECK (kind IN ('text','video','link')),
  body TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  duration_min INTEGER NOT NULL DEFAULT 0 CHECK (duration_min BETWEEN 0 AND 600),
  position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_lms_lessons_module ON lms_lessons(module_id, position);

CREATE TABLE lms_enrollments (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES lms_courses(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES students(id),
  enrolled_on TEXT NOT NULL,
  completed_on TEXT,
  UNIQUE (course_id, student_id)
);
CREATE INDEX idx_lms_enrollments_student ON lms_enrollments(student_id);

CREATE TABLE lms_progress (
  student_id INTEGER NOT NULL REFERENCES students(id),
  lesson_id INTEGER NOT NULL REFERENCES lms_lessons(id) ON DELETE CASCADE,
  completed_at TEXT NOT NULL,
  PRIMARY KEY (student_id, lesson_id)
);
CREATE INDEX idx_lms_progress_lesson ON lms_progress(lesson_id);

CREATE TABLE lms_assignments (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES lms_courses(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  instructions TEXT NOT NULL DEFAULT '',
  due_date TEXT,
  max_points INTEGER NOT NULL DEFAULT 100 CHECK (max_points BETWEEN 1 AND 1000),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_lms_assignments_course ON lms_assignments(course_id);

CREATE TABLE lms_submissions (
  id INTEGER PRIMARY KEY,
  assignment_id INTEGER NOT NULL REFERENCES lms_assignments(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES students(id),
  body TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','graded')),
  points INTEGER,
  feedback TEXT NOT NULL DEFAULT '',
  graded_by INTEGER REFERENCES users(id),
  graded_at TEXT,
  UNIQUE (assignment_id, student_id)
);
CREATE INDEX idx_lms_submissions_status ON lms_submissions(status);

CREATE TABLE lms_quizzes (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES lms_courses(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  instructions TEXT NOT NULL DEFAULT '',
  pass_percent INTEGER NOT NULL DEFAULT 70 CHECK (pass_percent BETWEEN 1 AND 100),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
  is_published INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_lms_quizzes_course ON lms_quizzes(course_id);

CREATE TABLE lms_questions (
  id INTEGER PRIMARY KEY,
  quiz_id INTEGER NOT NULL REFERENCES lms_quizzes(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_lms_questions_quiz ON lms_questions(quiz_id, position);

CREATE TABLE lms_options (
  id INTEGER PRIMARY KEY,
  question_id INTEGER NOT NULL REFERENCES lms_questions(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  is_correct INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_lms_options_question ON lms_options(question_id);

CREATE TABLE lms_attempts (
  id INTEGER PRIMARY KEY,
  quiz_id INTEGER NOT NULL REFERENCES lms_quizzes(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES students(id),
  submitted_at TEXT NOT NULL,
  score INTEGER NOT NULL,
  total INTEGER NOT NULL,
  passed INTEGER NOT NULL
);
CREATE INDEX idx_lms_attempts_quiz_student ON lms_attempts(quiz_id, student_id);

CREATE TABLE lms_answers (
  attempt_id INTEGER NOT NULL REFERENCES lms_attempts(id) ON DELETE CASCADE,
  question_id INTEGER NOT NULL REFERENCES lms_questions(id) ON DELETE CASCADE,
  option_id INTEGER REFERENCES lms_options(id) ON DELETE SET NULL,
  PRIMARY KEY (attempt_id, question_id)
);

CREATE TABLE lms_announcements (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES lms_courses(id) ON DELETE CASCADE,
  author_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_lms_announcements_course ON lms_announcements(course_id, id);

CREATE TABLE lms_certificates (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES lms_courses(id),
  student_id INTEGER NOT NULL REFERENCES students(id),
  code TEXT NOT NULL UNIQUE,
  issued_on TEXT NOT NULL,
  UNIQUE (course_id, student_id)
);

COMMIT;
PRAGMA foreign_keys = ON;
