from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, execute_db, pg_json_dumps, get_db, log_error


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
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
                WHERE timestamp::timestamptz > CURRENT_TIMESTAMP - INTERVAL '24 hours'
                GROUP BY event_type
            """)

            last_scan = query_db(
                "SELECT * FROM scans ORDER BY timestamp DESC LIMIT 1"
            )

            total_events = query_db("SELECT COUNT(*) as count FROM events")[0]["count"]
            total_scans = query_db("SELECT COUNT(*) as count FROM scans")[0]["count"]

            result = {
                "products": products,
                "events_24h": events_24h,
                "last_scan": last_scan[0] if last_scan else None,
                "total_events": total_events,
                "total_scans": total_scans,
            }

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(pg_json_dumps(result).encode())
        except Exception as e:
            log_error('api/stats', str(e), traceback.format_exc())
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass
