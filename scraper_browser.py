"""
scraper_browser.py — Playwright version of scraper.py (bot-detection bypass).

Install:
    pip install playwright && playwright install chromium

Usage:
  python scraper_browser.py
  python scraper_browser.py --tracker-id 3
"""

import sqlite3, re, logging, sys, argparse, os
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "prices.db"))

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


def selector_to_playwright(selector: str) -> str:
    """Convert data-attr= style selector to Playwright CSS."""
    m = re.match(r'(\w[\w-]*)=["\']?([^"\'>\s]+)["\']?', selector.strip())
    if m:
        return f'[{m.group(1)}="{m.group(2)}"]'
    return selector


def fetch_price_browser(url: str, selector: str) -> tuple[int, str]:
    pw_sel = selector_to_playwright(selector)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            page.wait_for_selector(pw_sel, timeout=10_000)
        except PWTimeout:
            log.warning("Selector '%s' timed out, trying partial match", pw_sel)
            # try attribute prefix
            m = re.match(r'\[(\w[\w-]*)="([^"]+)"\]', pw_sel)
            if m:
                pw_sel = f'[{m.group(1)}*="{m.group(2)[:20]}"]'
                page.wait_for_selector(pw_sel, timeout=5_000)

        el = page.query_selector(pw_sel)
        if el is None:
            raise ValueError(f"'{pw_sel}' not found on {url}")
        raw_text = (el.inner_text() or "").strip()
        browser.close()

    digits = re.sub(r"[^\d]", "", raw_text)
    if not digits:
        raise ValueError(f"No digits in: {raw_text!r}")
    return int(digits), raw_text


def scrape_tracker(conn, tracker) -> None:
    tid, label, url, sel = tracker["id"], tracker["label"], tracker["url"], tracker["selector"]
    log.info("[%d] %s", tid, label)
    try:
        price, raw = fetch_price_browser(url, sel)
    except Exception as exc:
        log.error("[%d] FAILED: %s", tid, exc)
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO prices (tracker_id, fetched_at, price_jpy, raw_text) VALUES (?,?,?,?)",
        (tid, now, price, raw),
    )
    conn.commit()
    log.info("[%d] Saved ¥%s", tid, f"{price:,}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracker-id", type=int, default=None)
    args = parser.parse_args()

    conn = init_db(DB_PATH)
    rows = (
        [conn.execute("SELECT * FROM trackers WHERE id=?", (args.tracker_id,)).fetchone()]
        if args.tracker_id
        else conn.execute("SELECT * FROM trackers WHERE active=1").fetchall()
    )
    if not rows or rows[0] is None:
        log.info("No trackers found.")
    else:
        for row in rows:
            scrape_tracker(conn, row)
    conn.close()


if __name__ == "__main__":
    main()
