from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, execute_db, pg_json_dumps, get_db


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        sku = params.get('sku', [None])[0]
        region = params.get('region', [None])[0]

        if not sku:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(pg_json_dumps({"windows": []}).encode())
            return

        if region:
            rows = query_db(
                "SELECT timestamp, status FROM price_history WHERE sku = %s AND region = %s ORDER BY timestamp",
                (sku, region),
            )
        else:
            rows = query_db(
                "SELECT timestamp, status FROM price_history WHERE sku = %s ORDER BY timestamp",
                (sku,),
            )

        if not rows:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(pg_json_dumps({"windows": []}).encode())
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
                    start = datetime.fromisoformat(str(w["start"]).replace("Z", "+00:00"))
                    end = datetime.fromisoformat(str(w["end"]).replace("Z", "+00:00"))
                    w["duration_minutes"] = round((end - start).total_seconds() / 60, 1)
                except Exception:
                    w["duration_minutes"] = None
            else:
                try:
                    start = datetime.fromisoformat(str(w["start"]).replace("Z", "+00:00"))
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

        result = {"windows": windows, "summary": summary}

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(pg_json_dumps(result).encode())

    def log_message(self, format, *args):
        pass
