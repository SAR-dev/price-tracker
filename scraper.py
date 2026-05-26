"""
scraper.py — generic price scraper for any MakeShop / data-id product page.

Usage:
  python scraper.py                          # scrape all active trackers
  python scraper.py --tracker-id 3           # scrape one tracker by id

Config is read from the 'trackers' table in prices.db.
"""

import sqlite3, re, logging, sys, argparse, os
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "prices.db"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / "scraper.log"),
    ],
)
log = logging.getLogger(__name__)


def init_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trackers (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            label      TEXT    NOT NULL,
            url        TEXT    NOT NULL,
            selector   TEXT    NOT NULL,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS prices (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tracker_id INTEGER NOT NULL REFERENCES trackers(id),
            fetched_at TEXT    NOT NULL,
            price_jpy  INTEGER NOT NULL,
            raw_text   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_prices_tracker ON prices (tracker_id, fetched_at);
    """)
    conn.commit()
    return conn


def fetch_price(url: str, selector: str) -> tuple[int, str]:
    """Fetch page and extract price text from the given CSS/attr selector."""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    el = None

    # 1. Try as attribute selector:  data-id="foo:1"
    attr_match = re.match(r'(\w[\w-]*)=["\']?([^"\'>\s]+)["\']?', selector.strip())
    if attr_match:
        attr, val = attr_match.group(1), attr_match.group(2)
        el = soup.find(attrs={attr: val})
        if el is None:
            # partial / regex match
            el = soup.find(attrs={attr: re.compile(re.escape(val))})

    # 2. Try as CSS class or id shorthand  (.foo  #bar)
    if el is None and selector.startswith(('.', '#')):
        el = soup.select_one(selector)

    # 3. Try raw CSS selector via select_one
    if el is None:
        try:
            el = soup.select_one(selector)
        except Exception:
            pass

    if el is None:
        raise ValueError(f"Selector '{selector}' not found on {url}")

    raw_text = el.get_text(strip=True)
    log.debug("Raw text: %r", raw_text)

    digits = re.sub(r"[^\d]", "", raw_text)
    if not digits:
        raise ValueError(f"No digits in: {raw_text!r}")

    return int(digits), raw_text


def scrape_tracker(conn: sqlite3.Connection, tracker: sqlite3.Row) -> None:
    tid   = tracker["id"]
    label = tracker["label"]
    url   = tracker["url"]
    sel   = tracker["selector"]

    log.info("[%d] %s  url=%s  selector=%s", tid, label, url, sel)
    try:
        price, raw = fetch_price(url, sel)
    except Exception as exc:
        log.error("[%d] FAILED: %s", tid, exc)
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO prices (tracker_id, fetched_at, price_jpy, raw_text) VALUES (?,?,?,?)",
        (tid, now, price, raw),
    )
    conn.commit()
    log.info("[%d] Saved ¥%s  raw=%r", tid, f"{price:,}", raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracker-id", type=int, default=None)
    args = parser.parse_args()

    conn = init_db(DB_PATH)

    if args.tracker_id:
        row = conn.execute("SELECT * FROM trackers WHERE id=?", (args.tracker_id,)).fetchone()
        if row is None:
            log.error("Tracker id=%d not found", args.tracker_id)
            sys.exit(1)
        rows = [row]
    else:
        rows = conn.execute("SELECT * FROM trackers WHERE active=1").fetchall()

    if not rows:
        log.info("No active trackers found.")
    for row in rows:
        scrape_tracker(conn, row)

    conn.close()


if __name__ == "__main__":
    main()
