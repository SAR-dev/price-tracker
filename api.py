"""
api.py — Flask API for the dynamic price tracker dashboard.

Start (dev):  python api.py
Start (prod): gunicorn -w 2 -b 0.0.0.0:8080 api:app

Endpoints:
  GET  /                           — dashboard UI
  GET  /api/trackers               — list all trackers
  POST /api/trackers               — create tracker {label, url, selector}
  PUT  /api/trackers/<id>          — update tracker
  DELETE /api/trackers/<id>        — delete tracker
  PATCH /api/trackers/<id>/toggle  — toggle active flag
  GET  /api/trackers/<id>/prices   — price rows (?hours=N&limit=N)
  GET  /api/trackers/<id>/summary  — current, 24h stats, change
  POST /api/trackers/<id>/fetch    — fetch now (runs scraper for this tracker)
  POST /api/fetch-all              — fetch all active trackers
"""

import os, sqlite3, subprocess, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, render_template, abort
from flask_cors import CORS

DB_PATH  = Path(os.environ.get("DB_PATH",  Path(__file__).parent / "prices.db"))
SCRAPER  = Path(os.environ.get("SCRAPER",  Path(__file__).parent / "scraper.py"))
SCRAPER2 = Path(os.environ.get("SCRAPER_BROWSER", Path(__file__).parent / "scraper_browser.py"))

app = Flask(__name__)
CORS(app)


# ── DB ─────────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
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


def t_dict(r):
    return {"id": r["id"], "label": r["label"], "url": r["url"],
            "selector": r["selector"], "active": bool(r["active"]),
            "created_at": r["created_at"]}

def p_dict(r):
    return {"id": r["id"], "tracker_id": r["tracker_id"],
            "fetched_at": r["fetched_at"], "price_jpy": r["price_jpy"],
            "raw_text": r["raw_text"]}


# ── UI ─────────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


# ── trackers CRUD ──────────────────────────────────────────────────────────────

@app.get("/api/trackers")
def list_trackers():
    conn = get_db()
    rows = conn.execute("SELECT * FROM trackers ORDER BY id ASC").fetchall()
    conn.close()
    return jsonify([t_dict(r) for r in rows])


@app.post("/api/trackers")
def create_tracker():
    data = request.get_json(force=True)
    label    = (data.get("label") or "").strip()
    url      = (data.get("url")   or "").strip()
    selector = (data.get("selector") or "").strip()
    if not label or not url or not selector:
        return jsonify({"error": "label, url, selector are required"}), 400

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO trackers (label, url, selector, active, created_at) VALUES (?,?,?,1,?)",
        (label, url, selector, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM trackers WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(t_dict(row)), 201


@app.put("/api/trackers/<int:tid>")
def update_tracker(tid):
    data = request.get_json(force=True)
    conn = get_db()
    row  = conn.execute("SELECT * FROM trackers WHERE id=?", (tid,)).fetchone()
    if not row:
        conn.close(); abort(404)
    label    = data.get("label",    row["label"])
    url      = data.get("url",      row["url"])
    selector = data.get("selector", row["selector"])
    conn.execute("UPDATE trackers SET label=?, url=?, selector=? WHERE id=?",
                 (label, url, selector, tid))
    conn.commit()
    row = conn.execute("SELECT * FROM trackers WHERE id=?", (tid,)).fetchone()
    conn.close()
    return jsonify(t_dict(row))


@app.delete("/api/trackers/<int:tid>")
def delete_tracker(tid):
    conn = get_db()
    conn.execute("DELETE FROM prices WHERE tracker_id=?", (tid,))
    conn.execute("DELETE FROM trackers WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": tid})


