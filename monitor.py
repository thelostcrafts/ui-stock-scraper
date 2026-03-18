#!/usr/bin/env python3
"""
UI Store Inventory Monitor
==========================
Polls store.ui.com via the Next.js _next/data JSON API.
Tracks price changes, stock status transitions, and new/removed products
at the variant (SKU) level.

Data source: /_next/data/{buildId}/us/en/category/{cat}.json
  → pageProps.subCategories[].products[].variants[]

Designed for unattended cron on Mac Mini:
  */30 * * * * cd /path/to/ui-stock-scraper && python3 monitor.py

Requirements: pip3 install httpx
"""

import hashlib
import httpx
import json
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── Configuration ───────────────────────────────────────────────────────────

BASE_URL = "https://store.ui.com"

# Region-to-subdomain mapping. Non-US regions use their own subdomain.
REGION_HOSTS = {
    "us/en": "store.ui.com",
    "eu/en": "eu.store.ui.com",
    "uk/en": "uk.store.ui.com",
    "ca/en": "ca.store.ui.com",
    "jp/ja": "jp.store.ui.com",
    "mx/es": "mx.store.ui.com",
    "br/pt": "br.store.ui.com",
    "in/en": "in.store.ui.com",
    "sg/en": "sg.store.ui.com",
    "me/en": "me.store.ui.com",
    "za/en": "za.store.ui.com",
    "tw/en": "tw.store.ui.com",
    "cn/en": "cn.store.ui.com",
}

# Regions to monitor. Add/remove as needed.
# Set via env var UI_REGIONS (comma-separated) or default to US only.
import os
DEFAULT_REGIONS = ["us/en"]
ALL_KNOWN_REGIONS = list(REGION_HOSTS.keys())
REGIONS = os.environ.get("UI_REGIONS", "").split(",") if os.environ.get("UI_REGIONS") else DEFAULT_REGIONS
REGIONS = [r.strip() for r in REGIONS if r.strip()]

