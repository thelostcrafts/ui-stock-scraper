# UI Store Inventory Monitor

## Deployment

This project runs as a launchd service on the **Mac Mini at 192.168.1.10** (SSH key access, user `abrunetto`).

**Any code changes to `monitor.py` or `dashboard.py` must be pushed to the Mac Mini after editing:**

```bash
# Push code (excludes data/ to avoid overwriting live DB)
rsync -avz --exclude='__pycache__' --exclude='data/' /Users/abrunetto/Projects/ui-stock-scraper/ 192.168.1.10:~/Projects/ui-stock-scraper/

# Restart services after code changes
ssh 192.168.1.10 "launchctl unload ~/Library/LaunchAgents/com.abrunetto.ui-store-dashboard.plist && launchctl load ~/Library/LaunchAgents/com.abrunetto.ui-store-dashboard.plist"
# Monitor doesn't need restart — it picks up changes on next 30-min run
```

**Services:**
- `com.abrunetto.ui-store-monitor` — runs every 30 min, scrapes store.ui.com
- `com.abrunetto.ui-store-dashboard` — persistent HTTP server on port 8080 (KeepAlive)

**Dashboard:** `http://192.168.1.10:8080`

**Logs:** `~/Projects/ui-stock-scraper/data/logs/` on the Mac Mini

## Project Structure

- `monitor.py` — scraper, runs via cron/launchd, writes to SQLite
- `dashboard.py` — self-contained HTTP dashboard, reads from SQLite
- `data/inventory.db` — SQLite database (WAL mode)
- `data/logs/` — monitor and dashboard logs
- `run-monitor.sh` — wrapper script for launchd (handles logging)

## Key Details

- Python 3.9.6 (system Python on both machines) — use `typing.Optional` not `X | None`
- Single dependency: `httpx`
- Dashboard binds to `0.0.0.0:8080` for LAN access
- Multi-region support via `UI_REGIONS` env var (default: US only)
- 14 known store regions: us, ca, eu, uk, jp, br, in, me, za, tw, sg, cn, mx, ph
