# Silver 1kg Bar — Price Tracker

Scrapes `data-id="makeshop-item-price:1"` from ASAHI Online Store hourly,
stores results in SQLite, and serves them via a small Flask API.

---

## Files

```
silver_tracker/
├── scraper.py        # fetch + store one price snapshot
├── api.py            # Flask JSON API  (optional, for the UI)
├── requirements.txt
├── prices.db         # created automatically on first run
└── scraper.log       # append-only run log
```

---

## Setup

```bash
cd silver_tracker
pip install -r requirements.txt
```

---

## Run the scraper once

```bash
python scraper.py
```

Output example:
```
2026-05-26T10:00:01  INFO      Starting price fetch  url=https://...
2026-05-26T10:00:02  INFO      Saved  price=¥447,700  raw='￥447,700（税込）'  db=prices.db
2026-05-26T10:00:02  INFO      Recent entries:
2026-05-26T10:00:02  INFO        2026-05-26T01:00:01+00:00  ¥447,700
```

---

## Schedule hourly with cron (Linux/macOS)

Open crontab:
```bash
crontab -e
```

Add this line (runs every hour Mon–Fri, adjust path):
```
0 * * * 1-5 /usr/bin/python3 /path/to/silver_tracker/scraper.py >> /path/to/silver_tracker/scraper.log 2>&1
```

To run every hour every day:
```
0 * * * * /usr/bin/python3 /path/to/silver_tracker/scraper.py
```

Check cron is working:
```bash
tail -f scraper.log
```

---

## Start the API server (optional)

```bash
python api.py
# Listening on http://0.0.0.0:8080
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/prices` | All rows. Params: `?limit=N`, `?hours=N` |
| GET | `/prices/latest` | Most recent record |
| GET | `/prices/summary` | Current price, 24h high/low, change % |

### Example responses

**GET /prices/latest**
```json
{
  "id": 42,
  "fetched_at": "2026-05-26T01:00:01+00:00",
  "price_jpy": 447700,
  "raw_text": "￥447,700（税込）"
}
```

**GET /prices/summary**
```json
{
  "current": {
    "price_jpy": 447700,
    "fetched_at": "2026-05-26T01:00:01+00:00",
    "raw_text": "￥447,700（税込）"
  },
  "change_vs_prev": {
    "amount_jpy": -2200,
    "percent": -0.489
  },
  "last_24h": {
    "high_jpy": 452100,
    "low_jpy":  445300,
    "count": 8
  },
  "all_time": {
    "total_records": 156,
    "since": "2026-04-01T01:00:00+00:00"
  }
}
```

---

## Query the DB directly

```bash
sqlite3 prices.db "SELECT fetched_at, price_jpy FROM prices ORDER BY fetched_at DESC LIMIT 10;"
```

---

## Notes

- The site only updates prices on **weekdays 10:00–18:00 JST**, so overnight/weekend rows will repeat the last known price.
- The scraper falls back through 3 CSS selector strategies in case the site's HTML changes.
- Prices outside ¥100,000–¥10,000,000 are rejected as invalid to catch broken scrapes.

---

## Docker

### Files added

```
silver_tracker/
├── Dockerfile            # single image: Python 3.12 + Playwright Chromium
├── docker-compose.yml    # two services: api + scheduler
└── .dockerignore
```

### Start everything (API + hourly scraper)

```bash
docker compose up -d
```

- API available at `http://localhost:8080`
- Scheduler fires the scraper immediately, then every hour
- SQLite DB stored in a named Docker volume (`silver-data`) — survives restarts and rebuilds

### Check logs

```bash
docker compose logs -f scheduler   # watch scraper output
docker compose logs -f api         # watch API
```

### Run scraper manually inside container

```bash
docker compose exec scheduler python scraper_browser.py
# or plain requests version:
docker compose exec scheduler python scraper.py
```

### Stop

```bash
docker compose down       # stop, keep volume (data safe)
docker compose down -v    # stop + delete DB volume (data lost)
```

### Rebuild after code changes

```bash
docker compose up -d --build
```

### Switch to plain requests scraper

Edit `docker-compose.yml` scheduler command — change `scraper_browser.py` → `scraper.py`.
Playwright is still installed in the image but won't be used.
