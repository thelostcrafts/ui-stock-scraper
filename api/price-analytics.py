from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import query_db, execute_db, pg_json_dumps, get_db


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Price changes in last 7 days
        recent_changes = query_db("""
            SELECT e.sku, e.name, e.old_value, e.new_value, e.timestamp,
                   e.details::json->>'delta_cents' as delta_cents
            FROM events e
            WHERE e.event_type = 'price_change'
              AND e.timestamp > CURRENT_TIMESTAMP - INTERVAL '7 days'
            ORDER BY e.timestamp DESC
        """)

        # Biggest price drops (all time)
        biggest_drops = query_db("""
            SELECT e.sku, e.name, e.old_value, e.new_value,
                   e.details::json->>'delta_cents' as delta_cents,
                   e.timestamp
            FROM events e
            WHERE e.event_type = 'price_change'
              AND (e.details::json->>'delta_cents')::int < 0
            ORDER BY (e.details::json->>'delta_cents')::int ASC
            LIMIT 20
        """)

        # Average price by category
        avg_by_category = query_db("""
            SELECT category,
                   ROUND(AVG(price_cents)) as avg_price,
                   MIN(price_cents) as min_price,
                   MAX(price_cents) as max_price,
                   COUNT(*) as count
            FROM products
            GROUP BY category
            ORDER BY avg_price DESC
        """)

        # Status transitions in last 7 days
        status_changes = query_db("""
            SELECT e.sku, e.name, e.old_value, e.new_value, e.timestamp
            FROM events e
            WHERE e.event_type = 'status_change'
              AND e.timestamp > CURRENT_TIMESTAMP - INTERVAL '7 days'
            ORDER BY e.timestamp DESC
        """)

        # Products on sale (regular_price_cents != NULL and > price_cents)
        on_sale = query_db("""
            SELECT sku, name, price_cents, regular_price_cents, category, currency,
                   (regular_price_cents - price_cents) as savings_cents
            FROM products
            WHERE regular_price_cents IS NOT NULL
              AND regular_price_cents > price_cents
            ORDER BY savings_cents DESC
            LIMIT 20
        """)

        result = {
            "recent_price_changes": recent_changes,
            "biggest_drops": biggest_drops,
            "avg_by_category": avg_by_category,
            "recent_status_changes": status_changes,
            "on_sale": on_sale,
        }

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(pg_json_dumps(result).encode())

    def log_message(self, format, *args):
        pass
