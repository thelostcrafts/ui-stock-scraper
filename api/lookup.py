from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import traceback
import sys
import os
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, execute_db, pg_json_dumps, log_error


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        endpoint = params.get('endpoint', [''])[0]

        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            if endpoint == 'log-access':
                # Extract real client IP from Vercel/proxy headers
                client_ip = (self.headers.get('x-forwarded-for', '') or '').split(',')[0].strip()
                if not client_ip:
                    client_ip = self.headers.get('x-real-ip', '') or self.client_address[0] if self.client_address else 'unknown'
                # Vercel geo headers (free, automatic)
                country = self.headers.get('x-vercel-ip-country', '') or ''
                city = self.headers.get('x-vercel-ip-city', '') or ''
                ip_region = self.headers.get('x-vercel-ip-country-region', '') or ''
                execute_db(
                    "INSERT INTO access_log (timestamp, remote_ip, method, path, status_code, user_agent, country, city, region) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (body.get('timestamp', ''), client_ip, body.get('method', 'GET'),
                     body.get('path', '/'), body.get('status_code', 200), body.get('user_agent', ''),
                     country, city, ip_region),
                )
                self._json_response({"ok": True})
            elif endpoint == 'log-error':
                execute_db(
                    "INSERT INTO error_log (timestamp, source, level, message, traceback, context) VALUES (%s, %s, %s, %s, %s, %s)",
                    (body.get('timestamp', ''), body.get('source', 'unknown'), body.get('level', 'error'),
                     body.get('message', ''), body.get('traceback', None), body.get('context', None)),
                )
                self._json_response({"ok": True})
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error": "unknown endpoint"}')
        except Exception as e:
            log_error('api/lookup:POST:' + endpoint, str(e), traceback.format_exc())
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        endpoint = params.get('endpoint', [''])[0]

        try:
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
            elif endpoint == 'catalog-metrics':
                metrics = query_db("""
                    SELECT timestamp, total_skus, unique_skus, unique_products, regions
                    FROM catalog_metrics
                    ORDER BY timestamp ASC
                """)
                current = query_db("""
                    SELECT COUNT(*) as total_skus,
                           COUNT(DISTINCT sku) as unique_skus,
                           COUNT(DISTINCT name) as unique_products,
                           COUNT(DISTINCT region) as regions
                    FROM products
                """)[0]
                result = {"current": current, "history": metrics}
            elif endpoint == 'access-logs':
                limit = int(params.get('limit', ['200'])[0])
                result = query_db(
                    'SELECT * FROM access_log ORDER BY timestamp DESC LIMIT %s',
                    (limit,),
                )
            elif endpoint == 'error-logs':
                limit = int(params.get('limit', ['200'])[0])
                source = params.get('source', [''])[0]
                level = params.get('level', [''])[0]
                where = []
                args = []
                if source:
                    where.append('source LIKE %s')
                    args.append('%' + source + '%')
                if level:
                    where.append('level = %s')
                    args.append(level)
                clause = 'WHERE ' + ' AND '.join(where) if where else ''
                args.append(limit)
                result = query_db(
                    'SELECT * FROM error_log {} ORDER BY timestamp DESC LIMIT %s'.format(clause),
                    tuple(args),
                )
            elif endpoint == 'trigger-monitor':
                secret = (params.get('key', [''])[0] or self.headers.get('x-cron-secret', '')).strip()
                expected = os.environ.get('CRON_SECRET', '').strip()
                if not expected:
                    self._json_response({"error": "CRON_SECRET not configured"})
                    return
                if secret != expected:
                    self.send_response(403)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"error": "forbidden"}')
                    return
                gh_token = os.environ.get('GH_DISPATCH_TOKEN', '')
                if not gh_token:
                    self._json_response({"error": "no GH_DISPATCH_TOKEN configured"})
                    return
                req = Request(
                    'https://api.github.com/repos/thelostcrafts/ui-stock-scraper/actions/workflows/monitor.yml/dispatches',
                    data=json.dumps({"ref": "main"}).encode(),
                    headers={
                        'Authorization': 'token ' + gh_token,
                        'Accept': 'application/vnd.github.v3+json',
                        'User-Agent': 'ui-stock-scraper-cron',
                    },
                    method='POST',
                )
                resp = urlopen(req, timeout=10)
                result = {"ok": True, "status": resp.status}
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error": "unknown endpoint"}')
                return

            self._json_response(result)
        except Exception as e:
            log_error('api/lookup:GET:' + endpoint, str(e), traceback.format_exc())
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _json_response(self, data):
        body = pg_json_dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass
