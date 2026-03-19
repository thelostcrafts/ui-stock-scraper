from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, pg_json_dumps


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        endpoint = params.get('endpoint', [''])[0]

        if endpoint == 'categories':
            result = query_db("""
                SELECT category,
                       COUNT(*) as total,
                       SUM(CASE WHEN status='Available' THEN 1 ELSE 0 END) as available,
                       SUM(CASE WHEN status='SoldOut' THEN 1 ELSE 0 END) as sold_out,
                       SUM(CASE WHEN status='ComingSoon' THEN 1 ELSE 0 END) as coming_soon
                FROM products
                GROUP BY category
                ORDER BY category
            """)
        elif endpoint == 'regions':
            result = query_db("""
                SELECT DISTINCT region, currency, COUNT(*) as product_count
                FROM products
                GROUP BY region, currency
                ORDER BY region
            """)
        elif endpoint == 'region-stock':
            result = query_db("""
                SELECT region,
                       COUNT(*) as total,
                       SUM(CASE WHEN status='Available' THEN 1 ELSE 0 END) as available,
                       SUM(CASE WHEN status='SoldOut' THEN 1 ELSE 0 END) as sold_out,
                       SUM(CASE WHEN status='ComingSoon' THEN 1 ELSE 0 END) as coming_soon,
                       SUM(CASE WHEN status='RegionNotAvailable' THEN 1 ELSE 0 END) as region_na
                FROM products
                GROUP BY region
                ORDER BY sold_out DESC
            """)
        elif endpoint == 'scans':
            limit = int(params.get('limit', ['200'])[0])
            result = query_db(
                'SELECT * FROM scans ORDER BY timestamp DESC LIMIT %s',
                (limit,),
            )
        else:
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error": "unknown endpoint"}')
            return

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(pg_json_dumps(result).encode())

    def log_message(self, format, *args):
        pass
