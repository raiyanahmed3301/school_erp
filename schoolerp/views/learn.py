"""Student portal (/learn) and public certificate verification (/verify)."""
import random
import re
from datetime import timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..db import execute, get_db, query
from ..lms_core import completed_ids, course_progress, lesson_order, maybe_issue_certificate, next_lesson_id
from ..security import audit, student_required
from ..utils import clean, school_now, school_today

bp = Blueprint("learn", __name__, url_prefix="/learn")
public = Blueprint("public", __name__)


# ------------------------------------------------------------------ access helpers
def my_course(course_id):
    """A student may only open a PUBLISHED course they are enrolled in."""
    row = query("""SELECT c.* FROM lms_courses c JOIN lms_enrollments e ON e.course_id=c.id AND e.student_id=?
                   WHERE c.id=? AND c.status='published'""", (g.student_id, course_id), one=True)
    return row or abort(404)


def _owned(sql, ident):
    """Load a row that carries course_id, then verify enrolment in that course."""
    row = query(sql, (ident,), one=True) or abort(404)
    return row, my_course(row["course_id"])


# ------------------------------------------------------------------ home
@bp.route("/")
@student_required
def home():
    sid = g.student_id
    courses = query("""SELECT c.id, c.title, c.summary, c.level, e.completed_on FROM lms_enrollments e
                       JOIN lms_courses c ON c.id=e.course_id WHERE e.student_id=? AND c.status='published' ORDER BY c.title""", (sid,))
    cards = []
    for c in courses:
        p = course_progress(c["id"], sid)
        cards.append({**dict(c), **p, "next": next_lesson_id(c["id"], sid)})
    news = query("""SELECT a.title, a.body, a.created_at, c.title AS course_title, c.id AS course_id FROM lms_announcements a
                    JOIN lms_courses c ON c.id=a.course_id JOIN lms_enrollments e ON e.course_id=c.id AND e.student_id=?
                    WHERE c.status='published' ORDER BY a.id DESC LIMIT 5""", (sid,))
    todo = query("""SELECT a.id, a.title, a.due_date, c.title AS course_title FROM lms_assignments a
                    JOIN lms_courses c ON c.id=a.course_id JOIN lms_enrollments e ON e.course_id=c.id AND e.student_id=?
                    WHERE c.status='published' AND NOT EXISTS (SELECT 1 FROM lms_submissions s WHERE s.assignment_id=a.id AND s.student_id=?)
                    ORDER BY a.due_date IS NULL, a.due_date LIMIT 8""", (sid, sid))
    certs = query("""SELECT ce.course_id, ce.issued_on, c.title FROM lms_certificates ce JOIN lms_courses c ON c.id=ce.course_id
                     WHERE ce.student_id=? ORDER BY ce.issued_on DESC""", (sid,))
    return render_template("learn/home.html", cards=cards, news=news, todo=todo, certs=certs, today=school_today().isoformat())


# ------------------------------------------------------------------ course page
@bp.route("/course/<int:course_id>")
@student_required
def course(course_id):
    c = my_course(course_id)
    sid = g.student_id
    done = completed_ids(course_id, sid)
    lessons = lesson_order(course_id)
    mods = {}
    for l in lessons:
        mods.setdefault((l["module_id"], l["module_title"]), []).append(l)
    prog = course_progress(course_id, sid)
    assignments = query("""SELECT a.*, s.status AS sub_status, s.points FROM lms_assignments a
                           LEFT JOIN lms_submissions s ON s.assignment_id=a.id AND s.student_id=?
                           WHERE a.course_id=? ORDER BY a.due_date IS NULL, a.due_date, a.id""", (sid, course_id))
    quizzes = query("""SELECT z.*, (SELECT COUNT(*) FROM lms_questions q WHERE q.quiz_id=z.id) n_q,
                       (SELECT COUNT(*) FROM lms_attempts a WHERE a.quiz_id=z.id AND a.student_id=:s) n_att,
                       (SELECT MAX(passed) FROM lms_attempts a WHERE a.quiz_id=z.id AND a.student_id=:s) passed
                       FROM lms_quizzes z WHERE z.course_id=:c AND z.is_published=1 ORDER BY z.id""", {"s": sid, "c": course_id})
    news = query("SELECT * FROM lms_announcements WHERE course_id=? ORDER BY id DESC LIMIT 10", (course_id,))
    cert = query("SELECT code FROM lms_certificates WHERE course_id=? AND student_id=?", (course_id, sid), one=True)
    return render_template("learn/course.html", c=c, mods=mods, done=done, prog=prog, assignments=assignments,
                           quizzes=quizzes, news=news, cert=cert, next_id=next_lesson_id(course_id, sid), today=school_today().isoformat())