CATEGORIES = [
    "all-cloud-gateways",
    "all-switching",
    "all-wifi",
    "all-cameras-nvrs",
    "all-door-access",
    "all-integrations",
    "all-advanced-hosting",
    "accessories-cables-dacs",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.3 Safari/605.1.15"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

REQUEST_DELAY = 1.0  # seconds between requests (polite)
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds, exponential backoff base

DATA_DIR = Path(__file__).parent / "data"
SNAPSHOT_FILE = DATA_DIR / "latest_snapshot.json"
HASHES_FILE = DATA_DIR / "content_hashes.json"
CHANGES_DIR = DATA_DIR / "changes"
BUILD_ID_FILE = DATA_DIR / "build_id.txt"
DB_FILE = DATA_DIR / "inventory.db"
BACKOFF_FILE = DATA_DIR / "backoff_state.json"

# ─── Helpers ─────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def cents_to_dollars(amount: int) -> str:
    return f"${amount / 100:,.2f}"

def ensure_dirs():
    DATA_DIR.mkdir(exist_ok=True)
    CHANGES_DIR.mkdir(exist_ok=True)

def load_json(path: Path) -> Optional[dict]:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None

def save_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

# ─── Database ────────────────────────────────────────────────────────────────

def init_db() -> sqlite3.Connection:
    """Create/open the SQLite database and ensure schema exists."""
    conn = sqlite3.connect(str(DB_FILE))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            sku         TEXT NOT NULL,
            region      TEXT NOT NULL DEFAULT 'us/en',
            slug        TEXT NOT NULL,
            name        TEXT NOT NULL,
            category    TEXT NOT NULL,
            subcategory TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            currency    TEXT NOT NULL DEFAULT 'USD',
            regular_price_cents INTEGER,
            status      TEXT NOT NULL,
            variant_id  TEXT,
            thumbnail   TEXT,
            first_seen  TEXT NOT NULL,
            last_updated TEXT NOT NULL,
            PRIMARY KEY (sku, region)
        );

        CREATE TABLE IF NOT EXISTS watchlist (
            sku         TEXT PRIMARY KEY,
            added_at    TEXT NOT NULL,
            notes       TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
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

        CREATE TABLE IF NOT EXISTS scans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            build_id    TEXT NOT NULL,
            sku_count   INTEGER NOT NULL,
            available   INTEGER NOT NULL DEFAULT 0,
            sold_out    INTEGER NOT NULL DEFAULT 0,
            coming_soon INTEGER NOT NULL DEFAULT 0,
            categories_changed INTEGER NOT NULL DEFAULT 0,
            categories_unchanged INTEGER NOT NULL DEFAULT 0,
            requests    INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS price_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            sku         TEXT NOT NULL,
            region      TEXT NOT NULL DEFAULT 'us/en',
            price_cents INTEGER NOT NULL,
            status      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ph_sku ON price_history(sku);
        CREATE INDEX IF NOT EXISTS idx_ph_ts ON price_history(timestamp);
        CREATE INDEX IF NOT EXISTS idx_ph_region ON price_history(region);
    """)
    conn.commit()
    return conn


def db_upsert_products(conn: sqlite3.Connection, products: dict, region: str, now: str):
    """Insert or update all products in a single transaction."""
    conn.executemany("""
        INSERT INTO products (sku, region, slug, name, category, subcategory,
                              price_cents, currency, regular_price_cents,
                              status, variant_id, thumbnail,
                              first_seen, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sku, region) DO UPDATE SET
            slug=excluded.slug, name=excluded.name,
            category=excluded.category, subcategory=excluded.subcategory,
            price_cents=excluded.price_cents, currency=excluded.currency,
            regular_price_cents=excluded.regular_price_cents,
            status=excluded.status, variant_id=excluded.variant_id,
            thumbnail=excluded.thumbnail,
            last_updated=excluded.last_updated
    """, [
        (p["sku"], region, p["slug"], p["name"], p["category"], p["subcategory"],
         p["price_cents"], p["currency"], p.get("regular_price_cents"),
         p["status"], p.get("variant_id"), p.get("thumbnail"), now, now)
        for p in products.values()
    ])
    conn.commit()


def db_record_events(conn: sqlite3.Connection, changes: dict, region: str, now: str):
    """Write change events to the events table."""
    rows = []
    for c in changes.get("status_changes", []):
        rows.append((now, c["sku"], region, c["name"], "status_change",
                      c["old_status"], c["new_status"], None))
    for c in changes.get("price_changes", []):
        rows.append((now, c["sku"], region, c["name"], "price_change",
                      c["old_price"], c["new_price"],
                      json.dumps({"delta_cents": c["delta_cents"]})))
    for c in changes.get("new_skus", []):
        rows.append((now, c["sku"], region, c["name"], "new_product",
                      None, c["price"], json.dumps({"status": c["status"]})))
    for c in changes.get("removed_skus", []):
        rows.append((now, c["sku"], region, c["name"], "removed_product",
                      None, None, None))

    if rows:
        conn.executemany("""
            INSERT INTO events (timestamp, sku, region, name, event_type,
                                old_value, new_value, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()


def db_record_prices(conn: sqlite3.Connection, products: dict, region: str, now: str):
    """Record price + status snapshot for every SKU (time-series data)."""
    conn.executemany("""
        INSERT INTO price_history (timestamp, sku, region, price_cents, status)
        VALUES (?, ?, ?, ?, ?)
    """, [
        (now, p["sku"], region, p["price_cents"], p["status"])
        for p in products.values()
    ])
    conn.commit()


def db_record_scan(conn: sqlite3.Connection, now: str, build_id: str,
                   sku_count: int, status_counts: dict, stats: dict):
    """Record scan metadata."""
    conn.execute("""
        INSERT INTO scans (timestamp, build_id, sku_count,
                           available, sold_out, coming_soon,
                           categories_changed, categories_unchanged, requests)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (now, build_id, sku_count,
          status_counts.get("Available", 0),
          status_counts.get("SoldOut", 0),
          status_counts.get("ComingSoon", 0),
          stats["categories_changed"],
          stats["categories_unchanged"],
          stats["requests"]))
    conn.commit()


# ─── Adaptive Backoff ───────────────────────────────────────────────────────

def load_backoff() -> dict:
    """Load persistent backoff state. Increases delay if errors accumulate."""
    state = load_json(BACKOFF_FILE)
    if not state:
        state = {"consecutive_errors": 0, "delay_multiplier": 1.0}
    return state

def save_backoff(state: dict):
    save_json(BACKOFF_FILE, state)

def get_request_delay(state: dict) -> float:
    """Current delay between requests, scaled by error history."""
    return REQUEST_DELAY * state.get("delay_multiplier", 1.0)

def record_error(state: dict):
    """Increase backoff after an error."""
    state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
    # Double delay after 3 consecutive errors, cap at 10x
    if state["consecutive_errors"] >= 3:
        state["delay_multiplier"] = min(state.get("delay_multiplier", 1.0) * 1.5, 10.0)
        log(f"  Backoff increased: delay multiplier now {state['delay_multiplier']:.1f}x")
    save_backoff(state)

def record_success(state: dict):
    """Reset backoff after a successful run."""
    if state.get("consecutive_errors", 0) > 0 or state.get("delay_multiplier", 1.0) > 1.0:
        old_mult = state.get("delay_multiplier", 1.0)
        state["consecutive_errors"] = 0
        # Gradually reduce multiplier (don't snap back instantly)
        state["delay_multiplier"] = max(1.0, old_mult * 0.7)
        if state["delay_multiplier"] <= 1.1:
            state["delay_multiplier"] = 1.0
        if old_mult > 1.0:
            log(f"  Backoff reduced: delay multiplier now {state['delay_multiplier']:.1f}x")
        save_backoff(state)

# ─── Build ID Management ────────────────────────────────────────────────────

def fetch_build_id(client: httpx.Client) -> str:
    """Extract buildId from any page's __NEXT_DATA__."""
    resp = client.get(f"{BASE_URL}/{REGIONS[0]}", headers={
        **HEADERS,
        "Accept": "text/html",
    })
    resp.raise_for_status()

    match = re.search(r'"buildId"\s*:\s*"([^"]+)"', resp.text)
    if not match:
        raise RuntimeError("Could not extract buildId from homepage")
    return match.group(1)

def get_build_id(client: httpx.Client) -> str:
    """Get current buildId, fetching fresh if needed."""
    cached = None
    if BUILD_ID_FILE.exists():
        cached = BUILD_ID_FILE.read_text().strip()

    fresh = fetch_build_id(client)

    if cached and cached != fresh:
        log(f"  Build ID rotated: {cached} → {fresh} (new deploy detected)")

    BUILD_ID_FILE.write_text(fresh)
    return fresh

# ─── Category Fetching ───────────────────────────────────────────────────────

def fetch_category(
    client: httpx.Client,
    build_id: str,
    category: str,
    region: str = "us/en",
) -> dict:
    """Fetch a category's JSON data from the _next/data API with retry/backoff."""
    host = REGION_HOSTS.get(region, "store.ui.com")
    url = f"https://{host}/_next/data/{build_id}/{region}/category/{category}.json"

    for attempt in range(MAX_RETRIES):
        try:
            resp = client.get(url, headers=HEADERS)

            if resp.status_code == 404:
                raise RuntimeError(f"404 for {category} — buildId may be stale")

            if resp.status_code == 429:
                delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                log(f"  Rate limited (429), retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue

            if resp.status_code >= 500:
                delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                log(f"  Server error ({resp.status_code}), retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue

            resp.raise_for_status()
            return resp.json()

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                log(f"  Network error ({type(e).__name__}), retrying in {delay:.1f}s...")
                time.sleep(delay)
            else:
                raise

    # Exhausted retries
    resp.raise_for_status()
    return resp.json()


def content_hash(data: dict) -> str:
    """Stable hash of JSON data for change detection."""
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]

# ─── Product Extraction ─────────────────────────────────────────────────────

def extract_products(category_json: dict) -> list:
    """
    Extract flat product+variant records from category JSON.
    Returns list of dicts, one per variant (SKU).
    """
    records = []
    page_props = category_json.get("pageProps", {})
    sub_categories = page_props.get("subCategories", [])

    for sub in sub_categories:
        sub_id = sub.get("id", "unknown")
        products = sub.get("products", [])

        for product in products:
            slug = product.get("slug", "")
            name = product.get("title") or product.get("name", "")
            product_status = product.get("status", "Unknown")
            display_sku = product.get("displaySku", "")
            min_price = product.get("minDisplayPrice", {})
            min_regular = product.get("minDisplayRegularPrice")
            thumb = product.get("thumbnail", {})
            thumbnail_url = thumb.get("url") if thumb else None

            variants = product.get("variants", [])

            if not variants:
                # Product with no variants — use product-level data
                records.append({
                    "sku": display_sku or slug,
                    "slug": slug,
                    "name": name,
                    "subcategory": sub_id,
                    "price_cents": min_price.get("amount", 0) if min_price else 0,
                    "currency": min_price.get("currency", "USD") if min_price else "USD",
                    "regular_price_cents": (
                        min_regular.get("amount") if min_regular else None
                    ),
                    "status": product_status,
                    "variant_id": None,
                    "thumbnail": thumbnail_url,
                })
            else:
                for variant in variants:
                    if not variant.get("isVisibleInStore", True):
                        continue
                    v_price = variant.get("displayPrice", {})
                    v_regular = variant.get("displayRegularPrice")
                    records.append({
                        "sku": variant.get("sku", display_sku),
                        "slug": slug,
                        "name": name,
                        "subcategory": sub_id,
                        "price_cents": v_price.get("amount", 0) if v_price else 0,
                        "currency": v_price.get("currency", "USD") if v_price else "USD",
                        "regular_price_cents": (
                            v_regular.get("amount") if v_regular else None
                        ),
                        "status": variant.get("status", product_status),
                        "variant_id": variant.get("id"),
                        "thumbnail": thumbnail_url,
                    })

    return records

# ─── Diffing ─────────────────────────────────────────────────────────────────

def diff_snapshots(old: dict, new: dict) -> dict:
    """
    Compare two snapshots keyed by SKU.
    Returns dict of changes: price_changes, status_changes, new_skus, removed_skus.
    """
    changes = {
        "price_changes": [],
        "status_changes": [],
        "new_skus": [],
        "removed_skus": [],
    }

    old_skus = set(old.keys())
    new_skus = set(new.keys())

    # New products
    for sku in sorted(new_skus - old_skus):
        item = new[sku]
        changes["new_skus"].append({
            "sku": sku,
            "name": item["name"],
            "price": cents_to_dollars(item["price_cents"]),
            "status": item["status"],
        })

    # Removed products
    for sku in sorted(old_skus - new_skus):
        item = old[sku]
        changes["removed_skus"].append({
            "sku": sku,
            "name": item["name"],
        })

    # Changed products
    for sku in sorted(old_skus & new_skus):
        o = old[sku]
        n = new[sku]

        if o["price_cents"] != n["price_cents"]:
            changes["price_changes"].append({
                "sku": sku,
                "name": n["name"],
                "old_price": cents_to_dollars(o["price_cents"]),
                "new_price": cents_to_dollars(n["price_cents"]),
                "delta_cents": n["price_cents"] - o["price_cents"],
            })

        if o["status"] != n["status"]:
            changes["status_changes"].append({
                "sku": sku,
                "name": n["name"],
                "old_status": o["status"],
                "new_status": n["status"],
            })

    return changes

def has_changes(changes: dict) -> bool:
    return any(len(v) > 0 for v in changes.values())

def print_changes(changes: dict):
    if changes["status_changes"]:
        log("  STOCK STATUS CHANGES:")
        for c in changes["status_changes"]:
            emoji = "+" if c["new_status"] == "Available" else "-"
            log(f"    [{emoji}] {c['sku']:30s} {c['old_status']:12s} → {c['new_status']}")

    if changes["price_changes"]:
        log("  PRICE CHANGES:")
        for c in changes["price_changes"]:
            direction = "UP" if c["delta_cents"] > 0 else "DOWN"
            log(f"    [{direction}] {c['sku']:30s} {c['old_price']:>12s} → {c['new_price']}")

    if changes["new_skus"]:
        log("  NEW PRODUCTS:")
        for c in changes["new_skus"]:
            log(f"    [NEW] {c['sku']:30s} {c['price']:>12s} ({c['status']})")

    if changes["removed_skus"]:
        log("  REMOVED PRODUCTS:")
        for c in changes["removed_skus"]:
            log(f"    [DEL] {c['sku']:30s} {c['name']}")

# ─── Main ────────────────────────────────────────────────────────────────────

def scan_region(client: httpx.Client, build_id: str, region: str,
                prev_hashes: dict, prev_products: dict, stats: dict,
                backoff: dict) -> tuple:
    """Scan all categories for a single region. Returns (products, hashes, changes)."""
    new_hashes = {}
    all_products = {}
    delay = get_request_delay(backoff)

    for cat in CATEGORIES:
        log(f"  [{region}] {cat}")
        try:
            cat_json = fetch_category(client, build_id, cat, region)
        except RuntimeError as e:
            if "404" in str(e):
                log(f"    Build ID stale, re-fetching...")
                build_id = fetch_build_id(client)
                BUILD_ID_FILE.write_text(build_id)
                stats["requests"] += 1
                time.sleep(delay)
                cat_json = fetch_category(client, build_id, cat, region)
            else:
                raise

        stats["requests"] += 1

        page_props = cat_json.get("pageProps", {})
        h = content_hash(page_props)
        hash_key = f"{region}:{cat}"
        new_hashes[hash_key] = h

        if h == prev_hashes.get(hash_key):
            stats["categories_unchanged"] += 1
            log(f"    unchanged (hash {h})")
            for sku, record in prev_products.items():
                if record.get("category") == cat and record.get("region", "us/en") == region:
                    all_products[sku] = record
        else:
            records = extract_products(cat_json)
            stats["categories_changed"] += 1
            log(f"    {len(records)} SKUs extracted (hash {h})")
            for r in records:
                r["category"] = cat
                r["region"] = region
                all_products[r["sku"]] = r

        time.sleep(delay)

    # Diff against previous
    region_prev = {k: v for k, v in prev_products.items()
                   if v.get("region", "us/en") == region}
    changes = diff_snapshots(region_prev, all_products) if region_prev else None

    return all_products, new_hashes, changes, build_id


def main():
    ensure_dirs()
    now = datetime.now(timezone.utc)
    log("UI Store Inventory Monitor")
    log(f"Regions: {', '.join(REGIONS)}")
    log("=" * 50)

    # Load previous state
    prev_hashes = load_json(HASHES_FILE) or {}
    prev_snapshot = load_json(SNAPSHOT_FILE)
    prev_products = prev_snapshot.get("products", {}) if prev_snapshot else {}

    stats = {
        "requests": 0,
        "categories_changed": 0,
        "categories_unchanged": 0,
    }

    backoff = load_backoff()
    if backoff.get("delay_multiplier", 1.0) > 1.0:
        log(f"Backoff active: delay multiplier {backoff['delay_multiplier']:.1f}x")

    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        # Phase 1: Get current build ID (shared across all regions)
        log("Fetching build ID...")
        build_id = get_build_id(client)
        stats["requests"] += 1
        log(f"  Build ID: {build_id}")
        time.sleep(get_request_delay(backoff))

        # Phase 2: Fetch all categories for all regions
        combined_products = {}
        combined_hashes = {}
        combined_changes = {}

        for region in REGIONS:
            log(f"\nScanning region: {region}")
            products, hashes, changes, build_id = scan_region(
                client, build_id, region, prev_hashes, prev_products, stats, backoff
            )
            combined_products.update(products)
            combined_hashes.update(hashes)
            if changes:
                combined_changes[region] = changes

        # Save hashes for next run
        save_json(HASHES_FILE, combined_hashes)

        # Phase 3: Report changes
        total_skus = len(combined_products)
        log(f"\nTotal SKUs across {len(REGIONS)} region(s): {total_skus}")

        any_changes = False
        for region, changes in combined_changes.items():
            if has_changes(changes):
                any_changes = True
                log(f"\n*** CHANGES DETECTED [{region}] ***")
                print_changes(changes)

        if combined_changes and any_changes:
            change_file = CHANGES_DIR / f"{now.strftime('%Y%m%d_%H%M%S')}.json"
            save_json(change_file, {
                "timestamp": now.isoformat(),
                "regions": {r: c for r, c in combined_changes.items() if has_changes(c)},
            })
            log(f"\nChange log saved: {change_file.name}")
        elif prev_products:
            log("No changes since last run.")
        else:
            log("First run — baseline snapshot created.")

        # Count stock statuses
        status_counts = {}
        for p in combined_products.values():
            s = p["status"]
            status_counts[s] = status_counts.get(s, 0) + 1

        log(f"\nStock summary: {status_counts}")
        log(f"Requests made: {stats['requests']} "
            f"({stats['categories_unchanged']} unchanged, "
            f"{stats['categories_changed']} changed)")

        # Save current snapshot (JSON)
        save_json(SNAPSHOT_FILE, {
            "timestamp": now.isoformat(),
            "build_id": build_id,
            "sku_count": total_skus,
            "regions": REGIONS,
            "status_counts": status_counts,
            "products": combined_products,
        })

        # Persist to SQLite
        ts = now.isoformat()
        conn = init_db()
        try:
            for region in REGIONS:
                region_products = {k: v for k, v in combined_products.items()
                                   if v.get("region", "us/en") == region}
                db_upsert_products(conn, region_products, region, ts)
                db_record_prices(conn, region_products, region, ts)
                changes = combined_changes.get(region)
                if changes and has_changes(changes):
                    db_record_events(conn, changes, region, ts)

            db_record_scan(conn, ts, build_id, total_skus,
                           status_counts, stats)
            log(f"Database updated: {DB_FILE.name}")
        finally:
            conn.close()

    # Successful scan — reduce backoff
    record_success(backoff)
    log("Done.\n")


if __name__ == "__main__":
    try:
        main()
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
        print(f"\nConnection error: {e}")
        backoff = load_backoff()
        record_error(backoff)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"\nHTTP error: {e}")
        backoff = load_backoff()
        record_error(backoff)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
