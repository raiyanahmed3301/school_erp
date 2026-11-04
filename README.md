# Quran Live Class – School ERP + LMS (v0.2)

A small, secure, self-hosted school management system for an online Islamic school.
Inspired by Frappe Education (students, attendance, scheduling, fees, reports) but trimmed to what
a live-class academy needs. **Attendance is the core feature.**

## What's in v0.1
| Area | Features |
|---|---|
| **Attendance** | Per-class, per-day sheet: Present / Late / Absent / Excused + remark; class status (held, cancelled by teacher, student no-show, holiday); "Mark all present"; make-up classes; teachers can edit only the last 7 days; dashboard warns about **classes whose attendance was never marked**; students with 3+ absences in 30 days |
| **Students** | Profile, guardian details, country/time zone, language, status (trial/active/paused/left), monthly fee, auto ID (QLC-0001) |
| **Classes** | Course, teacher, weekdays, time, duration, Zoom/Meet link, enrolments (history kept) |
| **Lesson progress** | Sabaq / Sabqi / Manzil, Nazra, Tajweed, Arabic, Tafsir… with from→to, grade, remarks |
| **Reports** | Monthly attendance % per student/class, teacher report (classes held, hours – for payroll), CSV export |
| **Fees** | Generate monthly fees in one click, mark paid (PayPal/UPI/bank…), waive, overdue tracking |
| **Staff** | Admin and Teacher roles; auto-generated one-time temporary passwords; deactivate instantly |
| **Activity log** | Who did what and when |

## NEW in v0.2 – Learning Management System (LMS) + Student Portal
| Who | What they can do |
|---|---|
| **Teacher / Admin** | **Online Courses** → build a course (modules → lessons: reading text, YouTube/Vimeo video, external link); auto-graded **multiple-choice quizzes**; **assignments** with grading + feedback; **announcements**; enrol one student or a whole class; **progress report** (+CSV); a setup checklist guides beginners step by step |
| **Student** | Own login → **My Learning**: continue where you left off, progress bars, quizzes (limited attempts), assignments, to-do list, announcements, **printable certificate** (public verification link `/verify/<code>`), **My Attendance** and lesson-progress log |
| **Admin** | Student page → **Create portal login** (temporary password shown once, forced change at first sign-in); disable/reset any time |

A certificate is issued automatically when a student finishes every lesson **and** passes every published quiz.

**Beginner's first 10 minutes:** sign in as admin → *Staff & Teachers* add a teacher → *Students* add a student → open the student → *Create portal login* → *Online Courses → New course* → add a module + lesson → *Settings / Publish* → *Enrol* the student → sign in as the student in a private window and click **Start learning**.

### Upgrading an existing v0.1 database
```bash
flask --app schoolerp migrate     # makes a backup copy (school.db.bak-DATE) first, then upgrades. Safe to re-run.
```

## Quick start (local)
```bash
python3 -m venv venv && source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
export SCHOOL_ERP_ENV=development                      # Windows: set SCHOOL_ERP_ENV=development
flask --app schoolerp init-db
flask --app schoolerp create-admin                     # asks for username + password (no default login exists)
flask --app schoolerp seed-demo                        # optional sample data (dev only)
flask --app schoolerp run                              # open http://127.0.0.1:5000
python -m pytest tests                                 # 52 tests incl. security + LMS tests
```
Set your school's time zone with `SCHOOL_TZ` (default `Asia/Kolkata`). Class times are in school time.

## Putting it online (production)
1. Use a small Linux VPS. Install Python, `pip install -r requirements.txt`.
2. **Do NOT set `SCHOOL_ERP_ENV`** (this turns on Secure cookies + HSTS, so you *must* serve over HTTPS).
3. Run: `BEHIND_PROXY=1 gunicorn -w 2 -b 127.0.0.1:8000 "schoolerp:create_app()"` (as a systemd service, non-root user).
4. Put **Caddy** or **nginx** in front for HTTPS (Caddy gets free certificates automatically):
   `erp.quranliveclass.com { reverse_proxy 127.0.0.1:8000 }`
5. Back up `instance/school.db` daily (copy it off the server). Keep `instance/` readable only by the app user.
6. Use a separate sub-domain (e.g. `erp.`), not your public WordPress site.

## Security design (what protects you)
- **Passwords**: scrypt hashing with salts; min 10 chars + policy; no default accounts; temp passwords shown once and must be changed.
- **Login protection**: generic error messages, constant-time check for unknown users, lockout (5 failures/15 min per user, 25 per IP).
- **Sessions**: HttpOnly + Secure + SameSite cookies (`__Host-` prefix in production), 30-min idle and 8-hour absolute timeout, session reset at login, and **all sessions revoked** on password change / deactivation / role change.
- **CSRF**: token on every POST + Origin check; logout is POST-only.
- **Injection**: every SQL query is parameterised; LIKE wildcards escaped; Jinja auto-escapes all output (XSS); strict **Content-Security-Policy** with no inline scripts/styles.
- **Access control**: server-side role checks on every route; teachers can only open *their own* classes/students (no ID-guessing); teachers can't see guardian phone/email or fees.
- **Input validation**: whitelists for every dropdown, date/time/money parsing, only `https://` meeting links, length caps, no future-dated attendance.
- **Data integrity**: foreign keys on, unique constraints, attendance saved in one transaction.
- **CSV export** neutralises spreadsheet-formula injection. Errors never show stack traces. Pages are `no-store` cached.
- **Audit trail** of logins, edits, attendance saves, payments.

Known limits (by design for v0.1): no 2-factor login yet; SQLite = single server (fine for hundreds of students).

## LMS security notes
- Students are **deny-by-default**: a student login can reach only `/learn` pages (enforced centrally), never staff screens.
- Students see only **published** courses they're **enrolled** in; every lesson/quiz/attempt/assignment is ownership-checked (no ID-guessing).
- Quiz answers are never sent to the browser before submission; options are validated per question; attempt limits are atomic.
- Lesson text is plain text, always escaped (no HTML injection). Videos: only YouTube/Vimeo, embedded via a CSP `frame-src` allow-list.
- Certificates use unguessable 64-bit codes; the public verify page shows only name, course, date.
- Database upgrades run in a transaction, with automatic backup and a foreign-key integrity check.

## Scaling notes
SQLite (WAL) comfortably serves a few hundred students/teachers on one small server. When you outgrow it: move to PostgreSQL, add Redis for rate-limits/sessions, and run several gunicorn workers behind Caddy/nginx. See `MASTER_PROMPT.md` for the full production/SaaS plan.

## Roadmap (suggested next steps)
1. Parent accounts (one login for several children) + invoices in the portal; file uploads (PDF/audio recitation) with virus scanning
2. WhatsApp/email reminders for absentees and fee dues
3. Admissions / free-trial pipeline (enquiry → trial → active)
4. Two-factor login for admins
5. Teacher-wise salary sheet, certificates, exam/Hifz milestone tracking
6. Auto-create Zoom links; recurring-session calendar view
