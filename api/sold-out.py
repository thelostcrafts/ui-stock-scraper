from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, execute_db, pg_json_dumps


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)

        where = ["status = 'SoldOut'"]
        args = []

        region = params.get('region', [None])[0]
        if region:
            where.append('region = %s')
            args.append(region)

        category = params.get('category', [None])[0]
        if category:
            where.append('category = %s')
            args.append(category)

        search = params.get('q', [None])[0]
        if search:
            where.append('(sku ILIKE %s OR name ILIKE %s)')
            args.extend(['%' + search + '%', '%' + search + '%'])

        clause = 'WHERE ' + ' AND '.join(where)

        rows = query_db(
            'SELECT sku, slug, name, category, subcategory, price_cents, currency, region FROM products ' + clause + ' ORDER BY category, name',
            tuple(args),
        )

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(pg_json_dumps(rows).encode())

    def log_message(self, format, *args):
        pass  # Suppress request logging in serverless
