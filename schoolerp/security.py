"""Authentication, sessions, CSRF, role checks, login throttling, security headers."""
import functools
import hmac
import secrets
import time
from urllib.parse import urlparse

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

from .db import execute, get_db, query

# Verified when the username doesn't exist, so timing doesn't reveal valid usernames.
DUMMY_HASH = generate_password_hash(secrets.token_hex(16))

LOCK_WINDOW = 15 * 60
MAX_FAILS_USER = 5
MAX_FAILS_IP = 25
COMMON_PASSWORDS = {"password123", "1234567890", "qwertyuiop", "iloveyou123", "admin12345",
                    "password1234", "letmein1234", "welcome1234", "bismillah123", "allahuakbar"}
TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


# ---------- passwords ----------
def password_problems(pw: str, username: str = "") -> list:
    errs = []
    if len(pw) < 10:
        errs.append("Password must be at least 10 characters.")
    if len(pw) > 128:
        errs.append("Password must be at most 128 characters.")
    if pw.isdigit() or pw.isalpha():
        errs.append("Password must mix letters with numbers or symbols.")
    if username and username.lower() in pw.lower():
        errs.append("Password must not contain the username.")
    if pw.lower() in COMMON_PASSWORDS:
        errs.append("That password is too common.")
    return errs


def temp_password() -> str:
    while True:
        pw = "".join(secrets.choice(TEMP_ALPHABET) for _ in range(12))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def hash_password(pw: str) -> str:
    return generate_password_hash(pw)  # scrypt with per-password salt


# ---------- audit ----------
def audit(action, entity="", entity_id=None, details=""):
    user = getattr(g, "user", None)
    db = get_db()
    db.execute(
        "INSERT INTO audit_log (user_id, username, action, entity, entity_id, details, ip) VALUES (?,?,?,?,?,?,?)",
        (user["id"] if user else None, user["username"] if user else "", action, entity, entity_id,
         details[:500], request.remote_addr or ""),
    )
    db.commit()


# ---------- login throttling ----------
def is_locked_out(username: str, ip: str) -> bool:
    since = int(time.time()) - LOCK_WINDOW
    u = query("SELECT COUNT(*) c FROM login_attempts WHERE username=? AND success=0 AND ts>?", (username, since), one=True)["c"]
    i = query("SELECT COUNT(*) c FROM login_attempts WHERE ip=? AND success=0 AND ts>?", (ip, since), one=True)["c"]
    return u >= MAX_FAILS_USER or i >= MAX_FAILS_IP


def record_attempt(username: str, ip: str, success: bool):
    execute("INSERT INTO login_attempts (username, ip, success, ts) VALUES (?,?,?,?)",
            (username[:64], ip, int(success), int(time.time())))
    execute("DELETE FROM login_attempts WHERE ts < ?", (int(time.time()) - 86400,))
    if success:  # a good login resets the counter for that user
        execute("DELETE FROM login_attempts WHERE username=? AND success=0", (username[:64],))


# ---------- CSRF ----------
def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_input() -> Markup:
    return Markup('<input type="hidden" name="_csrf" value="%s">') % csrf_token()


def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            abort(400)
        sent = request.form.get("_csrf", "")
        expected = session.get("csrf", "")
        if not sent or not expected or not hmac.compare_digest(sent, expected):
            abort(400)


# ---------- session / user loading ----------
def load_user():
    g.user = None
    uid = session.get("uid")
    if uid is None:
        return
    now = int(time.time())
    if now - session.get("last_seen", 0) > current_app.config["IDLE_TIMEOUT"]:
        session.clear()
        return
    row = query("SELECT * FROM users WHERE id=? AND is_active=1", (uid,), one=True)
    if row is None or row["token_version"] != session.get("tv"):
        session.clear()
        return
    session["last_seen"] = now
    g.user = row


def enforce_password_change():
    if g.user and g.user["must_change_password"]:
        if request.endpoint not in ("auth.change_password", "auth.logout", "static"):
            return redirect(url_for("auth.change_password"))


def login_user(user):
    session.clear()  # new session on login (prevents session fixation)
    session.permanent = True
    session["uid"] = user["id"]
    session["tv"] = user["token_version"]
    session["last_seen"] = int(time.time())
    csrf_token()


# ---------- access control ----------
def login_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if g.user["role"] != "admin":
            abort(403)
        return view(*a, **kw)
    return wrapped


def is_admin() -> bool:
    return g.user is not None and g.user["role"] == "admin"


def get_class_or_404(class_id):
    """Object-level authorization: teachers can only touch their own classes."""
    row = query("""SELECT c.*, co.name AS course_name, u.full_name AS teacher_name
                   FROM classes c JOIN courses co ON co.id=c.course_id JOIN users u ON u.id=c.teacher_id
                   WHERE c.id=?""", (class_id,), one=True)
    if row is None or (not is_admin() and row["teacher_id"] != g.user["id"]):
        abort(404)
    return row


def get_student_or_404(student_id):
    """Teachers can only see students enrolled (now or before) in their own classes."""
    row = query("SELECT * FROM students WHERE id=?", (student_id,), one=True)
    if row is None:
        abort(404)
    if not is_admin():
        ok = query("""SELECT 1 FROM enrollments e JOIN classes c ON c.id=e.class_id
                      WHERE e.student_id=? AND c.teacher_id=? LIMIT 1""", (student_id, g.user["id"]), one=True)
        if ok is None:
            abort(404)
    return row


# ---------- response headers ----------
def add_security_headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if current_app.config["SESSION_COOKIE_SECURE"]:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.endpoint != "static":  # never cache pages that contain student data
        resp.headers["Cache-Control"] = "no-store"
    return resp
