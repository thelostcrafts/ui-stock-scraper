from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, execute_db, pg_json_dumps, log_error


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)

            limit = int(params.get('limit', ['200'])[0])
            event_type = params.get('type', [None])[0]
            sku = params.get('sku', [None])[0]
            region = params.get('region', [None])[0]

            where = []
            args = []
            if event_type:
                types = [t.strip() for t in event_type.split(',') if t.strip()]
                if len(types) == 1:
                    where.append('event_type = %s')
                    args.append(types[0])
                elif types:
                    where.append('event_type IN (' + ','.join(['%s'] * len(types)) + ')')
                    args.extend(types)
            if sku:
                where.append('sku = %s')
                args.append(sku)
            if region:
                where.append('region = %s')
                args.append(region)

            clause = 'WHERE ' + ' AND '.join(where) if where else ''
            args.append(limit)

            rows = query_db(
                'SELECT * FROM events ' + clause + ' ORDER BY timestamp DESC LIMIT %s',
                tuple(args),
            )

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(pg_json_dumps(rows).encode())
        except Exception as e:
            log_error('api/events', str(e), traceback.format_exc())
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass  # Suppress request logging in serverless
