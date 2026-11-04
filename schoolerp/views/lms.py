"""Staff side of the LMS: build courses, lessons, assignments, quizzes; enrol students; grade; report."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import execute, get_db, query
from ..lms_core import course_progress
from ..security import audit, is_admin, staff_required
from ..utils import clean, csv_response, embed_url, parse_date, school_now, school_today, valid_https_url

bp = Blueprint("lms", __name__, url_prefix="/lms")

LEVELS = ("beginner", "intermediate", "advanced")
KINDS = {"text": "Reading / notes", "video": "Video (YouTube / Vimeo link)", "link": "External link"}
STATUSES = ("draft", "published", "archived")


# ------------------------------------------------------------------ access helpers (object-level authorization)
def own_course(course_id):
    """Admins: any course. Teachers: only courses they own. Otherwise 404 (don't reveal existence)."""
    row = query("""SELECT c.*, u.full_name AS owner_name FROM lms_courses c JOIN users u ON u.id=c.owner_id
                   WHERE c.id=?""", (course_id,), one=True)
    if row is None or (not is_admin() and row["owner_id"] != g.user["id"]):
        abort(404)
    return row


def _via(sql, ident):
    row = query(sql, (ident,), one=True) or abort(404)
    return row, own_course(row["course_id"])


def module_and_course(mid):
    return _via("SELECT * FROM lms_modules WHERE id=?", mid)


def lesson_and_course(lid):
    return _via("SELECT l.*, m.course_id FROM lms_lessons l JOIN lms_modules m ON m.id=l.module_id WHERE l.id=?", lid)


def assignment_and_course(aid):
    return _via("SELECT * FROM lms_assignments WHERE id=?", aid)


def quiz_and_course(qid):
    return _via("SELECT * FROM lms_quizzes WHERE id=?", qid)


def back(course_id, anchor=""):
    return redirect(url_for("lms.course_manage", course_id=course_id) + (f"#{anchor}" if anchor else ""))


def _next_pos(table, col, parent_id):
    # table/col are constants from this file, never user input
    return query(f"SELECT COALESCE(MAX(position),0)+1 p FROM {table} WHERE {col}=?", (parent_id,), one=True)["p"]


def _move(table, col, row_id, direction):
    row = query(f"SELECT id, {col} AS parent, position FROM {table} WHERE id=?", (row_id,), one=True)
    op, order = ("<", "DESC") if direction == "up" else (">", "ASC")
    nb = query(f"""SELECT id, position FROM {table} WHERE {col}=? AND position {op} ?
                   ORDER BY position {order} LIMIT 1""", (row["parent"], row["position"]), one=True)
    if nb:
        db = get_db()
        with db:
            db.execute(f"UPDATE {table} SET position=? WHERE id=?", (nb["position"], row["id"]))
            db.execute(f"UPDATE {table} SET position=? WHERE id=?", (row["position"], nb["id"]))


# ------------------------------------------------------------------ courses
@bp.route("/")
@staff_required
def courses():
    where, args = ("", ()) if is_admin() else ("WHERE c.owner_id=?", (g.user["id"],))
    rows = query(f"""SELECT c.*, u.full_name AS owner_name,
        (SELECT COUNT(*) FROM lms_lessons l JOIN lms_modules m ON m.id=l.module_id WHERE m.course_id=c.id) AS n_lessons,
        (SELECT COUNT(*) FROM lms_enrollments e WHERE e.course_id=c.id) AS n_students,
        (SELECT COUNT(*) FROM lms_submissions s JOIN lms_assignments a ON a.id=s.assignment_id
          WHERE a.course_id=c.id AND s.status='submitted') AS to_grade
        FROM lms_courses c JOIN users u ON u.id=c.owner_id {where}
        ORDER BY c.status='archived', c.title""", args)
    return render_template("lms/list.html", courses=rows)


def _course_form_data(form, existing=None):
    d, errs = {}, []
    d["title"] = clean(form.get("title"), 120)
    d["summary"] = clean(form.get("summary"), 600)
    d["level"] = form.get("level", "beginner")
    d["catalog_course_id"] = form.get("catalog_course_id", "")
    if not d["title"]: errs.append("Course title is required.")
    if d["level"] not in LEVELS: errs.append("Invalid level.")
    if d["catalog_course_id"] and not (d["catalog_course_id"].isdigit() and query(
            "SELECT 1 FROM courses WHERE id=?", (d["catalog_course_id"],), one=True)):
        errs.append("Invalid subject.")
    if is_admin():
        d["owner_id"] = form.get("owner_id", "")
        if not (d["owner_id"].isdigit() and query(
                "SELECT 1 FROM users WHERE id=? AND role IN ('teacher','admin') AND is_active=1", (d["owner_id"],), one=True)):
            errs.append("Choose a teacher/owner.")
    else:
        d["owner_id"] = g.user["id"]
    return d, errs


def _course_form_lists():
    return (query("SELECT id, name FROM courses WHERE is_active=1 ORDER BY name"),
            query("SELECT id, full_name FROM users WHERE role IN ('teacher','admin') AND is_active=1 ORDER BY full_name"))


@bp.route("/courses/new", methods=["GET", "POST"])
@staff_required
def course_new():
    if request.method == "POST":
        d, errs = _course_form_data(request.form)
        if not errs:
            cid = execute("""INSERT INTO lms_courses (title, summary, level, owner_id, catalog_course_id) VALUES (?,?,?,?,?)""",
                          (d["title"], d["summary"], d["level"], int(d["owner_id"]), d["catalog_course_id"] or None))
            audit("lms_course_created", "lms_course", cid, d["title"])
            flash("Course created. Next: add a module and your first lesson.", "success")
            return back(cid)
        for e in errs: flash(e, "error")
        cat, owners = _course_form_lists()
        return render_template("lms/course_form.html", c=d, mode="new", catalog=cat, owners=owners, LEVELS=LEVELS), 400
    cat, owners = _course_form_lists()
    return render_template("lms/course_form.html", c={"level": "beginner", "owner_id": g.user["id"]}, mode="new",
                           catalog=cat, owners=owners, LEVELS=LEVELS)


@bp.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
@staff_required
def course_edit(course_id):
    course = own_course(course_id)
    if request.method == "POST":
        d, errs = _course_form_data(request.form)
        status = request.form.get("status", course["status"])
        if status not in STATUSES: errs.append("Invalid status.")
        n_lessons = query("SELECT COUNT(*) c FROM lms_lessons l JOIN lms_modules m ON m.id=l.module_id WHERE m.course_id=?",
                          (course_id,), one=True)["c"]
        if status == "published" and n_lessons == 0:
            errs.append("Add at least one lesson before publishing.")
        if not errs:
            execute("""UPDATE lms_courses SET title=?, summary=?, level=?, owner_id=?, catalog_course_id=?, status=? WHERE id=?""",
                    (d["title"], d["summary"], d["level"], int(d["owner_id"]), d["catalog_course_id"] or None, status, course_id))
            audit("lms_course_updated", "lms_course", course_id, f"{d['title']} [{status}]")
            flash("Course saved.", "success")
            return back(course_id)
        for e in errs: flash(e, "error")
        d.update(id=course_id, status=status)
        cat, owners = _course_form_lists()
        return render_template("lms/course_form.html", c=d, mode="edit", catalog=cat, owners=owners, LEVELS=LEVELS, STATUSES=STATUSES), 400
    cat, owners = _course_form_lists()
    return render_template("lms/course_form.html", c=course, mode="edit", catalog=cat, owners=owners, LEVELS=LEVELS, STATUSES=STATUSES)


@bp.route("/courses/<int:course_id>")
@staff_required
def course_manage(course_id):
    course = own_course(course_id)
    mods = query("SELECT * FROM lms_modules WHERE course_id=? ORDER BY position, id", (course_id,))
    lessons = query("""SELECT l.* FROM lms_lessons l JOIN lms_modules m ON m.id=l.module_id
                       WHERE m.course_id=? ORDER BY l.position, l.id""", (course_id,))
    by_mod = {}
    for l in lessons:
        by_mod.setdefault(l["module_id"], []).append(l)
    assignments = query("""SELECT a.*, (SELECT COUNT(*) FROM lms_submissions s WHERE s.assignment_id=a.id) n_sub,
                           (SELECT COUNT(*) FROM lms_submissions s WHERE s.assignment_id=a.id AND s.status='submitted') n_pending
                           FROM lms_assignments a WHERE a.course_id=? ORDER BY a.due_date IS NULL, a.due_date, a.id""", (course_id,))
    quizzes = query("""SELECT z.*, (SELECT COUNT(*) FROM lms_questions q WHERE q.quiz_id=z.id) n_q,
                       (SELECT COUNT(*) FROM lms_attempts a WHERE a.quiz_id=z.id) n_attempts
                       FROM lms_quizzes z WHERE z.course_id=? ORDER BY z.id""", (course_id,))
    students = query("""SELECT s.id, s.full_name, s.student_code, s.user_id, e.enrolled_on, e.completed_on FROM lms_enrollments e
                        JOIN students s ON s.id=e.student_id WHERE e.course_id=? ORDER BY s.full_name""", (course_id,))
    if is_admin():
        pickable = query("""SELECT id, full_name, student_code, user_id FROM students WHERE status IN ('trial','active')
                            AND id NOT IN (SELECT student_id FROM lms_enrollments WHERE course_id=?) ORDER BY full_name""", (course_id,))
        classes = query("SELECT id, name FROM classes WHERE is_active=1 ORDER BY name")
    else:
        pickable = query("""SELECT DISTINCT s.id, s.full_name, s.student_code, s.user_id FROM students s
                            JOIN enrollments e ON e.student_id=s.id AND e.ended_on IS NULL JOIN classes c ON c.id=e.class_id
                            WHERE c.teacher_id=? AND s.status IN ('trial','active')
                            AND s.id NOT IN (SELECT student_id FROM lms_enrollments WHERE course_id=?) ORDER BY s.full_name""",
                         (g.user["id"], course_id))
        classes = query("SELECT id, name FROM classes WHERE is_active=1 AND teacher_id=? ORDER BY name", (g.user["id"],))
    announcements = query("SELECT a.*, u.full_name AS author FROM lms_announcements a JOIN users u ON u.id=a.author_id "
                          "WHERE a.course_id=? ORDER BY a.id DESC LIMIT 20", (course_id,))
    checklist = [("Add modules and lessons", len(lessons) > 0), ("Add a quiz or assignment (optional)", bool(quizzes or assignments)),
                 ("Enrol students", len(students) > 0), ("Publish the course", course["status"] == "published")]
    return render_template("lms/course.html", c=course, mods=mods, by_mod=by_mod, assignments=assignments, quizzes=quizzes,
                           students=students, pickable=pickable, classes=classes, announcements=announcements,
                           checklist=checklist, KINDS=KINDS)


# ------------------------------------------------------------------ modules
@bp.route("/courses/<int:course_id>/modules", methods=["POST"])
@staff_required
def module_add(course_id):
    own_course(course_id)
    title = clean(request.form.get("title"), 120)
    if not title:
        flash("Module title is required.", "error")
    else:
        mid = execute("INSERT INTO lms_modules (course_id, title, position) VALUES (?,?,?)",
                      (course_id, title, _next_pos("lms_modules", "course_id", course_id)))
        audit("lms_module_created", "lms_module", mid, title)
        flash("Module added. Now add a lesson to it.", "success")
    return back(course_id, "outline")


@bp.route("/modules/<int:module_id>/rename", methods=["POST"])
@staff_required
def module_rename(module_id):
    m, _c = module_and_course(module_id)
    title = clean(request.form.get("title"), 120)
    if title:
        execute("UPDATE lms_modules SET title=? WHERE id=?", (title, module_id))
    return back(m["course_id"], "outline")


@bp.route("/modules/<int:module_id>/delete", methods=["POST"])
@staff_required
def module_delete(module_id):
    m, _c = module_and_course(module_id)
    execute("DELETE FROM lms_modules WHERE id=?", (module_id,))
    audit("lms_module_deleted", "lms_module", module_id, m["title"])
    flash("Module and its lessons deleted.", "success")
    return back(m["course_id"], "outline")


@bp.route("/modules/<int:module_id>/move", methods=["POST"])
@staff_required
def module_move(module_id):
    m, _c = module_and_course(module_id)
    if request.form.get("dir") in ("up", "down"):
        _move("lms_modules", "course_id", module_id, request.form["dir"])
    return back(m["course_id"], "outline")


# ------------------------------------------------------------------ lessons
def _lesson_data(form):
    d, errs = {}, []
    d["title"] = clean(form.get("title"), 150)
    d["kind"] = form.get("kind", "text")
    d["body"] = clean(form.get("body"), 20000)
    d["url"] = clean(form.get("url"), 300)
    dur = form.get("duration_min", "0")
    d["duration_min"] = int(dur) if dur.isdigit() and int(dur) <= 600 else None
    if not d["title"]: errs.append("Lesson title is required.")
    if d["kind"] not in KINDS: errs.append("Invalid lesson type.")
    if d["duration_min"] is None: errs.append("Duration must be 0-600 minutes.")
    if d["kind"] == "video":
        if not embed_url(d["url"]): errs.append("Paste a YouTube or Vimeo link (https://...).")
    elif d["kind"] == "link":
        if not (d["url"] and valid_https_url(d["url"])): errs.append("Link must start with https://")
    else:
        d["url"] = ""
        if not d["body"]: errs.append("Write the lesson text.")
    if d["kind"] != "text" and d["url"] and not valid_https_url(d["url"]): errs.append("Link must start with https://")
    return d, errs


@bp.route("/modules/<int:module_id>/lessons/new", methods=["GET", "POST"])
@staff_required
def lesson_new(module_id):
    m, course = module_and_course(module_id)
    if request.method == "POST":
        d, errs = _lesson_data(request.form)
        if not errs:
            lid = execute("""INSERT INTO lms_lessons (module_id, title, kind, body, url, duration_min, position) VALUES (?,?,?,?,?,?,?)""",
                          (module_id, d["title"], d["kind"], d["body"], d["url"], d["duration_min"],
                           _next_pos("lms_lessons", "module_id", module_id)))
            audit("lms_lesson_created", "lms_lesson", lid, d["title"])
            flash("Lesson added.", "success")
            return back(course["id"], "outline")
        for e in errs: flash(e, "error")
        return render_template("lms/lesson_form.html", l=d, m=m, c=course, KINDS=KINDS, mode="new"), 400
    return render_template("lms/lesson_form.html", l={"kind": "text", "duration_min": 10}, m=m, c=course, KINDS=KINDS, mode="new")


@bp.route("/lessons/<int:lesson_id>/edit", methods=["GET", "POST"])
@staff_required
def lesson_edit(lesson_id):
    l, course = lesson_and_course(lesson_id)
    m = query("SELECT * FROM lms_modules WHERE id=?", (l["module_id"],), one=True)
    if request.method == "POST":
        d, errs = _lesson_data(request.form)
        if not errs:
            execute("UPDATE lms_lessons SET title=?, kind=?, body=?, url=?, duration_min=? WHERE id=?",
                    (d["title"], d["kind"], d["body"], d["url"], d["duration_min"], lesson_id))
            audit("lms_lesson_updated", "lms_lesson", lesson_id, d["title"])
            flash("Lesson saved.", "success")
            return back(course["id"], "outline")
        for e in errs: flash(e, "error")
        d["id"] = lesson_id
        return render_template("lms/lesson_form.html", l=d, m=m, c=course, KINDS=KINDS, mode="edit"), 400
    return render_template("lms/lesson_form.html", l=l, m=m, c=course, KINDS=KINDS, mode="edit")


@bp.route("/lessons/<int:lesson_id>/delete", methods=["POST"])
@staff_required
def lesson_delete(lesson_id):
    l, course = lesson_and_course(lesson_id)
    execute("DELETE FROM lms_lessons WHERE id=?", (lesson_id,))
    audit("lms_lesson_deleted", "lms_lesson", lesson_id, l["title"])
    flash("Lesson deleted.", "success")
    return back(course["id"], "outline")


@bp.route("/lessons/<int:lesson_id>/move", methods=["POST"])
@staff_required
def lesson_move(lesson_id):
    _l, course = lesson_and_course(lesson_id)
    if request.form.get("dir") in ("up", "down"):
        _move("lms_lessons", "module_id", lesson_id, request.form["dir"])
    return back(course["id"], "outline")


# ------------------------------------------------------------------ assignments
def _assignment_data(form):
    d, errs = {}, []
    d["title"] = clean(form.get("title"), 150)
    d["instructions"] = clean(form.get("instructions"), 5000)
    due = clean(form.get("due_date"), 10)
    d["due_date"] = due
    mp = form.get("max_points", "100")
    d["max_points"] = int(mp) if mp.isdigit() and 1 <= int(mp) <= 1000 else None
    if not d["title"]: errs.append("Title is required.")
    if due and not parse_date(due): errs.append("Due date must be YYYY-MM-DD.")
    if d["max_points"] is None: errs.append("Points must be 1-1000.")
    return d, errs


@bp.route("/courses/<int:course_id>/assignments/new", methods=["GET", "POST"])
@staff_required
def assignment_new(course_id):
    course = own_course(course_id)
    if request.method == "POST":
        d, errs = _assignment_data(request.form)
        if not errs:
            aid = execute("INSERT INTO lms_assignments (course_id, title, instructions, due_date, max_points) VALUES (?,?,?,?,?)",
                          (course_id, d["title"], d["instructions"], d["due_date"] or None, d["max_points"]))
            audit("lms_assignment_created", "lms_assignment", aid, d["title"])
            flash("Assignment created.", "success")
            return back(course_id, "assignments")
        for e in errs: flash(e, "error")
        return render_template("lms/assignment_form.html", a=d, c=course, mode="new"), 400
    return render_template("lms/assignment_form.html", a={"max_points": 100}, c=course, mode="new")


@bp.route("/assignments/<int:assignment_id>/edit", methods=["GET", "POST"])
@staff_required
def assignment_edit(assignment_id):
    a, course = assignment_and_course(assignment_id)
    if request.method == "POST":
        d, errs = _assignment_data(request.form)
        if not errs:
            execute("UPDATE lms_assignments SET title=?, instructions=?, due_date=?, max_points=? WHERE id=?",
                    (d["title"], d["instructions"], d["due_date"] or None, d["max_points"], assignment_id))
            flash("Assignment saved.", "success")
            return back(course["id"], "assignments")
        for e in errs: flash(e, "error")
        d["id"] = assignment_id
        return render_template("lms/assignment_form.html", a=d, c=course, mode="edit"), 400
    return render_template("lms/assignment_form.html", a=a, c=course, mode="edit")


@bp.route("/assignments/<int:assignment_id>/delete", methods=["POST"])
@staff_required
def assignment_delete(assignment_id):
    a, course = assignment_and_course(assignment_id)
    execute("DELETE FROM lms_assignments WHERE id=?", (assignment_id,))
    audit("lms_assignment_deleted", "lms_assignment", assignment_id, a["title"])
    flash("Assignment deleted.", "success")
    return back(course["id"], "assignments")


@bp.route("/assignments/<int:assignment_id>/submissions")
@staff_required
def submissions(assignment_id):
    a, course = assignment_and_course(assignment_id)
    rows = query("""SELECT s.*, st.full_name, st.student_code FROM lms_submissions s JOIN students st ON st.id=s.student_id
                    WHERE s.assignment_id=? ORDER BY s.status='graded', s.submitted_at""", (assignment_id,))
    not_submitted = query("""SELECT st.id, st.full_name FROM lms_enrollments e JOIN students st ON st.id=e.student_id
                             WHERE e.course_id=? AND st.id NOT IN (SELECT student_id FROM lms_submissions WHERE assignment_id=?)
                             ORDER BY st.full_name""", (course["id"], assignment_id))
    return render_template("lms/submissions.html", a=a, c=course, rows=rows, not_submitted=not_submitted)


@bp.route("/submissions/<int:submission_id>", methods=["GET", "POST"])
@staff_required
def grade(submission_id):
    sub, course = _via("""SELECT s.*, a.course_id, a.title AS a_title, a.max_points, a.instructions, st.full_name
                          FROM lms_submissions s JOIN lms_assignments a ON a.id=s.assignment_id
                          JOIN students st ON st.id=s.student_id WHERE s.id=?""", submission_id)
    if request.method == "POST":
        pts = request.form.get("points", "")
        feedback = clean(request.form.get("feedback"), 2000)
        if not (pts.isdigit() and 0 <= int(pts) <= sub["max_points"]):
            flash(f"Points must be a whole number from 0 to {sub['max_points']}.", "error")
            return render_template("lms/grade.html", s=sub, c=course), 400
        execute("""UPDATE lms_submissions SET status='graded', points=?, feedback=?, graded_by=?, graded_at=? WHERE id=?""",
                (int(pts), feedback, g.user["id"], school_now().strftime("%Y-%m-%d %H:%M"), submission_id))
        audit("lms_graded", "lms_submission", submission_id, f"{pts}/{sub['max_points']}")
        flash("Grade saved.", "success")
        return redirect(url_for("lms.submissions", assignment_id=sub["assignment_id"]))
    return render_template("lms/grade.html", s=sub, c=course)


# ------------------------------------------------------------------ quizzes
@bp.route("/courses/<int:course_id>/quizzes/new", methods=["POST"])
@staff_required
def quiz_new(course_id):
    own_course(course_id)
    title = clean(request.form.get("title"), 150)
    if not title:
        flash("Quiz title is required.", "error")
        return back(course_id, "quizzes")
    qid = execute("INSERT INTO lms_quizzes (course_id, title) VALUES (?,?)", (course_id, title))
    audit("lms_quiz_created", "lms_quiz", qid, title)
    flash("Quiz created. Add your questions below.", "success")
    return redirect(url_for("lms.quiz_manage", quiz_id=qid))


@bp.route("/quizzes/<int:quiz_id>")
@staff_required
def quiz_manage(quiz_id):
    z, course = quiz_and_course(quiz_id)
    questions = query("SELECT * FROM lms_questions WHERE quiz_id=? ORDER BY position, id", (quiz_id,))
    opts = query("""SELECT o.* FROM lms_options o JOIN lms_questions q ON q.id=o.question_id WHERE q.quiz_id=?
                    ORDER BY o.position, o.id""", (quiz_id,))
    by_q = {}
    for o in opts:
        by_q.setdefault(o["question_id"], []).append(o)
    return render_template("lms/quiz.html", z=z, c=course, questions=questions, by_q=by_q)


@bp.route("/quizzes/<int:quiz_id>/settings", methods=["POST"])
@staff_required
def quiz_settings(quiz_id):
    z, course = quiz_and_course(quiz_id)
    title = clean(request.form.get("title"), 150)
    pp, ma = request.form.get("pass_percent", ""), request.form.get("max_attempts", "")
    if not (title and pp.isdigit() and 1 <= int(pp) <= 100 and ma.isdigit() and 1 <= int(ma) <= 20):
        flash("Check the title, pass mark (1-100) and attempts (1-20).", "error")
    else:
        execute("UPDATE lms_quizzes SET title=?, instructions=?, pass_percent=?, max_attempts=? WHERE id=?",
                (title, clean(request.form.get("instructions"), 2000), int(pp), int(ma), quiz_id))
        flash("Quiz settings saved.", "success")
    return redirect(url_for("lms.quiz_manage", quiz_id=quiz_id))


@bp.route("/quizzes/<int:quiz_id>/questions", methods=["POST"])
@staff_required
def question_add(quiz_id):
    quiz_and_course(quiz_id)
    text = clean(request.form.get("text"), 500)
    options = [(i, clean(request.form.get(f"opt{i}"), 200)) for i in range(1, 7)]
    options = [(i, t) for i, t in options if t]
    correct = request.form.get("correct", "")
    errs = []
    if not text: errs.append("Write the question.")
    if len(options) < 2: errs.append("Give at least 2 answer choices.")
    if not (correct.isdigit() and int(correct) in [i for i, _ in options]): errs.append("Select which answer is correct.")
    if errs:
        for e in errs: flash(e, "error")
        return redirect(url_for("lms.quiz_manage", quiz_id=quiz_id))
    db = get_db()
    with db:
        cur = db.execute("INSERT INTO lms_questions (quiz_id, text, position) VALUES (?,?,?)",
                         (quiz_id, text, _next_pos("lms_questions", "quiz_id", quiz_id)))
        for n, (i, t) in enumerate(options, 1):
            db.execute("INSERT INTO lms_options (question_id, text, is_correct, position) VALUES (?,?,?,?)",
                       (cur.lastrowid, t, 1 if i == int(correct) else 0, n))
    flash("Question added.", "success")
    return redirect(url_for("lms.quiz_manage", quiz_id=quiz_id))


@bp.route("/questions/<int:question_id>/delete", methods=["POST"])
@staff_required
def question_delete(question_id):
    q, _c = _via("SELECT q.*, z.course_id FROM lms_questions q JOIN lms_quizzes z ON z.id=q.quiz_id WHERE q.id=?", question_id)
    execute("DELETE FROM lms_questions WHERE id=?", (question_id,))
    flash("Question deleted.", "success")
    return redirect(url_for("lms.quiz_manage", quiz_id=q["quiz_id"]))


@bp.route("/quizzes/<int:quiz_id>/publish", methods=["POST"])
@staff_required
def quiz_publish(quiz_id):
    z, _c = quiz_and_course(quiz_id)
    want = 1 if request.form.get("publish") == "1" else 0
    n = query("SELECT COUNT(*) c FROM lms_questions WHERE quiz_id=?", (quiz_id,), one=True)["c"]
    if want and n == 0:
        flash("Add at least one question before publishing the quiz.", "error")
    else:
        execute("UPDATE lms_quizzes SET is_published=? WHERE id=?", (want, quiz_id))
        audit("lms_quiz_published" if want else "lms_quiz_unpublished", "lms_quiz", quiz_id, z["title"])
        flash("Quiz published." if want else "Quiz hidden from students.", "success")
    return redirect(url_for("lms.quiz_manage", quiz_id=quiz_id))


@bp.route("/quizzes/<int:quiz_id>/delete", methods=["POST"])
@staff_required
def quiz_delete(quiz_id):
    z, course = quiz_and_course(quiz_id)
    execute("DELETE FROM lms_quizzes WHERE id=?", (quiz_id,))
    audit("lms_quiz_deleted", "lms_quiz", quiz_id, z["title"])
    flash("Quiz deleted.", "success")
    return back(course["id"], "quizzes")


@bp.route("/quizzes/<int:quiz_id>/results")
@staff_required
def quiz_results(quiz_id):
    z, course = quiz_and_course(quiz_id)
    rows = query("""SELECT st.full_name, st.student_code, COUNT(a.id) attempts, MAX(a.score) best, MAX(a.total) total,
                    MAX(a.passed) passed FROM lms_enrollments e JOIN students st ON st.id=e.student_id
                    LEFT JOIN lms_attempts a ON a.student_id=st.id AND a.quiz_id=? WHERE e.course_id=?
                    GROUP BY st.id ORDER BY st.full_name""", (quiz_id, course["id"]))
    return render_template("lms/quiz_results.html", z=z, c=course, rows=rows)


# ------------------------------------------------------------------ announcements
@bp.route("/courses/<int:course_id>/announce", methods=["POST"])
@staff_required
def announce(course_id):
    own_course(course_id)
    title, body = clean(request.form.get("title"), 150), clean(request.form.get("body"), 3000)
    if not title:
        flash("Announcement needs a title.", "error")
    else:
        execute("INSERT INTO lms_announcements (course_id, author_id, title, body) VALUES (?,?,?,?)",
                (course_id, g.user["id"], title, body))
        flash("Announcement posted. Enrolled students will see it.", "success")
    return back(course_id, "announcements")


@bp.route("/announcements/<int:ann_id>/delete", methods=["POST"])
@staff_required
def announce_delete(ann_id):
    a, course = _via("SELECT * FROM lms_announcements WHERE id=?", ann_id)
    execute("DELETE FROM lms_announcements WHERE id=?", (ann_id,))
    return back(course["id"], "announcements")


# ------------------------------------------------------------------ enrolment
def _allowed_student_ids():
    if is_admin():
        return None  # no restriction
    return {r["id"] for r in query("""SELECT DISTINCT s.id FROM students s JOIN enrollments e ON e.student_id=s.id AND e.ended_on IS NULL
                                      JOIN classes c ON c.id=e.class_id WHERE c.teacher_id=?""", (g.user["id"],))}


@bp.route("/courses/<int:course_id>/enroll", methods=["POST"])
@staff_required
def enroll(course_id):
    course = own_course(course_id)
    if course["status"] == "archived":
        flash("Archived courses cannot take new students.", "error")
        return back(course_id, "students")
    allowed = _allowed_student_ids()
    ids = []
    sid, cid = request.form.get("student_id", ""), request.form.get("class_id", "")
    if sid.isdigit():
        ids = [int(sid)]
    elif cid.isdigit():
        cls = query("SELECT id, teacher_id FROM classes WHERE id=?", (cid,), one=True)
        if cls and (is_admin() or cls["teacher_id"] == g.user["id"]):
            ids = [r["student_id"] for r in query("SELECT student_id FROM enrollments WHERE class_id=? AND ended_on IS NULL", (cls["id"],))]
    added = 0
    today = school_today().isoformat()
    db = get_db()
    with db:
        for i in ids:
            if allowed is not None and i not in allowed:
                continue
            if not db.execute("SELECT 1 FROM students WHERE id=? AND status IN ('trial','active')", (i,)).fetchone():
                continue
            added += db.execute("INSERT OR IGNORE INTO lms_enrollments (course_id, student_id, enrolled_on) VALUES (?,?,?)",
                                (course_id, i, today)).rowcount
    audit("lms_enrolled", "lms_course", course_id, f"{added} student(s)")
    flash(f"{added} student(s) enrolled." if added else "Nobody was added (already enrolled, or not allowed).", "success" if added else "error")
    return back(course_id, "students")


@bp.route("/courses/<int:course_id>/unenroll", methods=["POST"])
@staff_required
def unenroll(course_id):
    own_course(course_id)
    sid = request.form.get("student_id", "")
    if sid.isdigit():
        execute("DELETE FROM lms_enrollments WHERE course_id=? AND student_id=?", (course_id, int(sid)))
        audit("lms_unenrolled", "lms_course", course_id, f"student {sid}")
        flash("Student removed from the course.", "success")
    return back(course_id, "students")


# ------------------------------------------------------------------ progress report
@bp.route("/courses/<int:course_id>/progress")
@staff_required
def progress(course_id):
    course = own_course(course_id)
    n_lessons = course_progress(course_id, -1)["total"]
    n_quizzes = query("SELECT COUNT(*) c FROM lms_quizzes WHERE course_id=? AND is_published=1", (course_id,), one=True)["c"]
    rows = query("""SELECT s.id, s.full_name, s.student_code, e.enrolled_on, e.completed_on,
        (SELECT COUNT(*) FROM lms_progress p JOIN lms_lessons l ON l.id=p.lesson_id JOIN lms_modules m ON m.id=l.module_id
          WHERE m.course_id=:c AND p.student_id=s.id) AS done,
        (SELECT COUNT(*) FROM lms_quizzes z WHERE z.course_id=:c AND z.is_published=1 AND EXISTS
          (SELECT 1 FROM lms_attempts a WHERE a.quiz_id=z.id AND a.student_id=s.id AND a.passed=1)) AS quizzes_passed,
        (SELECT COUNT(*) FROM lms_submissions sb JOIN lms_assignments a ON a.id=sb.assignment_id
          WHERE a.course_id=:c AND sb.student_id=s.id) AS submitted,
        (SELECT code FROM lms_certificates ce WHERE ce.course_id=:c AND ce.student_id=s.id) AS cert
        FROM lms_enrollments e JOIN students s ON s.id=e.student_id WHERE e.course_id=:c ORDER BY s.full_name""", {"c": course_id})
    n_assign = query("SELECT COUNT(*) c FROM lms_assignments WHERE course_id=?", (course_id,), one=True)["c"]
    if request.args.get("format") == "csv":
        return csv_response(f"course_{course_id}_progress.csv",
                            ["Student ID", "Name", "Enrolled", "Lessons done", "Lessons total", "Quizzes passed", "Assignments submitted", "Completed on", "Certificate"],
                            [(r["student_code"], r["full_name"], r["enrolled_on"], r["done"], n_lessons, r["quizzes_passed"],
                              r["submitted"], r["completed_on"] or "", r["cert"] or "") for r in rows])
    return render_template("lms/progress.html", c=course, rows=rows, n_lessons=n_lessons, n_quizzes=n_quizzes, n_assign=n_assign)
