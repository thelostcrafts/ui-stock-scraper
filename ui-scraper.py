#!/usr/bin/env python3
"""
UI Store Reconnaissance Script
===============================
Probes store.ui.com to discover:
  1. Whether __NEXT_DATA__ JSON payloads exist (structured product data)
  2. Any underlying API endpoints the frontend calls
  3. Stock status indicators and how they're represented
  4. Tariff surcharge data and where it appears
  5. Rate limit headers and behavior
  6. Response sizes and ETags for smart diffing

Run on Mac Mini: python3 ui_store_recon.py
Requirements: pip3 install httpx beautifulsoup4
"""

import httpx
import json
import re
import time
import hashlib
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# ─── Configuration ───────────────────────────────────────────────────────────

BASE_URL = "https://store.ui.com"
REGION = "/us/en"

CATEGORY_PATHS = [
    "/category/all-cloud-gateways",
    "/category/all-switching",
    "/category/all-wifi",
    "/category/all-cameras-nvrs",
    "/category/all-door-access",
    "/category/all-integrations",
    "/category/all-advanced-hosting",
    "/category/accessories-cables-dacs",
]

# A few individual product pages to probe for tariff surcharge data
SAMPLE_PRODUCT_PATHS = [
    "/category/all-cloud-gateways/products/udm-se",
    "/category/all-wifi/products/u7-pro",
    "/category/all-cameras-nvrs/products/uvc-g6-180",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                  "Version/18.3 Safari/605.1.15",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# Output file for the report
REPORT_FILE = "ui_store_recon_report.json"

# ─── Helpers ─────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def safe_delay(seconds: float = 1.5):
    """Polite delay between requests."""
    time.sleep(seconds)

def extract_next_data(html: str) -> Optional[dict]:
    """Extract __NEXT_DATA__ JSON from a Next.js page."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if script and script.string:
        try:
            return json.loads(script.string)
        except json.JSONDecodeError:
            return None
    return None

def find_json_endpoints(html: str) -> list[str]:
    """Scan HTML/JS for potential API endpoint patterns."""
    patterns = [
        r'fetch\(["\']([^"\']+)["\']',
        r'axios\.[a-z]+\(["\']([^"\']+)["\']',
        r'["\'](/api/[^"\']+)["\']',
        r'["\'](https?://[^"\']*api[^"\']*)["\']',
        r'["\'](https?://[^"\']*graphql[^"\']*)["\']',
        r'["\'](/_next/data/[^"\']+)["\']',
    ]
    endpoints = set()
    for pattern in patterns:
        for match in re.findall(pattern, html):
            endpoints.add(match)
    return sorted(endpoints)

def extract_product_cards(html: str) -> list[dict]:
    """Parse product cards from a category page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    products = []
    
    # Look for product links with pricing and stock info
    # The store renders product cards as anchor tags with product data
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if "/products/" not in href:
            continue
        
        text = link.get_text(separator=" | ", strip=True)
        
        # Extract product slug from URL
        slug_match = re.search(r'/products/([^/?]+)', href)
        slug = slug_match.group(1) if slug_match else None
        
        # Look for price patterns
        price_match = re.search(r'\$[\d,]+\.?\d*', text)
        price = price_match.group(0) if price_match else None
        
        # Determine stock status from text
        stock_status = "unknown"
        text_lower = text.lower()
        if "sold out" in text_lower:
            stock_status = "sold_out"
        elif "add to cart" in text_lower:
            stock_status = "in_stock"
        elif "select" in text_lower:
            stock_status = "in_stock_variants"
        elif "available" in text_lower:
            avail_match = re.search(r'available\s+(\w+\s+\d{4})', text_lower)
            stock_status = f"preorder_{avail_match.group(1)}" if avail_match else "preorder"
        elif "coming soon" in text_lower:
            stock_status = "coming_soon"
        
        if slug and (price or stock_status != "unknown"):
            products.append({
                "slug": slug,
                "url": href,
                "price": price,
                "stock_status": stock_status,
                "raw_text_snippet": text[:200],
            })
    
    return products

def extract_tariff_info(html: str) -> dict:
    """Look for tariff surcharge data in product page HTML."""
    info = {
        "tariff_in_html": False,
        "tariff_amount": None,
        "tariff_patterns_found": [],
    }
    
    tariff_patterns = [
        r'tariff',
        r'surcharge',
        r'import\s*(?:duty|fee|tax)',
        r'additional\s*(?:fee|charge|cost)',
    ]
    
    html_lower = html.lower()
    for pattern in tariff_patterns:
        matches = re.findall(pattern, html_lower)
        if matches:
            info["tariff_in_html"] = True
            info["tariff_patterns_found"].extend(matches)
    
    # Look for tariff amount patterns
    tariff_amount_pattern = r'(?:tariff|surcharge)[^$]*\$(\d+\.?\d*)'
    amount_matches = re.findall(tariff_amount_pattern, html_lower)
    if amount_matches:
        info["tariff_amount"] = amount_matches
    
    return info

def check_next_data_api(client: httpx.Client, build_id: str, path: str) -> dict:
    """
    Next.js serves JSON data at /_next/data/{buildId}/{path}.json
    This is the goldmine — structured product data without HTML parsing.
    """
    # Convert path like /us/en/category/all-cloud-gateways 
    # to /_next/data/{buildId}/us/en/category/all-cloud-gateways.json
    clean_path = path.strip("/")
    url = f"{BASE_URL}/_next/data/{build_id}/{clean_path}.json"
    
    try:
        resp = client.get(url, headers=HEADERS)
        return {
            "url": url,
            "status": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "size_bytes": len(resp.content),
            "is_json": "application/json" in resp.headers.get("content-type", ""),
            "sample_keys": list(json.loads(resp.text).get("pageProps", {}).keys())[:10]
                          if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", "")
                          else [],
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


# ─── Main Recon ──────────────────────────────────────────────────────────────

def main():
    report = {
        "timestamp": datetime.now().isoformat(),
        "findings": {},
    }
    
    log("Starting UI Store reconnaissance...")
    log("="*60)
    
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        
        # ── Phase 1: Probe homepage for __NEXT_DATA__ and build ID ────────
        log("\n[Phase 1] Probing homepage for Next.js data...")
        resp = client.get(f"{BASE_URL}{REGION}", headers=HEADERS)
        
        report["findings"]["homepage"] = {
            "status": resp.status_code,
            "response_size": len(resp.content),
            "etag": resp.headers.get("etag"),
            "cache_control": resp.headers.get("cache-control"),
            "x_powered_by": resp.headers.get("x-powered-by"),
            "server": resp.headers.get("server"),
            "cf_ray": resp.headers.get("cf-ray"),  # Cloudflare indicator
            "rate_limit_headers": {
                k: v for k, v in resp.headers.items()
                if "rate" in k.lower() or "limit" in k.lower() or "retry" in k.lower()
            },
        }
        
        # Check for Cloudflare
        is_cloudflare = bool(resp.headers.get("cf-ray"))
        report["findings"]["cloudflare_detected"] = is_cloudflare
        log(f"  Cloudflare detected: {is_cloudflare}")
        log(f"  Status: {resp.status_code}")
        log(f"  Response size: {len(resp.content):,} bytes")
        log(f"  ETag: {resp.headers.get('etag', 'none')}")
        log(f"  Cache-Control: {resp.headers.get('cache-control', 'none')}")
        
        # Extract __NEXT_DATA__
        next_data = extract_next_data(resp.text)
        has_next_data = next_data is not None
        report["findings"]["next_data"] = {
            "present": has_next_data,
        }
        
        build_id = None
        if has_next_data:
            build_id = next_data.get("buildId")
            report["findings"]["next_data"]["build_id"] = build_id
            report["findings"]["next_data"]["top_level_keys"] = list(next_data.keys())
            
            # Dump props keys for analysis
            props = next_data.get("props", {})
            page_props = props.get("pageProps", {})
            report["findings"]["next_data"]["page_props_keys"] = list(page_props.keys())
            
            # Check if product data is embedded
            next_data_str = json.dumps(next_data)
            report["findings"]["next_data"]["total_size_bytes"] = len(next_data_str)
            report["findings"]["next_data"]["contains_price"] = "$" in next_data_str or "price" in next_data_str.lower()
            report["findings"]["next_data"]["contains_stock"] = any(
                term in next_data_str.lower() 
                for term in ["in_stock", "instock", "sold_out", "soldout", "inventory", "available"]
            )
            
            log(f"  __NEXT_DATA__ found! Build ID: {build_id}")
            log(f"  __NEXT_DATA__ size: {len(next_data_str):,} bytes")
            log(f"  Top keys: {list(next_data.keys())}")
            log(f"  PageProps keys: {list(page_props.keys())[:10]}")
        else:
            log("  __NEXT_DATA__ NOT found — may use client-side hydration or RSC")
            
            # Check for React Server Components (app router)
            has_rsc = "__next_f" in resp.text or "self.__next_f" in resp.text
            report["findings"]["next_data"]["possible_rsc"] = has_rsc
            if has_rsc:
                log("  React Server Components detected (Next.js app router)")
        
        # Scan for API endpoints in page source
        endpoints = find_json_endpoints(resp.text)
        report["findings"]["discovered_endpoints"] = endpoints
        log(f"  Discovered {len(endpoints)} potential API endpoints")
        for ep in endpoints[:10]:
            log(f"    → {ep}")
        
        safe_delay(2.0)
        
        # ── Phase 2: Probe category pages ─────────────────────────────────
        log("\n[Phase 2] Probing category pages...")
        report["findings"]["categories"] = {}
        
        for cat_path in CATEGORY_PATHS:
            full_path = f"{REGION}{cat_path}"
            url = f"{BASE_URL}{full_path}"
            cat_name = cat_path.split("/")[-1]
            
            log(f"\n  Fetching: {cat_name}")
            resp = client.get(url, headers=HEADERS)
            
            # Extract products from HTML
            products = extract_product_cards(resp.text)
            
            # Try __NEXT_DATA__ on this page too
            cat_next_data = extract_next_data(resp.text)
            cat_next_data_info = None
            if cat_next_data:
                cat_page_props = cat_next_data.get("props", {}).get("pageProps", {})
                cat_next_data_info = {
                    "present": True,
                    "page_props_keys": list(cat_page_props.keys())[:15],
                    "size_bytes": len(json.dumps(cat_next_data)),
                }
                # Look for product arrays in pageProps
                for key, val in cat_page_props.items():
                    if isinstance(val, list) and len(val) > 0:
                        cat_next_data_info[f"array__{key}__count"] = len(val)
                        if isinstance(val[0], dict):
                            cat_next_data_info[f"array__{key}__sample_keys"] = list(val[0].keys())[:10]
                    elif isinstance(val, dict):
                        cat_next_data_info[f"dict__{key}__keys"] = list(val.keys())[:10]
            
            report["findings"]["categories"][cat_name] = {
                "url": url,
                "status": resp.status_code,
                "response_size": len(resp.content),
                "etag": resp.headers.get("etag"),
                "product_count_from_html": len(products),
                "products": products,
                "next_data": cat_next_data_info,
            }
            
            log(f"    Status: {resp.status_code} | Size: {len(resp.content):,}b | Products parsed: {len(products)}")
            if cat_next_data_info:
                log(f"    __NEXT_DATA__: {cat_next_data_info.get('size_bytes', 0):,}b | Keys: {cat_next_data_info.get('page_props_keys', [])}")
            
            for p in products[:3]:
                log(f"      → {p['slug']:30s} | {p['price'] or 'no price':>12s} | {p['stock_status']}")
            if len(products) > 3:
                log(f"      ... and {len(products) - 3} more")
            
            safe_delay(2.0)
        
        # ── Phase 3: Probe _next/data API (if build ID found) ─────────────
        if build_id:
            log(f"\n[Phase 3] Probing Next.js data API (buildId: {build_id})...")
            report["findings"]["next_data_api"] = {}
            
            for cat_path in CATEGORY_PATHS[:3]:  # Test just a few
                full_path = f"{REGION}{cat_path}"
                result = check_next_data_api(client, build_id, full_path)
                cat_name = cat_path.split("/")[-1]
                report["findings"]["next_data_api"][cat_name] = result
                
                log(f"  {cat_name}: status={result.get('status', 'error')} | "
                    f"json={result.get('is_json', False)} | "
                    f"size={result.get('size_bytes', 0):,}b")
                if result.get("sample_keys"):
                    log(f"    PageProps keys: {result['sample_keys']}")
                
                safe_delay(1.5)
        
        # ── Phase 4: Probe individual product pages for tariff data ───────
        log("\n[Phase 4] Probing product pages for tariff surcharge data...")
        report["findings"]["product_pages"] = {}
        
        for prod_path in SAMPLE_PRODUCT_PATHS:
            full_path = f"{REGION}{prod_path}"
            url = f"{BASE_URL}{full_path}"
            prod_name = prod_path.split("/")[-1]
            
            log(f"\n  Fetching: {prod_name}")
            resp = client.get(url, headers=HEADERS)
            
            tariff_info = extract_tariff_info(resp.text)
            
            # Also check __NEXT_DATA__ on product page
            prod_next_data = extract_next_data(resp.text)
            prod_data_keys = []
            prod_data_sample = {}
            if prod_next_data:
                pp = prod_next_data.get("props", {}).get("pageProps", {})
                prod_data_keys = list(pp.keys())
                # Look for price/stock/tariff in the structured data
                pp_str = json.dumps(pp).lower()
                prod_data_sample = {
                    "has_price_field": "price" in pp_str,
                    "has_stock_field": any(t in pp_str for t in ["stock", "inventory", "available", "sold"]),
                    "has_tariff_field": any(t in pp_str for t in ["tariff", "surcharge", "duty"]),
                    "page_props_keys": prod_data_keys[:15],
                    "size_bytes": len(json.dumps(pp)),
                }
            
            report["findings"]["product_pages"][prod_name] = {
                "url": url,
                "status": resp.status_code,
                "response_size": len(resp.content),
                "tariff_info": tariff_info,
                "next_data_product": prod_data_sample,
            }
            
            log(f"    Status: {resp.status_code} | Size: {len(resp.content):,}b")
            log(f"    Tariff in HTML: {tariff_info['tariff_in_html']}")
            if tariff_info['tariff_patterns_found']:
                log(f"    Tariff patterns: {tariff_info['tariff_patterns_found']}")
            if prod_data_sample:
                log(f"    __NEXT_DATA__ keys: {prod_data_sample.get('page_props_keys', [])}")
                log(f"    Has price: {prod_data_sample.get('has_price_field')} | "
                    f"Has stock: {prod_data_sample.get('has_stock_field')} | "
                    f"Has tariff: {prod_data_sample.get('has_tariff_field')}")
            
            safe_delay(2.0)
        
        # ── Phase 5: Test ETag / conditional request support ──────────────
        log("\n[Phase 5] Testing conditional request support (ETag/If-None-Match)...")
        test_url = f"{BASE_URL}{REGION}/category/all-cloud-gateways"
        
        resp1 = client.get(test_url, headers=HEADERS)
        etag = resp1.headers.get("etag")
        last_modified = resp1.headers.get("last-modified")
        
        conditional_support = {"etag_present": bool(etag), "last_modified_present": bool(last_modified)}
        
        if etag:
            cond_headers = {**HEADERS, "If-None-Match": etag}
            safe_delay(1.5)
            resp2 = client.get(test_url, headers=cond_headers)
            conditional_support["304_on_etag"] = resp2.status_code == 304
            log(f"  ETag: {etag}")
            log(f"  Conditional request returned: {resp2.status_code} "
                f"({'304 Not Modified — GREAT!' if resp2.status_code == 304 else 'Full response (no 304)'})")
        
        if last_modified:
            cond_headers = {**HEADERS, "If-Modified-Since": last_modified}
            safe_delay(1.5)
            resp3 = client.get(test_url, headers=cond_headers)
            conditional_support["304_on_last_modified"] = resp3.status_code == 304
            log(f"  Last-Modified: {last_modified}")
            log(f"  If-Modified-Since returned: {resp3.status_code}")
        
        report["findings"]["conditional_requests"] = conditional_support
        
        # ── Phase 6: Check for common API patterns ────────────────────────
        log("\n[Phase 6] Probing common API endpoint patterns...")
        api_probes = [
            f"{BASE_URL}/api/products",
            f"{BASE_URL}/api/catalog",
            f"{BASE_URL}/api/inventory",
            f"{BASE_URL}/api/store/products",
            f"{BASE_URL}/graphql",
            f"{BASE_URL}/us/en/api/products",
            # Ecomm API patterns (seen in asset URLs: assets.ecomm.ui.com)
            "https://ecomm.ui.com/api/products",
            "https://api.ecomm.ui.com/products",
        ]
        
        report["findings"]["api_probes"] = {}
        for probe_url in api_probes:
            try:
                resp = client.get(probe_url, headers={
                    **HEADERS,
                    "Accept": "application/json",
                })
                result = {
                    "status": resp.status_code,
                    "content_type": resp.headers.get("content-type", ""),
                    "size": len(resp.content),
                    "is_json": "json" in resp.headers.get("content-type", ""),
                }
                if result["is_json"] and resp.status_code == 200:
                    try:
                        data = resp.json()
                        result["response_keys"] = list(data.keys())[:10] if isinstance(data, dict) else f"array[{len(data)}]"
                    except:
                        pass
                
                report["findings"]["api_probes"][probe_url] = result
                status_icon = "✓" if resp.status_code == 200 else "✗"
                log(f"  {status_icon} {probe_url} → {resp.status_code} ({resp.headers.get('content-type', 'n/a')[:40]})")
                
            except Exception as e:
                report["findings"]["api_probes"][probe_url] = {"error": str(e)}
                log(f"  ✗ {probe_url} → ERROR: {e}")
            
            safe_delay(1.0)
    
    # ── Summary ───────────────────────────────────────────────────────────────
    log("\n" + "="*60)
    log("RECON SUMMARY")
    log("="*60)
    
    total_products = sum(
        len(cat.get("products", []))
        for cat in report["findings"].get("categories", {}).values()
    )
    
    stock_counts = {"in_stock": 0, "sold_out": 0, "other": 0}
    for cat in report["findings"].get("categories", {}).values():
        for p in cat.get("products", []):
            if p["stock_status"] == "in_stock":
                stock_counts["in_stock"] += 1
            elif p["stock_status"] == "sold_out":
                stock_counts["sold_out"] += 1
            else:
                stock_counts["other"] += 1
    
    log(f"\n  Total products discovered: {total_products}")
    log(f"  In stock: {stock_counts['in_stock']}")
    log(f"  Sold out: {stock_counts['sold_out']}")
    log(f"  Other (preorder/variants/unknown): {stock_counts['other']}")
    log(f"  Cloudflare: {'YES' if report['findings'].get('cloudflare_detected') else 'NO'}")
    log(f"  __NEXT_DATA__: {'YES' if report['findings']['next_data']['present'] else 'NO'}")
    log(f"  ETag support: {'YES' if conditional_support.get('etag_present') else 'NO'}")
    log(f"  304 support: {'YES' if conditional_support.get('304_on_etag') else 'NO'}")
    
    tariff_found = any(
        pp.get("tariff_info", {}).get("tariff_in_html")
        for pp in report["findings"].get("product_pages", {}).values()
    )
    log(f"  Tariff data in HTML: {'YES' if tariff_found else 'NO (may be JS-rendered or checkout-only)'}")
    
    log(f"\n  Recommended scraping strategy:")
    if report["findings"]["next_data"].get("present"):
        log(f"  → PRIMARY: Parse __NEXT_DATA__ from category pages (structured JSON, ~8 requests for full catalog)")
        if build_id:
            log(f"  → BETTER: Use /_next/data/{build_id}/... API for pure JSON (no HTML parsing needed)")
    else:
        log(f"  → PRIMARY: HTML parsing of category pages (~8 requests for full catalog)")
    
    if conditional_support.get("304_on_etag"):
        log(f"  → USE ETags for conditional requests (304 saves bandwidth & is rate-limit friendly)")
    
    log(f"  → Total requests per full catalog scan: ~8-12")
    log(f"  → At 30-min intervals: ~384-576 requests/day (very light)")
    
    # Save full report
    report["summary"] = {
        "total_products": total_products,
        "stock_counts": stock_counts,
        "cloudflare": report["findings"].get("cloudflare_detected"),
        "has_next_data": report["findings"]["next_data"]["present"],
        "has_etag": conditional_support.get("etag_present"),
        "has_304": conditional_support.get("304_on_etag"),
        "tariff_in_html": tariff_found,
        "requests_made": len(CATEGORY_PATHS) + len(SAMPLE_PRODUCT_PATHS) + 8,  # approx
    }
    
    with open(REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2, default=str)
    
    log(f"\n  Full report saved to: {REPORT_FILE}")
    log(f"  Total requests made during recon: ~{report['summary']['requests_made']}")
    log("\nDone! Review the JSON report for full details.\n")


if __name__ == "__main__":
    try:
        main()
    except httpx.ProxyError:
        print("\n❌ Proxy blocked access to store.ui.com")
        print("   This script must run on a machine with direct internet access (e.g., your Mac Mini).")
        print("   Run: python3 ui_store_recon.py")
    except httpx.ConnectError as e:
        print(f"\n❌ Connection error: {e}")
        print("   Check your internet connection and try again.")
    except KeyboardInterrupt:
        print("\n\nRecon cancelled by user.")
