"""De SQLite-database: alles wat we bewaren staat hier.

Een datum is altijd een tekst in de vorm JJJJ-MM-DD, zodat sorteren en
vergelijken altijd klopt en de database leesbaar blijft.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bot.config import DB_PAD

SCHEMA = """
-- Dagkoersen in dollars (aandelen en ETF's in USD, crypto in USDT, wat we als
-- dollar behandelen). Yahoo past oude koersen achteraf aan na een splitsing;
-- dividend zit niet in de koers. Daarom staan ze er apart bij.
CREATE TABLE IF NOT EXISTS koersen (
    symbool   TEXT NOT NULL,
    datum     TEXT NOT NULL,
    open      REAL NOT NULL,
    hoog      REAL,
    laag      REAL,
    slot      REAL NOT NULL,
    volume    REAL,
    valuta    TEXT NOT NULL,
    bron      TEXT NOT NULL,
    dividend  REAL,           -- dividend per stuk met deze dag als ex-datum
    splitsing REAL,           -- factor van een splitsing op deze dag (10 = 1 wordt 10)
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
    kas_usd         REAL NOT NULL,
    gestart_op      TEXT,
    actief          INTEGER NOT NULL DEFAULT 1
);

-- Open posities. Fractionele aantallen zijn toegestaan: zie README.
CREATE TABLE IF NOT EXISTS posities (
    portefeuille     TEXT NOT NULL,
    symbool          TEXT NOT NULL,
    aantal           REAL NOT NULL,
    kostprijs_usd    REAL NOT NULL,  -- totaal ingelegd, inclusief kosten
    gekocht_op       TEXT NOT NULL,
    koopprijs        REAL,           -- uitgevoerde prijs per stuk
    dividend_usd     REAL,           -- ontvangen dividend sinds de aankoop
    koopreden        TEXT,
    bevestigd_op     TEXT,           -- AI: laatste herbevestiging (houdtijd)
    PRIMARY KEY (portefeuille, symbool)
);

-- Dividend en splitsingen zoals ze op posities zijn verwerkt.
CREATE TABLE IF NOT EXISTS uitkeringen (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    portefeuille TEXT NOT NULL,
    symbool      TEXT NOT NULL,
    datum        TEXT NOT NULL,
    soort        TEXT NOT NULL,      -- dividend of splitsing
    waarde       REAL NOT NULL,      -- per stuk, of de factor
    bedrag_usd   REAL NOT NULL
);

-- Datums van kwartaalcijfers, zoals ze op een dag bekend waren.
CREATE TABLE IF NOT EXISTS kwartaalcijfers (
    symbool      TEXT NOT NULL,
    opgehaald_op TEXT NOT NULL,      -- JJJJ-MM-DD
    datum        TEXT NOT NULL,      -- de verwachte datum van de cijfers
    PRIMARY KEY (symbool, opgehaald_op)
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
    prijs_usd      REAL NOT NULL,
    aantal         REAL NOT NULL,
    bedrag_usd     REAL NOT NULL,          -- aantal x prijs_usd
    kosten_usd     REAL NOT NULL,
    reden          TEXT,
    zekerheid      REAL,                   -- 0..1, alleen de AI-analist vult dit
    resultaat_usd  REAL,                   -- alleen bij VERKOOP
    resultaat_pct  REAL                    -- alleen bij VERKOOP
);

-- De waarde van elke portefeuille per dag, voor de grafiek en de statistiek.
CREATE TABLE IF NOT EXISTS waarde (
    portefeuille TEXT NOT NULL,
    datum        TEXT NOT NULL,
    kas_usd      REAL NOT NULL,
    posities_usd REAL NOT NULL,
    totaal_usd   REAL NOT NULL,
    PRIMARY KEY (portefeuille, datum)
);

-- Orders die nog moeten worden uitgevoerd. Een besluit van vandaag gaat pas
-- morgen af, en tussendoor stopt het dagelijkse script. Daarom staat de
-- wachtrij hier en niet in het geheugen.
CREATE TABLE IF NOT EXISTS wachtrij (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    portefeuille  TEXT NOT NULL,
    symbool       TEXT NOT NULL,
    actie         TEXT NOT NULL,
    reden         TEXT,
    zekerheid     REAL,
    besluit_datum TEXT NOT NULL,
    gewicht       REAL,         -- deel van de portefeuille; leeg = de standaard (10%)
    UNIQUE (portefeuille, symbool, actie, besluit_datum)
);

