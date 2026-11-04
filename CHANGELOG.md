# Changelog

## 0.2.0
- LMS: courses, modules, lessons (text/video/link), quizzes (auto-graded), assignments (graded), announcements, progress reports, certificates + public verification.
- Student role + portal (`/learn`), student attendance & lesson-progress view; admin creates/resets/disables portal logins.
- Database migrations (`flask --app schoolerp migrate`) with automatic backup and integrity check.
- Security: deny-by-default guard for student accounts, CSP frame-src allow-list for YouTube/Vimeo, atomic quiz-attempt limit.
- Tests: 52 (30 existing + 22 LMS/migration/security).

## 0.1.0
- Attendance, students, classes, lesson log, fees, reports, staff roles, audit log.
