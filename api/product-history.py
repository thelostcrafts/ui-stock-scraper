from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, execute_db, pg_json_dumps, get_db


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        sku = params.get('sku', [None])[0]
        region = params.get('region', [None])[0]

        if not sku:
            result = {"product": None, "events": [], "prices": []}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(pg_json_dumps(result).encode())
            return

        if region:
            events = query_db(
                "SELECT * FROM events WHERE sku = %s AND region = %s ORDER BY timestamp DESC LIMIT 50",
                (sku, region),
            )
            prices = query_db(
                "SELECT timestamp, price_cents, status FROM price_history WHERE sku = %s AND region = %s ORDER BY timestamp",
                (sku, region),
            )
            product = query_db(
                "SELECT * FROM products WHERE sku = %s AND region = %s",
                (sku, region),
            )
        else:
            events = query_db(
                "SELECT * FROM events WHERE sku = %s ORDER BY timestamp DESC LIMIT 50",
                (sku,),
            )
            prices = query_db(
                "SELECT timestamp, price_cents, status FROM price_history WHERE sku = %s ORDER BY timestamp",
                (sku,),
            )
            product = query_db(
                "SELECT * FROM products WHERE sku = %s ORDER BY region LIMIT 1",
                (sku,),
            )

        result = {
            "product": product[0] if product else None,
            "events": events,
            "prices": prices,
        }

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(pg_json_dumps(result).encode())

    def log_message(self, format, *args):
        pass
