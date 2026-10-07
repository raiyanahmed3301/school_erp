import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..db import execute, get_db, query
from ..security import admin_required, audit
from ..utils import (
    clean,
    csv_response,
    money,
    parse_date,
    parse_money,
    school_today,
    valid_month,
)
from .students import CURRENCIES

bp = Blueprint("fees", __name__, url_prefix="/fees")
METHODS = ("PayPal", "UPI", "Bank transfer", "Card", "Cash", "Other")


@bp.route("/")
@admin_required
def list_():
    status = request.args.get("status", "")
    month = request.args.get("month", "")
    q = clean(request.args.get("q"), 60)
    sql = """SELECT f.*, s.full_name, s.student_code FROM fees f JOIN students s ON s.id=f.student_id"""
    where, args = [], []
    if status in ("pending", "paid", "waived", "overdue"):
        if status == "overdue":
            where.append("f.status='pending' AND f.due_date<?")
            args.append(school_today().isoformat())
        else:
            where.append("f.status=?")
            args.append(status)
    if valid_month(month):
        where.append("f.period=?")
        args.append(month)
    if q:
        where.append("s.full_name LIKE ? ESCAPE '\\'")
        args.append(
            "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = query(sql + " ORDER BY f.due_date DESC, s.full_name LIMIT 500", args)
    if request.args.get("format") == "csv":
        return csv_response(
            "fees.csv",
            [
                "Student ID",
                "Name",
                "Period",
                "Amount",
                "Currency",
                "Due",
                "Status",
                "Paid on",
                "Method",
                "Reference",
            ],
            [
                (
                    r["student_code"],
                    r["full_name"],
                    r["period"],
                    money(r["amount_cents"]),
                    r["currency"],
                    r["due_date"],
                    r["status"],
                    r["paid_on"] or "",
                    r["method"],
                    r["reference"],
                )
                for r in rows
            ],
        )
    return render_template(
        "fees/list.html",
        fees=rows,
        status=status,
        month=month,
        q=q,
        today=school_today().isoformat(),
    )


@bp.route("/new", methods=["GET", "POST"])
@admin_required
def new():
    students = query(
        "SELECT id, student_code, full_name, monthly_fee_cents, currency FROM students WHERE status IN ('trial','active','paused') ORDER BY full_name"
    )
    student_opts = [
        (s["id"], f"{s['full_name']} ({s['student_code']})") for s in students
    ]
    if request.method == "POST":
        sid = request.form.get("student_id", "")
        period = request.form.get("period", "")
        amount = parse_money(request.form.get("amount"))
        currency = request.form.get("currency", "")
        due = parse_date(request.form.get("due_date"))
        errs = []
        if not (
            sid.isdigit()
            and query("SELECT 1 FROM students WHERE id=?", (sid,), one=True)
        ):
            errs.append("Choose a student.")
        if not valid_month(period):
            errs.append("Period must be YYYY-MM.")
        if amount is None:
            errs.append("Amount is invalid.")
        if currency not in CURRENCIES:
            errs.append("Invalid currency.")
        if due is None:
            errs.append("Due date must be YYYY-MM-DD.")
        if not errs:
            try:
                fid = execute(
                    "INSERT INTO fees (student_id, period, amount_cents, currency, due_date, notes) VALUES (?,?,?,?,?,?)",
                    (
                        int(sid),
                        period,
                        amount,
                        currency,
                        due.isoformat(),
                        clean(request.form.get("notes"), 200),
                    ),
                )
                audit("fee_created", "fee", fid, f"student {sid} {period}")
                flash("Fee record added.", "success")
                return redirect(url_for("fees.list_"))
            except sqlite3.IntegrityError:
                errs.append("That student already has a fee record for this month.")
        for e in errs:
            flash(e, "error")
        return render_template(
            "fees/form.html",
            student_opts=student_opts,
            CURRENCIES=CURRENCIES,
            f=request.form,
        ), 400
    today = school_today()
    return render_template(
        "fees/form.html",
        student_opts=student_opts,
        CURRENCIES=CURRENCIES,
        f={
            "period": today.strftime("%Y-%m"),
            "due_date": today.isoformat(),
            "currency": "INR",
        },
    )


@bp.route("/generate", methods=["POST"])
@admin_required
def generate():
    """Create a pending fee for every active student with a monthly fee (skips ones that already exist)."""
    period = request.form.get("period", "")
    due = parse_date(request.form.get("due_date"))
    if not valid_month(period) or due is None:
        flash("Choose a valid month and due date.", "error")
        return redirect(url_for("fees.list_"))
    db = get_db()
    with db:
        cur = db.execute(
            """INSERT OR IGNORE INTO fees (student_id, period, amount_cents, currency, due_date)
                            SELECT id, ?, monthly_fee_cents, currency, ? FROM students
                            WHERE status='active' AND monthly_fee_cents>0""",
            (period, due.isoformat()),
        )
    audit("fees_generated", "fee", None, f"{period}: {cur.rowcount} created")
    flash(f"{cur.rowcount} fee record(s) created for {period}.", "success")
    return redirect(url_for("fees.list_", month=period))


@bp.route("/<int:fee_id>/pay", methods=["POST"])
@admin_required
def pay(fee_id):
    f = query("SELECT * FROM fees WHERE id=?", (fee_id,), one=True) or abort(404)
    action = request.form.get("action", "")
    if action == "paid":
        raw_paid_on = request.form.get("paid_on", "").strip()
        paid = parse_date(raw_paid_on) if raw_paid_on else school_today()
        if paid is None or paid > school_today():
            abort(400)
        method = request.form.get("method", "Other")
        if method not in METHODS:
            abort(400)
        execute(
            "UPDATE fees SET status='paid', paid_on=?, method=?, reference=? WHERE id=?",
            (
                paid.isoformat(),
                method,
                clean(request.form.get("reference"), 80),
                fee_id,
            ),
        )
        audit("fee_paid", "fee", fee_id, f"{f['period']} {method}")
        flash("Marked as paid.", "success")
    elif action == "waived":
        execute("UPDATE fees SET status='waived' WHERE id=?", (fee_id,))
        audit("fee_waived", "fee", fee_id)
        flash("Fee waived.", "success")
    elif action == "reopen":
        execute("UPDATE fees SET status='pending', paid_on=NULL WHERE id=?", (fee_id,))
        audit("fee_reopened", "fee", fee_id)
    else:
        abort(400)
    return redirect(url_for("fees.list_"))
