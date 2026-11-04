"""Shared LMS logic: lesson ordering, progress, completion and certificates."""
import secrets

from .db import get_db, query
from .utils import school_today


def lesson_order(course_id):
    return query("""SELECT l.id, l.title, l.kind, l.duration_min, m.id AS module_id, m.title AS module_title
                    FROM lms_lessons l JOIN lms_modules m ON m.id=l.module_id WHERE m.course_id=?
                    ORDER BY m.position, m.id, l.position, l.id""", (course_id,))


def completed_ids(course_id, student_id):
    rows = query("""SELECT p.lesson_id FROM lms_progress p JOIN lms_lessons l ON l.id=p.lesson_id
                    JOIN lms_modules m ON m.id=l.module_id WHERE m.course_id=? AND p.student_id=?""", (course_id, student_id))
    return {r["lesson_id"] for r in rows}


def course_progress(course_id, student_id):
    total = query("""SELECT COUNT(*) c FROM lms_lessons l JOIN lms_modules m ON m.id=l.module_id WHERE m.course_id=?""",
                  (course_id,), one=True)["c"]
    done = len(completed_ids(course_id, student_id))
    q_total = query("SELECT COUNT(*) c FROM lms_quizzes WHERE course_id=? AND is_published=1", (course_id,), one=True)["c"]
    q_passed = query("""SELECT COUNT(*) c FROM lms_quizzes z WHERE z.course_id=? AND z.is_published=1 AND EXISTS
                        (SELECT 1 FROM lms_attempts a WHERE a.quiz_id=z.id AND a.student_id=? AND a.passed=1)""",
                     (course_id, student_id), one=True)["c"]
    pct = round(100 * done / total) if total else 0
    return {"total": total, "done": done, "pct": pct, "quizzes_total": q_total, "quizzes_passed": q_passed,
            "complete": total > 0 and done >= total and q_passed >= q_total}


def maybe_issue_certificate(course_id, student_id):
    """Issue a certificate once every lesson is done and every published quiz is passed. Returns code or None."""
    if not course_progress(course_id, student_id)["complete"]:
        return None
    row = query("SELECT code FROM lms_certificates WHERE course_id=? AND student_id=?", (course_id, student_id), one=True)
    if row:
        return row["code"]
    code = secrets.token_hex(8).upper()
    today = school_today().isoformat()
    db = get_db()
    with db:
        db.execute("INSERT OR IGNORE INTO lms_certificates (course_id, student_id, code, issued_on) VALUES (?,?,?,?)",
                   (course_id, student_id, code, today))
        db.execute("UPDATE lms_enrollments SET completed_on=? WHERE course_id=? AND student_id=? AND completed_on IS NULL",
                   (today, course_id, student_id))
    return query("SELECT code FROM lms_certificates WHERE course_id=? AND student_id=?", (course_id, student_id), one=True)["code"]


def next_lesson_id(course_id, student_id):
    done = completed_ids(course_id, student_id)
    for l in lesson_order(course_id):
        if l["id"] not in done:
            return l["id"]
    return None
