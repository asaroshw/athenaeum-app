"""
Point-in-time snapshot store for Athenaeum's OWN analysis outputs.

SCOPE — read this before assuming this is more than it is:

WHAT THIS IS: every time run_predictive_pipeline produces a verdict for a
ticker, save_snapshot() persists a timestamped record of that verdict, the
target price, the model used, and the key assumptions (growth, Ke, WACC) to
a local SQLite file. Query it later with get_snapshot_history() /
get_all_latest_snapshots(). This is real, working, queryable infrastructure —
not a mocked stand-in.

WHAT THIS IS NOT:
1. NOT a point-in-time database of the underlying FMP/yfinance FUNDAMENTAL
   DATA (revenue, EPS, balance sheet). It does not solve look-ahead bias for
   historical research — it has no visibility into what FMP/yfinance would
   have returned for a ticker on a past date, only what THIS APP concluded
   at the moment it ran. Genuine point-in-time fundamentals require either a
   specialized vendor (e.g. a restated-vs-as-reported financials feed) or
   years of accumulated daily snapshots of the raw provider data itself —
   neither of which this module attempts.
2. NOT a backtesting engine. Knowing "this app said STRONG BUY on ticker X
   on date Y at price Z" is the necessary raw material for backtesting, not
   a backtest result. Turning this into "STRONG BUY calls outperformed the
   Nifty by N% on average" requires a separate evaluation harness that joins
   this table against actual subsequent price history — not yet built.
3. NOT guaranteed durable on every deployment. SQLite writes to a local file
   (ATHENAEUM_SNAPSHOT_DB_PATH, default ./athenaeum_snapshots.db). On a
   platform with an ephemeral filesystem or multiple non-shared-volume
   replicas, this file — and the history in it — will not survive a
   restart/redeploy or be consistent across instances. Point it at a
   persistent, shared volume in any deployment where the history matters.

This module is the honest foundation described above: it starts the clock
on real point-in-time verdict logging going forward. It is deliberately
scoped down from "point-in-time research database" — that larger goal needs
dedicated data-engineering work this module does not attempt to fake.
"""
from __future__ import annotations
import json
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

logger = logging.getLogger("athenaeum")

DB_PATH = os.environ.get("ATHENAEUM_SNAPSHOT_DB_PATH", "athenaeum_snapshots.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    as_of_utc TEXT NOT NULL,
    verdict TEXT,
    composite_score REAL,
    target_price REAL,
    current_price REAL,
    model_used TEXT,
    growth_used REAL,
    ke_pct REAL,
    wacc_pct REAL,
    fundamental_score REAL,
    intrinsic_score REAL,
    technical_score REAL,
    data_source TEXT,
    audit_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_time ON snapshots(ticker, as_of_utc);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def save_snapshot(ticker, metrics, predictive_data):
    """Persist one timestamped record of this app's own verdict for `ticker`.

    Best-effort: a storage failure is logged and swallowed, never raised —
    logging an analysis should never be able to break the analysis itself.
    Returns True on success, False otherwise.
    """
    try:
        audit = (predictive_data or {}).get("audit", {}) or {}
        row = (
            ticker,
            datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            predictive_data.get("verdict") if predictive_data else None,
            predictive_data.get("composite_score") if predictive_data else None,
            predictive_data.get("target_price") if predictive_data else None,
            (metrics or {}).get("price"),
            predictive_data.get("model_used") if predictive_data else None,
            predictive_data.get("growth_used") if predictive_data else None,
            audit.get("ke"),
            predictive_data.get("wacc_pct") if predictive_data else None,
            predictive_data.get("fundamental_score") if predictive_data else None,
            predictive_data.get("intrinsic_score") if predictive_data else None,
            predictive_data.get("technical_score") if predictive_data else None,
            (metrics or {}).get("data_source"),
            json.dumps(audit, default=str),
        )
        with closing(_connect()) as conn:
            conn.execute(
                """INSERT INTO snapshots
                   (ticker, as_of_utc, verdict, composite_score, target_price, current_price,
                    model_used, growth_used, ke_pct, wacc_pct, fundamental_score,
                    intrinsic_score, technical_score, data_source, audit_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            conn.commit()
        return True
    except Exception as e:
        logger.warning("Snapshot store: failed to save snapshot for %s (%s).", ticker, e)
        return False


def get_snapshot_history(ticker, limit=50):
    """Past snapshots for one ticker, most recent first. Returns a list of dicts."""
    try:
        with closing(_connect()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM snapshots WHERE ticker = ? ORDER BY as_of_utc DESC LIMIT ?",
                (ticker, limit),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("Snapshot store: failed to read history for %s (%s).", ticker, e)
        return []


def get_all_latest_snapshots(limit=500):
    """Most recent snapshot per ticker across the whole store — the basis for
    an eventual "how have STRONG BUY calls actually performed" report, once
    joined against subsequent price history by a future evaluation harness.

    Ties on `id` (the autoincrement primary key) rather than on `as_of_utc`
    directly — id is guaranteed unique and monotonically increasing, so
    "highest id for this ticker" is unambiguously "most recently inserted
    row," even if two saves for the same ticker land in the same
    microsecond (unlikely, but not something the correctness of this query
    should depend on)."""
    try:
        with closing(_connect()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """SELECT s.* FROM snapshots s
                   INNER JOIN (
                       SELECT ticker, MAX(id) AS max_id FROM snapshots GROUP BY ticker
                   ) latest ON s.ticker = latest.ticker AND s.id = latest.max_id
                   ORDER BY s.as_of_utc DESC LIMIT ?""",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("Snapshot store: failed to read latest snapshots (%s).", e)
        return []
