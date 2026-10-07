from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from ..db import execute, query
from ..security import (DUMMY_HASH, audit, hash_password, is_locked_out, login_required, login_user,
                        password_problems, record_attempt)
from ..utils import clean, safe_next

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        username = clean(request.form.get("username"), 64).lower()
        password = (request.form.get("password") or "")[:256]
        ip = request.remote_addr or ""
        if is_locked_out(username, ip):
            flash("Too many failed attempts. Please wait 15 minutes and try again.", "error")
            return render_template("login.html"), 429
        user = query("SELECT * FROM users WHERE username=?", (username,), one=True)
        hash_ = user["password_hash"] if user else DUMMY_HASH
        ok = check_password_hash(hash_, password) and user is not None and user["is_active"] == 1
        record_attempt(username, ip, ok)
        if not ok:
            flash("Incorrect username or password.", "error")  # deliberately generic
            return render_template("login.html"), 401
        login_user(user)
        g.user = user
        execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (user["id"],))
        audit("login", "user", user["id"])
        return redirect(safe_next(request.args.get("next")) or url_for("dashboard.index"))
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    if g.user:
        audit("logout", "user", g.user["id"])
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current", "")[:256]
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        errors = []
        if not check_password_hash(g.user["password_hash"], current):
            errors.append("Current password is incorrect.")
        if new != confirm:
            errors.append("New passwords do not match.")
        if new == current:
            errors.append("New password must be different from the current one.")
        errors += password_problems(new, g.user["username"])
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("change_password.html"), 400
        execute("UPDATE users SET password_hash=?, must_change_password=0, token_version=token_version+1 WHERE id=?",
                (hash_password(new), g.user["id"]))
        audit("password_changed", "user", g.user["id"])
        fresh = query("SELECT * FROM users WHERE id=?", (g.user["id"],), one=True)
        login_user(fresh)  # other sessions die (token_version changed); this one continues
        flash("Password updated.", "success")
        return redirect(url_for("dashboard.index"))
    return render_template("change_password.html")
