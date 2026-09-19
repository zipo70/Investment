"""
trading/db.py — Fælles SQLite-lager: "overleverings"-mekanismen mellem de
tre lag (Signal -> Beslutning -> Eksekvering).

Hvorfor SQLite (og ikke en flad fil eller en rigtig kø som Redis/RabbitMQ)
— dette var et bevidst valg, ikke en tilfældig standard:
  - Idempotens: hvert signal har en unik external_reference (se
    decision.make_external_reference). Et genstartet job kan sikkert
    spørge "har jeg allerede behandlet denne?" via en UNIQUE-constraint,
    uden en separat broker-proces at holde kørende ved siden af.
  - Revisionsspor: så snart der er tale om ordrer mod en (om end kun
    SIM-) mæglerkonto, er det værdifuldt at kunne se hele historikken —
    hvornår et signal opstod, hvad beslutningslaget besluttede, og hvad
    eksekveringslaget rent faktisk fik igennem hos Saxo.
  - Minimalt driftsoverhead: kun én fil, ingen ekstra service.

VIGTIGT — hvor denne fil lever: se SAXO_INTEGRATION.md, "Hosting". I den
simple GitHub Actions-baserede opsætning forventes trading/state/trading.db
committet tilbage til git efter hver kørsel (det er sådan "persistent
tilstand" opnås uden en rigtig server). Læg ALDRIG OAuth-tokens i denne
database — de har deres eget lager (token_store.py), netop fordi denne
fil forventes committet til git og derfor ikke er et sikkert sted for
hemmeligheder.
"""

import datetime
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "state" / "trading.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_reference TEXT NOT NULL UNIQUE,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('KØB', 'SÆLG')),
    score REAL,
    last_price REAL,
    stop_price REAL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'rejected', 'ordered', 'failed')),
    reject_reason TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_reference TEXT NOT NULL UNIQUE
        REFERENCES signals(external_reference),
    saxo_order_id TEXT,
    uic INTEGER,
    asset_type TEXT,
    direction TEXT NOT NULL,
    notional REAL,
    shares REAL,
    stop_price REAL,
    status TEXT NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'working', 'filled', 'cancelled', 'rejected', 'error')),
    error_message TEXT,
    raw_response_json TEXT,
    placed_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT PRIMARY KEY,
    uic INTEGER,
    asset_type TEXT,
    shares REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_date TEXT NOT NULL,
    external_reference TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS instrument_cache (
    yahoo_ticker TEXT PRIMARY KEY,
    uic INTEGER NOT NULL,
    asset_type TEXT NOT NULL,
    currency TEXT,
    symbol TEXT,
    cached_at TEXT NOT NULL
);

-- Fuld log af ALT der sker i eksekveringslaget (bruges bl.a. til at
-- kunne genskabe "notifikation ved al ordreaktivitet"-kravet i et
-- efterfølgende review, ikke kun i selve notifikationen der blev sendt).
CREATE TABLE IF NOT EXISTS order_activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


@contextmanager
def connect(db_path=DEFAULT_DB_PATH):
    """Kontekstmanager: `with db.connect() as conn: ...`. Opretter skemaet
    hvis det ikke findes, og committer automatisk ved normal afslutning."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


# --- Signals -----------------------------------------------------------

def insert_signal(conn, *, external_reference, ticker, direction, score,
                   last_price, stop_price, created_at=None) -> bool:
    """Idempotent indsættelse. Returnerer False (ingen exception) hvis
    external_reference allerede findes — dvs. samme signal er set før.
    Det er den NORMALE vej et gentaget/genkørt job bliver harmløst på."""
    try:
        conn.execute(
            """INSERT INTO signals
                   (external_reference, ticker, direction, score, last_price,
                    stop_price, created_at, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (external_reference, ticker, direction, score, last_price,
             stop_price, created_at or _now(), _now()),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def get_pending_signals(conn):
    return conn.execute(
        "SELECT * FROM signals WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()


def mark_signal(conn, external_reference, status, reject_reason=None):
    conn.execute(
        """UPDATE signals SET status = ?, reject_reason = ?, updated_at = ?
           WHERE external_reference = ?""",
        (status, reject_reason, _now(), external_reference),
    )


# --- Positions (lokalt spejl af Saxo-kontoen — beslutningslaget læser
# sin Portfolio herfra, se trading/run_execution_cycle.py) --------------

def load_positions(conn) -> dict:
    rows = conn.execute("SELECT * FROM positions").fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def upsert_position(conn, *, ticker, uic, asset_type, shares, entry_price,
                     entry_date, external_reference):
    conn.execute(
        """INSERT INTO positions
               (ticker, uic, asset_type, shares, entry_price, entry_date,
                external_reference, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(ticker) DO UPDATE SET
               uic=excluded.uic, asset_type=excluded.asset_type,
               shares=excluded.shares, entry_price=excluded.entry_price,
               entry_date=excluded.entry_date,
               external_reference=excluded.external_reference,
               updated_at=excluded.updated_at""",
        (ticker, uic, asset_type, shares, entry_price, entry_date,
         external_reference, _now()),
    )


def remove_position(conn, ticker):
    conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))


# --- Orders --------------------------------------------------------------

def insert_order(conn, *, external_reference, saxo_order_id, uic, asset_type,
                  direction, notional=None, shares=None, stop_price=None,
                  status="submitted", raw_response_json=None):
    conn.execute(
        """INSERT INTO orders
               (external_reference, saxo_order_id, uic, asset_type, direction,
                notional, shares, stop_price, status, raw_response_json,
                placed_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(external_reference) DO UPDATE SET
               saxo_order_id=excluded.saxo_order_id, status=excluded.status,
               raw_response_json=excluded.raw_response_json,
               updated_at=excluded.updated_at""",
        (external_reference, saxo_order_id, uic, asset_type, direction,
         notional, shares, stop_price, status, raw_response_json,
         _now(), _now()),
    )


def update_order_status(conn, external_reference, status, error_message=None,
                         raw_response_json=None):
    conn.execute(
        """UPDATE orders SET status = ?, error_message = ?,
               raw_response_json = COALESCE(?, raw_response_json),
               updated_at = ?
           WHERE external_reference = ?""",
        (status, error_message, raw_response_json, _now(), external_reference),
    )


# --- Instrument-cache (Yahoo-ticker -> Saxo Uic/AssetType) ----------------

def get_cached_instrument(conn, yahoo_ticker):
    row = conn.execute(
        "SELECT * FROM instrument_cache WHERE yahoo_ticker = ?", (yahoo_ticker,)
    ).fetchone()
    return dict(row) if row else None


def cache_instrument(conn, yahoo_ticker, uic, asset_type, currency=None, symbol=None):
    conn.execute(
        """INSERT INTO instrument_cache
               (yahoo_ticker, uic, asset_type, currency, symbol, cached_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(yahoo_ticker) DO UPDATE SET
               uic=excluded.uic, asset_type=excluded.asset_type,
               currency=excluded.currency, symbol=excluded.symbol,
               cached_at=excluded.cached_at""",
        (yahoo_ticker, uic, asset_type, currency, symbol, _now()),
    )


# --- Aktivitetslog ---------------------------------------------------------

def log_activity(conn, kind: str, detail: str):
    conn.execute(
        "INSERT INTO order_activity_log (ts, kind, detail) VALUES (?, ?, ?)",
        (_now(), kind, detail),
    )
