PRAGMA journal_mode = WAL;

CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  full_name TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK (role IN ('admin','teacher')),
  gender TEXT NOT NULL DEFAULT '' CHECK (gender IN ('','male','female')),
  password_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  must_change_password INTEGER NOT NULL DEFAULT 1,
  token_version INTEGER NOT NULL DEFAULT 0,   -- bump to kill all sessions of a user
  last_login TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE login_attempts (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL,
  ip TEXT NOT NULL,
  success INTEGER NOT NULL,
  ts INTEGER NOT NULL
);
CREATE INDEX idx_login_attempts_ts ON login_attempts(ts);

CREATE TABLE courses (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE students (
  id INTEGER PRIMARY KEY,
  student_code TEXT UNIQUE,
  full_name TEXT NOT NULL,
  gender TEXT NOT NULL CHECK (gender IN ('male','female')),
  date_of_birth TEXT,
  guardian_name TEXT NOT NULL DEFAULT '',
  guardian_phone TEXT NOT NULL DEFAULT '',
  guardian_email TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT '',
  timezone TEXT NOT NULL DEFAULT '',
  language TEXT NOT NULL DEFAULT 'English',
  status TEXT NOT NULL DEFAULT 'trial' CHECK (status IN ('trial','active','paused','left')),
  joined_on TEXT,
  monthly_fee_cents INTEGER NOT NULL DEFAULT 0 CHECK (monthly_fee_cents >= 0),
  currency TEXT NOT NULL DEFAULT 'INR',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_students_status ON students(status);

CREATE TABLE classes (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  course_id INTEGER NOT NULL REFERENCES courses(id),
  teacher_id INTEGER NOT NULL REFERENCES users(id),
  weekdays TEXT NOT NULL,                     -- "0,2,4"  (Mon=0 ... Sun=6)
  start_time TEXT NOT NULL,                   -- HH:MM in school timezone
  duration_min INTEGER NOT NULL CHECK (duration_min BETWEEN 10 AND 240),
  meeting_link TEXT NOT NULL DEFAULT '',
  start_date TEXT,
  end_date TEXT,
  is_active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_classes_teacher ON classes(teacher_id);

CREATE TABLE enrollments (
  id INTEGER PRIMARY KEY,
  class_id INTEGER NOT NULL REFERENCES classes(id),
  student_id INTEGER NOT NULL REFERENCES students(id),
  enrolled_on TEXT NOT NULL,
  ended_on TEXT,                              -- NULL = currently enrolled
  UNIQUE (class_id, student_id)
);
CREATE INDEX idx_enrollments_student ON enrollments(student_id);

CREATE TABLE sessions (                       -- one row per class per date
  id INTEGER PRIMARY KEY,
  class_id INTEGER NOT NULL REFERENCES classes(id),
  session_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('held','cancelled_teacher','cancelled_student','holiday')),
  note TEXT NOT NULL DEFAULT '',
  marked_by INTEGER REFERENCES users(id),
  marked_at TEXT NOT NULL,
  UNIQUE (class_id, session_date)
);
CREATE INDEX idx_sessions_date ON sessions(session_date);

CREATE TABLE attendance (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES students(id),
  status TEXT NOT NULL CHECK (status IN ('present','late','absent','excused')),
  remark TEXT NOT NULL DEFAULT '',
  UNIQUE (session_id, student_id)
);
CREATE INDEX idx_attendance_student ON attendance(student_id);

CREATE TABLE progress (                       -- Quran-specific lesson log
  id INTEGER PRIMARY KEY,
  student_id INTEGER NOT NULL REFERENCES students(id),
  entry_date TEXT NOT NULL,
  log_type TEXT NOT NULL CHECK (log_type IN ('sabaq','sabqi','manzil','nazra','tajweed','arabic','tafsir','islamic','other')),
  from_ref TEXT NOT NULL DEFAULT '',
  to_ref TEXT NOT NULL DEFAULT '',
  grade TEXT NOT NULL DEFAULT '' CHECK (grade IN ('','excellent','good','average','needs_work')),
  remarks TEXT NOT NULL DEFAULT '',
  recorded_by INTEGER NOT NULL REFERENCES users(id)
);
CREATE INDEX idx_progress_student ON progress(student_id, entry_date);

CREATE TABLE fees (
  id INTEGER PRIMARY KEY,
  student_id INTEGER NOT NULL REFERENCES students(id),
  period TEXT NOT NULL,                       -- YYYY-MM
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  currency TEXT NOT NULL,
  due_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','paid','waived')),
  paid_on TEXT,
  method TEXT NOT NULL DEFAULT '',
  reference TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  UNIQUE (student_id, period)
);
CREATE INDEX idx_fees_status ON fees(status, due_date);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  user_id INTEGER,
  username TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  entity TEXT NOT NULL DEFAULT '',
  entity_id INTEGER,
  details TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_audit_ts ON audit_log(ts);

INSERT INTO courses (name, description) VALUES
 ('Quran Tajweed', 'Rules of correct recitation'),
 ('Quran Recitation (Nazra)', 'Reading the Quran with fluency'),
 ('Quran Memorization (Hifz)', 'Memorization with revision cycles'),
 ('Arabic Language', 'Reading, writing and understanding Arabic'),
 ('Quran Tafsir', 'Understanding the meaning of the Quran'),
 ('Complementary Course', 'Duas, Adhkaar and daily practice'),
 ('Islamic Studies', 'Fiqh, Seerah, manners and beliefs');
