import sqlite3
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import execute, query
from ..security import admin_required, audit, get_class_or_404, is_admin, login_required
from ..utils import clean, day_set, parse_date, school_today, valid_https_url, valid_time

bp = Blueprint("academics", __name__)


# ---------------- Courses ----------------
@bp.route("/courses")
@admin_required
def courses():
    rows = query("""SELECT co.*, (SELECT COUNT(*) FROM classes c WHERE c.course_id=co.id AND c.is_active=1) n_classes
                    FROM courses co ORDER BY co.is_active DESC, co.name""")
    return render_template("courses/list.html", courses=rows)


@bp.route("/courses/save", methods=["POST"])
@admin_required
def course_save():
    cid = request.form.get("id", "")
    name = clean(request.form.get("name"), 80)
    desc = clean(request.form.get("description"), 200)
    active = 1 if request.form.get("is_active", "1") == "1" else 0
    if not name:
        flash("Course name is required.", "error")
        return redirect(url_for("academics.courses"))
    try:
        if cid.isdigit():
            execute("UPDATE courses SET name=?, description=?, is_active=? WHERE id=?", (name, desc, active, int(cid)))
            audit("course_updated", "course", int(cid), name)
        else:
            new_id = execute("INSERT INTO courses (name, description) VALUES (?,?)", (name, desc))
            audit("course_created", "course", new_id, name)
        flash("Course saved.", "success")
    except sqlite3.IntegrityError:
        flash("A course with that name already exists.", "error")
    return redirect(url_for("academics.courses"))


# ---------------- Classes ----------------
@bp.route("/classes")
@login_required
def classes():
    sql = """SELECT c.*, co.name course_name, u.full_name teacher_name,
             (SELECT COUNT(*) FROM enrollments e WHERE e.class_id=c.id AND e.ended_on IS NULL) n_students
             FROM classes c JOIN courses co ON co.id=c.course_id JOIN users u ON u.id=c.teacher_id"""
    if is_admin():
        rows = query(sql + " ORDER BY c.is_active DESC, c.start_time, c.name")
    else:
        rows = query(sql + " WHERE c.teacher_id=? ORDER BY c.is_active DESC, c.start_time, c.name", (g.user["id"],))
    return render_template("classes/list.html", classes=rows)


def _form_lists():
    return (query("SELECT id, name FROM courses WHERE is_active=1 ORDER BY name"),
            query("SELECT id, full_name FROM users WHERE role='teacher' AND is_active=1 ORDER BY full_name"))


def _validate_class(form):
    d, errs = {}, []
    d["name"] = clean(form.get("name"), 100)
    d["course_id"] = form.get("course_id", "")
    d["teacher_id"] = form.get("teacher_id", "")
    days = sorted({int(x) for x in form.getlist("weekdays") if x.isdigit() and int(x) < 7})
    d["weekdays"] = ",".join(map(str, days))
    d["start_time"] = clean(form.get("start_time"), 5)
    d["duration_min"] = form.get("duration_min", "30")
    d["meeting_link"] = clean(form.get("meeting_link"), 300)
    d["start_date"] = clean(form.get("start_date"), 10)
    d["end_date"] = clean(form.get("end_date"), 10)
    d["is_active"] = 1 if form.get("is_active", "1") == "1" else 0
    if not d["name"]: errs.append("Class name is required.")
    if not (d["course_id"].isdigit() and query("SELECT 1 FROM courses WHERE id=?", (d["course_id"],), one=True)):
        errs.append("Choose a course.")
    if not (d["teacher_id"].isdigit() and query("SELECT 1 FROM users WHERE id=? AND role='teacher' AND is_active=1", (d["teacher_id"],), one=True)):
        errs.append("Choose an active teacher.")
    if not days: errs.append("Pick at least one weekday.")
    if not valid_time(d["start_time"]): errs.append("Start time must look like 17:30.")
    if not (d["duration_min"].isdigit() and 10 <= int(d["duration_min"]) <= 240): errs.append("Duration must be 10-240 minutes.")
    if not valid_https_url(d["meeting_link"]): errs.append("Meeting link must start with https://")
    for k, label in (("start_date", "Start date"), ("end_date", "End date")):
        if d[k] and not parse_date(d[k]): errs.append(f"{label} must be YYYY-MM-DD.")
    if d["start_date"] and d["end_date"] and d["end_date"] < d["start_date"]: errs.append("End date is before start date.")
    return d, errs