# ------------------------------------------------------------------ lessons
@bp.route("/lesson/<int:lesson_id>")
@student_required
def lesson(lesson_id):
    l, c = _owned("SELECT l.*, m.course_id, m.title AS module_title FROM lms_lessons l JOIN lms_modules m ON m.id=l.module_id WHERE l.id=?", lesson_id)
    order = lesson_order(c["id"])
    ids = [x["id"] for x in order]
    i = ids.index(lesson_id)
    done = completed_ids(c["id"], g.student_id)
    return render_template("learn/lesson.html", l=l, c=c, prev_id=ids[i - 1] if i > 0 else None,
                           next_id=ids[i + 1] if i + 1 < len(ids) else None, is_done=lesson_id in done, pos=i + 1, total=len(ids))


@bp.route("/lesson/<int:lesson_id>/complete", methods=["POST"])
@student_required
def lesson_complete(lesson_id):
    l, c = _owned("SELECT l.*, m.course_id FROM lms_lessons l JOIN lms_modules m ON m.id=l.module_id WHERE l.id=?", lesson_id)
    execute("INSERT OR IGNORE INTO lms_progress (student_id, lesson_id, completed_at) VALUES (?,?,?)",
            (g.student_id, lesson_id, school_now().strftime("%Y-%m-%d %H:%M")))
    code = maybe_issue_certificate(c["id"], g.student_id)
    if code:
        flash("🎉 Congratulations! You completed the course. Your certificate is ready.", "success")
        return redirect(url_for("learn.course", course_id=c["id"]))
    ids = [x["id"] for x in lesson_order(c["id"])]
    i = ids.index(lesson_id)
    if i + 1 < len(ids):
        return redirect(url_for("learn.lesson", lesson_id=ids[i + 1]))
    flash("Lesson completed. Check the course page for quizzes still to pass.", "success")
    return redirect(url_for("learn.course", course_id=c["id"]))


# ------------------------------------------------------------------ assignments
@bp.route("/assignment/<int:assignment_id>", methods=["GET", "POST"])
@student_required
def assignment(assignment_id):
    a, c = _owned("SELECT * FROM lms_assignments WHERE id=?", assignment_id)
    sub = query("SELECT * FROM lms_submissions WHERE assignment_id=? AND student_id=?", (assignment_id, g.student_id), one=True)
    late = bool(a["due_date"] and school_today().isoformat() > a["due_date"])
    if request.method == "POST":
        if sub and sub["status"] == "graded":
            flash("This assignment has already been graded and can't be changed.", "error")
            return redirect(url_for("learn.assignment", assignment_id=assignment_id))
        body = clean(request.form.get("body"), 10000)
        if not body:
            flash("Please write your answer before submitting.", "error")
            return render_template("learn/assignment.html", a=a, c=c, sub=sub, late=late, draft=body), 400
        now = school_now().strftime("%Y-%m-%d %H:%M")
        execute("""INSERT INTO lms_submissions (assignment_id, student_id, body, submitted_at) VALUES (?,?,?,?)
                   ON CONFLICT(assignment_id, student_id) DO UPDATE SET body=excluded.body, submitted_at=excluded.submitted_at
                   WHERE lms_submissions.status='submitted'""", (assignment_id, g.student_id, body, now))
        flash("Submitted! Your teacher will review it." + (" (Note: this was after the due date.)" if late else ""), "success")
        return redirect(url_for("learn.assignment", assignment_id=assignment_id))
    return render_template("learn/assignment.html", a=a, c=c, sub=sub, late=late, draft=sub["body"] if sub else "")


# ------------------------------------------------------------------ quizzes
@bp.route("/quiz/<int:quiz_id>", methods=["GET", "POST"])
@student_required
def quiz(quiz_id):
    z, c = _owned("SELECT * FROM lms_quizzes WHERE id=? AND is_published=1", quiz_id)
    attempts = query("SELECT * FROM lms_attempts WHERE quiz_id=? AND student_id=? ORDER BY id DESC", (quiz_id, g.student_id))
    remaining = z["max_attempts"] - len(attempts)
    questions = query("SELECT * FROM lms_questions WHERE quiz_id=? ORDER BY position, id", (quiz_id,))
    # NOTE: is_correct is deliberately NOT selected here, so answers can never leak to the page before submission.
    opts = query("""SELECT o.id, o.question_id, o.text FROM lms_options o JOIN lms_questions q ON q.id=o.question_id
                    WHERE q.quiz_id=? ORDER BY o.position, o.id""", (quiz_id,))
    by_q = {}
    for o in opts:
        by_q.setdefault(o["question_id"], []).append(o)

    if request.method == "POST":
        if remaining <= 0:
            flash("You have used all attempts for this quiz.", "error")
            return redirect(url_for("learn.quiz", quiz_id=quiz_id))
        correct = {r["question_id"]: r["id"] for r in query(
            "SELECT o.id, o.question_id FROM lms_options o JOIN lms_questions q ON q.id=o.question_id WHERE q.quiz_id=? AND o.is_correct=1", (quiz_id,))}
        chosen, score = {}, 0
        for q in questions:
            raw = request.form.get(f"q_{q['id']}", "")
            valid = {o["id"] for o in by_q.get(q["id"], [])}
            oid = int(raw) if raw.isdigit() and int(raw) in valid else None  # option must belong to THIS question
            chosen[q["id"]] = oid
            if oid is not None and correct.get(q["id"]) == oid:
                score += 1
        total = len(questions)
        passed = 1 if total and round(100 * score / total) >= z["pass_percent"] else 0
        db = get_db()
        with db:
            # single atomic statement => attempt limit can't be bypassed by double-clicking / parallel requests
            cur = db.execute("""INSERT INTO lms_attempts (quiz_id, student_id, submitted_at, score, total, passed)
                                SELECT ?,?,?,?,?,? WHERE (SELECT COUNT(*) FROM lms_attempts WHERE quiz_id=? AND student_id=?) < ?""",
                             (quiz_id, g.student_id, school_now().strftime("%Y-%m-%d %H:%M"), score, total, passed,
                              quiz_id, g.student_id, z["max_attempts"]))
            if cur.rowcount == 0:
                flash("You have used all attempts for this quiz.", "error")
                return redirect(url_for("learn.quiz", quiz_id=quiz_id))
            att_id = cur.lastrowid
            for qid, oid in chosen.items():
                db.execute("INSERT INTO lms_answers (attempt_id, question_id, option_id) VALUES (?,?,?)", (att_id, qid, oid))
        if passed:
            maybe_issue_certificate(c["id"], g.student_id)
        return redirect(url_for("learn.attempt", attempt_id=att_id))

    for qid in by_q:
        random.shuffle(by_q[qid])
    return render_template("learn/quiz.html", z=z, c=c, questions=questions, by_q=by_q, attempts=attempts, remaining=remaining)


