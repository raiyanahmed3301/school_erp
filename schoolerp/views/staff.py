import sqlite3
from flask import Blueprint, flash, g, redirect, render_template, request, url_for, abort

from ..db import execute, query
from ..security import admin_required, audit, hash_password, password_problems, temp_password
from ..utils import USERNAME_RE, clean, valid_email

bp = Blueprint("staff", __name__, url_prefix="/staff")


def _validate(form, creating):
    d = {"full_name": clean(form.get("full_name"), 100), "email": clean(form.get("email"), 150).lower(),
         "role": form.get("role", "teacher"), "gender": form.get("gender", ""),
         "username": clean(form.get("username"), 32).lower()}
    errs = []
    if not d["full_name"]: errs.append("Full name is required.")
    if d["role"] not in ("admin", "teacher"): errs.append("Invalid role.")
    if d["gender"] not in ("", "male", "female"): errs.append("Invalid gender.")
    if not valid_email(d["email"]): errs.append("Email looks invalid.")
    if creating and not USERNAME_RE.match(d["username"]):
        errs.append("Username: 3-32 characters, lowercase letters, numbers, . _ -")
    return d, errs


def _active_admins():
    return query("SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1", one=True)["c"]


@bp.route("/")
@admin_required
def list_():
    return render_template("staff/list.html", users=query("SELECT * FROM users ORDER BY is_active DESC, role, full_name"))


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def new():
    if request.method == "POST":
        d, errs = _validate(request.form, True)
        if errs:
            for e in errs: flash(e, "error")
            return render_template("staff/form.html", u=d, mode="new"), 400
        pw = temp_password()
        try:
            uid = execute("""INSERT INTO users (username, full_name, email, role, gender, password_hash, must_change_password)
                             VALUES (?,?,?,?,?,?,1)""",
                          (d["username"], d["full_name"], d["email"], d["role"], d["gender"], hash_password(pw)))
        except sqlite3.IntegrityError:
            flash("That username is already taken.", "error")
            return render_template("staff/form.html", u=d, mode="new"), 400
        audit("user_created", "user", uid, f"{d['username']} ({d['role']})")
        # Shown once, in this response only (not stored, not in a redirect/flash).
        return render_template("staff/credentials.html", username=d["username"], password=pw, full_name=d["full_name"])
    return render_template("staff/form.html", u={"role": "teacher"}, mode="new")


@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(user_id):
    u = query("SELECT * FROM users WHERE id=?", (user_id,), one=True) or abort(404)
    if request.method == "POST":
        d, errs = _validate(request.form, False)
        active = 1 if request.form.get("is_active") == "1" else 0
        if u["id"] == g.user["id"] and (d["role"] != "admin" or not active):
            errs.append("You cannot remove your own admin access or deactivate yourself.")
        if u["role"] == "admin" and (d["role"] != "admin" or not active) and _active_admins() <= 1:
            errs.append("At least one active admin is required.")
        if errs:
            for e in errs: flash(e, "error")
            d.update(id=u["id"], username=u["username"], is_active=active)
            return render_template("staff/form.html", u=d, mode="edit"), 400
        bump = 1 if (not active or d["role"] != u["role"]) else 0  # kill sessions on deactivate / role change
        execute("""UPDATE users SET full_name=?, email=?, role=?, gender=?, is_active=?, token_version=token_version+? WHERE id=?""",
                (d["full_name"], d["email"], d["role"], d["gender"], active, bump, user_id))
        audit("user_updated", "user", user_id, u["username"])
        flash("Account updated.", "success")
        return redirect(url_for("staff.list_"))
    return render_template("staff/form.html", u=u, mode="edit")


@bp.route("/<int:user_id>/reset", methods=["POST"])
@admin_required
def reset(user_id):
    u = query("SELECT * FROM users WHERE id=?", (user_id,), one=True) or abort(404)
    pw = temp_password()
    execute("UPDATE users SET password_hash=?, must_change_password=1, token_version=token_version+1 WHERE id=?",
            (hash_password(pw), user_id))
    audit("password_reset", "user", user_id, u["username"])
    return render_template("staff/credentials.html", username=u["username"], password=pw, full_name=u["full_name"])
