"""SQLite helpers. ALL queries use ? placeholders -- never string formatting."""
import os
import sqlite3
import stat
import time
from pathlib import Path

from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migrate(backup=True):
    """Apply pending migrations/NNN_*.sql files in order (each exactly once). Returns names applied."""
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations
                  (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    db.commit()
    applied = {r[0] for r in db.execute("SELECT version FROM schema_migrations")}
    pending = [f for f in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")) if int(f.name[:3]) not in applied]
    path = current_app.config["DATABASE"]
    if pending and backup and os.path.exists(path) and os.path.getsize(path) > 0:
        bak = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        dst = sqlite3.connect(bak)
        db.backup(dst)  # consistent copy even with WAL
        dst.close()
        os.chmod(bak, stat.S_IRUSR | stat.S_IWUSR)
    for f in pending:
        try:
            db.executescript(f.read_text())
        except Exception:
            db.rollback()
            raise
        db.execute("INSERT INTO schema_migrations (version, name) VALUES (?,?)", (int(f.name[:3]), f.name))
        db.commit()
    bad = db.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError(f"Foreign key problems after migration: {len(bad)}")
    return [f.name for f in pending]


def init_db():
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode())
    db.commit()
    migrate(backup=False)
