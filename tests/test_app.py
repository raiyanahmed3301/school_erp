import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from conftest import PW, login, token
from schoolerp.db import query


def school_today():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def post(c, url, data=None, page=None, **kw):
    data = dict(data or {}); data["_csrf"] = token(c, page or "/")
    return c.post(url, data=data, **kw)


# ---------------- authentication ----------------
def test_login_required(app):
    c = app.test_client()
    for u in ("/", "/students/", "/attendance/", "/fees/", "/staff/", "/reports/attendance"):
        r = c.get(u); assert r.status_code == 302 and "/login" in r.location

def test_login_ok_and_generic_error(app):
    c = app.test_client()
    assert login(c, "admin").status_code == 302
    c2 = app.test_client()
    a = login(c2, "admin", "wrong-password-1"); b = login(app.test_client(), "nobody", "wrong-password-1")
    assert a.status_code == b.status_code == 401
    assert re.sub(r'value="[^"]+"', "", a.get_data(as_text=True)) == re.sub(r'value="[^"]+"', "", b.get_data(as_text=True))

def test_lockout_after_failures(app):
    c = app.test_client()
    for _ in range(5): login(c, "t1", "bad-password-xx")
    assert login(c, "t1", PW).status_code == 429  # even the right password is refused during lockout

def test_open_redirect_blocked(app):
    c = app.test_client()
    r = c.post("/login?next=//evil.com", data={"username": "admin", "password": PW, "_csrf": token(c)})
    assert r.location == "/"
    c = app.test_client()
    r = c.post("/login?next=/students/", data={"username": "admin", "password": PW, "_csrf": token(c)})
    assert r.location == "/students/"

def test_logout_post_only_and_kills_session(admin):
    assert admin.get("/logout").status_code == 405
    post(admin, "/logout")
    assert admin.get("/").status_code == 302

def test_must_change_password_forced(app):
    from schoolerp.db import execute
    with app.app_context(): execute("UPDATE users SET must_change_password=1 WHERE username='t1'")
    c = app.test_client(); login(c, "t1")
    r = c.get("/students/"); assert r.status_code == 302 and "/account/password" in r.location

def test_password_change_policy_and_session_invalidation(app):
    c1, c2 = app.test_client(), app.test_client(); login(c1, "t1"); login(c2, "t1")
    r = post(c1, "/account/password", {"current": PW, "new": "short1", "confirm": "short1"}, page="/account/password")
    assert r.status_code == 400
    r = post(c1, "/account/password", {"current": PW, "new": "NewPassw0rd-ok", "confirm": "NewPassw0rd-ok"}, page="/account/password")
    assert r.status_code == 302
    assert c1.get("/").status_code == 200       # this session continues
    assert c2.get("/").status_code == 302       # other sessions were revoked

def test_deactivated_user_loses_access_immediately(app, admin, t1):
    assert t1.get("/").status_code == 200
    post(admin, "/staff/2/edit", {"full_name": "T1", "role": "teacher", "gender": "", "is_active": "0"}, page="/staff/")
    assert t1.get("/").status_code == 302


# ---------------- CSRF / headers ----------------
def test_csrf_required(admin):
    assert admin.post("/students/new", data={"full_name": "x"}).status_code == 400
    assert admin.post("/students/new", data={"full_name": "x", "_csrf": "wrong"}).status_code == 400

