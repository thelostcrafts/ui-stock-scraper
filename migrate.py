#!/usr/bin/env python3
"""
One-time migration: SQLite -> Neon Postgres.

WARNING: This script is NOT idempotent. Running it twice will duplicate rows
in the events, scans, and price_history tables. Only run once, or TRUNCATE
those tables first if re-running.

Usage:
    export $(grep DATABASE_URL .env.local | xargs)
    python3 migrate.py
"""

import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

DATA_DIR = Path(__file__).parent / 'data'
SQLITE_DB = DATA_DIR / 'inventory.db'


def migrate():
    # --- Connect to SQLite (source) ---
    if not SQLITE_DB.exists():
        print(f"ERROR: SQLite database not found at {SQLITE_DB}")
        return
    lite = sqlite3.connect(str(SQLITE_DB))
    lite.row_factory = sqlite3.Row

    # --- Connect to Postgres (destination) ---
    pg_url = os.environ.get('DATABASE_URL', '')
    if not pg_url:
        env_file = Path(__file__).parent / '.env.local'
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith('DATABASE_URL='):
                    pg_url = line.split('=', 1)[1].strip()
    if not pg_url:
        print("ERROR: DATABASE_URL not set and .env.local not found")
        return

    pg = psycopg2.connect(pg_url, sslmode='require')
    now = datetime.now(timezone.utc).isoformat()

    with pg.cursor() as cur:
        # --- products (ON CONFLICT DO NOTHING for safety) ---
        rows = lite.execute("SELECT * FROM products").fetchall()
        if rows:
            cols = rows[0].keys()
            col_names = ', '.join(cols)
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO products ({}) VALUES %s ON CONFLICT DO NOTHING".format(col_names),
                [tuple(r) for r in rows]
            )
            print("products: {} rows migrated".format(len(rows)))
        else:
            print("products: 0 rows (table empty)")

        # --- events (no conflict handling — append-only) ---
        rows = lite.execute(
            "SELECT timestamp, sku, region, name, event_type, old_value, new_value, details "
            "FROM events ORDER BY id"
        ).fetchall()
        if rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO events (timestamp, sku, region, name, event_type, old_value, new_value, details) VALUES %s",
                [tuple(r) for r in rows]
            )
            print("events: {} rows migrated".format(len(rows)))
        else:
            print("events: 0 rows (table empty)")

        # --- scans ---
        rows = lite.execute(
            "SELECT timestamp, build_id, sku_count, available, sold_out, coming_soon, "
            "categories_changed, categories_unchanged, requests "
            "FROM scans ORDER BY id"
        ).fetchall()
        if rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO scans (timestamp, build_id, sku_count, available, sold_out, coming_soon, "
                "categories_changed, categories_unchanged, requests) VALUES %s",
                [tuple(r) for r in rows]
            )
            print("scans: {} rows migrated".format(len(rows)))
        else:
            print("scans: 0 rows (table empty)")

        # --- price_history ---
        rows = lite.execute(
            "SELECT timestamp, sku, region, price_cents, status "
            "FROM price_history ORDER BY id"
        ).fetchall()
        if rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO price_history (timestamp, sku, region, price_cents, status) VALUES %s",
                [tuple(r) for r in rows]
            )
            print("price_history: {} rows migrated".format(len(rows)))
        else:
            print("price_history: 0 rows (table empty)")

        # --- watchlist (ON CONFLICT DO NOTHING for safety) ---
        rows = lite.execute("SELECT * FROM watchlist").fetchall()
        if rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO watchlist (sku, added_at, notes) VALUES %s ON CONFLICT DO NOTHING",
                [tuple(r) for r in rows]
            )
            print("watchlist: {} rows migrated".format(len(rows)))
        else:
            print("watchlist: 0 rows (table empty)")

        # --- monitor_state (seed from JSON state files) ---
        state_items = []

        hashes_file = DATA_DIR / 'content_hashes.json'
        if hashes_file.exists():
            state_items.append(('content_hashes', hashes_file.read_text().strip(), now))

        build_file = DATA_DIR / 'build_id.txt'
        if build_file.exists():
            state_items.append(('build_id', build_file.read_text().strip(), now))

        backoff_file = DATA_DIR / 'backoff_state.json'
        if backoff_file.exists():
            state_items.append(('backoff_state', backoff_file.read_text().strip(), now))

        if state_items:
            cur.executemany(
                "INSERT INTO monitor_state (key, value, updated_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
                state_items
            )
            print("monitor_state: {} keys seeded".format(len(state_items)))
        else:
            print("monitor_state: no state files found to seed")

        pg.commit()

    lite.close()
    pg.close()
    print("Migration complete.")


if __name__ == '__main__':
    migrate()