-- Welke dagen al verwerkt zijn, per portefeuille. Hiermee kan het dagelijkse
-- script twee keer op dezelfde dag draaien zonder dubbele trades.
CREATE TABLE IF NOT EXISTS verwerkte_dagen (
    portefeuille TEXT NOT NULL,
    datum        TEXT NOT NULL,
    verwerkt_op  TEXT NOT NULL,
    PRIMARY KEY (portefeuille, datum)
);

-- Losse instellingen die de bot zelf onthoudt, zoals de startdatum van de meting.
CREATE TABLE IF NOT EXISTS instellingen (
    sleutel   TEXT PRIMARY KEY,
    waarde    TEXT NOT NULL,
    gewijzigd TEXT NOT NULL
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
    opgehaald_op TEXT,
    UNIQUE (symbool, datum, titel)
);

-- Elke beslissing van de AI-analist, met wat hij zag en waarom hij koos.
-- Bestaat er voor een portefeuille, symbool en dag al een besluit, dan wordt
-- de API niet opnieuw aangeroepen. Twee keer draaien kost dus geen extra geld.
CREATE TABLE IF NOT EXISTS ai_besluiten (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    portefeuille  TEXT NOT NULL,
    symbool       TEXT NOT NULL,
    datum         TEXT NOT NULL,
    actie         TEXT NOT NULL,          -- KOOP, VERKOOP of NIETS
    onderbouwing  TEXT,
    zekerheid     REAL,
    stappen       TEXT,                   -- JSON: wat elke rol zei
    koppen        TEXT,                   -- JSON: de nieuwskoppen die hij zag
    koppen_gezien INTEGER,
    kosten_usd    REAL,
    overgeslagen  TEXT,                   -- waarom er geen volledig debat was
    nieuws_tot    TEXT NOT NULL,          -- alleen nieuws van voor dit moment
    gemaakt_op    TEXT NOT NULL,
    -- De voorspelling, voor elk symbool elke nacht, ook bij niets doen. Hierop
    -- wordt later gescoord: klopte de kans? (zie README, de hoofdvraag)
    kans          REAL,                   -- kans dat het antwoord op de vraag ja is
    verwacht_pct  REAL,                   -- verwacht extra rendement in procentpunten
    referentie    TEXT,                   -- SPY, BTC/USDT, of kas (voor SPY en BTC zelf)
    horizon_dagen INTEGER,                -- over hoeveel kalenderdagen
    vraag         TEXT,                   -- relatief (tegen SPY of BTC) of absoluut (tegen kas)
    -- Na de horizon ingevuld door bot/ai/kalibratie.py: wat er echt gebeurde.
    uitkomst_pct  REAL,                   -- werkelijk (extra) rendement over de horizon, voor kosten
    geraakt       INTEGER,                -- 1 als dat meer was dan de kosten, anders 0
    gescoord_op   TEXT,
    UNIQUE (portefeuille, symbool, datum)
);

-- Een voorgestelde verbetering die op nep-geld naast het origineel draait.
-- status: loopt, wacht_op_akkoord, aangenomen, afgewezen
CREATE TABLE IF NOT EXISTS challengers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategie       TEXT NOT NULL,          -- welke regelstrategie
    parameter       TEXT NOT NULL,          -- welke instelling
    oude_waarde     REAL NOT NULL,
    nieuwe_waarde   REAL NOT NULL,
    onderbouwing    TEXT,
    -- Een challenger draait als eerlijk paar: twee nieuwe portefeuilles die op
    -- dezelfde dag met hetzelfde geld beginnen. A met de huidige instellingen,
    -- B met de voorgestelde. Anders vergelijk je een verse portefeuille met een
    -- die al weken vol posities zit.
    portefeuille    TEXT NOT NULL UNIQUE,   -- B: de voorgestelde instellingen
    portefeuille_a  TEXT NOT NULL UNIQUE,   -- A: de huidige instellingen
    instellingen_a  TEXT NOT NULL,          -- JSON, vastgelegd bij de start
    instellingen_b  TEXT NOT NULL,          -- JSON, vastgelegd bij de start
    voorgesteld_op  TEXT NOT NULL,
    gestart_op      TEXT NOT NULL,
    status          TEXT NOT NULL,
    beoordeeld_op   TEXT,
    uitslag         TEXT                    -- de vergelijking, in gewone taal
);

