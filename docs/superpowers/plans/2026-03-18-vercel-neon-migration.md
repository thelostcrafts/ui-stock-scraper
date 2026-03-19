# Vercel + Neon Postgres Migration Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the Mac Mini single point of failure by migrating the dashboard to Vercel, the database to Neon Postgres, and the scraper to GitHub Actions — all on free tiers.

**Architecture:**
```
GitHub Actions (cron: */15) → monitor_cloud.py → Neon Postgres ← Vercel API routes ← Static SPA
```
The cloud monitor (`monitor_cloud.py`) runs as a scheduled GitHub Action every 15 minutes, writing to Neon Postgres. The dashboard is a Vercel app: static SPA (copied from `dashboard.py`'s `HTML_PAGE`) + Python serverless functions for API routes reading from the same Neon database. JSON state files (content_hashes, build_id, backoff) move to a `monitor_state` table; the previous product snapshot is queried from the `products` table directly.

**Standalone version is untouched.** `monitor.py` and `dashboard.py` continue working on the Mac Mini with SQLite exactly as they do today. Both versions can run simultaneously.

**Tech Stack:** Python 3.9+, Neon Postgres (free tier), Vercel (Python serverless functions), GitHub Actions, psycopg2-binary, httpx

---

## File Structure

```
ui-stock-scraper/
├── api/                              # NEW: Vercel Python serverless functions
│   ├── products.py                   # GET /api/products
│   ├── events.py                     # GET /api/events
│   ├── scans.py                      # GET /api/scans
│   ├── stats.py                      # GET /api/stats
│   ├── categories.py                 # GET /api/categories
│   ├── product-history.py            # GET /api/product-history
│   ├── sold-out.py                   # GET /api/sold-out
│   ├── health.py                     # GET /api/health
│   ├── price-analytics.py            # GET /api/price-analytics
│   ├── regions.py                    # GET /api/regions
│   ├── region-stock.py               # GET /api/region-stock
│   ├── availability-windows.py       # GET /api/availability-windows
│   ├── hot-items.py                  # GET /api/hot-items
│   └── watchlist/
│       ├── index.py                  # GET/POST /api/watchlist
│       └── remove.py                 # POST /api/watchlist/remove
├── db.py                             # NEW: Shared Postgres connection helper
├── public/
│   └── index.html                    # NEW: Static SPA (copied from dashboard.py HTML_PAGE)
├── monitor_cloud.py                  # NEW: Cloud scraper (Postgres + state table)
├── migrate.py                        # NEW: One-time SQLite → Postgres migration script
├── .github/
│   └── workflows/
│       └── monitor.yml               # NEW: Scheduled scraper (every 15 min)
├── vercel.json                       # NEW: Vercel config (routes, python runtime)
├── requirements.txt                  # NEW: psycopg2-binary, httpx
├── .env.local                        # DATABASE_URL (local dev, gitignored)
│
│── monitor.py                        # UNCHANGED: Standalone SQLite scraper (Mac Mini)
│── dashboard.py                      # UNCHANGED: Standalone SQLite dashboard (Mac Mini)
│── run-monitor.sh                    # UNCHANGED: launchd wrapper (Mac Mini)
├── .gitignore                        # Updated (add .vercel, .env*.local)
├── CLAUDE.md                         # Updated (add cloud deployment section)
└── README.md                         # Updated (add cloud deployment section)
```

**No files removed.** The standalone version (`monitor.py`, `dashboard.py`, `run-monitor.sh`) remains fully functional. The cloud version (`monitor_cloud.py`, `api/`, `public/`) is additive.

**Files preserved (unchanged):**
- `monitor.py` — standalone SQLite scraper (Mac Mini)
- `dashboard.py` — standalone SQLite dashboard (Mac Mini)
- `run-monitor.sh` — launchd wrapper (Mac Mini)
- `ui-scraper.py` — original recon script (reference only)
- `ui_store_recon_report.json` — recon findings (reference only)

---

## SQL Dialect Translation Reference

All queries need these substitutions when porting from SQLite to Postgres:

| SQLite | Postgres | Notes |
|--------|----------|-------|
| `?` | `%s` | Parameter placeholder |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` | Auto-increment |
| `INSERT OR REPLACE INTO` | `INSERT ... ON CONFLICT DO UPDATE` | Watchlist upsert |
| `json_extract(details, '$.delta_cents')` | `details::json->>'delta_cents'` | JSON field access |
| `datetime('now', '-7 days')` | `CURRENT_TIMESTAMP - INTERVAL '7 days'` | Date math |
| `datetime('now', '-24 hours')` | `CURRENT_TIMESTAMP - INTERVAL '24 hours'` | Date math |
| `LIKE` (case-insensitive in SQLite) | `ILIKE` | Search queries (products, sold-out) |
| `CAST(json_extract(...) AS INTEGER)` | `CAST(details::json->>'key' AS INTEGER)` or `(details::json->>'key')::int` | Price analytics |
| `conn.row_factory = sqlite3.Row` | `cursor_factory=RealDictCursor` | Dict results |

**Postgres type serialization:** `AVG()` and `ROUND()` return `Decimal` in Postgres (not `float`). Use `pg_json_dumps()` from `db.py` instead of `json.dumps()` in all API functions to handle `Decimal`, `datetime`, and `date` types.

---

## Task 1: Neon Project & Schema Setup

**Files:**
- Create: `schema.sql`

- [ ] **Step 1: Create Neon project**

Go to https://console.neon.tech → Create Project → name: `ui-stock-scraper` → Region: US East (closest to GitHub Actions). Copy the connection string (starts with `postgresql://`).

- [ ] **Step 2: Store connection string locally**

```bash
echo 'DATABASE_URL=postgresql://neondb_owner:****@ep-****.us-east-2.aws.neon.tech/neondb?sslmode=require' > .env.local
```

Verify `.env.local` is in `.gitignore` (it already is).

- [ ] **Step 3: Write the Postgres schema**

Create `schema.sql` with all 6 tables (5 existing + 1 new `monitor_state` table). The schema is nearly identical to SQLite with these changes:
- `AUTOINCREMENT` → `SERIAL`
- Add `monitor_state` table for JSON state files
- Add `TIMESTAMP` defaults where appropriate

```sql
-- Products: primary inventory table
CREATE TABLE IF NOT EXISTS products (
    sku             TEXT NOT NULL,
    region          TEXT NOT NULL DEFAULT 'us/en',
    slug            TEXT NOT NULL,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    subcategory     TEXT NOT NULL,
    price_cents     INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    regular_price_cents INTEGER,
    status          TEXT NOT NULL,
    variant_id      TEXT,
    thumbnail       TEXT,
    first_seen      TEXT NOT NULL,
    last_updated    TEXT NOT NULL,
    PRIMARY KEY (sku, region)
);

-- Watchlist: user-tagged SKUs for focused tracking
CREATE TABLE IF NOT EXISTS watchlist (
    sku         TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    notes       TEXT
);

-- Events: append-only change log
CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    sku         TEXT NOT NULL,
    region      TEXT NOT NULL DEFAULT 'us/en',
    name        TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    details     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_sku ON events(sku);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

-- Scans: metadata for each monitor run
CREATE TABLE IF NOT EXISTS scans (
    id          SERIAL PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    build_id    TEXT NOT NULL,
    sku_count   INTEGER NOT NULL,
    available   INTEGER NOT NULL DEFAULT 0,
    sold_out    INTEGER NOT NULL DEFAULT 0,
    coming_soon INTEGER NOT NULL DEFAULT 0,
    categories_changed  INTEGER NOT NULL DEFAULT 0,
    categories_unchanged INTEGER NOT NULL DEFAULT 0,
    requests    INTEGER NOT NULL DEFAULT 0
);

-- Price history: one row per SKU per scan (time-series)
CREATE TABLE IF NOT EXISTS price_history (
    id          SERIAL PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    sku         TEXT NOT NULL,
    region      TEXT NOT NULL DEFAULT 'us/en',
    price_cents INTEGER NOT NULL,
    status      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ph_sku ON price_history(sku);
CREATE INDEX IF NOT EXISTS idx_ph_ts ON price_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_ph_region ON price_history(region);

-- Monitor state: replaces JSON state files (content_hashes, build_id, backoff)
CREATE TABLE IF NOT EXISTS monitor_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

- [ ] **Step 4: Apply schema to Neon**

```bash
# Load DATABASE_URL from .env.local
export $(grep DATABASE_URL .env.local | xargs)
psql "$DATABASE_URL" -f schema.sql
```

Verify with: `psql "$DATABASE_URL" -c "\dt"` — should show 6 tables.

- [ ] **Step 5: Commit**

```bash
git add schema.sql
git commit -m "Add Postgres schema for Neon migration"
```

---

## Task 2: Shared Database Module

**Files:**
- Create: `db.py`

- [ ] **Step 1: Create db.py**

```python
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


def pg_json_dumps(obj) -> str:
    """JSON serialize with Postgres type support."""
    return json.dumps(obj, cls=PgJsonEncoder)


def get_db_url() -> str:
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
    """Get a new Postgres connection."""
    conn = psycopg2.connect(get_db_url(), sslmode='require')
    return conn


def query_db(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """Execute a SELECT and return rows as list of dicts."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def execute_db(sql: str, params: tuple = ()):
    """Execute an INSERT/UPDATE/DELETE and commit."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
    finally:
        conn.close()


def execute_many_db(sql: str, params_list: list):
    """Execute a parameterized statement for many rows and commit."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)
            conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Test the connection**

```bash
export $(grep DATABASE_URL .env.local | xargs)
python3 -c "from db import query_db; print(query_db('SELECT 1 as ok'))"
```

Expected: `[{'ok': 1}]`

- [ ] **Step 3: Commit**

```bash
git add db.py
git commit -m "Add shared Postgres connection helper"
```

---

## Task 3: Data Migration Script

**Files:**
- Create: `migrate.py`

Migrates all existing data from the local SQLite database to Neon Postgres. Run once.

- [ ] **Step 1: Write migrate.py**

The script should:
1. Connect to local `data/inventory.db` (SQLite)
2. Connect to Neon (Postgres via DATABASE_URL)
3. For each table (products, events, scans, price_history, watchlist):
   - Read all rows from SQLite
   - Batch INSERT into Postgres using `psycopg2.extras.execute_values()` (NOT `executemany()` — `executemany` sends one INSERT per row over the network and is extremely slow for large tables like `price_history`)
   - Print row counts for verification
4. Seed `monitor_state` with current values from JSON files:
   - `content_hashes` from `data/content_hashes.json`
   - `build_id` from `data/build_id.txt`
   - `backoff_state` from `data/backoff_state.json`

**Warning:** This script is NOT idempotent. Running it twice will duplicate rows in `events`, `scans`, and `price_history`. Only run once, or `TRUNCATE` those tables first if re-running.

```python
"""One-time migration: SQLite → Neon Postgres."""

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
    # Connect to both databases
    lite = sqlite3.connect(str(SQLITE_DB))
    lite.row_factory = sqlite3.Row

    pg_url = os.environ.get('DATABASE_URL', '')
    if not pg_url:
        env_file = Path(__file__).parent / '.env.local'
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith('DATABASE_URL='):
                    pg_url = line.split('=', 1)[1].strip()
    pg = psycopg2.connect(pg_url, sslmode='require')

    now = datetime.now(timezone.utc).isoformat()

    with pg.cursor() as cur:
        # --- products ---
        rows = lite.execute("SELECT * FROM products").fetchall()
        if rows:
            cols = rows[0].keys()
            col_names = ', '.join(cols)
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO products ({col_names}) VALUES %s ON CONFLICT DO NOTHING",
                [tuple(r) for r in rows]
            )
            print(f"products: {len(rows)} rows migrated")

        # --- events ---
        rows = lite.execute("SELECT timestamp, sku, region, name, event_type, old_value, new_value, details FROM events ORDER BY id").fetchall()
        if rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO events (timestamp, sku, region, name, event_type, old_value, new_value, details) VALUES %s",
                [tuple(r) for r in rows]
            )
            print(f"events: {len(rows)} rows migrated")

        # --- scans ---
        rows = lite.execute("SELECT timestamp, build_id, sku_count, available, sold_out, coming_soon, categories_changed, categories_unchanged, requests FROM scans ORDER BY id").fetchall()
        if rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO scans (timestamp, build_id, sku_count, available, sold_out, coming_soon, categories_changed, categories_unchanged, requests) VALUES %s",
                [tuple(r) for r in rows]
            )
            print(f"scans: {len(rows)} rows migrated")

        # --- price_history ---
        rows = lite.execute("SELECT timestamp, sku, region, price_cents, status FROM price_history ORDER BY id").fetchall()
        if rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO price_history (timestamp, sku, region, price_cents, status) VALUES %s",
                [tuple(r) for r in rows]
            )
            print(f"price_history: {len(rows)} rows migrated")

        # --- watchlist ---
        rows = lite.execute("SELECT * FROM watchlist").fetchall()
        if rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO watchlist (sku, added_at, notes) VALUES %s ON CONFLICT DO NOTHING",
                [tuple(r) for r in rows]
            )
            print(f"watchlist: {len(rows)} rows migrated")

        # --- monitor_state (seed from JSON files) ---
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
                "INSERT INTO monitor_state (key, value, updated_at) VALUES (%s,%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at",
                state_items
            )
            print(f"monitor_state: {len(state_items)} keys seeded")

        pg.commit()

    lite.close()
    pg.close()
    print("Migration complete.")

if __name__ == '__main__':
    migrate()
```

- [ ] **Step 2: Run the migration**

```bash
export $(grep DATABASE_URL .env.local | xargs)
python3 migrate.py
```

Expected output (numbers will vary):
```
products: 1918 rows migrated
events: <N> rows migrated
scans: <N> rows migrated
price_history: <N> rows migrated
watchlist: <N> rows migrated
monitor_state: 3 keys seeded
Migration complete.
```

- [ ] **Step 3: Verify data in Neon**

```bash
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM products;"
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM price_history;"
psql "$DATABASE_URL" -c "SELECT * FROM monitor_state;"
```

- [ ] **Step 4: Commit**

```bash
git add migrate.py
git commit -m "Add one-time SQLite to Postgres migration script"
```

---

## Task 4: Create monitor_cloud.py (Postgres version of monitor)

**Files:**
- Create: `monitor_cloud.py` (based on `monitor.py`, adapted for Postgres)
- Preserve: `monitor.py` (unchanged, standalone SQLite version)

Create `monitor_cloud.py` by copying `monitor.py` and making these changes:
1. Replace `sqlite3` imports and `init_db()` with `psycopg2` via `db.py`
2. Replace `?` placeholders with `%s`
3. Replace JSON state file I/O with `monitor_state` table reads/writes
4. Get previous product state from `products` table instead of `latest_snapshot.json`
5. Remove `latest_snapshot.json` and `changes/` directory writes
6. Keep `httpx` HTTP logic completely unchanged

- [ ] **Step 1: Copy monitor.py to monitor_cloud.py and add db.py import + state helpers**

```bash
cp monitor.py monitor_cloud.py
```

At the top of `monitor_cloud.py`, replace the `sqlite3` import with:
```python
import psycopg2
import psycopg2.extras
from db import get_db, get_db_url
```

Add state helper functions to replace JSON file I/O:
```python
def load_state(key: str, default: str = '{}') -> str:
    """Load a value from monitor_state table."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM monitor_state WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else default
    finally:
        conn.close()

def save_state(key: str, value: str):
    """Save a value to monitor_state table."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO monitor_state (key, value, updated_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
                (key, value, now)
            )
            conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Replace JSON file reads in main()**

Replace:
```python
prev_hashes = load_json(HASHES_FILE, {})
prev_snapshot = load_json(SNAPSHOT_FILE, {})
```

With:
```python
prev_hashes = json.loads(load_state('content_hashes', '{}'))
# Get previous products from database instead of JSON file
conn = get_db()
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("SELECT * FROM products")
    prev_rows = cur.fetchall()
conn.close()
# Key by SKU string (per-region), matching the structure that
# scan_region() and diff_snapshots() expect
prev_products = {}
for r in prev_rows:
    region = r['region']
    if region not in prev_products:
        prev_products[region] = {}
    prev_products[region][r['sku']] = dict(r)
```

**Important:** The keying must match `scan_region()`'s expectations. The current code builds `region_prev` per-region keyed by SKU string. The database query replaces the flat snapshot JSON — group by region first, then key by SKU within each region.

- [ ] **Step 3: Replace JSON file writes in main()**

Replace `save_json(HASHES_FILE, ...)` with `save_state('content_hashes', json.dumps(all_hashes))`.
Replace build_id file reads/writes with `load_state('build_id', '')` / `save_state('build_id', build_id)`.
Replace backoff file reads/writes with `load_state('backoff_state', '{}')` / `save_state('backoff_state', json.dumps(state))`.
Remove `latest_snapshot.json` and `changes/` writes entirely.

- [ ] **Step 4: Replace db_* functions with Postgres equivalents**

Update all 4 database functions:
- `db_upsert_products()`: Change `?` → `%s`, use `psycopg2` connection
- `db_record_events()`: Change `?` → `%s`
- `db_record_prices()`: Change `?` → `%s`
- `db_record_scan()`: Change `?` → `%s`

Remove `init_db()` entirely (schema is managed by `schema.sql`).

Replace the database persistence block at the end of `main()`:
```python
# Old: conn = sqlite3.connect(...); init_db(conn); ...
# New: Use get_db() from db.py, no init needed
conn = get_db()
try:
    with conn.cursor() as cur:
        # ... execute all inserts with %s placeholders ...
        conn.commit()
finally:
    conn.close()
```

- [ ] **Step 5: Migrate `get_build_id()` and `scan_region()` BUILD_ID_FILE references**

The `get_build_id()` function (lines 325-337) reads/writes `BUILD_ID_FILE` directly. Replace:
- Line 328: `BUILD_ID_FILE.read_text().strip()` → `load_state('build_id', '')`
- Line 336: `BUILD_ID_FILE.write_text(build_id)` → `save_state('build_id', build_id)`

The `scan_region()` function (line 560) also writes `BUILD_ID_FILE` when a stale buildId triggers re-fetch. Replace:
- Line 560: `BUILD_ID_FILE.write_text(build_id)` → `save_state('build_id', build_id)`

**These are critical** — without this fix, monitor.py will crash at runtime after the path constants are removed.

- [ ] **Step 6: Remove unused imports, paths, and functions from monitor_cloud.py**

Remove:
- `import sqlite3`
- `DATA_DIR`, `DB_FILE`, `SNAPSHOT_FILE`, `HASHES_FILE`, `BUILD_ID_FILE`, `BACKOFF_FILE`, `CHANGES_DIR` path constants
- `load_json()`, `save_json()` functions
- `init_db()` function
- `mkdir` calls for data directories

- [ ] **Step 7: Test locally**

```bash
export $(grep DATABASE_URL .env.local | xargs)
export UI_REGIONS=us/en
python3 monitor_cloud.py
```

Verify:
```bash
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM scans ORDER BY id DESC LIMIT 1;"
psql "$DATABASE_URL" -c "SELECT timestamp, sku_count FROM scans ORDER BY id DESC LIMIT 1;"
```

- [ ] **Step 8: Commit**

```bash
git add monitor_cloud.py
git commit -m "Add cloud monitor for Neon Postgres (GitHub Actions)"
```

---

## Task 5: Copy SPA HTML into public/index.html

**Files:**
- Create: `public/index.html`
- Reference: `dashboard.py` (lines 596–1856, `HTML_PAGE` variable — **do not modify dashboard.py**)

- [ ] **Step 1: Create public directory**

```bash
mkdir -p public
```

- [ ] **Step 2: Copy the HTML**

Copy the content of the `HTML_PAGE` triple-quoted string from `dashboard.py` (everything between the `"""` delimiters, lines ~597–1855) into `public/index.html`. **Do not remove it from dashboard.py** — the standalone version still needs it.

This is a straight copy — no modifications needed. The SPA already fetches from relative `/api/*` paths which will map directly to Vercel serverless functions.

- [ ] **Step 3: Verify the HTML is valid**

Open `public/index.html` in a browser. It won't have working API endpoints yet but should render the layout with "Loading..." states.

- [ ] **Step 4: Commit**

```bash
git add public/index.html
git commit -m "Extract SPA frontend into static HTML file"
```

---

## Task 6: Create Vercel Project Structure

**Files:**
- Create: `vercel.json`
- Create: `requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: Create vercel.json**

```json
{
  "buildCommand": "",
  "outputDirectory": "public",
  "functions": {
    "api/**/*.py": {
      "runtime": "@vercel/python@4.5.0"
    }
  },
  "rewrites": [
    { "source": "/api/(.*)", "destination": "/api/$1" },
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

- [ ] **Step 2: Create requirements.txt**

```
psycopg2-binary==2.9.9
httpx==0.27.0
```

- [ ] **Step 3: Update .gitignore**

Add (`.env*.local` ensures all local env files are excluded — the current `.gitignore` only has `.env`):
```
.vercel
.env*.local
```

- [ ] **Step 4: Link Vercel project**

```bash
# Install Vercel CLI if not present
npm i -g vercel

# Link to Vercel (creates project)
vercel link
```

- [ ] **Step 5: Add DATABASE_URL to Vercel environment**

```bash
vercel env add DATABASE_URL
# Paste the Neon connection string when prompted
# Select: Production, Preview, Development
```

Or install Neon via Vercel Marketplace for auto-provisioned env vars:
```bash
vercel integration add neon
```

- [ ] **Step 6: Commit**

```bash
git add vercel.json requirements.txt .gitignore
git commit -m "Add Vercel project configuration"
```

---

## Task 7: Migrate API Endpoints — Simple Queries (Batch 1)

**Files:**
- Create: `api/products.py`
- Create: `api/events.py`
- Create: `api/scans.py`
- Create: `api/categories.py`
- Create: `api/regions.py`
- Create: `api/region-stock.py`
- Create: `api/health.py`
- Create: `api/sold-out.py`

Each file follows the same pattern — adapt from the corresponding `api_*` function in `dashboard.py`. The handler pattern for Vercel Python:

```python
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os

# Add project root to path for db.py import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        # ... query logic from dashboard.py api_* function ...
        # Change ? → %s, datetime() → INTERVAL, json_extract → ->>
        result = query_db(sql, tuple_params)

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())
```

**For ALL API functions:** Use `pg_json_dumps()` from `db.py` (not `json.dumps()`) when serializing query results, to handle Postgres `Decimal`/`datetime` types. Change `LIKE` to `ILIKE` in all search queries for case-insensitive parity with SQLite.

- [ ] **Step 1: Create api/products.py**

Port `api_products()` (dashboard.py lines 108–148). Key changes:
- `?` → `%s` in SQL
- `LIKE` → `ILIKE` for search filter
- Build WHERE clauses with `%s` params
- `params = parse_qs(urlparse(self.path).query)` for query string access
- Use `pg_json_dumps(result)` for response serialization

- [ ] **Step 2: Create api/events.py**

Port `api_events()` (lines 150–175). Change `?` → `%s`.

- [ ] **Step 3: Create api/scans.py**

Port `api_scans()` (lines 177–183). Change `?` → `%s`.

- [ ] **Step 4: Create api/categories.py**

Port `api_categories()` (lines 217–228). No parameter changes needed (no `?` placeholders).

- [ ] **Step 5: Create api/regions.py**

Port `api_regions()` (lines 411–418). No parameter changes needed.

- [ ] **Step 6: Create api/region-stock.py**

Port `api_region_stock()` (lines 420–433). No parameter changes needed.

- [ ] **Step 7: Create api/health.py**

Port `api_health()` (lines 284–305). Change `datetime('now','-2 hours')` → `CURRENT_TIMESTAMP - INTERVAL '2 hours'`.

- [ ] **Step 8: Create api/sold-out.py**

Port `api_sold_out()` (lines 262–282). Change `?` → `%s`.

- [ ] **Step 9: Test locally**

```bash
vercel dev
# In another terminal:
curl http://localhost:3000/api/products | python3 -m json.tool | head -20
curl http://localhost:3000/api/health
curl http://localhost:3000/api/categories
curl http://localhost:3000/api/regions
```

- [ ] **Step 10: Commit**

```bash
git add api/products.py api/events.py api/scans.py api/categories.py api/regions.py api/region-stock.py api/health.py api/sold-out.py
git commit -m "Add Vercel API functions: products, events, scans, categories, regions, health, sold-out"
```

---

## Task 8: Migrate API Endpoints — Complex Queries (Batch 2)

**Files:**
- Create: `api/stats.py`
- Create: `api/product-history.py`
- Create: `api/price-analytics.py`
- Create: `api/availability-windows.py`
- Create: `api/hot-items.py`
- Create: `api/watchlist/index.py`
- Create: `api/watchlist/remove.py`

- [ ] **Step 1: Create api/stats.py**

Port `api_stats()` (lines 185–215). Multiple queries. Change:
- `datetime('now', '-24 hours')` → `CURRENT_TIMESTAMP - INTERVAL '24 hours'`
- `?` → `%s`

- [ ] **Step 2: Create api/product-history.py**

Port `api_product_history()` (lines 230–260). Change `?` → `%s`.
Returns `{product, events, prices}` — three separate queries.

- [ ] **Step 3: Create api/price-analytics.py**

Port `api_price_analytics()` (lines 348–409). Key changes:
- `json_extract(e.details, '$.delta_cents')` → `e.details::json->>'delta_cents'`
- `datetime('now', '-7 days')` → `CURRENT_TIMESTAMP - INTERVAL '7 days'`
- `?` → `%s`

- [ ] **Step 4: Create api/availability-windows.py**

Port `api_availability_windows()` (lines 435–508). Change `?` → `%s`.
Post-processing logic (window computation) stays identical — it's pure Python.

- [ ] **Step 5: Create api/hot-items.py**

Port `api_hot_items()` (lines 510–584). Change `?` → `%s`.
This is the most complex endpoint — multiple queries and Python post-processing.

**Important optimization:** The current implementation has an N+1 query pattern — one query per qualifying SKU inside a loop. Each `query_db()` opens/closes a Postgres connection. On Vercel's 10-second free tier timeout, this could fail with many SKUs. Refactor to:
1. Use a single `get_db()` connection for the entire function
2. Or consolidate the loop queries into fewer SQL statements using `WHERE sku IN (%s, ...)`
3. Pass the cursor to avoid opening N connections

- [ ] **Step 6: Create api/watchlist/ directory and index.py**

Port watchlist GET/POST (lines 307–333). Handles both methods:
- `do_GET`: SELECT with LEFT JOIN
- `do_POST`: `INSERT ... ON CONFLICT(sku) DO UPDATE SET ...` (replaces SQLite's `INSERT OR REPLACE`)

```python
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # ... port api_watchlist_get() ...

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length))
        # ... port api_watchlist_post() ...
```

- [ ] **Step 7: Create api/watchlist/remove.py**

Port watchlist remove (lines 335–346):
```python
class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length))
        execute_db("DELETE FROM watchlist WHERE sku = %s", (body['sku'],))
        # ... return success ...
```

- [ ] **Step 8: Test all endpoints locally**

```bash
vercel dev
# Test each endpoint
curl http://localhost:3000/api/stats
curl http://localhost:3000/api/product-history?sku=USW-Flex-Mini-3
curl http://localhost:3000/api/price-analytics
curl http://localhost:3000/api/availability-windows?sku=USW-Flex-Mini-3
curl http://localhost:3000/api/hot-items
curl http://localhost:3000/api/watchlist
```

- [ ] **Step 9: Full visual test**

Open http://localhost:3000 in browser. Click through all tabs. Open a product detail modal. Verify:
- Products table loads with filters
- Events tab shows events
- Analytics tab renders charts
- Product modal shows price chart + availability timeline
- Time window buttons work
- Watchlist add/remove works

- [ ] **Step 10: Commit**

```bash
git add api/stats.py api/product-history.py api/price-analytics.py api/availability-windows.py api/hot-items.py api/watchlist/
git commit -m "Add Vercel API functions: stats, product-history, analytics, availability, hot-items, watchlist"
```

---

## Task 9: GitHub Actions Workflow

**Files:**
- Create: `.github/workflows/monitor.yml`

- [ ] **Step 1: Create workflow file**

```yaml
name: UI Store Monitor

on:
  schedule:
    # Every 15 minutes
    - cron: '*/15 * * * *'
  workflow_dispatch: # Allow manual trigger

jobs:
  scrape:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: pip install httpx psycopg2-binary

      - name: Run monitor
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          # 13 regions (ph/en excluded — returns 0 products)
          UI_REGIONS: us/en,eu/en,uk/en,ca/en,jp/ja,mx/es,br/pt,in/en,sg/en,me/en,za/en,tw/en,cn/en
        run: python monitor_cloud.py
```

- [ ] **Step 2: Add DATABASE_URL as GitHub secret**

Go to https://github.com/thelostcrafts/ui-stock-scraper/settings/secrets/actions → New repository secret → Name: `DATABASE_URL` → Value: the Neon connection string.

- [ ] **Step 3: Commit and push**

```bash
git add .github/workflows/monitor.yml
git commit -m "Add GitHub Actions workflow for scheduled monitor runs"
git push
```

- [ ] **Step 4: Trigger a manual test run**

```bash
gh workflow run "UI Store Monitor"
gh run list --limit 1
# Wait for completion, then check:
gh run view --log
```

- [ ] **Step 5: Verify data was written**

```bash
psql "$DATABASE_URL" -c "SELECT timestamp, sku_count, available, sold_out FROM scans ORDER BY id DESC LIMIT 1;"
```

---

## Task 10: Deploy to Vercel

**Files:**
- No new files

- [ ] **Step 1: Deploy preview**

```bash
vercel deploy
```

Open the preview URL. Test all tabs and product detail modals.

- [ ] **Step 2: Fix any issues**

Common issues:
- `db.py` import path in serverless functions (may need `sys.path` adjustment)
- `psycopg2-binary` build issues (fall back to `pg8000` if needed — pure Python, no C deps)
- CORS headers if needed (add `Access-Control-Allow-Origin: *`)
- Cold start time for Python functions (~1-2s first request)

- [ ] **Step 3: Deploy to production**

```bash
vercel --prod
```

- [ ] **Step 4: Configure custom domain (optional)**

```bash
vercel domains add yourdomain.com
```

Or use the free `.vercel.app` domain.

- [ ] **Step 5: Verify production**

Open the production URL. Run through all tabs. Confirm:
- Data matches what was in the Mac Mini dashboard
- Auto-refresh works (60s polling)
- Watchlist add/remove works
- Product modals load with charts
- Health endpoint returns healthy

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "Fix Vercel deployment issues"
git push
```

---

## Task 11: Documentation & Cleanup

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `.gitignore`
- Remove: `run-monitor.sh` (no longer needed)
- Preserve: `dashboard.py` (keep as reference, not actively used)

- [ ] **Step 1: Update CLAUDE.md**

**Add** a cloud deployment section (keep the existing Mac Mini section):
```markdown
## Cloud Deployment (Vercel + Neon + GitHub Actions)

- **Dashboard:** Vercel (auto-deploys on push to main)
- **Database:** Neon Postgres (connection via DATABASE_URL env var)
- **Monitor:** GitHub Actions (runs every 15 minutes via cron, uses `monitor_cloud.py`)

Monitor runs visible at: https://github.com/thelostcrafts/ui-stock-scraper/actions

### Environment Variables (Cloud)

- `DATABASE_URL` — Neon Postgres connection string
  - Set in Vercel project settings (for dashboard API)
  - Set in GitHub Actions secrets (for monitor)
```

- [ ] **Step 2: Update README.md**

**Add** a Cloud Deployment section alongside the existing launchd section. Both deployment methods are valid:
- Mac Mini: standalone with SQLite (using `monitor.py` + `dashboard.py`)
- Cloud: Vercel + Neon + GitHub Actions (using `monitor_cloud.py` + `api/` + `public/`)

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md .gitignore
git commit -m "Update docs for Vercel + Neon + GitHub Actions architecture"
git push
```

---

## Task 12: (Optional) Decommission Mac Mini Services

**Not a code task — manual steps on the Mac Mini. Only do this once you've verified the cloud version is stable for several days.**

- [ ] **Step 1: Stop and unload launchd services**

```bash
ssh 192.168.1.10 "launchctl unload ~/Library/LaunchAgents/com.thelostcrafts.ui-store-monitor.plist"
ssh 192.168.1.10 "launchctl unload ~/Library/LaunchAgents/com.thelostcrafts.ui-store-dashboard.plist"
```

- [ ] **Step 2: Remove plist files**

```bash
ssh 192.168.1.10 "rm ~/Library/LaunchAgents/com.thelostcrafts.ui-store-monitor.plist"
ssh 192.168.1.10 "rm ~/Library/LaunchAgents/com.thelostcrafts.ui-store-dashboard.plist"
```

- [ ] **Step 3: Verify services are gone**

```bash
ssh 192.168.1.10 "launchctl list | grep ui-store"
```

Expected: no output.

- [ ] **Step 4: Keep data/inventory.db as backup**

Don't delete the SQLite database immediately — keep it as a backup until you've verified the Vercel + Neon setup is stable for a few days.

---

## Rollback Plan

If anything goes wrong during migration:
1. **Mac Mini services are still running** until Task 12 — the old system keeps working in parallel
2. **SQLite database is untouched** — migration only reads from it
3. **Neon database can be reset** — drop all tables and re-run `schema.sql` + `migrate.py`
4. To restore Mac Mini: `launchctl load` the plist files and the old system comes back up

**Recommended:** Run both systems in parallel for 2-3 days after migration. Compare data between the Vercel dashboard and Mac Mini dashboard to verify parity before decommissioning.