@bp.route("/attempt/<int:attempt_id>")
@student_required
def attempt(attempt_id):
    at = query("""SELECT a.*, z.title, z.course_id, z.pass_percent, z.max_attempts FROM lms_attempts a JOIN lms_quizzes z ON z.id=a.quiz_id
                  WHERE a.id=? AND a.student_id=?""", (attempt_id, g.student_id), one=True) or abort(404)
    c = my_course(at["course_id"])
    rows = query("""SELECT q.text, q.position, ch.text AS chosen, ch.is_correct AS ok,
                    (SELECT o.text FROM lms_options o WHERE o.question_id=q.id AND o.is_correct=1 LIMIT 1) AS right
                    FROM lms_answers an JOIN lms_questions q ON q.id=an.question_id LEFT JOIN lms_options ch ON ch.id=an.option_id
                    WHERE an.attempt_id=? ORDER BY q.position, q.id""", (attempt_id,))
    pct = round(100 * at["score"] / at["total"]) if at["total"] else 0
    used = query("SELECT COUNT(*) c FROM lms_attempts WHERE quiz_id=? AND student_id=?", (at["quiz_id"], g.student_id), one=True)["c"]
    return render_template("learn/attempt.html", at=at, c=c, rows=rows, pct=pct, can_retry=used < at["max_attempts"])


# ------------------------------------------------------------------ certificate, attendance
@bp.route("/certificate/<int:course_id>")
@student_required
def certificate(course_id):
    c = my_course(course_id)
    maybe_issue_certificate(course_id, g.student_id)
    cert = query("""SELECT ce.*, s.full_name FROM lms_certificates ce JOIN students s ON s.id=ce.student_id
                    WHERE ce.course_id=? AND ce.student_id=?""", (course_id, g.student_id), one=True)
    if cert is None:
        flash("Finish all lessons and pass all quizzes to earn your certificate.", "info")
        return redirect(url_for("learn.course", course_id=course_id))
    return render_template("learn/certificate.html", c=c, cert=cert, verify_url=url_for("public.verify", code=cert["code"], _external=True))


@bp.route("/attendance")
@student_required
def my_attendance():
    since = (school_today() - timedelta(days=90)).isoformat()
    rows = query("""SELECT se.session_date, c.name AS class_name, a.status, a.remark FROM attendance a
                    JOIN sessions se ON se.id=a.session_id AND se.status='held' JOIN classes c ON c.id=se.class_id
                    WHERE a.student_id=? AND se.session_date>=? ORDER BY se.session_date DESC""", (g.student_id, since))
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    counted = counts.get("present", 0) + counts.get("late", 0) + counts.get("absent", 0)
    pct = round(100 * (counts.get("present", 0) + counts.get("late", 0)) / counted) if counted else None
    prog = query("""SELECT entry_date, log_type, from_ref, to_ref, grade, remarks FROM progress WHERE student_id=?
                    ORDER BY entry_date DESC, id DESC LIMIT 20""", (g.student_id,))
    return render_template("learn/attendance.html", rows=rows, counts=counts, pct=pct, prog=prog)


# ------------------------------------------------------------------ public verification (no login)
@public.route("/verify/<code>")
def verify(code):
    if not re.fullmatch(r"[A-F0-9]{16}", code):
        abort(404)
    cert = query("""SELECT ce.issued_on, ce.code, s.full_name, c.title FROM lms_certificates ce
                    JOIN students s ON s.id=ce.student_id JOIN lms_courses c ON c.id=ce.course_id WHERE ce.code=?""", (code,), one=True)
    return render_template("public/verify.html", cert=cert), (200 if cert else 404)
