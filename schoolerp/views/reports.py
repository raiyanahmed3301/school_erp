from flask import Blueprint, g, render_template, request

from ..db import query
from ..security import admin_required, is_admin, login_required
from ..utils import csv_response, month_bounds, school_today, valid_month
from .dashboard import classes_for_user

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _month():
    m = request.args.get("month", "")
    return m if valid_month(m) else school_today().strftime("%Y-%m")


@bp.route("/attendance")
@login_required
def attendance():
    month = _month()
    start, end = month_bounds(month)
    class_id = request.args.get("class_id", "")
    my_classes = classes_for_user()
    allowed = {str(c["id"]) for c in my_classes}
    sql = """SELECT s.id, s.student_code, s.full_name,
                    SUM(a.status='present') present, SUM(a.status='late') late,
                    SUM(a.status='absent') absent, SUM(a.status='excused') excused, COUNT(a.id) total
             FROM attendance a JOIN sessions se ON se.id=a.session_id AND se.status='held'
             JOIN students s ON s.id=a.student_id JOIN classes c ON c.id=se.class_id
             WHERE se.session_date>=? AND se.session_date<?"""
    args = [start, end]
    if class_id in allowed:
        sql += " AND c.id=?"; args.append(int(class_id))
    elif not is_admin():
        sql += " AND c.teacher_id=?"; args.append(g.user["id"])
    sql += " GROUP BY s.id ORDER BY s.full_name"
    rows = []
    for r in query(sql, args):
        counted = r["present"] + r["late"] + r["absent"]  # excused days don't count against the student
        pct = round(100 * (r["present"] + r["late"]) / counted) if counted else None
        rows.append({**dict(r), "pct": pct})
    if request.args.get("format") == "csv":
        return csv_response(f"attendance_{month}.csv",
                            ["Student ID", "Name", "Present", "Late", "Absent", "Excused", "Attendance %"],
                            [(r["student_code"], r["full_name"], r["present"], r["late"], r["absent"], r["excused"],
                              "" if r["pct"] is None else r["pct"]) for r in rows])
    return render_template("reports/attendance.html", rows=rows, month=month, classes=my_classes, class_id=class_id)


@bp.route("/teachers")
@admin_required
def teachers():
    month = _month()
    start, end = month_bounds(month)
    rows = query("""SELECT u.id, u.full_name,
                    SUM(se.status='held') held, SUM(se.status='cancelled_teacher') cancelled_teacher,
                    SUM(se.status='cancelled_student') cancelled_student, SUM(se.status='holiday') holiday,
                    COALESCE(SUM(CASE WHEN se.status='held' THEN c.duration_min END),0) minutes
                    FROM users u JOIN classes c ON c.teacher_id=u.id
                    LEFT JOIN sessions se ON se.class_id=c.id AND se.session_date>=? AND se.session_date<?
                    WHERE u.role='teacher' GROUP BY u.id ORDER BY u.full_name""", (start, end))
    if request.args.get("format") == "csv":
        return csv_response(f"teachers_{month}.csv",
                            ["Teacher", "Classes held", "Hours taught", "Cancelled by teacher", "Student cancelled", "Holidays"],
                            [(r["full_name"], r["held"] or 0, round((r["minutes"] or 0) / 60, 2), r["cancelled_teacher"] or 0,
                              r["cancelled_student"] or 0, r["holiday"] or 0) for r in rows])
    return render_template("reports/teachers.html", rows=rows, month=month)
