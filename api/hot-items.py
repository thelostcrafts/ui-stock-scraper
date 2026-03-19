from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, execute_db, pg_json_dumps, get_db, log_error

import psycopg2.extras


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            conn = get_db()
            try:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

                # Get all SKU+region combos that have had status transitions
                cur.execute("""
                    SELECT DISTINCT ph.sku, ph.region, p.name, p.status, p.category,
                           p.price_cents, p.currency, p.thumbnail
                    FROM price_history ph
                    JOIN products p ON ph.sku = p.sku AND ph.region = p.region
                    GROUP BY ph.sku, ph.region, p.name, p.status, p.category,
                             p.price_cents, p.currency, p.thumbnail
                    HAVING COUNT(DISTINCT ph.status) > 1
                """)
                all_entries = [dict(row) for row in cur.fetchall()]

                # Compute hotness per SKU+region
                per_sku = {}  # sku -> list of region results
                for row in all_entries:
                    cur.execute(
                        "SELECT timestamp, status FROM price_history WHERE sku = %s AND region = %s ORDER BY timestamp",
                        (row["sku"], row["region"]),
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
                    windows.append({"status": cur_status, "start": win_start, "end": None})

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

                    entry = {
                        "region": row["region"],
                        "current_status": row["status"],
                        "price_cents": row["price_cents"],
                        "currency": row["currency"],
                        "avg_instock_minutes": round(avg_mins, 1),
                        "min_instock_minutes": round(min_mins, 1),
                        "instock_windows": len(avail_windows),
                        "total_transitions": transitions,
                    }

                    sku = row["sku"]
                    if sku not in per_sku:
                        per_sku[sku] = {
                            "sku": sku,
                            "name": row["name"],
                            "category": row["category"],
                            "thumbnail": row["thumbnail"],
                            "regions": [],
                        }
                    per_sku[sku]["regions"].append(entry)

                cur.close()
            finally:
                conn.close()

            # Build final list: pick hottest region per SKU, include all regions info
            hot = []
            for sku, data in per_sku.items():
                regions = data["regions"]
                # Sort regions by avg_instock_minutes (hottest first)
                regions.sort(key=lambda x: x["avg_instock_minutes"])
                hottest = regions[0]

                hot.append({
                    "sku": data["sku"],
                    "name": data["name"],
                    "category": data["category"],
                    "thumbnail": data["thumbnail"],
                    "current_status": hottest["current_status"],
                    "hottest_region": hottest["region"],
                    "avg_instock_minutes": hottest["avg_instock_minutes"],
                    "min_instock_minutes": hottest["min_instock_minutes"],
                    "instock_windows": hottest["instock_windows"],
                    "total_transitions": hottest["total_transitions"],
                    "regions_hot": len(regions),
                    "region_details": [
                        {
                            "region": r["region"],
                            "avg_instock_minutes": r["avg_instock_minutes"],
                            "current_status": r["current_status"],
                        }
                        for r in regions
                    ],
                })

            hot.sort(key=lambda x: x["avg_instock_minutes"])
            result = hot[:30]

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(pg_json_dumps(result).encode())
        except Exception as e:
            log_error('api/hot-items', str(e), traceback.format_exc())
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass
