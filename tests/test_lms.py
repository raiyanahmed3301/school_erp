import re
import sqlite3
from pathlib import Path

import pytest
from conftest import PW, login, token
from schoolerp import create_app
from schoolerp.db import execute, migrate, query

SPW = "Student-Pass-99x"


def post(c, url, data=None, page="/"):
    d = dict(data or {}); d["_csrf"] = token(c, page)
    return c.post(url, data=d)


def build_course(app, owner=2, status="published"):
    with app.app_context():
        cid = execute("INSERT INTO lms_courses (title, owner_id, status) VALUES ('Tajweed 101',?,?)", (owner, status))
        mid = execute("INSERT INTO lms_modules (course_id,title,position) VALUES (?,'M1',1)", (cid,))
        l1 = execute("INSERT INTO lms_lessons (module_id,title,kind,body,position) VALUES (?,'L1','text','Bismillah',1)", (mid,))
        l2 = execute("INSERT INTO lms_lessons (module_id,title,kind,body,position) VALUES (?,'L2','text','Next',2)", (mid,))
        return cid, mid, l1, l2


def build_quiz(app, cid, published=1):
    with app.app_context():
        zid = execute("INSERT INTO lms_quizzes (course_id,title,pass_percent,max_attempts,is_published) VALUES (?,'Q1',50,2,?)", (cid, published))
        qs = []
        for n in (1, 2):
            q = execute("INSERT INTO lms_questions (quiz_id,text,position) VALUES (?,?,?)", (zid, f"Question {n}", n))
            good = execute("INSERT INTO lms_options (question_id,text,is_correct,position) VALUES (?,?,1,1)", (q, f"right{n}"))
            bad = execute("INSERT INTO lms_options (question_id,text,is_correct,position) VALUES (?,?,0,2)", (q, f"wrong{n}"))
            qs.append((q, good, bad))
        return zid, qs


def student_client(app, admin, sid):
    html = post(admin, f"/students/{sid}/portal", page=f"/students/{sid}").get_data(as_text=True)
    user = re.search(r"Username</dt><dd><code>([^<]+)</code>", html).group(1)
    pw = re.search(r"Temporary password</dt><dd><code>([^<]+)</code>", html).group(1)
    c = app.test_client(); login(c, user, pw)
    post(c, "/account/password", {"current": pw, "new": SPW, "confirm": SPW}, page="/account/password")
    return c


def enroll(app, cid, sid):
    with app.app_context():
        execute("INSERT INTO lms_enrollments (course_id,student_id,enrolled_on) VALUES (?,?,'2026-01-01')", (cid, sid))


