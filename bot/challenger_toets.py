"""De backtest die een voorstel eerst moet doorstaan, voordat het als proef start.

Werkwijze ("kiezen op de eerdere jaren, controleren op het laatste jaar"):
* De geschiedenis wordt in tweeën gedeeld: een eerdere periode en het laatste jaar.
* In elke periode draaien A (huidige instellingen) en B (voorstel) naast elkaar,
  met precies dezelfde motor, kosten en regels als de echte meting.
* Het voorstel slaagt alleen als B het in BEIDE perioden beter doet dan A.

Een voorstel dat alleen in de eerdere periode beter is, past zich waarschijnlijk
aan toevallige patronen aan; het laatste jaar is de controle daarop.

Dit is nog steeds geen bewijs (zie de README over backtests). Het houdt alleen
de slechtste ideeën tegen voordat ze vier weken lang een plek innemen.
"""

from __future__ import annotations

import copy
import sqlite3
from datetime import date, timedelta

from bot import database, statistiek
from bot.tekst import getal
from bot.challenger import Voorstel, instellingen_paar
from bot.data.wisselkoers import Wisselkoers
from bot.markt import Markt
from bot.motor import Motor


def _kopieer_koersen(bron: sqlite3.Connection) -> sqlite3.Connection:
    doel = database.verbind(":memory:")
    rijen = [dict(r) for r in bron.execute("SELECT * FROM koersen")]
    database.bewaar_koersen(doel, rijen)
    database.bewaar_wisselkoersen(
        doel, [dict(r) for r in bron.execute("SELECT datum, eurusd FROM wisselkoersen")]
    )
    return doel


def _draai(con, cfg, markt, wk, soort, inst_a, inst_b, symbolen, van, tot) -> tuple[float, float]:
    database.leeg_handelsdata(con)
    motor = Motor(con, cfg, markt, wk)
    for naam, inst in (("toets_A", inst_a), ("toets_B", inst_b)):
        s = soort(copy.deepcopy(inst))
        s.naam = naam
        motor.voeg_toe(s, symbolen, gestart_op=van)
    motor.draai_periode(van, tot)
    uitkomst = []
    for naam in ("toets_A", "toets_B"):
        rij = con.execute(
            "SELECT totaal_usd FROM waarde WHERE portefeuille=? ORDER BY datum DESC LIMIT 1", (naam,)
        ).fetchone()
        start = float(cfg["algemeen"]["start_kapitaal_usd"])
        uitkomst.append(statistiek.rendement_pct(start, rij["totaal_usd"]) if rij else 0.0)
    return uitkomst[0], uitkomst[1]


def toets(bron: sqlite3.Connection, cfg: dict, voorstel: Voorstel) -> tuple[bool, str]:
    """Geeft (geslaagd, uitleg in gewone taal)."""
    from bot import config as conf
    from bot.opzet import SOORTEN

    soort = SOORTEN.get(voorstel.strategie)
    if soort is None:
        return False, f"geen backtest mogelijk voor {voorstel.strategie}"

    con = _kopieer_koersen(bron)
    markt = Markt.uit_database(con)
    wk = Wisselkoers.uit_database(con)
    if not markt.datums or len(wk) == 0:
        return False, "geen koersen om een backtest op te draaien"

    eind = markt.datums[-1]
    eerste = markt.datums[0]
    opwarm = int(cfg["backtest"].get("opwarm_dagen", 300))
    test_begin = (date.fromisoformat(eind) - timedelta(days=365)).isoformat()
    train_begin = (date.fromisoformat(eerste) + timedelta(days=opwarm)).isoformat()
    train_eind = (date.fromisoformat(test_begin) - timedelta(days=1)).isoformat()
    if (date.fromisoformat(train_eind) - date.fromisoformat(train_begin)).days < 180:
        return False, "te weinig geschiedenis voor een eerlijke backtest (minder dan een half jaar om op te kiezen)"

    inst_a, inst_b = instellingen_paar(cfg, voorstel)
    symbolen = [s for s in conf.alle_symbolen(cfg) if markt.heeft(s)]
    ra1, rb1 = _draai(con, cfg, markt, wk, soort, inst_a, inst_b, symbolen, train_begin, train_eind)
    ra2, rb2 = _draai(con, cfg, markt, wk, soort, inst_a, inst_b, symbolen, test_begin, eind)
    con.close()

    geslaagd = rb1 > ra1 and rb2 > ra2
    tekst = (
        f"eerdere periode ({train_begin} tot {train_eind}): huidig {getal(ra1, 1, teken=True)}%, voorstel {getal(rb1, 1, teken=True)}%; "
        f"laatste jaar ({test_begin} tot {eind}): huidig {getal(ra2, 1, teken=True)}%, voorstel {getal(rb2, 1, teken=True)}%. "
        + ("Geslaagd: in beide perioden beter." if geslaagd else "Niet geslaagd: niet in beide perioden beter.")
    )
    return geslaagd, tekst
