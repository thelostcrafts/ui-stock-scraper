#!/usr/bin/env python3
"""
UI Store Inventory Dashboard
=============================
Self-contained local web dashboard for the inventory monitor.
Reads from data/inventory.db (SQLite) and serves a single-page app.

Usage: python3 dashboard.py [port]
  Default port: 8080
  Open http://localhost:8080 in your browser.
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

DB_FILE = Path(__file__).parent / "data" / "inventory.db"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn


def query_db(sql: str, params: tuple = ()) -> list:
    conn = get_db()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Silence request logs

    def respond_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self.serve_html()
        elif path == "/api/products":
            self.api_products(params)
        elif path == "/api/events":
            self.api_events(params)
        elif path == "/api/scans":
            self.api_scans(params)
        elif path == "/api/stats":
            self.api_stats()
        elif path == "/api/categories":
            self.api_categories()
        elif path == "/api/product-history":
            self.api_product_history(params)
        elif path == "/api/sold-out":
            self.api_sold_out(params)
        elif path == "/api/health":
            self.api_health()
        elif path == "/api/watchlist":
            self.api_watchlist_get()
        elif path == "/api/price-analytics":
            self.api_price_analytics()
        elif path == "/api/regions":
            self.api_regions()
        elif path == "/api/availability-windows":
            self.api_availability_windows(params)
        elif path == "/api/hot-items":
            self.api_hot_items()
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if path == "/api/watchlist":
            self.api_watchlist_add(body)
        elif path == "/api/watchlist/remove":
            self.api_watchlist_remove(body)
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def api_products(self, params):
        where = []
        args = []

        region = params.get("region", [None])[0]
        if region:
            where.append("region = ?")
            args.append(region)

        status = params.get("status", [None])[0]
        if status:
            where.append("status = ?")
            args.append(status)

        category = params.get("category", [None])[0]
        if category:
            where.append("category = ?")
            args.append(category)

        search = params.get("q", [None])[0]
        if search:
            where.append("(sku LIKE ? OR name LIKE ?)")
            args.extend([f"%{search}%", f"%{search}%"])

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        order = params.get("sort", ["name"])[0]
        allowed_sorts = {
            "name": "name",
            "sku": "sku",
            "price": "price_cents",
            "-price": "price_cents DESC",
            "status": "status",
            "category": "category",
        }
        order_sql = allowed_sorts.get(order, "name")

        rows = query_db(
            f"SELECT * FROM products {clause} ORDER BY {order_sql}",
            tuple(args),
        )
        self.respond_json(rows)

    def api_events(self, params):
        limit = int(params.get("limit", ["100"])[0])
        event_type = params.get("type", [None])[0]
        sku = params.get("sku", [None])[0]

        where = []
        args = []
        if event_type:
            where.append("event_type = ?")
            args.append(event_type)
        if sku:
            where.append("sku = ?")
            args.append(sku)

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        args.append(limit)

        rows = query_db(
            f"SELECT * FROM events {clause} ORDER BY timestamp DESC LIMIT ?",
            tuple(args),
        )
        self.respond_json(rows)

    def api_scans(self, params):
        limit = int(params.get("limit", ["200"])[0])
        rows = query_db(
            "SELECT * FROM scans ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        self.respond_json(rows)

    def api_stats(self):
        products = query_db("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='Available' THEN 1 ELSE 0 END) as available,
                SUM(CASE WHEN status='SoldOut' THEN 1 ELSE 0 END) as sold_out,
                SUM(CASE WHEN status='ComingSoon' THEN 1 ELSE 0 END) as coming_soon
            FROM products
        """)[0]

        events_24h = query_db("""
            SELECT event_type, COUNT(*) as count
            FROM events
            WHERE timestamp > datetime('now', '-24 hours')
            GROUP BY event_type
        """)

        last_scan = query_db(
            "SELECT * FROM scans ORDER BY timestamp DESC LIMIT 1"
        )

        total_events = query_db("SELECT COUNT(*) as count FROM events")[0]["count"]
        total_scans = query_db("SELECT COUNT(*) as count FROM scans")[0]["count"]

        self.respond_json({
            "products": products,
            "events_24h": events_24h,
            "last_scan": last_scan[0] if last_scan else None,
            "total_events": total_events,
            "total_scans": total_scans,
        })

    def api_categories(self):
        rows = query_db("""
            SELECT category,
                   COUNT(*) as total,
                   SUM(CASE WHEN status='Available' THEN 1 ELSE 0 END) as available,
                   SUM(CASE WHEN status='SoldOut' THEN 1 ELSE 0 END) as sold_out,
                   SUM(CASE WHEN status='ComingSoon' THEN 1 ELSE 0 END) as coming_soon
            FROM products
            GROUP BY category
            ORDER BY category
        """)
        self.respond_json(rows)

    def api_product_history(self, params):
        sku = params.get("sku", [None])[0]
        if not sku:
            self.respond_json({"events": [], "prices": []})
            return
        events = query_db(
            "SELECT * FROM events WHERE sku = ? ORDER BY timestamp DESC LIMIT 50",
            (sku,),
        )
        prices = query_db(
            "SELECT timestamp, price_cents, status FROM price_history WHERE sku = ? ORDER BY timestamp",
            (sku,),
        )
        product = query_db("SELECT * FROM products WHERE sku = ?", (sku,))
        self.respond_json({
            "product": product[0] if product else None,
            "events": events,
            "prices": prices,
        })

    def api_sold_out(self, params):
        rows = query_db("""
            SELECT sku, slug, name, category, subcategory, price_cents, currency
            FROM products
            WHERE status = 'SoldOut'
            ORDER BY category, name
        """)
        self.respond_json(rows)

    def api_health(self):
        last_scan = query_db(
            "SELECT timestamp, build_id, sku_count FROM scans ORDER BY timestamp DESC LIMIT 1"
        )
        now = datetime.now(timezone.utc)
        if last_scan:
            scan_ts = last_scan[0]["timestamp"]
            try:
                last_dt = datetime.fromisoformat(scan_ts.replace("Z", "+00:00"))
                age_minutes = (now - last_dt).total_seconds() / 60
            except Exception:
                age_minutes = -1
            status = "healthy" if age_minutes < 60 else "stale" if age_minutes < 120 else "unhealthy"
        else:
            age_minutes = -1
            status = "no_data"
        self.respond_json({
            "status": status,
            "last_scan": last_scan[0] if last_scan else None,
            "age_minutes": round(age_minutes, 1),
            "checked_at": now.isoformat(),
        })

    def api_watchlist_get(self):
        rows = query_db("""
            SELECT w.sku, w.added_at, w.notes,
                   p.name, p.status, p.price_cents, p.category, p.thumbnail
            FROM watchlist w
            LEFT JOIN products p ON w.sku = p.sku
            ORDER BY w.added_at DESC
        """)
        self.respond_json(rows)

    def api_watchlist_add(self, body):
        sku = body.get("sku", "").strip()
        notes = body.get("notes", "").strip()
        if not sku:
            self.respond_json({"error": "sku required"})
            return
        now = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO watchlist (sku, added_at, notes) VALUES (?, ?, ?)",
                (sku, now, notes),
            )
            conn.commit()
        finally:
            conn.close()
        self.respond_json({"ok": True, "sku": sku})

    def api_watchlist_remove(self, body):
        sku = body.get("sku", "").strip()
        if not sku:
            self.respond_json({"error": "sku required"})
            return
        conn = get_db()
        try:
            conn.execute("DELETE FROM watchlist WHERE sku = ?", (sku,))
            conn.commit()
        finally:
            conn.close()
        self.respond_json({"ok": True, "sku": sku})

    def api_price_analytics(self):
        # Price changes in last 7 days
        recent_changes = query_db("""
            SELECT e.sku, e.name, e.old_value, e.new_value, e.timestamp,
                   json_extract(e.details, '$.delta_cents') as delta_cents
            FROM events e
            WHERE e.event_type = 'price_change'
              AND e.timestamp > datetime('now', '-7 days')
            ORDER BY e.timestamp DESC
        """)

        # Biggest price drops (all time)
        biggest_drops = query_db("""
            SELECT e.sku, e.name, e.old_value, e.new_value,
                   json_extract(e.details, '$.delta_cents') as delta_cents,
                   e.timestamp
            FROM events e
            WHERE e.event_type = 'price_change'
              AND CAST(json_extract(e.details, '$.delta_cents') AS INTEGER) < 0
            ORDER BY CAST(json_extract(e.details, '$.delta_cents') AS INTEGER) ASC
            LIMIT 20
        """)

        # Average price by category
        avg_by_category = query_db("""
            SELECT category,
                   ROUND(AVG(price_cents)) as avg_price,
                   MIN(price_cents) as min_price,
                   MAX(price_cents) as max_price,
                   COUNT(*) as count
            FROM products
            GROUP BY category
            ORDER BY avg_price DESC
        """)

        # Status transitions in last 7 days
        status_changes = query_db("""
            SELECT e.sku, e.name, e.old_value, e.new_value, e.timestamp
            FROM events e
            WHERE e.event_type = 'status_change'
              AND e.timestamp > datetime('now', '-7 days')
            ORDER BY e.timestamp DESC
        """)

        # Products on sale (regular_price_cents != NULL and > price_cents)
        on_sale = query_db("""
            SELECT sku, name, price_cents, regular_price_cents, category,
                   (regular_price_cents - price_cents) as savings_cents
            FROM products
            WHERE regular_price_cents IS NOT NULL
              AND regular_price_cents > price_cents
            ORDER BY savings_cents DESC
            LIMIT 20
        """)

        self.respond_json({
            "recent_price_changes": recent_changes,
            "biggest_drops": biggest_drops,
            "avg_by_category": avg_by_category,
            "recent_status_changes": status_changes,
            "on_sale": on_sale,
        })

    def api_regions(self):
        rows = query_db("""
            SELECT DISTINCT region, currency, COUNT(*) as product_count
            FROM products
            GROUP BY region
            ORDER BY region
        """)
        self.respond_json(rows)

    def api_availability_windows(self, params):
        """Compute availability windows for a SKU — how long it stayed in each status."""
        sku = params.get("sku", [None])[0]
        if not sku:
            self.respond_json({"windows": []})
            return

        rows = query_db(
            "SELECT timestamp, status FROM price_history WHERE sku = ? ORDER BY timestamp",
            (sku,),
        )
        if not rows:
            self.respond_json({"windows": []})
            return

        windows = []
        current_status = rows[0]["status"]
        window_start = rows[0]["timestamp"]

        for row in rows[1:]:
            if row["status"] != current_status:
                windows.append({
                    "status": current_status,
                    "start": window_start,
                    "end": row["timestamp"],
                })
                current_status = row["status"]
                window_start = row["timestamp"]

        # Current open window
        windows.append({
            "status": current_status,
            "start": window_start,
            "end": None,  # still ongoing
        })

        # Compute durations
        for w in windows:
            if w["end"]:
                try:
                    start = datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(w["end"].replace("Z", "+00:00"))
                    w["duration_minutes"] = round((end - start).total_seconds() / 60, 1)
                except Exception:
                    w["duration_minutes"] = None
            else:
                try:
                    start = datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    w["duration_minutes"] = round((now - start).total_seconds() / 60, 1)
                except Exception:
                    w["duration_minutes"] = None

        # Summary stats
        avail_windows = [w for w in windows if w["status"] == "Available" and w["duration_minutes"]]
        sold_windows = [w for w in windows if w["status"] == "SoldOut" and w["duration_minutes"]]

        summary = {
            "available_count": len(avail_windows),
            "avg_available_minutes": round(sum(w["duration_minutes"] for w in avail_windows) / len(avail_windows), 1) if avail_windows else None,
            "max_available_minutes": max((w["duration_minutes"] for w in avail_windows), default=None),
            "min_available_minutes": min((w["duration_minutes"] for w in avail_windows), default=None),
            "soldout_count": len(sold_windows),
            "avg_soldout_minutes": round(sum(w["duration_minutes"] for w in sold_windows) / len(sold_windows), 1) if sold_windows else None,
        }

        self.respond_json({"windows": windows, "summary": summary})

    def api_hot_items(self):
        """Find products that sell out quickly — short in-stock windows."""
        # Get all SKUs that have had at least one status transition
        skus_with_transitions = query_db("""
            SELECT DISTINCT sku FROM events
            WHERE event_type = 'status_change'
        """)

        # For products currently SoldOut that have been Available before,
        # or products that cycle frequently, compute their availability windows
        # from price_history
        all_skus = query_db("""
            SELECT DISTINCT ph.sku, p.name, p.status, p.category, p.price_cents, p.thumbnail
            FROM price_history ph
            JOIN products p ON ph.sku = p.sku AND p.region = 'us/en'
            WHERE ph.region = 'us/en'
            GROUP BY ph.sku
            HAVING COUNT(DISTINCT ph.status) > 1
        """)

        hot = []
        for row in all_skus:
            prices = query_db(
                "SELECT timestamp, status FROM price_history WHERE sku = ? AND region = 'us/en' ORDER BY timestamp",
                (row["sku"],),
            )
            if len(prices) < 2:
                continue

            # Compute windows
            windows = []
            cur_status = prices[0]["status"]
            win_start = prices[0]["timestamp"]
            for p in prices[1:]:
                if p["status"] != cur_status:
                    windows.append({"status": cur_status, "start": win_start, "end": p["timestamp"]})
                    cur_status = p["status"]
                    win_start = p["timestamp"]
            # Current open window
            windows.append({"status": cur_status, "start": win_start, "end": None})

            # Compute durations for Available windows
            avail_windows = []
            for w in windows:
                if w["status"] == "Available" and w["end"]:
                    try:
                        s = datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
                        e = datetime.fromisoformat(w["end"].replace("Z", "+00:00"))
                        avail_windows.append((e - s).total_seconds() / 60)
                    except Exception:
                        pass

            if not avail_windows:
                continue

            avg_mins = sum(avail_windows) / len(avail_windows)
            min_mins = min(avail_windows)
            transitions = len(windows) - 1

            hot.append({
                "sku": row["sku"],
                "name": row["name"],
                "current_status": row["status"],
                "category": row["category"],
                "price_cents": row["price_cents"],
                "thumbnail": row["thumbnail"],
                "avg_instock_minutes": round(avg_mins, 1),
                "min_instock_minutes": round(min_mins, 1),
                "instock_windows": len(avail_windows),
                "total_transitions": transitions,
            })

        # Sort by shortest average in-stock duration
        hot.sort(key=lambda x: x["avg_instock_minutes"])
        self.respond_json(hot[:30])

    def serve_html(self):
        html = HTML_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UI Store Inventory</title>
