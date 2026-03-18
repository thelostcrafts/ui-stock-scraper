#!/bin/bash
# Wrapper script for the UI Store monitor
# Called by launchd every 30 minutes

cd /Users/abrunetto/Projects/ui-stock-scraper

# Log file with rotation (keep last 50 runs)
LOG_DIR="data/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/monitor.log"

# Rotate: keep last 10000 lines
if [ -f "$LOG_FILE" ] && [ "$(wc -l < "$LOG_FILE")" -gt 10000 ]; then
    tail -5000 "$LOG_FILE" > "$LOG_FILE.tmp"
    mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

echo "=== Run: $(date) ===" >> "$LOG_FILE"
/usr/bin/python3 monitor.py >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"
