from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, execute_db, pg_json_dumps


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        last_scan = query_db(
            'SELECT timestamp, build_id, sku_count FROM scans ORDER BY timestamp DESC LIMIT 1'
        )

        now = datetime.now(timezone.utc)
        if last_scan:
            scan_ts = last_scan[0]['timestamp']
            try:
                if isinstance(scan_ts, datetime):
                    last_dt = scan_ts if scan_ts.tzinfo else scan_ts.replace(tzinfo=timezone.utc)
                else:
                    last_dt = datetime.fromisoformat(str(scan_ts).replace('Z', '+00:00'))
                age_minutes = (now - last_dt).total_seconds() / 60
            except Exception:
                age_minutes = -1
            status = 'healthy' if age_minutes < 60 else 'stale' if age_minutes < 120 else 'unhealthy'
        else:
            age_minutes = -1
            status = 'no_data'

        result = {
            'status': status,
            'last_scan': last_scan[0] if last_scan else None,
            'age_minutes': round(age_minutes, 1),
            'checked_at': now.isoformat(),
        }

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(pg_json_dumps(result).encode())

    def log_message(self, format, *args):
        pass  # Suppress request logging in serverless
