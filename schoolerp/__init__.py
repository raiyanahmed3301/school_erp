"""Quran Live Class -- School ERP. Application factory."""
import os
from datetime import timedelta

import click
from flask import Flask, g, render_template
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db, security, utils
from .config import INSTANCE_DIR, IS_VERCEL, load_secret_key


def create_app(test_config=None):
    database_url = os.environ.get("DATABASE_URL")
    if IS_VERCEL and not database_url:
        raise RuntimeError("Set DATABASE_URL to a persistent PostgreSQL database in Vercel Project Settings.")
    if IS_VERCEL and not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection string on Vercel.")
    app = Flask(__name__, instance_path=str(INSTANCE_DIR))
    dev = os.environ.get("SCHOOL_ERP_ENV") == "development"
    app.config.update(
        SECRET_KEY=load_secret_key(),
        DATABASE=database_url or str(INSTANCE_DIR / "school.db"),
        SCHOOL_NAME="Quran Live Class",
        SCHOOL_TZ=os.environ.get("SCHOOL_TZ", "Asia/Kolkata"),
        STUDENT_PREFIX="QLC",
        SESSION_COOKIE_NAME="session" if dev else "__Host-session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=not dev,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        IDLE_TIMEOUT=30 * 60,
        MAX_CONTENT_LENGTH=256 * 1024,
        TEACHER_BACKDATE_DAYS=7,
    )
    if test_config:
        app.config.update(test_config)
    INSTANCE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.environ.get("BEHIND_PROXY") == "1":  # one trusted reverse proxy (nginx/caddy)
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.teardown_appcontext(db.close_db)
    app.before_request(security.load_user)
    app.before_request(security.csrf_protect)
    app.before_request(security.enforce_password_change)
    app.after_request(security.add_security_headers)

    app.jinja_env.filters["money"] = utils.money
    app.jinja_env.filters["weekdays"] = utils.weekdays_label
    app.jinja_env.filters["pairs"] = lambda seq: [(x, x) for x in seq]
    app.jinja_env.filters["titled"] = lambda seq: [(x, x.title()) for x in seq]
    app.jinja_env.filters["rowopts"] = lambda rows, k, label: [(r[k], r[label]) for r in rows]

    @app.context_processor
    def inject():
        return dict(csrf_input=security.csrf_input, school_name=app.config["SCHOOL_NAME"],
                    is_admin=security.is_admin, WEEKDAYS=utils.WEEKDAY_NAMES)

    from .views import attendance, audit_view, auth, dashboard, fees, reports, staff, students, academics
    for m in (auth, dashboard, students, staff, academics, attendance, reports, fees, audit_view):
        app.register_blueprint(m.bp)

    for code, msg in {400: "Bad request (the form may have expired -- please go back and try again).",
                      403: "You don't have permission to do that.", 404: "Page not found.",
                      405: "Method not allowed.", 413: "Request too large."}.items():
        app.register_error_handler(code, lambda e, code=code, msg=msg: (render_template("error.html", code=code, msg=msg), code))

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("error.html", code=500, msg="Something went wrong on our side."), 500

    @app.cli.command("init-db")
    def init_db_cmd():
        """Create tables (refuses to overwrite an existing database)."""
        if os.path.exists(app.config["DATABASE"]) and os.path.getsize(app.config["DATABASE"]) > 0:
            raise click.ClickException("Database already exists. Delete instance/school.db to start over.")
        db.init_db()
        os.chmod(app.config["DATABASE"], 0o600)
        click.echo("Database created.")

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt=True)
    @click.password_option()
    def create_admin(username, full_name, password):
        """Create an administrator account."""
        username = username.strip().lower()
        if not utils.USERNAME_RE.match(username):
            raise click.ClickException("Username: 3-32 chars, a-z 0-9 . _ -")
        problems = security.password_problems(password, username)
        if problems:
            raise click.ClickException(" ".join(problems))
        db.execute("INSERT INTO users (username, full_name, role, password_hash, must_change_password) VALUES (?,?, 'admin', ?, 0)",
                   (username, full_name.strip(), security.hash_password(password)))
        click.echo(f"Admin '{username}' created.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """DEV ONLY: sample teachers, students and classes."""
        if not dev:
            raise click.ClickException("Refusing: set SCHOOL_ERP_ENV=development to seed demo data.")
        from .demo import seed
        seed()

    return app