<style>
  :root {
    --bg: #0a0a0a; --bg2: #141414; --bg3: #1e1e1e;
    --border: #2a2a2a; --text: #e5e5e5; --text2: #999;
    --green: #22c55e; --red: #ef4444; --yellow: #eab308; --blue: #3b82f6;
    --font: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: var(--font); background: var(--bg); color: var(--text); font-size: 13px; }
  a { color: var(--blue); text-decoration: none; }

  .header { padding: 16px 24px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 16px; font-weight: 600; }
  .header .meta { color: var(--text2); font-size: 12px; margin-left: auto; }

  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; padding: 16px 24px; }
  .stat { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
  .stat .label { color: var(--text2); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
  .stat .value { font-size: 28px; font-weight: 700; margin-top: 4px; }
  .stat .value.green { color: var(--green); }
  .stat .value.red { color: var(--red); }
  .stat .value.yellow { color: var(--yellow); }

  .tabs { display: flex; gap: 0; padding: 0 24px; border-bottom: 1px solid var(--border); }
  .tab { padding: 10px 20px; cursor: pointer; color: var(--text2); border-bottom: 2px solid transparent; font-size: 13px; font-family: var(--font); background: none; border-top: none; border-left: none; border-right: none; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--text); border-bottom-color: var(--blue); }

  .controls { padding: 12px 24px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
  .controls input, .controls select {
    background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); padding: 6px 10px; font-family: var(--font); font-size: 12px;
  }
  .controls input { width: 260px; }

  .panel { display: none; }
  .panel.active { display: block; }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; color: var(--text2); font-size: 11px; text-transform: uppercase;
       letter-spacing: 0.5px; border-bottom: 1px solid var(--border); position: sticky; top: 0;
       background: var(--bg); cursor: pointer; user-select: none; }
  th:hover { color: var(--text); }
  td { padding: 7px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  tr:hover { background: var(--bg2); }

  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .badge.available { background: #052e16; color: var(--green); }
  .badge.soldout { background: #2a0a0a; color: var(--red); }
  .badge.comingsoon { background: #1a1500; color: var(--yellow); }
  .badge.regionna { background: #1a1a2e; color: #7c8ba5; }

  .event-type { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
  .event-type.status_change { background: #1e1b4b; color: #818cf8; }
  .event-type.price_change { background: #1a1500; color: var(--yellow); }
  .event-type.new_product { background: #052e16; color: var(--green); }
  .event-type.removed_product { background: #2a0a0a; color: var(--red); }

  .arrow { color: var(--text2); }
  .table-wrap { overflow-x: auto; padding: 0 24px; max-height: calc(100vh - 300px); overflow-y: auto; }

  .cat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; padding: 16px 24px; }
  .cat-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
  .cat-card h3 { font-size: 12px; font-weight: 600; margin-bottom: 8px; word-break: break-all; }
  .cat-card .row { display: flex; justify-content: space-between; font-size: 12px; margin: 3px 0; }

  .empty { padding: 40px; text-align: center; color: var(--text2); }
  .price { font-variant-numeric: tabular-nums; }
  tr.clickable { cursor: pointer; }

  .modal-bg { display:none; position:fixed; inset:0; background:rgba(0,0,0,.7); z-index:100; justify-content:center; align-items:center; }
  .modal-bg.open { display:flex; }
  .modal { background:var(--bg2); border:1px solid var(--border); border-radius:12px; width:94%; max-width:1100px;
           max-height:90vh; overflow-y:auto; padding:32px 40px; }
  .modal h2 { font-size:22px; margin-bottom:4px; }
  .modal .sku-label { color:var(--text2); font-size:13px; margin-bottom:20px; }
  .modal .detail-row { display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border); font-size:14px; }
  .modal .section { margin-top:28px; }
  .modal .section h3 { font-size:14px; color:var(--text2); text-transform:uppercase; letter-spacing:.5px; margin-bottom:10px; }
  .modal .close-btn { position:absolute; top:16px; right:20px; background:none; border:none; color:var(--text2);
                      font-size:20px; cursor:pointer; font-family:var(--font); }
  .modal .close-btn:hover { color:var(--text); }
  .modal-inner { position:relative; }

  .chart-area { width:100%; height:220px; background:var(--bg3); border-radius:8px; padding:16px; margin-top:8px; position:relative; overflow:hidden; }
  .chart-area svg { width:100%; height:100%; }
  .chart-line { fill:none; stroke:var(--blue); stroke-width:2; }
  .chart-dot { fill:var(--blue); }
  .chart-label { fill:var(--text2); font-size:11px; font-family:var(--font); }
  .chart-grid { stroke:var(--border); stroke-width:0.5; }
  .status-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
  .status-dot.avail { background:var(--green); }
  .status-dot.sold { background:var(--red); }
  .status-dot.soon { background:var(--yellow); }

  .analytics-grid { padding:16px 24px; display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  .analytics-section { background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:16px; }
  .analytics-section.full { grid-column:1/-1; }
  .analytics-section h3 { font-size:13px; color:var(--text2); text-transform:uppercase; letter-spacing:.5px; margin-bottom:12px; }
  .analytics-section table { width:100%; }
  .analytics-section td { padding:5px 8px; border-bottom:1px solid var(--border); font-size:12px; }
  .analytics-section .highlight { color:var(--green); font-weight:600; }
  .analytics-section .drop { color:var(--red); font-weight:600; }

  .auto-refresh { display:inline-flex; align-items:center; gap:6px; margin-left:12px; }
  .auto-refresh .dot { width:6px; height:6px; border-radius:50%; background:var(--green); animation:pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

  .product-thumb { width:48px; height:48px; object-fit:contain; border-radius:4px; background:var(--bg3); vertical-align:middle; }
  .modal-thumb { width:200px; height:200px; object-fit:contain; border-radius:8px; background:var(--bg3); display:block; margin:0 auto 16px; }

  .remove-btn { background:none; border:1px solid var(--border); color:var(--red); border-radius:4px; padding:2px 8px;
                font-family:var(--font); font-size:11px; cursor:pointer; }
  .remove-btn:hover { background:var(--red); color:#fff; }

  .hot-card { flex:0 0 auto; background:var(--bg2); border:1px solid var(--border); border-radius:8px;
              padding:12px; width:180px; cursor:pointer; transition:border-color .15s; }
  .hot-card:hover { border-color:var(--blue); }
  .hot-card img { width:60px; height:60px; object-fit:contain; border-radius:4px; background:var(--bg3);
                  display:block; margin:0 auto 8px; }
  .hot-card .hc-name { font-size:11px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-bottom:4px; }
  .hot-card .hc-sku { font-size:10px; color:var(--text2); margin-bottom:6px; }
  .hot-card .hc-stat { font-size:11px; display:flex; justify-content:space-between; margin:2px 0; }
  .hot-card .hc-stat .val { font-weight:600; }
  .hot-card .hc-stat .val.fast { color:var(--red); }
</style>
</head>
<body>

<div class="header">
  <h1>UI Store Inventory</h1>
  <div class="meta" id="scan-meta">Loading...</div>
  <div class="auto-refresh"><div class="dot"></div><span style="color:var(--text2);font-size:11px">Auto-refresh</span></div>
</div>

<div class="stats" id="stats"></div>

<div id="hot-items-section" style="display:none; padding:0 24px 12px;">
  <div style="display:flex; align-items:center; gap:8px; margin-bottom:10px;">
    <span style="font-size:14px; font-weight:600;">Hot Items</span>
    <span style="font-size:11px; color:var(--text2);">Products that sell out fastest</span>
  </div>
  <div id="hot-items-list" style="display:flex; gap:10px; overflow-x:auto; padding-bottom:8px;"></div>
</div>

<div class="tabs">
  <button class="tab active" data-panel="products">Products</button>
  <button class="tab" data-panel="sold-out">Sold Out</button>
  <button class="tab" data-panel="events">Events</button>
  <button class="tab" data-panel="analytics">Analytics</button>
  <button class="tab" data-panel="watchlist">Watchlist</button>
  <button class="tab" data-panel="categories">Categories</button>
</div>

<div id="products" class="panel active">
  <div class="controls">
    <input type="text" id="search" placeholder="Search SKU or name...">
    <select id="filter-status">
      <option value="">All statuses</option>
      <option value="Available">Available</option>
      <option value="SoldOut">Sold Out</option>
      <option value="ComingSoon">Coming Soon</option>
      <option value="RegionNotAvailable">Region N/A</option>
    </select>
    <select id="filter-category">
      <option value="">All categories</option>
    </select>
    <select id="filter-region">
      <option value="">All regions</option>
    </select>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th data-sort="sku">SKU</th>
          <th data-sort="name">Name</th>
          <th data-sort="price">Price</th>
          <th data-sort="status">Status</th>
          <th data-sort="category">Category</th>
        </tr>
      </thead>
      <tbody id="product-body"></tbody>
    </table>
  </div>
</div>

<div id="events" class="panel">
  <div class="controls">
    <select id="filter-event-type">
      <option value="">All events</option>
      <option value="status_change">Status Changes</option>
      <option value="price_change">Price Changes</option>
      <option value="new_product">New Products</option>
      <option value="removed_product">Removed Products</option>
    </select>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Type</th>
          <th>SKU</th>
          <th>Name</th>
          <th>Change</th>
        </tr>
      </thead>
      <tbody id="event-body"></tbody>
    </table>
  </div>
</div>

<div id="sold-out" class="panel">
  <div class="controls">
    <input type="text" id="search-soldout" placeholder="Search sold out items...">
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>SKU</th>
          <th>Name</th>
          <th>Price</th>
          <th>Category</th>
          <th>Subcategory</th>
        </tr>
      </thead>
      <tbody id="soldout-body"></tbody>
    </table>
  </div>
</div>

<div id="analytics" class="panel">
  <div class="analytics-grid" id="analytics-content">
    <div class="empty">Loading analytics...</div>
  </div>
</div>

<div id="watchlist" class="panel">
  <div class="controls">
    <input type="text" id="watchlist-sku" placeholder="Enter SKU to watch...">
    <input type="text" id="watchlist-notes" placeholder="Notes (optional)" style="width:200px">
    <button id="watchlist-add-btn" style="background:var(--blue);color:#fff;border:none;border-radius:6px;padding:6px 14px;font-family:var(--font);font-size:12px;cursor:pointer;">Add to Watchlist</button>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>SKU</th>
          <th>Name</th>
          <th>Price</th>
          <th>Status</th>
          <th>Category</th>
          <th>Notes</th>
          <th>Added</th>
          <th></th>
        </tr>
      </thead>
      <tbody id="watchlist-body"></tbody>
    </table>
  </div>
</div>

<div id="categories" class="panel">
  <div class="cat-grid" id="cat-grid"></div>
</div>

<div class="modal-bg" id="modal-bg">
  <div class="modal">
    <div class="modal-inner">
      <button class="close-btn" id="modal-close">&times;</button>
      <div id="modal-content"></div>
    </div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

let currentSort = 'name';
let allProducts = [];

// Tabs
$$('.tab').forEach(t => t.addEventListener('click', () => {
  $$('.tab').forEach(x => x.classList.remove('active'));
  $$('.panel').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  $(`#${t.dataset.panel}`).classList.add('active');
}));

function fmt(cents) {
  return '$' + (cents / 100).toLocaleString('en-US', {minimumFractionDigits: 2});
}

function badge(status) {
  const cls = status === 'Available' ? 'available' : status === 'SoldOut' ? 'soldout' : status === 'RegionNotAvailable' ? 'regionna' : 'comingsoon';
  const label = status === 'SoldOut' ? 'Sold Out' : status === 'ComingSoon' ? 'Coming Soon' : status === 'RegionNotAvailable' ? 'Region N/A' : status;
  return `<span class="badge ${cls}">${label}</span>`;
}

function eventBadge(type) {
  const labels = {status_change:'Status',price_change:'Price',new_product:'New',removed_product:'Removed'};
  return `<span class="event-type ${type}">${labels[type]||type}</span>`;
}

function timeAgo(iso) {
  const d = new Date(iso);
  const now = new Date();
  const sec = Math.floor((now - d) / 1000);
  if (sec < 60) return sec + 's ago';
  if (sec < 3600) return Math.floor(sec/60) + 'm ago';
  if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
  return Math.floor(sec/86400) + 'd ago';
}

async function loadStats() {
  const data = await fetch('/api/stats').then(r => r.json());
  const p = data.products;
  $('#stats').innerHTML = `
    <div class="stat"><div class="label">Total SKUs</div><div class="value">${p.total}</div></div>
    <div class="stat"><div class="label">Available</div><div class="value green">${p.available}</div></div>
    <div class="stat"><div class="label">Sold Out</div><div class="value red">${p.sold_out}</div></div>
    <div class="stat"><div class="label">Coming Soon</div><div class="value yellow">${p.coming_soon}</div></div>
    <div class="stat"><div class="label">Total Events</div><div class="value">${data.total_events}</div></div>
    <div class="stat"><div class="label">Total Scans</div><div class="value">${data.total_scans}</div></div>
  `;
  if (data.last_scan) {
    $('#scan-meta').textContent = `Last scan: ${timeAgo(data.last_scan.timestamp)} · Build ${data.last_scan.build_id}`;
  }
}

async function loadProducts() {
  const params = new URLSearchParams();
  const q = $('#search').value;
  const status = $('#filter-status').value;
  const category = $('#filter-category').value;
  const region = $('#filter-region').value;
  if (q) params.set('q', q);
  if (status) params.set('status', status);
  if (category) params.set('category', category);
  if (region) params.set('region', region);
  params.set('sort', currentSort);

  allProducts = await fetch('/api/products?' + params).then(r => r.json());
  renderProducts();
}

function renderProducts() {
  const body = $('#product-body');
  if (!allProducts.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty">No products found</td></tr>';
    return;
  }
  body.innerHTML = allProducts.map(p => `
    <tr class="clickable" data-sku="${esc(p.sku)}">
      <td><strong>${esc(p.sku)}</strong></td>
      <td>${esc(p.name)}</td>
      <td class="price">${fmt(p.price_cents)}</td>
      <td>${badge(p.status)}</td>
      <td>${esc(p.category.replace('all-',''))}</td>
    </tr>
  `).join('');
  body.querySelectorAll('tr.clickable').forEach(tr => {
    tr.addEventListener('click', () => showProductDetail(tr.dataset.sku));
  });
}

async function loadEvents() {
  const type = $('#filter-event-type').value;
  const params = new URLSearchParams();
  if (type) params.set('type', type);
  params.set('limit', '200');

  const events = await fetch('/api/events?' + params).then(r => r.json());
  const body = $('#event-body');
  if (!events.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty">No events recorded yet. Changes will appear here after subsequent monitor runs.</td></tr>';
    return;
  }
  body.innerHTML = events.map(e => {
    let change = '';
    if (e.old_value && e.new_value) {
      change = `${esc(e.old_value)} <span class="arrow">→</span> ${esc(e.new_value)}`;
    } else if (e.new_value) {
      change = esc(e.new_value);
    }
    return `<tr>
      <td>${timeAgo(e.timestamp)}</td>
      <td>${eventBadge(e.event_type)}</td>
      <td><strong>${esc(e.sku)}</strong></td>
      <td>${esc(e.name)}</td>
      <td>${change}</td>
    </tr>`;
  }).join('');
}

async function loadCategories() {
  const cats = await fetch('/api/categories').then(r => r.json());
  $('#cat-grid').innerHTML = cats.map(c => `
    <div class="cat-card">
      <h3>${esc(c.category.replace('all-',''))}</h3>
      <div class="row"><span>Total</span><span>${c.total}</span></div>
      <div class="row"><span style="color:var(--green)">Available</span><span>${c.available}</span></div>
      <div class="row"><span style="color:var(--red)">Sold Out</span><span>${c.sold_out}</span></div>
      <div class="row"><span style="color:var(--yellow)">Coming Soon</span><span>${c.coming_soon}</span></div>
    </div>
  `).join('');
}

async function loadCategoryFilter() {
  const cats = await fetch('/api/categories').then(r => r.json());
  const sel = $('#filter-category');
  cats.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c.category;
    opt.textContent = c.category.replace('all-','');
    sel.appendChild(opt);
  });
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// Sort headers
$$('th[data-sort]').forEach(th => th.addEventListener('click', () => {
  const sort = th.dataset.sort;
  currentSort = currentSort === sort ? `-${sort}` : sort;
  loadProducts();
}));

// Filters
$('#search').addEventListener('input', debounce(loadProducts, 300));
$('#filter-status').addEventListener('change', loadProducts);
$('#filter-category').addEventListener('change', loadProducts);
$('#filter-region').addEventListener('change', loadProducts);
$('#filter-event-type').addEventListener('change', loadEvents);
$('#search-soldout').addEventListener('input', debounce(renderSoldOut, 300));

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// Sold out tab
let allSoldOut = [];
async function loadSoldOut() {
  allSoldOut = await fetch('/api/sold-out').then(r => r.json());
  renderSoldOut();
}
function renderSoldOut() {
  const q = ($('#search-soldout')?.value || '').toLowerCase();
  const filtered = q ? allSoldOut.filter(p =>
    p.sku.toLowerCase().includes(q) || p.name.toLowerCase().includes(q) || p.category.toLowerCase().includes(q)
  ) : allSoldOut;
  const body = $('#soldout-body');
  if (!filtered.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty">No sold out items found</td></tr>';
    return;
  }
  body.innerHTML = filtered.map(p => `
    <tr class="clickable" data-sku="${esc(p.sku)}">
      <td><strong>${esc(p.sku)}</strong></td>
      <td>${esc(p.name)}</td>
      <td class="price">${fmt(p.price_cents)}</td>
      <td>${esc(p.category.replace('all-',''))}</td>
      <td>${esc(p.subcategory)}</td>
    </tr>
  `).join('');
  body.querySelectorAll('tr.clickable').forEach(tr => {
    tr.addEventListener('click', () => showProductDetail(tr.dataset.sku));
  });
}

// Product detail modal
async function showProductDetail(sku) {
  const [data, winData] = await Promise.all([
    fetch('/api/product-history?sku=' + encodeURIComponent(sku)).then(r => r.json()),
    fetch('/api/availability-windows?sku=' + encodeURIComponent(sku)).then(r => r.json()),
  ]);
  const p = data.product;
  if (!p) return;

  const statusDot = p.status === 'Available' ? 'avail' : p.status === 'SoldOut' ? 'sold' : p.status === 'RegionNotAvailable' ? 'regionna' : 'soon';
  const statusLabel = p.status === 'SoldOut' ? 'Sold Out' : p.status === 'ComingSoon' ? 'Coming Soon' : p.status === 'RegionNotAvailable' ? 'Region N/A' : p.status;

  let html = `
    ${p.thumbnail ? `<img class="modal-thumb" src="${esc(p.thumbnail)}" alt="${esc(p.name)}">` : ''}
    <h2>${esc(p.name)}</h2>
    <div class="sku-label">${esc(p.sku)} · ${esc(p.category.replace('all-',''))} · ${esc(p.subcategory)}</div>
    <div class="detail-row"><span>Current Price</span><span class="price">${fmt(p.price_cents)}</span></div>
    <div class="detail-row"><span>Status</span><span><span class="status-dot ${statusDot}"></span>${statusLabel}</span></div>
    <div class="detail-row"><span>Store Link</span><span><a href="https://store.ui.com/us/en/category/${esc(p.category)}/products/${esc(p.slug)}" target="_blank">${esc(p.slug)} &#8599;</a></span></div>
    <div class="detail-row"><span>First seen</span><span>${fmtDate(p.first_seen)}</span></div>
    <div class="detail-row"><span>Last updated</span><span>${fmtDate(p.last_updated)}</span></div>
  `;

  // Charts
  if (data.prices && data.prices.length >= 1) {
    html += `<div class="section"><h3>Price History</h3>${buildPriceChart(data.prices)}</div>`;
    html += `<div class="section"><h3>Availability</h3>${buildAvailChart(data.prices)}</div>`;
    // Availability stats
    const total = data.prices.length;
    const avail = data.prices.filter(x => x.status === 'Available').length;
    const pct = total ? Math.round(avail / total * 100) : 0;
    html += `<div class="detail-row" style="margin-top:4px"><span>Availability rate</span><span>${pct}% (${avail}/${total} scans)</span></div>`;
  } else {
    html += `<div class="section"><h3>Price History</h3><div class="empty">Awaiting first scan data</div></div>`;
  }

  // Availability Windows (in-stock duration tracking)
  if (winData.windows && winData.windows.length > 1) {
    html += `<div class="section"><h3>Stock Duration Windows</h3>`;
    html += buildWindowsChart(winData.windows);

    // Summary stats
    const s = winData.summary;
    if (s.available_count > 0) {
      html += `<div class="detail-row" style="margin-top:8px"><span>In-stock windows</span><span>${s.available_count} times</span></div>`;
      html += `<div class="detail-row"><span>Avg in-stock duration</span><span>${fmtDuration(s.avg_available_minutes)}</span></div>`;
      html += `<div class="detail-row"><span>Longest in-stock</span><span>${fmtDuration(s.max_available_minutes)}</span></div>`;
      html += `<div class="detail-row"><span>Shortest in-stock</span><span>${fmtDuration(s.min_available_minutes)}</span></div>`;
    }
    if (s.soldout_count > 0) {
      html += `<div class="detail-row"><span>Out-of-stock windows</span><span>${s.soldout_count} times</span></div>`;
      html += `<div class="detail-row"><span>Avg out-of-stock duration</span><span>${fmtDuration(s.avg_soldout_minutes)}</span></div>`;
    }

    // Individual windows table
    html += `<div style="margin-top:12px; max-height:200px; overflow-y:auto;"><table style="width:100%">
      <tr><th style="font-size:11px">Status</th><th style="font-size:11px">Start</th><th style="font-size:11px">End</th><th style="font-size:11px">Duration</th></tr>`;
    winData.windows.forEach(w => {
      const color = w.status === 'Available' ? 'var(--green)' : w.status === 'SoldOut' ? 'var(--red)' : 'var(--yellow)';
      const endLabel = w.end ? fmtDate(w.end) : 'ongoing';
      html += `<tr>
        <td style="color:${color};font-weight:600;font-size:12px">${w.status === 'SoldOut' ? 'Out of Stock' : w.status}</td>
        <td style="font-size:12px">${fmtDate(w.start)}</td>
        <td style="font-size:12px">${endLabel}</td>
        <td style="font-size:12px">${w.duration_minutes != null ? fmtDuration(w.duration_minutes) : '—'}</td>
      </tr>`;
    });
    html += `</table></div></div>`;
  }

  // Events
  if (data.events && data.events.length) {
    html += `<div class="section"><h3>Change Log</h3>`;
    html += data.events.map(e => {
      let change = '';
      if (e.old_value && e.new_value) change = `${esc(e.old_value)} → ${esc(e.new_value)}`;
      else if (e.new_value) change = esc(e.new_value);
      return `<div class="detail-row"><span>${eventBadge(e.event_type)} ${timeAgo(e.timestamp)}</span><span>${change}</span></div>`;
    }).join('');
    html += `</div>`;
  }

  $('#modal-content').innerHTML = html;
  $('#modal-bg').classList.add('open');
}

function fmtDuration(minutes) {
  if (minutes == null) return '—';
  if (minutes < 60) return Math.round(minutes) + 'm';
  if (minutes < 1440) return Math.round(minutes / 60 * 10) / 10 + 'h';
  const days = Math.round(minutes / 1440 * 10) / 10;
  return days + 'd';
}

function buildWindowsChart(windows) {
  if (!windows || windows.length < 2) return '';
  const W = 1020, H = 80, pad = {t:5, r:20, b:25, l:65};
  const cW = W - pad.l - pad.r;
  const barH = H - pad.t - pad.b;

  // Compute time range
  const starts = windows.map(w => new Date(w.start).getTime());
  const ends = windows.map(w => w.end ? new Date(w.end).getTime() : Date.now());
  const tMin = Math.min(...starts);
  const tMax = Math.max(...ends);
  const tRange = tMax - tMin || 1;

  const x = (t) => pad.l + ((t - tMin) / tRange) * cW;

  // Draw bars for each window
  let bars = '';
  windows.forEach(w => {
    const t0 = new Date(w.start).getTime();
    const t1 = w.end ? new Date(w.end).getTime() : Date.now();
    const color = w.status === 'Available' ? 'var(--green)' : w.status === 'SoldOut' ? 'var(--red)' : 'var(--yellow)';
    const bx = x(t0);
    const bw = Math.max(x(t1) - bx, 2);
    bars += '<rect x="' + bx.toFixed(1) + '" y="' + pad.t + '" width="' + bw.toFixed(1) + '" height="' + barH + '" fill="' + color + '" opacity="0.85"/>';
    // Duration label inside bar if wide enough
    if (bw > 40 && w.duration_minutes != null) {
      const cx = bx + bw / 2;
      const label = fmtDuration(w.duration_minutes);
      bars += '<text x="' + cx.toFixed(1) + '" y="' + (pad.t + barH/2 + 1) + '" text-anchor="middle" dominant-baseline="middle" fill="#fff" font-size="10" font-family="var(--font)" font-weight="600">' + label + '</text>';
    }
  });

  // Label
  const label = '<text class="chart-label" x="' + (pad.l-8) + '" y="' + (pad.t + barH/2 + 1) + '" text-anchor="end" dominant-baseline="middle">Status</text>';

  // X-axis dates
  const dates = [tMin, tMin + tRange * 0.5, tMax];
  const xLabels = dates.map((t, k) => {
    const d = new Date(t).toISOString().split('T')[0];
    const anchor = k === 0 ? 'start' : k === dates.length - 1 ? 'end' : 'middle';
    return '<text class="chart-label" x="' + x(t).toFixed(1) + '" y="' + (H-2) + '" text-anchor="' + anchor + '">' + d + '</text>';
  }).join('');

  // Legend
  const legend = '<text class="chart-label" x="' + (W-pad.r) + '" y="' + (pad.t-1) + '" text-anchor="end"><tspan fill="var(--green)">■ </tspan><tspan>In Stock</tspan><tspan dx="6" fill="var(--red)">■ </tspan><tspan>Out of Stock</tspan><tspan dx="6" fill="var(--yellow)">■ </tspan><tspan>Coming Soon</tspan></text>';

  return '<div class="chart-area" style="height:100px"><svg viewBox="0 0 ' + W + ' ' + H + '">' + bars + label + xLabels + legend + '</svg></div>';
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'}) + ' ' +
         d.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit'});
}

function buildPriceChart(prices) {
  const W = 1020, H = 200, pad = {t:20, r:20, b:30, l:65};
  const cW = W - pad.l - pad.r;
  const cH = H - pad.t - pad.b;
  const n = prices.length;

  const vals = prices.map(p => p.price_cents);
  let minP = Math.min(...vals);
  let maxP = Math.max(...vals);
  // Add 5% padding so flat lines don't sit on the edge
  const margin = Math.max((maxP - minP) * 0.1, maxP * 0.02);
  minP = Math.max(0, minP - margin);
  maxP = maxP + margin;
  const range = maxP - minP || 1;

  const x = (i) => pad.l + (n > 1 ? (i / (n - 1)) * cW : cW / 2);
  const y = (v) => pad.t + cH - ((v - minP) / range) * cH;

  // Grid lines (3 levels)
  const gridVals = [minP, minP + range * 0.5, maxP];
  const grid = gridVals.map(v =>
    `<line class="chart-grid" x1="${pad.l}" y1="${y(v).toFixed(1)}" x2="${W-pad.r}" y2="${y(v).toFixed(1)}"/>`
  ).join('');

  // Y-axis labels
  const yLabels = gridVals.map(v =>
    `<text class="chart-label" x="${pad.l-8}" y="${y(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle">${fmt(Math.round(v))}</text>`
  ).join('');

  // Price line
  const points = prices.map((p, i) => `${x(i).toFixed(1)},${y(p.price_cents).toFixed(1)}`);
  const line = n > 1
    ? `<polyline class="chart-line" points="${points.join(' ')}"/>`
    : '';

  // Dots colored by status
  const dots = prices.map((p, i) => {
    const color = p.status === 'Available' ? 'var(--green)' : p.status === 'SoldOut' ? 'var(--red)' : 'var(--yellow)';
    const r = n <= 50 ? 3.5 : n <= 200 ? 2 : 1.5;
    return `<circle cx="${x(i).toFixed(1)}" cy="${y(p.price_cents).toFixed(1)}" r="${r}" fill="${color}"/>`;
  }).join('');

  // X-axis date labels
  const xLabels = buildXLabels(prices, x, H);

  // Legend
  const legend = `
    <text class="chart-label" x="${W-pad.r}" y="${pad.t-3}" text-anchor="end">
      <tspan fill="var(--green)">● </tspan><tspan>Available</tspan>
      <tspan dx="8" fill="var(--red)">● </tspan><tspan>Sold Out</tspan>
      <tspan dx="8" fill="var(--yellow)">● </tspan><tspan>Coming Soon</tspan>
    </text>`;

  return `<div class="chart-area"><svg viewBox="0 0 ${W} ${H}">${grid}${line}${dots}${yLabels}${xLabels}${legend}</svg></div>`;
}

function buildAvailChart(prices) {
  const W = 1020, H = 60, pad = {t:5, r:20, b:20, l:65};
  const cW = W - pad.l - pad.r;
  const barH = H - pad.t - pad.b;
  const n = prices.length;

  // Draw horizontal bars for each scan, colored by status
  const barW = Math.max(cW / n, 1);
  const bars = prices.map((p, i) => {
    const color = p.status === 'Available' ? 'var(--green)' : p.status === 'SoldOut' ? 'var(--red)' : 'var(--yellow)';
    const bx = pad.l + (i / n) * cW;
    return `<rect x="${bx.toFixed(1)}" y="${pad.t}" width="${(barW + 0.5).toFixed(1)}" height="${barH}" fill="${color}" opacity="0.8"/>`;
  }).join('');

  // Label
  const label = `<text class="chart-label" x="${pad.l-8}" y="${pad.t + barH/2 + 1}" text-anchor="end" dominant-baseline="middle">Status</text>`;

  // X-axis
  const x = (i) => pad.l + (i / (n - 1)) * cW;
  const xLabels = buildXLabels(prices, x, H);

  return `<div class="chart-area" style="height:80px"><svg viewBox="0 0 ${W} ${H}">${bars}${label}${xLabels}</svg></div>`;
}

function buildXLabels(prices, xFn, H) {
  const n = prices.length;
  if (n === 0) return '';
  if (n === 1) {
    const d = prices[0].timestamp.split('T')[0];
    return `<text class="chart-label" x="${xFn(0).toFixed(1)}" y="${H-2}" text-anchor="middle">${d}</text>`;
  }
  // Show 3-5 date labels evenly spaced
  const count = Math.min(5, n);
  const labels = [];
  for (let k = 0; k < count; k++) {
    const i = Math.round(k * (n - 1) / (count - 1));
    const d = prices[i].timestamp.split('T')[0];
    const anchor = k === 0 ? 'start' : k === count - 1 ? 'end' : 'middle';
    labels.push(`<text class="chart-label" x="${xFn(i).toFixed(1)}" y="${H-2}" text-anchor="${anchor}">${d}</text>`);
  }
  return labels.join('');
}

// Modal close
$('#modal-close').addEventListener('click', () => $('#modal-bg').classList.remove('open'));
$('#modal-bg').addEventListener('click', (e) => {
  if (e.target === $('#modal-bg')) $('#modal-bg').classList.remove('open');
});

// Watchlist
$('#watchlist-add-btn').addEventListener('click', async () => {
  const sku = $('#watchlist-sku').value.trim();
  if (!sku) return;
  const notes = $('#watchlist-notes').value.trim();
  await fetch('/api/watchlist', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sku, notes}),
  });
  $('#watchlist-sku').value = '';
  $('#watchlist-notes').value = '';
  loadWatchlist();
});

async function loadWatchlist() {
  const items = await fetch('/api/watchlist').then(r => r.json());
  const body = $('#watchlist-body');
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">No items in watchlist. Add SKUs above to track specific products.</td></tr>';
    return;
  }
  body.innerHTML = items.map(w => `
    <tr class="clickable" data-sku="${esc(w.sku)}">
      <td><strong>${esc(w.sku)}</strong></td>
      <td>${esc(w.name || '—')}</td>
      <td class="price">${w.price_cents ? fmt(w.price_cents) : '—'}</td>
      <td>${w.status ? badge(w.status) : '—'}</td>
      <td>${esc((w.category || '').replace('all-',''))}</td>
      <td>${esc(w.notes || '')}</td>
      <td>${timeAgo(w.added_at)}</td>
      <td><button class="remove-btn" data-sku="${esc(w.sku)}" onclick="event.stopPropagation();removeWatch('${esc(w.sku)}')">Remove</button></td>
    </tr>
  `).join('');
  body.querySelectorAll('tr.clickable').forEach(tr => {
    tr.addEventListener('click', () => showProductDetail(tr.dataset.sku));
  });
}

async function removeWatch(sku) {
  await fetch('/api/watchlist/remove', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sku}),
  });
  loadWatchlist();
}

// Analytics
async function loadAnalytics() {
  const data = await fetch('/api/price-analytics').then(r => r.json());
  let html = '';

  // Avg price by category
  html += '<div class="analytics-section"><h3>Average Price by Category</h3><table>';
  data.avg_by_category.forEach(c => {
    html += `<tr>
      <td>${esc(c.category.replace('all-',''))}</td>
      <td class="price">${fmt(c.avg_price)} avg</td>
      <td class="price">${fmt(c.min_price)} – ${fmt(c.max_price)}</td>
      <td>${c.count} SKUs</td>
    </tr>`;
  });
  html += '</table></div>';

  // On sale
  html += '<div class="analytics-section"><h3>Currently On Sale</h3>';
  if (data.on_sale.length) {
    html += '<table>';
    data.on_sale.forEach(p => {
      const pct = Math.round(p.savings_cents / p.regular_price_cents * 100);
      html += `<tr>
        <td><strong>${esc(p.sku)}</strong></td>
        <td>${esc(p.name)}</td>
        <td class="price">${fmt(p.price_cents)}</td>
        <td style="text-decoration:line-through;color:var(--text2)">${fmt(p.regular_price_cents)}</td>
        <td class="highlight">-${pct}%</td>
      </tr>`;
    });
    html += '</table>';
  } else {
    html += '<div class="empty">No products currently on sale</div>';
  }
  html += '</div>';

  // Recent price changes (7d)
  html += '<div class="analytics-section full"><h3>Price Changes (Last 7 Days)</h3>';
  if (data.recent_price_changes.length) {
    html += '<table>';
    data.recent_price_changes.forEach(c => {
      const cls = (c.delta_cents && c.delta_cents < 0) ? 'highlight' : 'drop';
      const arrow = (c.delta_cents && c.delta_cents < 0) ? '↓' : '↑';
      html += `<tr>
        <td>${timeAgo(c.timestamp)}</td>
        <td><strong>${esc(c.sku)}</strong></td>
        <td>${esc(c.name)}</td>
        <td>${esc(c.old_value)} → ${esc(c.new_value)}</td>
        <td class="${cls}">${arrow} ${c.delta_cents ? fmt(Math.abs(c.delta_cents)) : ''}</td>
      </tr>`;
    });
    html += '</table>';
  } else {
    html += '<div class="empty">No price changes in the last 7 days</div>';
  }
  html += '</div>';

  // Biggest drops all time
  html += '<div class="analytics-section full"><h3>Biggest Price Drops (All Time)</h3>';
  if (data.biggest_drops.length) {
    html += '<table>';
    data.biggest_drops.forEach(c => {
      html += `<tr>
        <td>${timeAgo(c.timestamp)}</td>
        <td><strong>${esc(c.sku)}</strong></td>
        <td>${esc(c.name)}</td>
        <td>${esc(c.old_value)} → ${esc(c.new_value)}</td>
        <td class="highlight">↓ ${c.delta_cents ? fmt(Math.abs(c.delta_cents)) : ''}</td>
      </tr>`;
    });
    html += '</table>';
  } else {
    html += '<div class="empty">No price drops recorded yet</div>';
  }
  html += '</div>';

  // Status changes (7d)
  html += '<div class="analytics-section full"><h3>Stock Status Changes (Last 7 Days)</h3>';
  if (data.recent_status_changes.length) {
    html += '<table>';
    data.recent_status_changes.forEach(c => {
      const cls = c.new_value === 'Available' ? 'highlight' : 'drop';
      html += `<tr>
        <td>${timeAgo(c.timestamp)}</td>
        <td><strong>${esc(c.sku)}</strong></td>
        <td>${esc(c.name)}</td>
        <td>${badge(c.old_value)} <span class="arrow">→</span> ${badge(c.new_value)}</td>
      </tr>`;
    });
    html += '</table>';
  } else {
    html += '<div class="empty">No status changes in the last 7 days</div>';
  }
  html += '</div>';

  $('#analytics-content').innerHTML = html;
}

// Hot items
async function loadHotItems() {
  const items = await fetch('/api/hot-items').then(r => r.json());
  const section = $('#hot-items-section');
  const list = $('#hot-items-list');
  if (!items.length) {
    section.style.display = 'none';
    return;
  }
  section.style.display = 'block';
  list.innerHTML = items.map(h => {
    const statusDot = h.current_status === 'Available' ? 'avail' : h.current_status === 'SoldOut' ? 'sold' : 'soon';
    return `<div class="hot-card" data-sku="${esc(h.sku)}" onclick="showProductDetail('${esc(h.sku)}')">
      ${h.thumbnail ? `<img src="${esc(h.thumbnail)}" alt="${esc(h.name)}">` : ''}
      <div class="hc-name" title="${esc(h.name)}">${esc(h.name)}</div>
      <div class="hc-sku">${esc(h.sku)} · <span class="status-dot ${statusDot}"></span></div>
      <div class="hc-stat"><span>Avg in-stock</span><span class="val fast">${fmtDuration(h.avg_instock_minutes)}</span></div>
      <div class="hc-stat"><span>Shortest</span><span class="val fast">${fmtDuration(h.min_instock_minutes)}</span></div>
      <div class="hc-stat"><span>Transitions</span><span class="val">${h.total_transitions}</span></div>
    </div>`;
  }).join('');
}

// Auto-refresh every 60 seconds
function refreshAll() {
  loadStats();
  loadProducts();
  loadEvents();
  loadSoldOut();
  loadCategories();
  loadWatchlist();
  loadHotItems();
}
setInterval(refreshAll, 60000);

// Load region filter
async function loadRegionFilter() {
  const regions = await fetch('/api/regions').then(r => r.json());
  const sel = $('#filter-region');
  regions.forEach(r => {
    const opt = document.createElement('option');
    opt.value = r.region;
    opt.textContent = r.region + ' (' + r.currency + ', ' + r.product_count + ')';
    sel.appendChild(opt);
  });
}

// Init
loadStats();
loadProducts();
loadEvents();
loadSoldOut();
loadCategories();
loadCategoryFilter();
loadRegionFilter();
loadWatchlist();
loadAnalytics();
loadHotItems();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    if not DB_FILE.exists():
        print(f"Database not found at {DB_FILE}")
        print("Run monitor.py first to create the database.")
        sys.exit(1)

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Dashboard running at http://localhost:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