# ---------------- migration ----------------
def test_upgrade_from_v01_keeps_data(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript((Path(__file__).parent.parent / "schoolerp" / "schema.sql").read_text())
    con.execute("INSERT INTO users (username, full_name, role, password_hash) VALUES ('boss','Boss','admin','x')")
    con.execute("INSERT INTO students (id, student_code, full_name, gender) VALUES (1,'QLC-0001','Aisha','female')")
    con.execute("INSERT INTO classes (name, course_id, teacher_id, weekdays, start_time, duration_min) VALUES ('C',1,1,'0','10:00',30)")
    con.execute("INSERT INTO enrollments (class_id, student_id, enrolled_on) VALUES (1,1,'2026-01-01')")
    con.commit(); con.close()
    app = create_app({"TESTING": True, "DATABASE": str(path), "SECRET_KEY": "t"})
    with app.app_context():
        assert migrate() == ["002_lms.sql"]
        assert query("SELECT username FROM users")[0]["username"] == "boss"
        assert query("SELECT COUNT(*) c FROM enrollments", one=True)["c"] == 1
        execute("INSERT INTO users (username, full_name, role, password_hash) VALUES ('stu','S','student','x')")  # role now allowed
        assert migrate() == []  # idempotent
    assert list(tmp_path.glob("old.db.bak-*"))  # backup made before upgrading


# ---------------- student isolation ----------------
def test_student_locked_out_of_staff_areas(app, admin):
    s = student_client(app, admin, 1)
    for u in ("/students/", "/students/1", "/attendance/", "/classes", "/lms/", "/reports/attendance", "/fees/", "/staff/", "/audit/", "/courses", "/lms/courses/new"):
        assert s.get(u).status_code == 403, u
    r = s.get("/"); assert r.status_code == 302 and "/learn" in r.location
    assert s.get("/learn/").status_code == 200

def test_staff_cannot_use_student_portal(admin, t1):
    assert admin.get("/learn/").status_code == 403
    assert t1.get("/learn/").status_code == 403

def test_student_accounts_hidden_from_staff_screens(app, admin):
    student_client(app, admin, 1)
    assert "qlc-0001" not in admin.get("/staff/").get_data(as_text=True)
    uid = query_one(app, "SELECT id FROM users WHERE username='qlc-0001'")["id"]
    assert admin.get(f"/staff/{uid}/edit").status_code == 404
    assert post(admin, f"/staff/{uid}/reset", page="/staff/").status_code == 404

def query_one(app, sql):
    with app.app_context():
        return query(sql, one=True)

def test_portal_login_deactivate(app, admin):
    s = student_client(app, admin, 1)
    assert s.get("/learn/").status_code == 200
    post(admin, "/students/1/portal/toggle", page="/students/1")
    assert s.get("/learn/").status_code == 302  # session killed at once


# ---------------- authorization on course management ----------------
def test_teacher_only_manages_own_courses(app, t1, admin):
    cid, mid, l1, _ = build_course(app, owner=3)  # owned by t2
    for u in (f"/lms/courses/{cid}", f"/lms/courses/{cid}/edit", f"/lms/modules/{mid}/lessons/new", f"/lms/lessons/{l1}/edit", f"/lms/courses/{cid}/progress"):
        assert t1.get(u).status_code == 404, u
    assert post(t1, f"/lms/lessons/{l1}/delete", page="/lms/").status_code == 404
    assert post(t1, f"/lms/courses/{cid}/enroll", {"student_id": "1"}, page="/lms/").status_code == 404
    assert admin.get(f"/lms/courses/{cid}").status_code == 200
    assert "Tajweed 101" not in t1.get("/lms/").get_data(as_text=True)

def test_teacher_can_only_enrol_own_students(app, t1):
    cid, *_ = build_course(app, owner=2)
    post(t1, f"/lms/courses/{cid}/enroll", {"student_id": "2"}, page="/lms/")   # student 2 belongs to t2's class
    assert query_one(app, "SELECT COUNT(*) c FROM lms_enrollments")["c"] == 0
    post(t1, f"/lms/courses/{cid}/enroll", {"student_id": "1"}, page="/lms/")
    assert query_one(app, "SELECT COUNT(*) c FROM lms_enrollments")["c"] == 1


# ---------------- course building ----------------
def test_course_builder_flow(t1, app):
    r = post(t1, "/lms/courses/new", {"title": "Hifz Basics", "level": "beginner"}, page="/lms/")
    assert r.status_code == 302
    cid = query_one(app, "SELECT id FROM lms_courses")["id"]
    # cannot publish without lessons
    assert post(t1, f"/lms/courses/{cid}/edit", {"title": "Hifz Basics", "level": "beginner", "status": "published"}, page="/lms/").status_code == 400
    post(t1, f"/lms/courses/{cid}/modules", {"title": "Week 1"}, page="/lms/")
    mid = query_one(app, "SELECT id FROM lms_modules")["id"]
    r = post(t1, f"/lms/modules/{mid}/lessons/new", {"title": "Alif", "kind": "text", "body": "Learn Alif", "duration_min": "10"}, page="/lms/")
    assert r.status_code == 302
    assert post(t1, f"/lms/courses/{cid}/edit", {"title": "Hifz Basics", "level": "beginner", "status": "published"}, page="/lms/").status_code == 302
    assert query_one(app, "SELECT status FROM lms_courses")["status"] == "published"

def test_lesson_validation_and_link_safety(t1, app):
    cid, mid, *_ = build_course(app)
    def add(**kw):
        return post(t1, f"/lms/modules/{mid}/lessons/new", {"title": "X", "duration_min": "5", **kw}, page="/lms/")
    assert add(kind="link", url="javascript:alert(1)").status_code == 400
    assert add(kind="link", url="http://insecure.example").status_code == 400
    assert add(kind="video", url="https://evil.example/video").status_code == 400
    assert add(kind="video", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ").status_code == 302
    assert add(kind="link", url="https://example.com/doc").status_code == 302
    assert add(kind="text", body="").status_code == 400

def test_reorder_modules_and_lessons(t1, app):
    cid, mid, l1, l2 = build_course(app)
    post(t1, f"/lms/lessons/{l2}/move", {"dir": "up"}, page="/lms/")
    pos = {r["id"]: r["position"] for r in query_rows(app, "SELECT id, position FROM lms_lessons")}
    assert pos[l2] < pos[l1]

def query_rows(app, sql):
    with app.app_context():
        return query(sql)


# ---------------- the student learning journey ----------------
def test_full_journey_to_certificate(app, admin):
    cid, mid, l1, l2 = build_course(app)
    zid, qs = build_quiz(app, cid)
    enroll(app, cid, 1)
    s = student_client(app, admin, 1)
    assert "Tajweed 101" in s.get("/learn/").get_data(as_text=True)
    assert s.get(f"/learn/lesson/{l1}").status_code == 200
    r = post(s, f"/learn/lesson/{l1}/complete", page="/learn/"); assert r.status_code == 302
    r = post(s, f"/learn/lesson/{l2}/complete", page="/learn/")
    assert s.get(f"/learn/certificate/{cid}").status_code == 302           # quiz still outstanding
    # fail first attempt (both wrong), pass second
    data = {f"q_{q}": bad for q, good, bad in qs}
    r = post(s, f"/learn/quiz/{zid}", data, page=f"/learn/quiz/{zid}"); assert r.status_code == 302
    assert "Not passed" in s.get(r.location).get_data(as_text=True)
    data = {f"q_{q}": good for q, good, bad in qs}
    r = post(s, f"/learn/quiz/{zid}", data, page=f"/learn/quiz/{zid}")
    assert "Passed" in s.get(r.location).get_data(as_text=True)
    cert = s.get(f"/learn/certificate/{cid}")
    assert cert.status_code == 200 and "Certificate of Completion" in cert.get_data(as_text=True)
    code = query_one(app, "SELECT code FROM lms_certificates")["code"]
    anon = app.test_client()
    v = anon.get(f"/verify/{code}"); assert v.status_code == 200 and "Valid certificate" in v.get_data(as_text=True)
    assert anon.get("/verify/0000000000000000").status_code == 404
    assert anon.get("/verify/../../etc").status_code == 404

def test_quiz_attempt_limit_and_cross_question_option(app, admin):
    cid, *_ = build_course(app); zid, qs = build_quiz(app, cid); enroll(app, cid, 1)
    s = student_client(app, admin, 1)
    page = f"/learn/quiz/{zid}"
    # smuggle q2's correct option into q1 -> must count as unanswered, not correct
    r = post(s, page, {f"q_{qs[0][0]}": qs[1][1], f"q_{qs[1][0]}": qs[1][1]}, page=page)
    at = query_one(app, "SELECT * FROM lms_attempts")
    assert at["score"] == 1  # only q2 is right
    post(s, page, {}, page=page)                                 # attempt 2 (max_attempts = 2)
    post(s, page, {}, page=page)                                 # attempt 3 must be refused
    assert query_one(app, "SELECT COUNT(*) c FROM lms_attempts")["c"] == 2

def test_quiz_page_does_not_leak_answers(app, admin):
    cid, *_ = build_course(app); zid, qs = build_quiz(app, cid); enroll(app, cid, 1)
    s = student_client(app, admin, 1)
    html = s.get(f"/learn/quiz/{zid}").get_data(as_text=True)
    assert "right1" in html and "wrong1" in html          # options are shown…
    assert "is_correct" not in html and 'class="opt right"' not in html and "✓" not in html  # …but never marked

def test_student_cannot_access_other_students_data(app, admin):
    cid, mid, l1, _ = build_course(app); zid, qs = build_quiz(app, cid)
    enroll(app, cid, 1)
    s1 = student_client(app, admin, 1); s2 = student_client(app, admin, 2)
    assert s2.get(f"/learn/course/{cid}").status_code == 404        # not enrolled
    assert s2.get(f"/learn/lesson/{l1}").status_code == 404
    assert s2.get(f"/learn/quiz/{zid}").status_code == 404
    assert post(s2, f"/learn/lesson/{l1}/complete", page="/learn/").status_code == 404
    r = post(s1, f"/learn/quiz/{zid}", {f"q_{qs[0][0]}": qs[0][1]}, page=f"/learn/quiz/{zid}")
    assert s2.get(r.location).status_code == 404                     # someone else's attempt

def test_draft_course_invisible_to_students(app, admin):
    cid, mid, l1, _ = build_course(app, status="draft"); enroll(app, cid, 1)
    s = student_client(app, admin, 1)
    assert s.get(f"/learn/course/{cid}").status_code == 404 and s.get(f"/learn/lesson/{l1}").status_code == 404
    assert "Tajweed 101" not in s.get("/learn/").get_data(as_text=True)

def test_unpublished_quiz_hidden(app, admin):
    cid, *_ = build_course(app); zid, _ = build_quiz(app, cid, published=0); enroll(app, cid, 1)
    assert student_client(app, admin, 1).get(f"/learn/quiz/{zid}").status_code == 404


# ---------------- assignments ----------------
def test_assignment_submit_grade_lock(app, admin, t1):
    cid, *_ = build_course(app); enroll(app, cid, 1)
    post(t1, f"/lms/courses/{cid}/assignments/new", {"title": "Recite Fatiha", "instructions": "Record notes", "max_points": "10"}, page="/lms/")
    aid = query_one(app, "SELECT id FROM lms_assignments")["id"]
    s = student_client(app, admin, 1)
    post(s, f"/learn/assignment/{aid}", {"body": "<b>my answer</b>"}, page=f"/learn/assignment/{aid}")
    post(s, f"/learn/assignment/{aid}", {"body": "edited answer"}, page=f"/learn/assignment/{aid}")   # edit before grading
    sub = query_one(app, "SELECT * FROM lms_submissions"); assert sub["body"] == "edited answer"
    assert "<b>" not in t1.get(f"/lms/submissions/{sub['id']}").get_data(as_text=True)
    assert post(t1, f"/lms/submissions/{sub['id']}", {"points": "99"}, page="/lms/").status_code == 400   # > max
    assert post(t1, f"/lms/submissions/{sub['id']}", {"points": "8", "feedback": "Good"}, page="/lms/").status_code == 302
    post(s, f"/learn/assignment/{aid}", {"body": "sneaky change"}, page=f"/learn/assignment/{aid}")
    assert query_one(app, "SELECT body FROM lms_submissions")["body"] == "edited answer"                 # locked once graded
    assert "8 / 10" in s.get(f"/learn/assignment/{aid}").get_data(as_text=True)

def test_other_teacher_cannot_grade(app, t1):
    cid, *_ = build_course(app, owner=3); enroll(app, cid, 2)
    with app.app_context():
        aid = execute("INSERT INTO lms_assignments (course_id,title) VALUES (?,'A')", (cid,))
        sid = execute("INSERT INTO lms_submissions (assignment_id,student_id,body,submitted_at) VALUES (?,2,'x','2026-01-01 10:00')", (aid,))
    assert t1.get(f"/lms/submissions/{sid}").status_code == 404
    assert post(t1, f"/lms/submissions/{sid}", {"points": "1"}, page="/lms/").status_code == 404


# ---------------- content safety ----------------
def test_lesson_text_is_escaped_and_video_embed_is_allowlisted(app, admin, t1):
    cid, mid, *_ = build_course(app); enroll(app, cid, 1)
    post(t1, f"/lms/modules/{mid}/lessons/new", {"title": "Evil <img src=x onerror=alert(1)>", "kind": "text",
         "body": "<script>alert(1)</script>\n\nسلام", "duration_min": "1"}, page="/lms/")
    post(t1, f"/lms/modules/{mid}/lessons/new", {"title": "Vid", "kind": "video", "url": "https://youtu.be/dQw4w9WgXcQ?x=1", "duration_min": "1"}, page="/lms/")
    s = student_client(app, admin, 1)
    ids = [r["id"] for r in query_rows(app, "SELECT id FROM lms_lessons ORDER BY id")]
    html = s.get(f"/learn/lesson/{ids[2]}").get_data(as_text=True)
    assert "<script>alert" not in html and "&lt;script&gt;" in html and "<img src=x" not in html and 'dir="auto"' in html
    vid = s.get(f"/learn/lesson/{ids[3]}")
    assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"' in vid.get_data(as_text=True)
    csp = vid.headers["Content-Security-Policy"]
    assert "frame-src https://www.youtube-nocookie.com https://player.vimeo.com" in csp and "'unsafe-inline'" not in csp

def test_no_inline_js_css_on_lms_pages(app, admin):
    cid, mid, l1, _ = build_course(app); zid, _q = build_quiz(app, cid); enroll(app, cid, 1)
    with app.app_context():
        aid = execute("INSERT INTO lms_assignments (course_id,title) VALUES (?,'A')", (cid,))
    pages = ["/lms/", "/lms/courses/new", f"/lms/courses/{cid}", f"/lms/courses/{cid}/edit", f"/lms/courses/{cid}/progress",
             f"/lms/modules/{mid}/lessons/new", f"/lms/lessons/{l1}/edit", f"/lms/quizzes/{zid}", f"/lms/quizzes/{zid}/results",
             f"/lms/courses/{cid}/assignments/new", f"/lms/assignments/{aid}/submissions", "/students/1"]
    for u in pages:
        r = admin.get(u); assert r.status_code == 200, u
        h = r.get_data(as_text=True)
        assert not re.search(r"<script(?![^>]*\bsrc=)", h) and " style=" not in h and "onclick=" not in h, u
    s = student_client(app, admin, 1)
    for u in ("/learn/", f"/learn/course/{cid}", f"/learn/lesson/{l1}", f"/learn/quiz/{zid}", f"/learn/assignment/{aid}", "/learn/attendance"):
        r = s.get(u); assert r.status_code == 200, u
        h = r.get_data(as_text=True)
        assert not re.search(r"<script(?![^>]*\bsrc=)", h) and " style=" not in h and "onclick=" not in h, u


# ---------------- reports ----------------
def test_progress_report_and_csv_injection(app, admin):
    cid, mid, l1, l2 = build_course(app); enroll(app, cid, 1)
    with app.app_context():
        execute("UPDATE students SET full_name='=cmd|calc' WHERE id=1")
    s = student_client(app, admin, 1)
    post(s, f"/learn/lesson/{l1}/complete", page="/learn/")
    csv = admin.get(f"/lms/courses/{cid}/progress?format=csv").get_data(as_text=True)
    assert "'=cmd|calc" in csv and ",=cmd" not in csv
    assert ",1,2," in csv   # 1 of 2 lessons done

def test_csrf_on_lms_posts(app, admin, t1):
    cid, *_ = build_course(app)
    assert t1.post(f"/lms/courses/{cid}/modules", data={"title": "x"}).status_code == 400
    s = student_client(app, admin, 1); enroll(app, cid, 1)
    assert s.post(f"/learn/lesson/1/complete", data={}).status_code == 400
