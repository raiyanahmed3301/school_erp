"""Database helpers for local SQLite and persistent PostgreSQL deployments."""
import re
import sqlite3

from flask import current_app, g

_PG_SCHEMA_READY = False


def _is_postgres():
    return str(current_app.config["DATABASE"]).startswith(("postgres://", "postgresql://"))


def _pg_sql(sql):
    sql = sql.replace("datetime('now')", "CURRENT_TIMESTAMP::text")
    ignore_conflicts = sql.lstrip().upper().startswith("INSERT OR IGNORE INTO")
    sql = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.I)
    if ignore_conflicts and "ON CONFLICT" not in sql.upper():
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql.replace("?", "%s")


class _PostgresConnection:
    """Small DB-API adapter so the existing query code can keep using ? params."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, args=()):
        from psycopg.rows import dict_row
        sql = _pg_sql(sql)
        # Return generated IDs for the application's insert helper.
        if sql.lstrip().upper().startswith("INSERT INTO") and "RETURNING" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        return self.conn.execute(sql, args, row_factory=dict_row)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        self._transaction = self.conn.transaction()
        self._transaction.__enter__()
        return self

    def __exit__(self, *exc):
        return self._transaction.__exit__(*exc)


def _initialize_postgres(conn):
    global _PG_SCHEMA_READY
    if _PG_SCHEMA_READY:
        return
    # Serialize cold starts and run the DDL once per database.
    # A transaction lock also works with pooled PostgreSQL URLs.
    conn.conn.execute("SELECT pg_advisory_xact_lock(73420911)")
    exists = conn.conn.execute(
        "SELECT to_regclass('public.school_erp_schema') AS name"
    ).fetchone()["name"]
    if not exists:
        schema = current_app.open_resource("schema.sql").read().decode()
        schema = re.sub(r"^\s*PRAGMA[^;]*;", "", schema, flags=re.I | re.M)
        schema = schema.replace("TEXT NOT NULL UNIQUE COLLATE NOCASE", "CITEXT NOT NULL UNIQUE")
        schema = re.sub(r"\bINTEGER PRIMARY KEY\b", "BIGSERIAL PRIMARY KEY", schema, flags=re.I)
        schema = schema.replace("datetime('now')", "CURRENT_TIMESTAMP::text")
        conn.conn.execute("CREATE EXTENSION IF NOT EXISTS citext")
        for statement in schema.split(";"):
            if statement.strip():
                conn.conn.execute(statement)
        conn.conn.execute("CREATE TABLE public.school_erp_schema (version INTEGER PRIMARY KEY)")
        conn.conn.execute("INSERT INTO public.school_erp_schema (version) VALUES (1)")
        conn.commit()
    else:
        conn.commit()


def get_db():
    if "db" not in g:
        database = str(current_app.config["DATABASE"])
        if database.startswith(("postgres://", "postgresql://")):
            import psycopg
            from psycopg.rows import dict_row
            url = "postgresql://" + database.split("://", 1)[1]
            raw = psycopg.connect(url, row_factory=dict_row)
            g.db = _PostgresConnection(raw)
            _initialize_postgres(g.db)
        else:
            g.db = sqlite3.connect(database)
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
    if _is_postgres() and sql.lstrip().upper().startswith("INSERT"):
        row = cur.fetchone()
        return row["id"] if row else None
    return cur.lastrowid


def init_db():
    db = get_db()
    if _is_postgres():
        _initialize_postgres(db)
        return
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode())
    db.commit()
