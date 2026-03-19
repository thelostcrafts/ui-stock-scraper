"""Shared Postgres connection helper for Vercel functions and monitor."""

import os
import json
from decimal import Decimal
from datetime import datetime, date
from typing import Optional, List, Dict, Any

import psycopg2
import psycopg2.extras


class PgJsonEncoder(json.JSONEncoder):
    """Handle Postgres types that json.dumps can't serialize by default."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


def pg_json_dumps(obj):
    # type: (...) -> str
    """JSON serialize with Postgres type support."""
    return json.dumps(obj, cls=PgJsonEncoder)


def get_db_url():
    # type: () -> str
    """Read DATABASE_URL from env, falling back to .env.local file."""
    url = os.environ.get('DATABASE_URL', '')
    if not url:
        # Try loading from .env.local for local dev
        env_file = os.path.join(os.path.dirname(__file__), '.env.local')
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    if line.startswith('DATABASE_URL='):
                        url = line.split('=', 1)[1].strip()
    return url


def get_db():
    """Get a new Postgres connection with sslmode=require."""
    conn = psycopg2.connect(get_db_url(), sslmode='require')
    return conn


def query_db(sql, params=()):
    # type: (str, tuple) -> List[Dict[str, Any]]
    """Execute a SELECT and return rows as list of dicts."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def execute_db(sql, params=()):
    # type: (str, tuple) -> None
    """Execute an INSERT/UPDATE/DELETE and commit."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
    finally:
        conn.close()


def execute_many_db(sql, params_list):
    # type: (str, list) -> None
    """Execute a parameterized statement for many rows and commit."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)
            conn.commit()
    finally:
        conn.close()


def log_error(source, message, traceback_str=None, context=None, level='error'):
    # type: (str, str, Optional[str], Optional[str], str) -> None
    """Log an error to the error_log table. Fails silently to avoid cascading."""
    try:
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        execute_db(
            "INSERT INTO error_log (timestamp, source, level, message, traceback, context) VALUES (%s, %s, %s, %s, %s, %s)",
            (now, source, level, message, traceback_str, context),
        )
    except Exception:
        pass