def test_cross_origin_post_rejected(admin):
    t = token(admin, "/students/new")
    r = admin.post("/students/new", data={"_csrf": t, "full_name": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 400

def test_security_headers(admin):
    r = admin.get("/")
    assert "script-src 'self'" in r.headers["Content-Security-Policy"] and "'unsafe-inline'" not in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"

def test_no_inline_script_or_style_in_pages(admin):
    for u in ("/", "/students/", "/students/1", "/classes", "/attendance/", "/attendance/1/" + school_today().isoformat(),
              "/fees/", "/staff/", "/courses", "/reports/attendance", "/reports/teachers", "/audit/", "/students/new", "/classes/new", "/fees/new"):
        r = admin.get(u); assert r.status_code == 200, u
        html = r.get_data(as_text=True)
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), u
        assert " style=" not in html and "onclick=" not in html, u


# ---------------- authorization ----------------
def test_teacher_cannot_access_admin_areas(t1):
    for u in ("/staff/", "/fees/", "/courses", "/audit/", "/students/new", "/classes/new", "/reports/teachers", "/staff/new"):
        assert t1.get(u).status_code == 403, u

def test_teacher_cannot_post_admin_actions(t1):
    assert post(t1, "/students/new", {"full_name": "x", "gender": "male"}).status_code == 403
    assert post(t1, "/fees/generate", {"period": "2026-10", "due_date": "2026-10-05"}).status_code == 403

def test_teacher_idor_students_and_classes(t1):
    assert t1.get("/students/1").status_code == 200
    assert t1.get("/students/2").status_code == 404       # other teacher's student
    assert t1.get("/classes/2").status_code == 404
    assert t1.get("/attendance/2/" + school_today().isoformat()).status_code == 404
    assert post(t1, "/students/2/progress", {"log_type": "sabaq", "entry_date": school_today().isoformat()}).status_code == 404
    html = t1.get("/students/").get_data(as_text=True)
    assert "Aisha" in html and "Yusuf" not in html

def test_teacher_cannot_see_guardian_contact_or_fees(t1, admin):
    h = t1.get("/students/1").get_data(as_text=True)
    assert "+911234" not in h and "Monthly fee" not in h
    assert "+911234" in admin.get("/students/1").get_data(as_text=True)

def test_teacher_report_scoped(t1, admin):
    d = school_today().isoformat()
    for c, cid in ((admin, 2),):
        post(c, f"/attendance/{cid}/{d}", {"session_status": "held", "status_2": "present"}, page=f"/attendance/{cid}/{d}")
    html = t1.get("/reports/attendance").get_data(as_text=True)
    assert "Yusuf" not in html


# ---------------- attendance ----------------
def test_mark_attendance_flow(t1, app):
    d = school_today().isoformat()
    r = post(t1, f"/attendance/1/{d}", {"session_status": "held", "status_1": "late", "remark_1": "Internet"}, page=f"/attendance/1/{d}")
    assert r.status_code == 302
    with app.app_context():
        a = query("SELECT status, remark FROM attendance")[0]
        assert (a["status"], a["remark"]) == ("late", "Internet")
    # edit -> upsert, not duplicate
    post(t1, f"/attendance/1/{d}", {"session_status": "held", "status_1": "absent"}, page=f"/attendance/1/{d}")
    with app.app_context():
        assert query("SELECT COUNT(*) c FROM attendance", one=True)["c"] == 1
        assert query("SELECT status FROM attendance", one=True)["status"] == "absent"
    # cancelling clears student marks
    post(t1, f"/attendance/1/{d}", {"session_status": "cancelled_teacher"}, page=f"/attendance/1/{d}")
    with app.app_context():
        assert query("SELECT COUNT(*) c FROM attendance", one=True)["c"] == 0

def test_attendance_validation(t1, app):
    d = school_today().isoformat()
    assert post(t1, f"/attendance/1/{d}", {"session_status": "held", "status_1": "hacked"}, page=f"/attendance/1/{d}").status_code == 400
    assert post(t1, f"/attendance/1/{d}", {"session_status": "bogus"}, page=f"/attendance/1/{d}").status_code == 400
    # a student from another class can't be smuggled in
    post(t1, f"/attendance/1/{d}", {"session_status": "held", "status_1": "present", "status_2": "present"}, page=f"/attendance/1/{d}")
    with app.app_context():
        assert [r["student_id"] for r in query("SELECT student_id FROM attendance")] == [1]

def test_future_and_old_dates(t1, admin):
    fut = (school_today() + timedelta(days=1)).isoformat()
    assert t1.get(f"/attendance/1/{fut}").status_code == 302
    old = (school_today() - timedelta(days=30)).isoformat()
    assert "locked for teachers" in t1.get(f"/attendance/1/{old}").get_data(as_text=True)
    assert post(t1, f"/attendance/1/{old}", {"session_status": "held", "status_1": "present"}, page=f"/attendance/1/{old}").status_code == 403
    assert post(admin, f"/attendance/1/{old}", {"session_status": "held", "status_1": "present"}, page=f"/attendance/1/{old}").status_code == 302
    assert t1.get("/attendance/1/not-a-date").status_code == 404

def test_report_math_and_csv_injection(admin, app):
    from schoolerp.db import execute
    with app.app_context():
        execute("UPDATE students SET full_name='=HYPERLINK(\"http://x\")' WHERE id=1")
    for i, st in enumerate(["present", "present", "late", "absent", "excused"]):
        d = (school_today() - timedelta(days=i)).isoformat()
        post(admin, f"/attendance/1/{d}", {"session_status": "held", "status_1": st}, page=f"/attendance/1/{d}")
    month = school_today().strftime("%Y-%m")
    csv = admin.get(f"/reports/attendance?month={month}&format=csv").get_data(as_text=True)
    assert "'=HYPERLINK" in csv and ",=HYPERLINK" not in csv
    rows = [l for l in csv.splitlines() if "QLC-0001" in l]
    if school_today().day >= 5:  # all 5 samples fall inside this month
        assert rows[0].endswith(",1,1,1,1,75") or ",75" in rows[0]   # (2 present + 1 late)/(2+1+1)=75%


# ---------------- injection / XSS ----------------
def test_sql_injection_search(admin):
    for payload in ("' OR 1=1 --", "%'; DROP TABLE students; --", "\\"):
        assert admin.get("/students/", query_string={"q": payload}).status_code == 200
    assert "Aisha" in admin.get("/students/").get_data(as_text=True)
    assert "Aisha" not in admin.get("/students/", query_string={"q": "' OR 1=1 --"}).get_data(as_text=True)
    assert "Aisha" not in admin.get("/students/", query_string={"q": "%"}).get_data(as_text=True)  # wildcard is escaped

def test_xss_escaped(admin):
    payload = "<script>alert(1)</script>"
    post(admin, "/students/new", {"full_name": payload, "gender": "male", "status": "active", "language": "English", "currency": "INR", "monthly_fee": "0"}, page="/students/new")
    html = admin.get("/students/").get_data(as_text=True)
    assert payload not in html and "&lt;script&gt;" in html

def test_meeting_link_must_be_https(admin):
    base = {"name": "X", "course_id": "1", "teacher_id": "2", "weekdays": "0", "start_time": "10:00", "duration_min": "30", "is_active": "1"}
    assert post(admin, "/classes/new", {**base, "meeting_link": "javascript:alert(1)"}, page="/classes/new").status_code == 400
    assert post(admin, "/classes/new", {**base, "meeting_link": "https://zoom.us/j/123"}, page="/classes/new").status_code == 302


# ---------------- admin features ----------------
def test_create_teacher_temp_password_flow(admin, app):
    r = post(admin, "/staff/new", {"username": "newt", "full_name": "New Teacher", "role": "teacher", "gender": "female"}, page="/staff/new")
    pw = re.search(r"<code>([^<]+)</code>", r.get_data(as_text=True).split("Temporary password</dt>")[1]).group(1)
    c = app.test_client(); login(c, "newt", pw)
    assert "/account/password" in c.get("/").location   # forced to change it

def test_cannot_lock_out_last_admin(admin):
    r = post(admin, "/staff/1/edit", {"full_name": "A", "role": "teacher", "gender": "", "is_active": "1"}, page="/staff/")
    assert r.status_code == 400

def test_student_crud_and_fees(admin, app):
    r = post(admin, "/students/new", {"full_name": "Zaid", "gender": "male", "status": "active", "language": "Urdu", "currency": "USD", "monthly_fee": "25.50"}, page="/students/new")
    assert r.status_code == 302
    with app.app_context():
        s = query("SELECT * FROM students WHERE full_name='Zaid'", one=True)
        assert s["student_code"].startswith("QLC-") and s["monthly_fee_cents"] == 2550
    r = post(admin, "/fees/generate", {"period": "2026-10", "due_date": "2026-10-05"}, page="/fees/")
    with app.app_context():
        assert query("SELECT COUNT(*) c FROM fees", one=True)["c"] == 1
    post(admin, "/fees/generate", {"period": "2026-10", "due_date": "2026-10-05"}, page="/fees/")
    with app.app_context():
        assert query("SELECT COUNT(*) c FROM fees", one=True)["c"] == 1   # idempotent
    post(admin, "/fees/1/pay", {"action": "paid", "method": "PayPal", "reference": "TX1"}, page="/fees/")
    with app.app_context():
        assert query("SELECT status FROM fees", one=True)["status"] == "paid"

def test_fee_payment_rejects_invalid_date_and_unknown_action(admin, app):
    from schoolerp.db import execute
    with app.app_context():
        execute("INSERT INTO fees (student_id, period, amount_cents, currency, due_date) VALUES (1, '2026-10', 2500, 'INR', '2026-10-10')")
    invalid_date = post(admin, "/fees/1/pay", {"action": "paid", "paid_on": "not-a-date", "method": "UPI"}, page="/fees/")
    assert invalid_date.status_code == 400
    future_date = (school_today() + timedelta(days=1)).isoformat()
    assert post(admin, "/fees/1/pay", {"action": "paid", "paid_on": future_date, "method": "UPI"}, page="/fees/").status_code == 400
    assert post(admin, "/fees/1/pay", {"action": "paid", "method": "Cryptocurrency"}, page="/fees/").status_code == 400
    unknown_action = post(admin, "/fees/1/pay", {"action": "delete"}, page="/fees/")
    assert unknown_action.status_code == 400
    with app.app_context():
        fee = query("SELECT status, paid_on FROM fees WHERE id=1", one=True)
        assert (fee["status"], fee["paid_on"]) == ("pending", None)

def test_audit_log_records(admin, app):
    post(admin, "/courses/save", {"name": "Test Course"}, page="/courses")
    with app.app_context():
        assert query("SELECT 1 FROM audit_log WHERE action='course_created'", one=True)
        assert query("SELECT 1 FROM audit_log WHERE action='login'", one=True)

def test_dashboard_shows_missing_attendance(t1):
    assert "Attendance not yet marked" in t1.get("/").get_data(as_text=True)

def test_dashboard_at_risk_students_are_scoped_to_teacher(t1, admin):
    for days_ago in range(3):
        d = (school_today() - timedelta(days=days_ago)).isoformat()
        post(admin, f"/attendance/2/{d}", {"session_status": "held", "status_2": "absent"}, page=f"/attendance/2/{d}")
    assert "Yusuf" in admin.get("/").get_data(as_text=True)
    assert "Yusuf" not in t1.get("/").get_data(as_text=True)

def test_error_pages_do_not_leak(admin):
    r = admin.get("/nope"); assert r.status_code == 404 and "Traceback" not in r.get_data(as_text=True)
