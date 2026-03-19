from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from db import query_db, execute_db, pg_json_dumps, get_db


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        rows = query_db("""
            SELECT w.sku, w.added_at, w.notes,
                   p.name, p.status, p.price_cents, p.currency, p.category, p.thumbnail, p.region
            FROM watchlist w
            LEFT JOIN products p ON w.sku = p.sku AND p.region = 'us/en'
            ORDER BY w.added_at DESC
        """)

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(pg_json_dumps(rows).encode())

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        sku = body.get('sku', '').strip()
        notes = body.get('notes', '').strip()

        if not sku:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(pg_json_dumps({"error": "sku required"}).encode())
            return

        now = datetime.now(timezone.utc).isoformat()
        execute_db(
            "INSERT INTO watchlist (sku, added_at, notes) VALUES (%s, %s, %s) ON CONFLICT(sku) DO UPDATE SET added_at=EXCLUDED.added_at, notes=EXCLUDED.notes",
            (sku, now, notes),
        )

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(pg_json_dumps({"ok": True, "sku": sku}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        pass
