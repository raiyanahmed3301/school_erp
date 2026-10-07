from flask import Blueprint, render_template, request
from ..db import query
from ..security import admin_required

bp = Blueprint("audit_view", __name__, url_prefix="/audit")


@bp.route("/")
@admin_required
def index():
    page = max(1, int(request.args.get("page", "1"))) if request.args.get("page", "1").isdigit() else 1
    rows = query("SELECT * FROM audit_log ORDER BY id DESC LIMIT 100 OFFSET ?", ((page - 1) * 100,))
    return render_template("audit.html", rows=rows, page=page)
