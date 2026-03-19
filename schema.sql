-- Postgres schema for Neon migration
-- Apply with: psql "$DATABASE_URL" -f schema.sql

-- Products: primary inventory table
CREATE TABLE IF NOT EXISTS products (
    sku             TEXT NOT NULL,
    region          TEXT NOT NULL DEFAULT 'us/en',
    slug            TEXT NOT NULL,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    subcategory     TEXT NOT NULL,
    price_cents     INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    regular_price_cents INTEGER,
    status          TEXT NOT NULL,
    variant_id      TEXT,
    thumbnail       TEXT,
    first_seen      TEXT NOT NULL,
    last_updated    TEXT NOT NULL,
    PRIMARY KEY (sku, region)
);

-- Watchlist: user-tagged SKUs for focused tracking
CREATE TABLE IF NOT EXISTS watchlist (
    sku         TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    notes       TEXT
);

-- Events: append-only change log
CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    sku         TEXT NOT NULL,
    region      TEXT NOT NULL DEFAULT 'us/en',
    name        TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    details     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_sku ON events(sku);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

-- Scans: metadata for each monitor run
CREATE TABLE IF NOT EXISTS scans (
    id          SERIAL PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    build_id    TEXT NOT NULL,
    sku_count   INTEGER NOT NULL,
    available   INTEGER NOT NULL DEFAULT 0,
    sold_out    INTEGER NOT NULL DEFAULT 0,
    coming_soon INTEGER NOT NULL DEFAULT 0,
    categories_changed  INTEGER NOT NULL DEFAULT 0,
    categories_unchanged INTEGER NOT NULL DEFAULT 0,
    requests    INTEGER NOT NULL DEFAULT 0
);

-- Price history: one row per SKU per scan (time-series)
CREATE TABLE IF NOT EXISTS price_history (
    id          SERIAL PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    sku         TEXT NOT NULL,
    region      TEXT NOT NULL DEFAULT 'us/en',
    price_cents INTEGER NOT NULL,
    status      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ph_sku ON price_history(sku);
CREATE INDEX IF NOT EXISTS idx_ph_ts ON price_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_ph_region ON price_history(region);

-- Catalog metrics: track unique SKU/product counts over time
CREATE TABLE IF NOT EXISTS catalog_metrics (
    id              SERIAL PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    total_skus      INTEGER NOT NULL,
    unique_skus     INTEGER NOT NULL,
    unique_products INTEGER NOT NULL,
    regions         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cm_ts ON catalog_metrics(timestamp);

-- Access log: dashboard visitor tracking
CREATE TABLE IF NOT EXISTS access_log (
    id          SERIAL PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    remote_ip   TEXT NOT NULL,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    user_agent  TEXT
);
CREATE INDEX IF NOT EXISTS idx_al_ts ON access_log(timestamp);

-- Error log: captures backend/frontend errors for debugging
CREATE TABLE IF NOT EXISTS error_log (
    id          SERIAL PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    source      TEXT NOT NULL,
    level       TEXT NOT NULL DEFAULT 'error',
    message     TEXT NOT NULL,
    traceback   TEXT,
    context     TEXT
);
CREATE INDEX IF NOT EXISTS idx_el_ts ON error_log(timestamp);

-- Monitor state: replaces JSON state files (content_hashes, build_id, backoff)
CREATE TABLE IF NOT EXISTS monitor_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
