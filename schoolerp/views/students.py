from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from datetime import timedelta

from ..db import execute, get_db, query
from ..security import admin_required, audit, get_student_or_404, is_admin, login_required
from ..utils import clean, parse_date, parse_money, school_today, valid_email
from flask import current_app

bp = Blueprint("students", __name__, url_prefix="/students")

STATUSES = ("trial", "active", "paused", "left")
GENDERS = ("male", "female")
LANGS = ("English", "Urdu", "Arabic", "Other")
CURRENCIES = ("INR", "USD", "GBP", "EUR", "AED", "SAR", "CAD", "AUD", "PKR", "EGP")
LOG_TYPES = {"sabaq": "Sabaq (new lesson)", "sabqi": "Sabqi (recent revision)", "manzil": "Manzil (old revision)",
             "nazra": "Nazra / Recitation", "tajweed": "Tajweed", "arabic": "Arabic", "tafsir": "Tafsir",
             "islamic": "Islamic Studies", "other": "Other"}
GRADES = {"": "—", "excellent": "Excellent", "good": "Good", "average": "Average", "needs_work": "Needs work"}


def validate(form):
    d, errs = {}, []
    d["full_name"] = clean(form.get("full_name"), 100)
    d["gender"] = form.get("gender", "")
    dob = clean(form.get("date_of_birth"), 10)
    d["date_of_birth"] = dob
    d["guardian_name"] = clean(form.get("guardian_name"), 100)
    d["guardian_phone"] = clean(form.get("guardian_phone"), 30)
    d["guardian_email"] = clean(form.get("guardian_email"), 150).lower()
    d["country"] = clean(form.get("country"), 60)
    d["timezone"] = clean(form.get("timezone"), 60)
    d["language"] = form.get("language", "English")
    d["status"] = form.get("status", "trial")
    joined = clean(form.get("joined_on"), 10)
    d["joined_on"] = joined
    d["currency"] = form.get("currency", "INR")
    d["notes"] = clean(form.get("notes"), 1000)
    fee = parse_money(form.get("monthly_fee", "0"))
    d["monthly_fee_cents"] = fee if fee is not None else 0
    d["monthly_fee"] = clean(form.get("monthly_fee", "0"), 12)

    if not d["full_name"]: errs.append("Full name is required.")
    if d["gender"] not in GENDERS: errs.append("Choose a gender.")
    if d["language"] not in LANGS: errs.append("Invalid language.")
    if d["status"] not in STATUSES: errs.append("Invalid status.")
    if d["currency"] not in CURRENCIES: errs.append("Invalid currency.")
    if fee is None: errs.append("Monthly fee must be a number like 1500 or 25.50.")
    if dob and not parse_date(dob): errs.append("Date of birth must be YYYY-MM-DD.")
    if joined and not parse_date(joined): errs.append("Join date must be YYYY-MM-DD.")
    if not valid_email(d["guardian_email"]): errs.append("Guardian email looks invalid.")
    return d, errs


