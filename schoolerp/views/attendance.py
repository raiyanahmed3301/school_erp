from datetime import timedelta
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import get_db, query
from ..security import audit, get_class_or_404, is_admin, login_required
from ..utils import clean, day_set, parse_date, school_now, school_today
from .dashboard import class_runs_on, classes_for_user

bp = Blueprint("attendance", __name__, url_prefix="/attendance")

SESSION_STATUSES = {"held": "Class held", "cancelled_teacher": "Cancelled by teacher",
                    "cancelled_student": "Cancelled / no-show by student", "holiday": "Holiday / school closed"}
ATT_STATUSES = {"present": "Present", "late": "Late", "absent": "Absent", "excused": "Excused"}


@bp.route("/")
@login_required
def index():
    today = school_today()
    d = parse_date(request.args.get("date")) or today
    d = min(d, today)
    classes = classes_for_user()
    sess = {r["class_id"]: r for r in query("SELECT * FROM sessions WHERE session_date=?", (d.isoformat(),))}
    scheduled = [c for c in classes if class_runs_on(c, d)]
    ids = {c["id"] for c in scheduled}
    others = [c for c in classes if c["id"] not in ids]
    return render_template("attendance/index.html", d=d, today=today, scheduled=scheduled, others=others, sess=sess,
                           SESSION_STATUSES=SESSION_STATUSES, prev_day=d - timedelta(days=1),
                           next_day=(d + timedelta(days=1)) if d < today else None)


def _students_for(cls, d, session_id):
    return query("""SELECT s.id, s.full_name, s.student_code, a.status AS att_status, a.remark
                    FROM students s LEFT JOIN attendance a ON a.student_id=s.id AND a.session_id=:sid
                    WHERE s.id IN (SELECT student_id FROM enrollments WHERE class_id=:cid AND enrolled_on<=:d
                                   AND (ended_on IS NULL OR ended_on>=:d)) OR a.id IS NOT NULL
                    ORDER BY s.full_name""", {"sid": session_id or -1, "cid": cls["id"], "d": d.isoformat()})


@bp.route("/<int:class_id>/<day>", methods=["GET", "POST"])
@login_required
def mark(class_id, day):
    cls = get_class_or_404(class_id)
    d = parse_date(day) or abort(404)
    today = school_today()
    if d > today:
        flash("You cannot mark attendance for a future date.", "error")
        return redirect(url_for("attendance.index"))
    editable = is_admin() or (cls["is_active"] and d >= today - timedelta(days=current_backdate()))
    sess = query("SELECT * FROM sessions WHERE class_id=? AND session_date=?", (class_id, d.isoformat()), one=True)
    students = _students_for(cls, d, sess["id"] if sess else None)

    if request.method == "POST":
        if not editable:
            abort(403)
        status = request.form.get("session_status", "")
        if status not in SESSION_STATUSES:
            abort(400)
        note = clean(request.form.get("note"), 300)
        marks = {}
        if status == "held":
            for s in students:  # only students that belong to this class/session are accepted
                st = request.form.get(f"status_{s['id']}", "")
                if st not in ATT_STATUSES:
                    flash(f"Please choose a status for {s['full_name']}.", "error")
                    return render_template("attendance/mark.html", c=cls, d=d, sess=sess, students=students, editable=True,
                                           SESSION_STATUSES=SESSION_STATUSES, ATT_STATUSES=ATT_STATUSES), 400
                marks[s["id"]] = (st, clean(request.form.get(f"remark_{s['id']}"), 200))
        now = school_now().strftime("%Y-%m-%d %H:%M")
        db = get_db()
        with db:  # all-or-nothing
            db.execute("""INSERT INTO sessions (class_id, session_date, status, note, marked_by, marked_at) VALUES (?,?,?,?,?,?)
                          ON CONFLICT(class_id, session_date) DO UPDATE SET status=excluded.status, note=excluded.note,
                          marked_by=excluded.marked_by, marked_at=excluded.marked_at""",
                       (class_id, d.isoformat(), status, note, g.user["id"], now))
            sid = db.execute("SELECT id FROM sessions WHERE class_id=? AND session_date=?", (class_id, d.isoformat())).fetchone()["id"]
            if status == "held":
                for stu_id, (st, rem) in marks.items():
                    db.execute("""INSERT INTO attendance (session_id, student_id, status, remark) VALUES (?,?,?,?)
                                  ON CONFLICT(session_id, student_id) DO UPDATE SET status=excluded.status, remark=excluded.remark""",
                               (sid, stu_id, st, rem))
            else:
                db.execute("DELETE FROM attendance WHERE session_id=?", (sid,))
        audit("attendance_saved", "class", class_id, f"{d.isoformat()} {status}")
        flash(f"Attendance saved for {cls['name']} on {d.isoformat()}.", "success")
        return redirect(url_for("attendance.index", date=d.isoformat()))

    return render_template("attendance/mark.html", c=cls, d=d, sess=sess, students=students, editable=editable,
                           SESSION_STATUSES=SESSION_STATUSES, ATT_STATUSES=ATT_STATUSES)


def current_backdate():
    from flask import current_app
    return current_app.config["TEACHER_BACKDATE_DAYS"]
