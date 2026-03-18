# UI Store Inventory Monitor

Automated inventory tracking system for [store.ui.com](https://store.ui.com) (Ubiquiti's online store). Monitors 735+ product SKUs across 8 categories, tracking price changes, stock status transitions, and product availability across 14 regional storefronts.

## How It Works

The monitor exploits Ubiquiti's Next.js `_next/data` JSON API to fetch structured product data without HTML parsing. Each scan:

1. Fetches the current `buildId` from the homepage `__NEXT_DATA__`
2. Requests `/_next/data/{buildId}/{region}/category/{category}.json` for each category
3. Content-hashes each response to skip extraction when nothing changed
4. Diffs against the previous snapshot to detect price changes, stock transitions, new/removed products
5. Persists everything to SQLite with full history

```
store.ui.com
  └─ /_next/data/{buildId}/{region}/category/{category}.json
       └─ pageProps.subCategories[].products[].variants[]
            └─ sku, displayPrice, status, thumbnail, ...
```

## Features

- **735+ SKUs tracked** across 8 product categories (gateways, switching, WiFi, cameras, door access, integrations, hosting, accessories)
- **Price change detection** with historical tracking and delta analysis
- **Stock status monitoring** — Available, SoldOut, ComingSoon transitions logged as events
- **Multi-region support** — 14 regional storefronts (US, EU, UK, CA, JP, MX, BR, IN, SG, ME, ZA, TW, CN, PH) with independent pricing and stock
- **Content hashing** — SHA256 of pageProps for cheap change detection; unchanged categories skip extraction
- **Adaptive backoff** — exponential retry on 429/5xx/network errors, persistent delay multiplier across runs
- **Product thumbnails** — CDN image URLs extracted and stored
- **Self-contained dashboard** — single-file Python HTTP server, no external dependencies
- **Tariff monitoring ready** — Ubiquiti's GraphQL API has dormant tariff surcharge fields that will activate if tariffs are applied

## Dashboard

The dashboard is a self-contained single-page app served by `dashboard.py`. No npm, no build step, no external CDN — everything is inline.

### Tabs

| Tab | Description |
|-----|-------------|
| **Products** | Full product table with search, sort, and filter by status/category/region |
| **Sold Out** | Filtered view of out-of-stock items with search |
| **Events** | Change log — price changes, stock transitions, new/removed products |
| **Analytics** | Price analytics: avg by category, on-sale items, biggest drops, recent changes |
| **Watchlist** | Tag specific SKUs for focused tracking with notes |
| **Categories** | Grid cards showing stock breakdown per category |

### Product Detail Modal

Click any product to see:
- Product thumbnail image
- Current price, status, store link
- Price history chart (SVG, color-coded by stock status)
- Availability timeline bar chart
- Stock duration windows — how long items stay in/out of stock
- Summary stats: availability rate, avg in-stock duration, transition count
- Change log for that specific SKU

### Other Features

- **Hot Items** — horizontal card row showing products that sell out fastest (appears after status transitions are recorded)
- **Auto-refresh** — polls all data every 60 seconds
- **Health check** — `/api/health` returns `healthy`/`stale`/`unhealthy` based on last scan age
- **Region filter** — dropdown to view products from specific regional storefronts

## Requirements

- Python 3.9+
- `httpx` (`pip3 install httpx`)

No other dependencies. The dashboard uses inline HTML/CSS/JS with no external resources.

## Quick Start

```bash
# Install dependency
pip3 install httpx

# Run the monitor (creates data/ directory and SQLite DB)
python3 monitor.py

# Start the dashboard
python3 dashboard.py
# Open http://localhost:8080
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UI_REGIONS` | `us/en` | Comma-separated regions to monitor |

### Multi-Region Scanning

```bash
# Scan US + EU + UK
UI_REGIONS=us/en,eu/en,uk/en python3 monitor.py

# Scan all 14 regions (takes ~2 minutes)
UI_REGIONS=us/en,eu/en,uk/en,ca/en,jp/ja,mx/es,br/pt,in/en,sg/en,me/en,za/en,tw/en,cn/en,ph/en python3 monitor.py
```

### Regional Storefronts

Stock and pricing vary significantly by region:

| Region | Subdomain | Currency | Notes |
|--------|-----------|----------|-------|
| us/en | store.ui.com | USD | Full catalog, primary store |
| eu/en | eu.store.ui.com | EUR | Full catalog, includes -EU/-UK variant SKUs |
| uk/en | uk.store.ui.com | GBP | Full catalog |
| ca/en | ca.store.ui.com | CAD | Full catalog |
| jp/ja | jp.store.ui.com | JPY | Reduced catalog, some items SoldOut |
| mx/es | mx.store.ui.com | MXN | Most constrained — majority ComingSoon/SoldOut |
| br/pt | br.store.ui.com | BRL | Good availability |
| in/en | in.store.ui.com | INR | Very limited catalog (5 products in some categories) |
| sg/en | sg.store.ui.com | SGD | Reduced catalog |
| me/en | me.store.ui.com | — | Middle East |
| za/en | za.store.ui.com | — | South Africa |
| tw/en | tw.store.ui.com | — | Taiwan |
| cn/en | cn.store.ui.com | — | China |
| ph/en | ph.store.ui.com | — | Philippines |

All regions share a single `buildId` (one Next.js deployment), but product catalogs, pricing, and stock are independent.

## Deployment (launchd on macOS)

The project is designed to run unattended on a Mac Mini as a launchd service.

### Install Services

```bash
# Copy the plist files to LaunchAgents
cp com.thelostcrafts.ui-store-monitor.plist ~/Library/LaunchAgents/
cp com.thelostcrafts.ui-store-dashboard.plist ~/Library/LaunchAgents/

# Load services (starts immediately + on boot)
launchctl load ~/Library/LaunchAgents/com.thelostcrafts.ui-store-monitor.plist
launchctl load ~/Library/LaunchAgents/com.thelostcrafts.ui-store-dashboard.plist
```

### Service Details

| Service | Schedule | Behavior |
|---------|----------|----------|
| `com.thelostcrafts.ui-store-monitor` | Every 15 minutes | Scrapes store, updates DB, auto-starts on boot |
| `com.thelostcrafts.ui-store-dashboard` | Continuous | HTTP server on port 8080, auto-restarts on crash |

### Manage Services

```bash
# Check status
launchctl list | grep ui-store

# Stop
launchctl unload ~/Library/LaunchAgents/com.thelostcrafts.ui-store-monitor.plist
launchctl unload ~/Library/LaunchAgents/com.thelostcrafts.ui-store-dashboard.plist

# Start
launchctl load ~/Library/LaunchAgents/com.thelostcrafts.ui-store-monitor.plist
launchctl load ~/Library/LaunchAgents/com.thelostcrafts.ui-store-dashboard.plist

# View logs
tail -50 ~/Projects/ui-stock-scraper/data/logs/monitor.log
tail -50 ~/Projects/ui-stock-scraper/data/logs/dashboard-error.log
```

## Project Structure

```
ui-stock-scraper/
├── monitor.py              # Scraper — runs on schedule, writes to SQLite
├── dashboard.py            # Dashboard — HTTP server, reads from SQLite
├── run-monitor.sh          # Wrapper script for launchd (logging/rotation)
├── ui-scraper.py           # Original recon script (discovery/documentation)
├── CLAUDE.md               # Development notes
├── data/
│   ├── inventory.db        # SQLite database (WAL mode)
│   ├── latest_snapshot.json # Current product state
│   ├── content_hashes.json # Per-region:category content hashes
│   ├── build_id.txt        # Cached Next.js buildId
│   ├── backoff_state.json  # Adaptive backoff state
│   ├── changes/            # Timestamped change log JSON files
│   └── logs/               # Monitor and dashboard logs
└── ui_store_recon_report.json # Initial recon findings
```

## Database Schema

### products
Primary key: `(sku, region)`

| Column | Type | Description |
|--------|------|-------------|
| sku | TEXT | Product variant SKU (e.g., `UDM-Pro`, `USW-Flex-Mini-3`) |
| region | TEXT | Store region (e.g., `us/en`, `eu/en`) |
| slug | TEXT | URL slug |
| name | TEXT | Product name |
| category | TEXT | Category (e.g., `all-cloud-gateways`) |
| subcategory | TEXT | Subcategory ID |
| price_cents | INTEGER | Current price in smallest currency unit |
| currency | TEXT | Currency code (USD, EUR, GBP, etc.) |
| regular_price_cents | INTEGER | Regular price if on sale, else NULL |
| status | TEXT | `Available`, `SoldOut`, or `ComingSoon` |
| variant_id | TEXT | Ubiquiti's internal variant UUID |
| thumbnail | TEXT | CDN URL for product image |
| first_seen | TEXT | ISO timestamp of first detection |
| last_updated | TEXT | ISO timestamp of last update |

### price_history
Time-series table — one row per SKU per scan.

| Column | Type | Description |
|--------|------|-------------|
| timestamp | TEXT | Scan timestamp |
| sku | TEXT | Product SKU |
| region | TEXT | Store region |
| price_cents | INTEGER | Price at scan time |
| status | TEXT | Status at scan time |

### events
Change log — only populated when something changes.

| Column | Type | Description |
|--------|------|-------------|
| timestamp | TEXT | Event timestamp |
| sku | TEXT | Product SKU |
| region | TEXT | Store region |
| event_type | TEXT | `price_change`, `status_change`, `new_product`, `removed_product` |
| old_value | TEXT | Previous value |
| new_value | TEXT | New value |
| details | TEXT | JSON with additional data (e.g., `delta_cents`) |

### scans
Metadata for each monitor run.

### watchlist
User-tagged SKUs for focused tracking.

## API Endpoints

The dashboard exposes these JSON endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/products` | GET | All products (filterable: `?q=`, `?status=`, `?category=`, `?region=`, `?sort=`) |
| `/api/events` | GET | Change events (`?type=`, `?sku=`, `?limit=`) |
| `/api/scans` | GET | Scan history |
| `/api/stats` | GET | Summary statistics |
| `/api/categories` | GET | Per-category stock breakdown |
| `/api/regions` | GET | Active regions with product counts |
| `/api/product-history` | GET | Detail for one SKU (`?sku=`) — prices, events, product info |
| `/api/availability-windows` | GET | Stock duration windows for one SKU (`?sku=`) |
| `/api/sold-out` | GET | All sold-out products |
| `/api/hot-items` | GET | Products with shortest in-stock durations |
| `/api/price-analytics` | GET | Price trends, drops, on-sale items, category averages |
| `/api/health` | GET | System health — last scan age, status |
| `/api/watchlist` | GET | Watchlist items |
| `/api/watchlist` | POST | Add to watchlist (`{"sku": "...", "notes": "..."}`) |
| `/api/watchlist/remove` | POST | Remove from watchlist (`{"sku": "..."}`) |

## Technical Details

### Data Source

The monitor uses Ubiquiti's Next.js `_next/data` API, which returns the same data that powers the storefront pages but as raw JSON. This is more reliable than HTML parsing and returns structured product data including variants, pricing, and stock status.

The `buildId` rotates on each Ubiquiti deployment. The monitor detects stale buildIds via 404 responses and automatically re-fetches.

### Change Detection

Each category response is content-hashed (SHA256 of the `pageProps` JSON). If the hash matches the previous run, extraction is skipped entirely and the previous data is carried forward. This minimizes processing on unchanged categories.

### Adaptive Backoff

The monitor tracks consecutive errors in `backoff_state.json`. After 3+ errors, the delay between requests increases by 1.5x (capped at 10x). Successful runs gradually reduce the multiplier back to 1.0x. This prevents hammering the server if it's having issues.

### Tariff System

Ubiquiti has a fully built but currently dormant tariff surcharge system in their GraphQL API (`ecomm.svc.ui.com/graphql`). Products have `tariff` and `tariffTotal` fields in checkout, with pre-built UI including a "Why You See a Tariff Surcharge" modal. All tariff values are currently $0 across all regions.

## License

MIT