-- Elke wijziging aan de instellingen, met wie er akkoord gaf en wanneer.
-- Zo is in de resultaten altijd te zien vanaf welke dag een strategie anders werkte.
CREATE TABLE IF NOT EXISTS wijzigingen (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    datum         TEXT NOT NULL,
    strategie     TEXT NOT NULL,
    parameter     TEXT NOT NULL,
    oude_waarde   REAL NOT NULL,
    nieuwe_waarde REAL NOT NULL,
    challenger_id INTEGER,
    akkoord       TEXT NOT NULL             -- bijvoorbeeld 'Telegram, 2026-12-14 08:12 UTC'
);

-- De weekrapporten, zoals ze verstuurd zijn.
CREATE TABLE IF NOT EXISTS rapporten (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    datum       TEXT NOT NULL UNIQUE,       -- de dag waarover het rapport gaat
    tekst       TEXT NOT NULL,
    conclusies  TEXT,                       -- JSON: de drie conclusies van Claude
    voorstel    TEXT,                       -- JSON: het voorstel, of null
    gemaakt_op  TEXT NOT NULL
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
    _vul_ontbrekende_kolommen_aan(con)
    con.commit()
    return con


# Kolommen die later zijn bijgekomen. CREATE TABLE IF NOT EXISTS raakt een
# bestaande tabel niet aan, dus een database van voor die wijziging mist ze.
LATERE_KOLOMMEN: dict[str, dict[str, str]] = {
    "nieuws": {"opgehaald_op": "TEXT"},
    "wachtrij": {"gewicht": "REAL"},
    "koersen": {"dividend": "REAL", "splitsing": "REAL"},
    "posities": {"koopprijs": "REAL", "dividend_usd": "REAL", "koopreden": "TEXT", "bevestigd_op": "TEXT"},
    "ai_besluiten": {
        "kans": "REAL", "verwacht_pct": "REAL", "referentie": "TEXT", "horizon_dagen": "INTEGER",
        "vraag": "TEXT", "uitkomst_pct": "REAL", "geraakt": "INTEGER", "gescoord_op": "TEXT",
    },
}

# Kolommen die een andere naam kregen. Sinds de bot in dollars rekent, heten
# de bedragen _usd in plaats van _eur.
HERNOEMD: dict[str, dict[str, str]] = {
    "portefeuilles": {"kas_eur": "kas_usd"},
    "posities": {"kostprijs_eur": "kostprijs_usd"},
    "trades": {
        "prijs_eur": "prijs_usd", "bedrag_eur": "bedrag_usd",
        "kosten_eur": "kosten_usd", "resultaat_eur": "resultaat_usd",
    },
    "waarde": {"kas_eur": "kas_usd", "posities_eur": "posities_usd", "totaal_eur": "totaal_usd"},
}


def _vul_ontbrekende_kolommen_aan(con: sqlite3.Connection) -> None:
    """Hernoem oude kolommen en voeg kolommen toe die in een oudere database ontbreken."""
    for tabel, namen in HERNOEMD.items():
        bestaand = {r["name"] for r in con.execute(f"PRAGMA table_info({tabel})").fetchall()}
        for oud, nieuw in namen.items():
            if oud in bestaand and nieuw not in bestaand:
                con.execute(f"ALTER TABLE {tabel} RENAME COLUMN {oud} TO {nieuw}")
    for tabel, kolommen in LATERE_KOLOMMEN.items():
        bestaand = {
            r["name"] for r in con.execute(f"PRAGMA table_info({tabel})").fetchall()
        }
        if not bestaand:
            continue  # de tabel bestaat nog niet; het schema maakt hem straks
        for naam, soort in kolommen.items():
            if naam not in bestaand:
                con.execute(f"ALTER TABLE {tabel} ADD COLUMN {naam} {soort}")


def bewaar_koersen(con: sqlite3.Connection, rijen: list[dict]) -> int:
    """Zet koersen in de database. Bestaande dagen worden overschreven."""
    if not rijen:
        return 0
    volledig = [{"dividend": None, "splitsing": None, **r} for r in rijen]
    con.executemany(
        """INSERT INTO koersen (symbool, datum, open, hoog, laag, slot, volume, valuta, bron,
                               dividend, splitsing)
           VALUES (:symbool, :datum, :open, :hoog, :laag, :slot, :volume, :valuta, :bron,
                   :dividend, :splitsing)
           ON CONFLICT(symbool, datum) DO UPDATE SET
             open=excluded.open, hoog=excluded.hoog, laag=excluded.laag,
             slot=excluded.slot, volume=excluded.volume, bron=excluded.bron,
             dividend=excluded.dividend, splitsing=excluded.splitsing""",
        volledig,
    )
    con.commit()
    return len(rijen)


def verwijder_onaffe_dagen(
    con: sqlite3.Connection,
    aandelen: list[str],
    crypto: list[str],
    onaf_aandelen: str,
    onaf_crypto: str,
) -> int:
    """Haal koersen weg van dagen die nog niet af waren toen ze werden opgehaald.

    Zo'n dag heeft als "slotkoers" de koers van dat moment. Die wordt bij een
    volgende ophaalronde niet vanzelf vervangen (we halen onaffe dagen niet meer
    op), dus moet hij hier weg.
    """
    weg = 0
    for symbolen, grens in ((aandelen, onaf_aandelen), (crypto, onaf_crypto)):
        if not symbolen:
            continue
        plekken = ",".join("?" * len(symbolen))
        cur = con.execute(
            f"DELETE FROM koersen WHERE datum >= ? AND symbool IN ({plekken})",
            (grens, *symbolen),
        )
        weg += cur.rowcount
    cur = con.execute("DELETE FROM wisselkoersen WHERE datum >= ?", (onaf_crypto,))
    weg += cur.rowcount
    con.commit()
    return weg


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
    """Wis alles wat bij een meting hoort: portefeuilles, trades, waardes,
    voortgang, AI-besluiten, proeven, wijzigingen en rapporten.

    Blijft staan: koersen, nieuws en de AI-kosten. Uitgegeven geld is
    uitgegeven, ook als je opnieuw begint; anders klopt de budgetteller van de
    maand niet meer met wat je werkelijk hebt betaald.
    """
    for tabel in (
        "trades", "posities", "waarde", "portefeuilles", "wachtrij", "verwerkte_dagen", "uitkeringen",
        "ai_besluiten", "challengers", "wijzigingen", "rapporten",
    ):
        con.execute(f"DELETE FROM {tabel}")
    con.commit()


def boek_correctie(con: sqlite3.Connection, datum: str, usd: float, reden: str) -> None:
    """Boek AI-kosten die buiten de bot om zijn gemaakt, zodat het budget klopt.

    Bijvoorbeeld een proef op een kopie van de database die toch de API
    aanriep. Zo'n boeking telt mee in wat er deze maand is uitgegeven, maar niet
    in de prognose van wat een normale maand kost.
    """
    con.execute(
        """INSERT INTO ai_kosten (datum, maand, rol, model, tokens_in, tokens_uit, kosten_usd)
           VALUES (?, ?, 'correctie', ?, 0, 0, ?)""",
        (datum, datum[:7], reden[:200], float(usd)),
    )
    con.commit()


# ---------------------------------------------------------------- instellingen


def zet_instelling(con: sqlite3.Connection, sleutel: str, waarde: str) -> None:
    """Onthoud een instelling, bijvoorbeeld de startdatum van de meting."""
    con.execute(
        """INSERT INTO instellingen (sleutel, waarde, gewijzigd)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(sleutel) DO UPDATE SET
             waarde=excluded.waarde, gewijzigd=excluded.gewijzigd""",
        (sleutel, waarde),
    )
    con.commit()


def lees_instelling(con: sqlite3.Connection, sleutel: str) -> str | None:
    rij = con.execute(
        "SELECT waarde FROM instellingen WHERE sleutel = ?", (sleutel,)
    ).fetchone()
    return rij["waarde"] if rij else None


# ------------------------------------------------------------------ voortgang


def dag_is_verwerkt(con: sqlite3.Connection, portefeuille: str, datum: str) -> bool:
    """Is deze dag voor deze portefeuille al gedaan?"""
    return (
        con.execute(
            "SELECT 1 FROM verwerkte_dagen WHERE portefeuille = ? AND datum = ?",
            (portefeuille, datum),
        ).fetchone()
        is not None
    )


def markeer_dag_verwerkt(con: sqlite3.Connection, portefeuille: str, datum: str) -> None:
    con.execute(
        """INSERT INTO verwerkte_dagen (portefeuille, datum, verwerkt_op)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(portefeuille, datum) DO NOTHING""",
        (portefeuille, datum),
    )


def laatste_verwerkte_dag(con: sqlite3.Connection, portefeuille: str) -> str | None:
    rij = con.execute(
        "SELECT MAX(datum) AS d FROM verwerkte_dagen WHERE portefeuille = ?",
        (portefeuille,),
    ).fetchone()
    return rij["d"] if rij and rij["d"] else None
