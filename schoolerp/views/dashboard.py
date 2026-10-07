from datetime import timedelta

from flask import Blueprint, g, render_template

from ..db import query
from ..security import is_admin, login_required
from ..utils import day_set, school_now

bp = Blueprint("dashboard", __name__)


def classes_for_user():
    if is_admin():
        return query("""SELECT c.*, co.name course_name, u.full_name teacher_name FROM classes c
                        JOIN courses co ON co.id=c.course_id JOIN users u ON u.id=c.teacher_id
                        WHERE c.is_active=1 ORDER BY c.start_time, c.name""")
    return query(
        """SELECT c.*, co.name course_name, u.full_name teacher_name FROM classes c
                    JOIN courses co ON co.id=c.course_id JOIN users u ON u.id=c.teacher_id
                    WHERE c.is_active=1 AND c.teacher_id=? ORDER BY c.start_time, c.name""",
        (g.user["id"],),
    )


def class_runs_on(c, d):
    if d.weekday() not in day_set(c["weekdays"]):
        return False
    if c["start_date"] and d.isoformat() < c["start_date"]:
        return False
    return not c["end_date"] or d.isoformat() <= c["end_date"]


@bp.route("/")
@login_required
def index():
    now = school_now()
    today = now.date()
    classes = classes_for_user()
    today_sessions = {
        r["class_id"]: r
        for r in query(
            "SELECT * FROM sessions WHERE session_date=?", (today.isoformat(),)
        )
    }
    todays = [c for c in classes if class_runs_on(c, today)]

    # Scheduled classes in the last 7 days whose attendance was never marked
    week_ago = (today - timedelta(days=7)).isoformat()
    done = {
        (r["class_id"], r["session_date"])
        for r in query(
            "SELECT class_id, session_date FROM sessions WHERE session_date>=?",
            (week_ago,),
        )
    }
    missing = []
    for i in range(8):
        d = today - timedelta(days=i)
        for c in classes:
            if class_runs_on(c, d) and (c["id"], d.isoformat()) not in done:
                if i == 0 and c["start_time"] > now.strftime("%H:%M"):
                    continue  # class hasn't started yet
                missing.append((d, c))

    since = (today - timedelta(days=30)).isoformat()
    if is_admin():
        at_risk = query(
            """SELECT s.id, s.full_name, SUM(a.status='absent') absences
                           FROM attendance a JOIN sessions se ON se.id=a.session_id AND se.status='held'
                           JOIN classes c ON c.id=se.class_id JOIN students s ON s.id=a.student_id
                           WHERE se.session_date>=? AND s.status IN ('trial','active')
                           GROUP BY s.id HAVING absences>=3 ORDER BY absences DESC LIMIT 10""",
            (since,),
        )
    else:
        at_risk = query(
            """SELECT s.id, s.full_name, SUM(a.status='absent') absences
                           FROM attendance a JOIN sessions se ON se.id=a.session_id AND se.status='held'
                           JOIN classes c ON c.id=se.class_id JOIN students s ON s.id=a.student_id
                           WHERE se.session_date>=? AND s.status IN ('trial','active') AND c.teacher_id=?
                           GROUP BY s.id HAVING absences>=3 ORDER BY absences DESC LIMIT 10""",
            (since, g.user["id"]),
        )

    if is_admin():
        n_students = query(
            "SELECT COUNT(*) c FROM students WHERE status='active'", one=True
        )["c"]
        n_trial = query(
            "SELECT COUNT(*) c FROM students WHERE status='trial'", one=True
        )["c"]
        overdue = query(
            "SELECT COUNT(*) c FROM fees WHERE status='pending' AND due_date<?",
            (today.isoformat(),),
            one=True,
        )["c"]
    else:
        n_students = query(
            """SELECT COUNT(DISTINCT e.student_id) c FROM enrollments e JOIN classes c ON c.id=e.class_id
                              WHERE c.teacher_id=? AND e.ended_on IS NULL""",
            (g.user["id"],),
            one=True,
        )["c"]
        n_trial = overdue = None
    return render_template(
        "dashboard.html",
        todays=todays,
        today_sessions=today_sessions,
        today=today,
        missing=missing,
        at_risk=at_risk,
        n_students=n_students,
        n_trial=n_trial,
        overdue=overdue,
    )
