from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, execute_db, pg_json_dumps, get_db

import psycopg2.extras


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Use a SINGLE connection for all queries to avoid exhausting
        # Neon's connection pool and stay within Vercel's 10-second timeout.
        conn = get_db()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # Get all SKUs that have had status transitions (multiple distinct statuses)
            cur.execute("""
                SELECT DISTINCT ph.sku, p.name, p.status, p.category, p.price_cents, p.thumbnail
                FROM price_history ph
                JOIN products p ON ph.sku = p.sku AND p.region = 'us/en'
                WHERE ph.region = 'us/en'
                GROUP BY ph.sku, p.name, p.status, p.category, p.price_cents, p.thumbnail
                HAVING COUNT(DISTINCT ph.status) > 1
            """)
            all_skus = [dict(row) for row in cur.fetchall()]

            hot = []
            for row in all_skus:
                cur.execute(
                    "SELECT timestamp, status FROM price_history WHERE sku = %s AND region = 'us/en' ORDER BY timestamp",
                    (row["sku"],),
                )
                prices = [dict(r) for r in cur.fetchall()]
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
                            s = datetime.fromisoformat(str(w["start"]).replace("Z", "+00:00"))
                            e = datetime.fromisoformat(str(w["end"]).replace("Z", "+00:00"))
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

            cur.close()
        finally:
            conn.close()

        # Sort by shortest average in-stock duration
        hot.sort(key=lambda x: x["avg_instock_minutes"])
        result = hot[:30]

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(pg_json_dumps(result).encode())

    def log_message(self, format, *args):
        pass