@app.patch("/api/trackers/<int:tid>/toggle")
def toggle_tracker(tid):
    conn = get_db()
    row = conn.execute("SELECT active FROM trackers WHERE id=?", (tid,)).fetchone()
    if not row: conn.close(); abort(404)
    new_val = 0 if row["active"] else 1
    conn.execute("UPDATE trackers SET active=? WHERE id=?", (new_val, tid))
    conn.commit()
    row = conn.execute("SELECT * FROM trackers WHERE id=?", (tid,)).fetchone()
    conn.close()
    return jsonify(t_dict(row))


# ── prices ─────────────────────────────────────────────────────────────────────

@app.get("/api/trackers/<int:tid>/prices")
def get_prices(tid):
    limit = min(int(request.args.get("limit", 500)), 5000)
    hours = request.args.get("hours", type=int)
    conn  = get_db()
    if hours:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
        rows = conn.execute(
            "SELECT * FROM prices WHERE tracker_id=? AND fetched_at>=? ORDER BY fetched_at ASC LIMIT ?",
            (tid, cutoff, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM prices WHERE tracker_id=? ORDER BY fetched_at ASC LIMIT ?",
            (tid, limit),
        ).fetchall()
    conn.close()
    return jsonify([p_dict(r) for r in rows])


@app.get("/api/trackers/<int:tid>/summary")
def get_summary(tid):
    conn = get_db()
    latest = conn.execute(
        "SELECT * FROM prices WHERE tracker_id=? ORDER BY fetched_at DESC LIMIT 1", (tid,)
    ).fetchone()
    if not latest:
        conn.close()
        return jsonify({"error": "no data yet"}), 404

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    stats  = conn.execute("""
        SELECT COUNT(*) cnt, MAX(price_jpy) hi, MIN(price_jpy) lo
        FROM prices WHERE tracker_id=? AND fetched_at>=?
    """, (tid, cutoff)).fetchone()

    prev = conn.execute(
        "SELECT price_jpy FROM prices WHERE tracker_id=? AND id<? ORDER BY fetched_at DESC LIMIT 1",
        (tid, latest["id"]),
    ).fetchone()

    total = conn.execute("SELECT COUNT(*) FROM prices WHERE tracker_id=?", (tid,)).fetchone()[0]
    first = conn.execute(
        "SELECT fetched_at FROM prices WHERE tracker_id=? ORDER BY fetched_at ASC LIMIT 1", (tid,)
    ).fetchone()
    conn.close()

    cur  = latest["price_jpy"]
    prv  = prev["price_jpy"] if prev else None
    chg  = (cur - prv) if prv else None
    pct  = round((chg / prv) * 100, 4) if prv else None

    return jsonify({
        "current":        {"price_jpy": cur, "fetched_at": latest["fetched_at"], "raw_text": latest["raw_text"]},
        "change_vs_prev": {"amount_jpy": chg, "percent": pct},
        "last_24h":       {"high_jpy": stats["hi"], "low_jpy": stats["lo"], "count": stats["cnt"]},
        "all_time":       {"total_records": total, "since": first["fetched_at"] if first else None},
    })


# ── fetch ──────────────────────────────────────────────────────────────────────

def _run_scraper(extra_args: list[str]) -> dict:
    scraper = SCRAPER if SCRAPER.exists() else SCRAPER2
    if not scraper.exists():
        return {"status": "error", "message": "scraper not found"}
    try:
        proc = subprocess.Popen(
            [sys.executable, str(scraper)] + extra_args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env={**os.environ, "DB_PATH": str(DB_PATH)},
        )
        try:
            out, _ = proc.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            proc.kill()
            return {"status": "timeout"}
        output = out.decode(errors="replace")
        if proc.returncode != 0:
            return {"status": "error", "output": output[-3000:]}
        return {"status": "ok", "output": output[-3000:]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.post("/api/trackers/<int:tid>/fetch")
def fetch_one(tid):
    return jsonify(_run_scraper(["--tracker-id", str(tid)]))


@app.post("/api/fetch-all")
def fetch_all():
    return jsonify(_run_scraper([]))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
