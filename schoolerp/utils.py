"""Input validation and small helpers."""
import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Response, current_app

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CTRL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")


def school_now() -> datetime:
    return datetime.now(ZoneInfo(current_app.config["SCHOOL_TZ"]))


def school_today() -> date:
    return school_now().date()


def clean(value, maxlen=200) -> str:
    """Strip, drop control characters, cap length."""
    return CTRL_CHARS.sub("", (value or "").strip())[:maxlen]


def parse_date(value):
    value = (value or "").strip()
    if not DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def valid_time(value) -> bool:
    return bool(TIME_RE.match(value or ""))


def valid_month(value) -> bool:
    return bool(MONTH_RE.match(value or ""))


def valid_email(value) -> bool:
    return value == "" or bool(EMAIL_RE.match(value))


def valid_https_url(value) -> bool:
    """Only https:// links -- blocks javascript: / data: URLs in href attributes."""
    if value == "":
        return True
    try:
        p = urlparse(value)
    except ValueError:
        return False
    return p.scheme == "https" and bool(p.netloc) and len(value) <= 300


def parse_money(value):
    """'1500.50' -> 150050 (cents). Returns None if invalid."""
    try:
        d = Decimal((value or "0").strip() or "0")
    except InvalidOperation:
        return None
    if not d.is_finite() or d < 0 or d > Decimal("10000000"):
        return None
    return int((d * 100).to_integral_value())


def money(cents) -> str:
    return f"{(cents or 0) / 100:,.2f}"


def safe_next(target):
    """Only allow same-site relative redirects (prevents open redirect)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        p = urlparse(target)
        if not p.scheme and not p.netloc:
            return target
    return None


def month_bounds(month: str):
    y, m = int(month[:4]), int(month[5:7])
    start = date(y, m, 1)
    end = date(y + (m == 12), (m % 12) + 1, 1)
    return start.isoformat(), end.isoformat()  # [start, end)


def csv_cell(value):
    """Neutralise spreadsheet formula injection (=, +, -, @ at start of a cell)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def csv_response(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([csv_cell(c) for c in r])
    resp = Response("\ufeff" + buf.getvalue(), mimetype="text/csv")  # BOM: Excel reads Arabic/Urdu names
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def weekdays_label(csv_days: str) -> str:
    return ", ".join(WEEKDAY_NAMES[int(d)] for d in csv_days.split(",") if d.isdigit() and int(d) < 7)


def day_set(csv_days: str) -> set:
    return {int(d) for d in csv_days.split(",") if d.isdigit()}
