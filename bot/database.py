"""De SQLite-database: alles wat we bewaren staat hier.

Een datum is altijd een tekst in de vorm JJJJ-MM-DD, zodat sorteren en
vergelijken altijd klopt en de database leesbaar blijft.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bot.config import DB_PAD

SCHEMA = """
-- Dagkoersen, in de eigen valuta van het symbool (aandelen en ETF's in USD,
-- crypto in USDT). Omrekenen naar euro's gebeurt bij het handelen.
CREATE TABLE IF NOT EXISTS koersen (
    symbool TEXT NOT NULL,
    datum   TEXT NOT NULL,
    open    REAL NOT NULL,
    hoog    REAL,
    laag    REAL,
    slot    REAL NOT NULL,
    volume  REAL,
    valuta  TEXT NOT NULL,
    bron    TEXT NOT NULL,
    PRIMARY KEY (symbool, datum)
);

-- Dagelijkse wisselkoers: hoeveel dollar is een euro waard.
CREATE TABLE IF NOT EXISTS wisselkoersen (
    datum  TEXT PRIMARY KEY,
    eurusd REAL NOT NULL
);

-- Een portefeuille per strategie. 'variant' is 'standaard' of 'challenger'.
CREATE TABLE IF NOT EXISTS portefeuilles (
    naam            TEXT PRIMARY KEY,
    strategie       TEXT NOT NULL,
    variant         TEXT NOT NULL DEFAULT 'standaard',
    start_kapitaal  REAL NOT NULL,
    kas_eur         REAL NOT NULL,
    gestart_op      TEXT,
    actief          INTEGER NOT NULL DEFAULT 1
);

-- Open posities. Fractionele aantallen zijn toegestaan: zie README.
CREATE TABLE IF NOT EXISTS posities (
    portefeuille     TEXT NOT NULL,
    symbool          TEXT NOT NULL,
    aantal           REAL NOT NULL,
    kostprijs_eur    REAL NOT NULL,  -- totaal ingelegd, inclusief kosten
    gekocht_op       TEXT NOT NULL,
    PRIMARY KEY (portefeuille, symbool)
);

-- Het logboek van elke trade.
CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    portefeuille   TEXT NOT NULL,
    strategie      TEXT NOT NULL,
    symbool        TEXT NOT NULL,
    actie          TEXT NOT NULL,          -- KOOP of VERKOOP
    besluit_datum  TEXT NOT NULL,          -- de dag waarop de data bekend was
    datum          TEXT NOT NULL,          -- de dag waarop is uitgevoerd
    prijs_lokaal   REAL NOT NULL,          -- openingskoers inclusief slippage
    valuta         TEXT NOT NULL,
    eurusd         REAL NOT NULL,
    prijs_eur      REAL NOT NULL,
    aantal         REAL NOT NULL,
    bedrag_eur     REAL NOT NULL,          -- aantal x prijs_eur
    kosten_eur     REAL NOT NULL,
    reden          TEXT,
    zekerheid      REAL,                   -- 0..1, alleen de AI-analist vult dit
    resultaat_eur  REAL,                   -- alleen bij VERKOOP
    resultaat_pct  REAL                    -- alleen bij VERKOOP
);

-- De waarde van elke portefeuille per dag, voor de grafiek en de statistiek.
CREATE TABLE IF NOT EXISTS waarde (
    portefeuille TEXT NOT NULL,
    datum        TEXT NOT NULL,
    kas_eur      REAL NOT NULL,
    posities_eur REAL NOT NULL,
    totaal_eur   REAL NOT NULL,
    PRIMARY KEY (portefeuille, datum)
);

-- Nieuwskoppen die de AI-analist te zien kreeg (wordt gevuld in stap 3).
CREATE TABLE IF NOT EXISTS nieuws (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbool     TEXT NOT NULL,
    datum       TEXT NOT NULL,
    gepubliceerd TEXT,
    titel       TEXT NOT NULL,
    samenvatting TEXT,
    bron        TEXT,
    url         TEXT,
    UNIQUE (symbool, datum, titel)
);

-- Kosten van de Claude-API, om het maandbudget te bewaken (stap 3).
CREATE TABLE IF NOT EXISTS ai_kosten (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    datum         TEXT NOT NULL,
    maand         TEXT NOT NULL,          -- JJJJ-MM
    rol           TEXT NOT NULL,          -- technisch, nieuws, bull, bear, risk
    model         TEXT NOT NULL,
    tokens_in     INTEGER NOT NULL,
    tokens_uit    INTEGER NOT NULL,
    kosten_usd    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_koersen_datum ON koersen (datum);
CREATE INDEX IF NOT EXISTS idx_trades_pf ON trades (portefeuille, datum);
CREATE INDEX IF NOT EXISTS idx_waarde_datum ON waarde (datum);
"""


def verbind(pad: Path | str | None = None) -> sqlite3.Connection:
    """Open de database en maak de tabellen aan als ze nog niet bestaan."""
    pad = Path(pad) if pad else DB_PAD
    if str(pad) != ":memory:":
        pad.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(pad))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    con.commit()
    return con


def bewaar_koersen(con: sqlite3.Connection, rijen: list[dict]) -> int:
    """Zet koersen in de database. Bestaande dagen worden overschreven."""
    if not rijen:
        return 0
    con.executemany(
        """INSERT INTO koersen (symbool, datum, open, hoog, laag, slot, volume, valuta, bron)
           VALUES (:symbool, :datum, :open, :hoog, :laag, :slot, :volume, :valuta, :bron)
           ON CONFLICT(symbool, datum) DO UPDATE SET
             open=excluded.open, hoog=excluded.hoog, laag=excluded.laag,
             slot=excluded.slot, volume=excluded.volume, bron=excluded.bron""",
        rijen,
    )
    con.commit()
    return len(rijen)


def bewaar_wisselkoersen(con: sqlite3.Connection, rijen: list[dict]) -> int:
    if not rijen:
        return 0
    con.executemany(
        """INSERT INTO wisselkoersen (datum, eurusd) VALUES (:datum, :eurusd)
           ON CONFLICT(datum) DO UPDATE SET eurusd=excluded.eurusd""",
        rijen,
    )
    con.commit()
    return len(rijen)


def koersen_van(con: sqlite3.Connection, symbool: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM koersen WHERE symbool = ? ORDER BY datum", (symbool,)
    ).fetchall()


def laatste_koersdatum(con: sqlite3.Connection, symbool: str) -> str | None:
    rij = con.execute(
        "SELECT MAX(datum) AS d FROM koersen WHERE symbool = ?", (symbool,)
    ).fetchone()
    return rij["d"] if rij and rij["d"] else None


def wisselkoersen_dict(con: sqlite3.Connection) -> dict[str, float]:
    return {
        r["datum"]: r["eurusd"]
        for r in con.execute("SELECT datum, eurusd FROM wisselkoersen ORDER BY datum")
    }


def leeg_handelsdata(con: sqlite3.Connection) -> None:
    """Wis portefeuilles, posities, trades en waardes. Koersen blijven staan.

    Gebruikt door de backtest, zodat die altijd schoon begint.
    """
    for tabel in ("trades", "posities", "waarde", "portefeuilles"):
        con.execute(f"DELETE FROM {tabel}")
    con.commit()