@bp.route("/classes/new", methods=["GET", "POST"])
@admin_required
def class_new():
    if request.method == "POST":
        d, errs = _validate_class(request.form)
        if errs:
            for e in errs: flash(e, "error")
            c, t = _form_lists()
            d["weekday_set"] = day_set(d["weekdays"])
            return render_template("classes/form.html", c=d, mode="new", courses=c, teachers=t), 400
        cid = execute("""INSERT INTO classes (name, course_id, teacher_id, weekdays, start_time, duration_min, meeting_link, start_date, end_date, is_active)
                         VALUES (?,?,?,?,?,?,?,?,?,?)""",
                      (d["name"], d["course_id"], d["teacher_id"], d["weekdays"], d["start_time"], int(d["duration_min"]),
                       d["meeting_link"], d["start_date"] or None, d["end_date"] or None, d["is_active"]))
        audit("class_created", "class", cid, d["name"])
        flash("Class created. Now add students to it.", "success")
        return redirect(url_for("academics.class_detail", class_id=cid))
    c, t = _form_lists()
    return render_template("classes/form.html", c={"duration_min": "30", "is_active": 1, "weekday_set": set()}, mode="new", courses=c, teachers=t)


@bp.route("/classes/<int:class_id>/edit", methods=["GET", "POST"])
@admin_required
def class_edit(class_id):
    cls = get_class_or_404(class_id)
    if request.method == "POST":
        d, errs = _validate_class(request.form)
        if errs:
            for e in errs: flash(e, "error")
            c, t = _form_lists()
            d["id"] = class_id; d["weekday_set"] = day_set(d["weekdays"])
            return render_template("classes/form.html", c=d, mode="edit", courses=c, teachers=t), 400
        execute("""UPDATE classes SET name=?, course_id=?, teacher_id=?, weekdays=?, start_time=?, duration_min=?, meeting_link=?,
                   start_date=?, end_date=?, is_active=? WHERE id=?""",
                (d["name"], d["course_id"], d["teacher_id"], d["weekdays"], d["start_time"], int(d["duration_min"]),
                 d["meeting_link"], d["start_date"] or None, d["end_date"] or None, d["is_active"], class_id))
        audit("class_updated", "class", class_id, d["name"])
        flash("Class updated.", "success")
        return redirect(url_for("academics.class_detail", class_id=class_id))
    c, t = _form_lists()
    row = dict(cls); row["weekday_set"] = day_set(cls["weekdays"])
    return render_template("classes/form.html", c=row, mode="edit", courses=c, teachers=t)


@bp.route("/classes/<int:class_id>")
@login_required
def class_detail(class_id):
    cls = get_class_or_404(class_id)
    students = query("""SELECT s.id, s.student_code, s.full_name, s.status, e.enrolled_on FROM enrollments e
                        JOIN students s ON s.id=e.student_id WHERE e.class_id=? AND e.ended_on IS NULL ORDER BY s.full_name""", (class_id,))
    available = []
    if is_admin():
        available = query("""SELECT id, student_code, full_name FROM students WHERE status IN ('trial','active')
                             AND id NOT IN (SELECT student_id FROM enrollments WHERE class_id=? AND ended_on IS NULL)
                             ORDER BY full_name""", (class_id,))
    return render_template("classes/detail.html", c=cls, students=students, available=available, today=school_today())


@bp.route("/classes/<int:class_id>/enroll", methods=["POST"])
@admin_required
def enroll(class_id):
    get_class_or_404(class_id)
    sid = request.form.get("student_id", "")
    if not (sid.isdigit() and query("SELECT 1 FROM students WHERE id=?", (sid,), one=True)):
        flash("Choose a student.", "error")
        return redirect(url_for("academics.class_detail", class_id=class_id))
    today = school_today().isoformat()
    execute("""INSERT INTO enrollments (class_id, student_id, enrolled_on) VALUES (?,?,?)
               ON CONFLICT(class_id, student_id) DO UPDATE SET ended_on=NULL, enrolled_on=excluded.enrolled_on""",
            (class_id, int(sid), today))
    audit("enrolled", "class", class_id, f"student {sid}")
    flash("Student added to class.", "success")
    return redirect(url_for("academics.class_detail", class_id=class_id))


@bp.route("/classes/<int:class_id>/unenroll", methods=["POST"])
@admin_required
def unenroll(class_id):
    get_class_or_404(class_id)
    sid = request.form.get("student_id", "")
    if sid.isdigit():
        execute("UPDATE enrollments SET ended_on=? WHERE class_id=? AND student_id=? AND ended_on IS NULL",
                (school_today().isoformat(), class_id, int(sid)))
        audit("unenrolled", "class", class_id, f"student {sid}")
        flash("Student removed from class (history is kept).", "success")
    return redirect(url_for("academics.class_detail", class_id=class_id))