@bp.route("/")
@login_required
def list_():
    q = clean(request.args.get("q"), 60)
    status = request.args.get("status", "")
    sql = """SELECT DISTINCT s.id, s.student_code, s.full_name, s.gender, s.country, s.status, s.language
             FROM students s"""
    args, where = [], []
    if not is_admin():
        sql += " JOIN enrollments e ON e.student_id=s.id AND e.ended_on IS NULL JOIN classes c ON c.id=e.class_id"
        where.append("c.teacher_id=?"); args.append(g.user["id"])
    if q:
        where.append("(s.full_name LIKE ? ESCAPE '\\' OR s.student_code LIKE ? ESCAPE '\\')")
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like, like]
    if status in STATUSES:
        where.append("s.status=?"); args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)  # fragments are constants; values are bound parameters
    sql += " ORDER BY s.full_name LIMIT 500"
    return render_template("students/list.html", students=query(sql, args), q=q, status=status, STATUSES=STATUSES)


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def new():
    if request.method == "POST":
        d, errs = validate(request.form)
        if errs:
            for e in errs: flash(e, "error")
            return render_template("students/form.html", s=d, mode="new", STATUSES=STATUSES, LANGS=LANGS, CURRENCIES=CURRENCIES), 400
        db = get_db()
        with db:
            cur = db.execute("""INSERT INTO students (full_name, gender, date_of_birth, guardian_name, guardian_phone,
                guardian_email, country, timezone, language, status, joined_on, monthly_fee_cents, currency, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d["full_name"], d["gender"], d["date_of_birth"] or None, d["guardian_name"], d["guardian_phone"],
                 d["guardian_email"], d["country"], d["timezone"], d["language"], d["status"],
                 d["joined_on"] or school_today().isoformat(), d["monthly_fee_cents"], d["currency"], d["notes"]))
            sid = cur.fetchone()["id"] if current_app.config["DATABASE"].startswith(("postgres://", "postgresql://")) else cur.lastrowid
            db.execute("UPDATE students SET student_code=? WHERE id=?", (f"{current_app.config['STUDENT_PREFIX']}-{sid:04d}", sid))
        audit("student_created", "student", sid, d["full_name"])
        flash("Student added.", "success")
        return redirect(url_for("students.detail", student_id=sid))
    return render_template("students/form.html", s={"status": "trial", "language": "English", "currency": "INR", "monthly_fee": "0"},
                           mode="new", STATUSES=STATUSES, LANGS=LANGS, CURRENCIES=CURRENCIES)


@bp.route("/<int:student_id>")
@login_required
def detail(student_id):
    s = get_student_or_404(student_id)
    today = school_today()
    enrolls = query("""SELECT c.id, c.name, co.name course, u.full_name teacher, c.weekdays, c.start_time, e.ended_on
                       FROM enrollments e JOIN classes c ON c.id=e.class_id JOIN courses co ON co.id=c.course_id
                       JOIN users u ON u.id=c.teacher_id WHERE e.student_id=? AND (? OR c.teacher_id=?)
                       ORDER BY e.ended_on IS NOT NULL, c.name""",
                    (student_id, 1 if is_admin() else 0, g.user["id"]))
    since = (today - timedelta(days=60)).isoformat()
    att = query("""SELECT a.status, COUNT(*) n FROM attendance a JOIN sessions se ON se.id=a.session_id AND se.status='held'
                   JOIN classes c ON c.id=se.class_id
                   WHERE a.student_id=? AND se.session_date>=? AND (? OR c.teacher_id=?) GROUP BY a.status""",
                (student_id, since, 1 if is_admin() else 0, g.user["id"]))
    att = {r["status"]: r["n"] for r in att}
    recent = query("""SELECT se.session_date, c.name class_name, a.status, a.remark FROM attendance a
                      JOIN sessions se ON se.id=a.session_id JOIN classes c ON c.id=se.class_id
                      WHERE a.student_id=? AND (? OR c.teacher_id=?) ORDER BY se.session_date DESC LIMIT 15""",
                   (student_id, 1 if is_admin() else 0, g.user["id"]))
    prog = query("""SELECT p.*, u.full_name by_name FROM progress p JOIN users u ON u.id=p.recorded_by
                    WHERE p.student_id=? ORDER BY p.entry_date DESC, p.id DESC LIMIT 30""", (student_id,))
    fees = query("SELECT * FROM fees WHERE student_id=? ORDER BY period DESC LIMIT 12", (student_id,)) if is_admin() else []
    return render_template("students/detail.html", s=s, enrolls=enrolls, att=att, recent=recent, prog=prog,
                           fees=fees, LOG_TYPES=LOG_TYPES, GRADES=GRADES, today=today, LOG_TYPES_ITEMS=LOG_TYPES.items())


@bp.route("/<int:student_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(student_id):
    s = get_student_or_404(student_id)
    if request.method == "POST":
        d, errs = validate(request.form)
        if errs:
            for e in errs: flash(e, "error")
            d["id"] = student_id; d["student_code"] = s["student_code"]
            return render_template("students/form.html", s=d, mode="edit", STATUSES=STATUSES, LANGS=LANGS, CURRENCIES=CURRENCIES), 400
        execute("""UPDATE students SET full_name=?, gender=?, date_of_birth=?, guardian_name=?, guardian_phone=?,
                   guardian_email=?, country=?, timezone=?, language=?, status=?, joined_on=?, monthly_fee_cents=?,
                   currency=?, notes=? WHERE id=?""",
                (d["full_name"], d["gender"], d["date_of_birth"] or None, d["guardian_name"], d["guardian_phone"],
                 d["guardian_email"], d["country"], d["timezone"], d["language"], d["status"], d["joined_on"] or None,
                 d["monthly_fee_cents"], d["currency"], d["notes"], student_id))
        audit("student_updated", "student", student_id, d["full_name"])
        flash("Student updated.", "success")
        return redirect(url_for("students.detail", student_id=student_id))
    row = dict(s)
    row["monthly_fee"] = f"{s['monthly_fee_cents'] / 100:.2f}"
    return render_template("students/form.html", s=row, mode="edit", STATUSES=STATUSES, LANGS=LANGS, CURRENCIES=CURRENCIES)


@bp.route("/<int:student_id>/progress", methods=["POST"])
@login_required
def add_progress(student_id):
    get_student_or_404(student_id)  # teacher must have this student
    log_type = request.form.get("log_type", "")
    grade = request.form.get("grade", "")
    entry_date = parse_date(request.form.get("entry_date"))
    if log_type not in LOG_TYPES or grade not in GRADES or entry_date is None or entry_date > school_today():
        flash("Please check the date, lesson type and grade.", "error")
        return redirect(url_for("students.detail", student_id=student_id))
    execute("""INSERT INTO progress (student_id, entry_date, log_type, from_ref, to_ref, grade, remarks, recorded_by)
               VALUES (?,?,?,?,?,?,?,?)""",
            (student_id, entry_date.isoformat(), log_type, clean(request.form.get("from_ref"), 80),
             clean(request.form.get("to_ref"), 80), grade, clean(request.form.get("remarks"), 500), g.user["id"]))
    audit("progress_added", "student", student_id, log_type)
    flash("Lesson log saved.", "success")
    return redirect(url_for("students.detail", student_id=student_id))
