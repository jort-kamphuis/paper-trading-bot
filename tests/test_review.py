"""Tests voor onderdelen die bij de review zijn bijgekomen.

* de backtest-poort van de challenger (eerst toetsen op de geschiedenis);
* kwartaalcijfers: alleen wat op de beslisdag bekend was;
* de blok-bootstrap voor vergelijkingen met de meetlat;
* de controle op verdachte koerssprongen;
* een oude database (bedragen in _eur) wordt netjes omgezet naar _usd.
"""

from __future__ import annotations

import copy
import sqlite3

import numpy as np
import pytest

from bot import config as conf
from bot import database, statistiek, volledigheid
from bot.challenger import Voorstel
from bot.data import kwartaalcijfers
from bot.markt import Markt
from tests.conftest import INSTELLINGEN, maak_df, werkdagen

# ------------------------------------------------------- de backtest-poort


def _vul_geschiedenis(con: sqlite3.Connection, dagen: list[str]) -> None:
    rng = np.random.default_rng(3)
    rijen = []
    for symbool, groei in (("SPY", 1.0004), ("AAA", 1.0006), ("BBB", 0.9999)):
        k = 100.0
        for d in dagen:
            k *= groei * (1 + rng.normal(0, 0.01))
            rijen.append({"symbool": symbool, "datum": d, "open": k, "hoog": k, "laag": k,
                          "slot": k, "volume": 1000.0, "valuta": "USD", "bron": "test"})
    database.bewaar_koersen(con, rijen)
    database.bewaar_wisselkoersen(con, [{"datum": dagen[0], "eurusd": 1.10}])
    con.commit()


def _cfg_klein():
    cfg = copy.deepcopy(conf.laad())
    cfg["algemeen"] = dict(INSTELLINGEN)
    cfg["watchlist"] = {"aandelen": ["AAA", "BBB"], "etfs": ["SPY"], "crypto": []}
    return cfg


def test_backtestpoort_toetst_in_twee_perioden_en_laat_de_bron_met_rust(con):
    from bot.challenger_toets import toets

    _vul_geschiedenis(con, werkdagen(750, start="2023-01-02"))
    geslaagd, tekst = toets(con, _cfg_klein(), Voorstel("trend", "snelle_ma", 30, "x"))
    assert isinstance(geslaagd, bool)
    assert "eerdere periode" in tekst and "laatste jaar" in tekst
    assert ("Geslaagd" in tekst) == geslaagd
    # De toets draait op een kopie: in de echte database verandert niets.
    assert con.execute("SELECT COUNT(*) n FROM portefeuilles").fetchone()["n"] == 0
    assert con.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] == 0


def test_backtestpoort_zonder_genoeg_geschiedenis_slaagt_niet(con):
    from bot.challenger_toets import toets

    _vul_geschiedenis(con, werkdagen(400, start="2025-01-01"))
    geslaagd, tekst = toets(con, _cfg_klein(), Voorstel("trend", "snelle_ma", 30, "x"))
    assert not geslaagd and "te weinig geschiedenis" in tekst


# ---------------------------------------------------------- kwartaalcijfers


def test_kwartaalcijfers_alleen_wat_toen_bekend_was(con):
    con.executemany(
        "INSERT INTO kwartaalcijfers (symbool, opgehaald_op, datum) VALUES (?,?,?)",
        [("AAPL", "2026-11-10", "2026-11-20"), ("AAPL", "2026-11-15", "2026-11-25")],
    )
    assert kwartaalcijfers.bekend_op(con, "AAPL", "2026-11-12") == "2026-11-20"
    assert kwartaalcijfers.bekend_op(con, "AAPL", "2026-11-16") == "2026-11-25", "de nieuwste kennis telt"
    assert kwartaalcijfers.bekend_op(con, "AAPL", "2026-11-09") is None, "toen nog niet opgehaald"
    assert kwartaalcijfers.bekend_op(con, "MSFT", "2026-11-12") is None


# ------------------------------------------------------------ blok-bootstrap


def test_blok_bootstrap_herkent_een_duidelijk_verschil():
    rng = np.random.default_rng(1)
    verschil = list(0.002 + rng.normal(0, 0.002, 60))
    assert statistiek.blok_bootstrap_p_waarde(verschil) < 0.01


def test_blok_bootstrap_bij_ruis_geen_bewijs():
    rng = np.random.default_rng(2)
    ruis = list(rng.normal(0, 0.01, 60))
    assert statistiek.blok_bootstrap_p_waarde(ruis) > 0.05


def test_blok_bootstrap_voorzichtiger_bij_samenhangende_dagen():
    """Goede dagen in een rij: losse dagen trekken overschat de zekerheid."""
    reeks = ([0.01] * 10 + [-0.008] * 10) * 3
    los = statistiek.blok_bootstrap_p_waarde(reeks, blok=1)
    blok = statistiek.blok_bootstrap_p_waarde(reeks, blok=10)
    assert blok > los


def test_blok_bootstrap_te_kort():
    assert statistiek.blok_bootstrap_p_waarde([0.01] * 10) is None


# ------------------------------------------------------- verdachte sprongen


def test_halvering_zonder_splitsing_is_verdacht():
    dagen = werkdagen(3, start="2026-11-10")
    markt = Markt({"AAPL": maak_df([200.0, 200.0, 99.0], dagen), "SPY": maak_df([1.0] * 3, dagen)})
    cfg = {"watchlist": {"aandelen": ["AAPL"], "etfs": ["SPY"], "crypto": []},
           "rapport": {"kalender_symbool": "SPY"}}
    problemen = volledigheid.controleer(markt, cfg, [dagen[-1]])
    assert any("verdacht" in p and "AAPL" in p for p in problemen)


def test_halvering_met_splitsing_is_niet_verdacht():
    dagen = werkdagen(3, start="2026-11-10")
    markt = Markt({
        "AAPL": maak_df([200.0, 200.0, 100.0], dagen, splitsing=[0, 0, 2.0]),
        "SPY": maak_df([1.0] * 3, dagen),
    })
    cfg = {"watchlist": {"aandelen": ["AAPL"], "etfs": ["SPY"], "crypto": []},
           "rapport": {"kalender_symbool": "SPY"}}
    assert volledigheid.controleer(markt, cfg, [dagen[-1]]) == []


# ------------------------------------------------- oude database omzetten


def test_oude_database_in_euro_wordt_omgezet(tmp_path):
    pad = tmp_path / "oud.db"
    oud = sqlite3.connect(pad)
    oud.executescript(
        """
        CREATE TABLE portefeuilles (naam TEXT PRIMARY KEY, strategie TEXT NOT NULL,
            variant TEXT NOT NULL DEFAULT 'standaard', start_kapitaal REAL NOT NULL,
            kas_eur REAL NOT NULL, gestart_op TEXT, actief INTEGER NOT NULL DEFAULT 1);
        INSERT INTO portefeuilles (naam, strategie, start_kapitaal, kas_eur) VALUES ('m', 'momentum', 1000, 812.5);
        """
    )
    oud.commit()
    oud.close()

    con = database.verbind(pad)
    kolommen = {r["name"] for r in con.execute("PRAGMA table_info(portefeuilles)")}
    assert "kas_usd" in kolommen and "kas_eur" not in kolommen
    assert con.execute("SELECT kas_usd FROM portefeuilles").fetchone()["kas_usd"] == pytest.approx(812.5)
    con.close()
    # Nog een keer openen gaat ook goed (de omzetting gebeurt maar een keer).
    database.verbind(pad).close()
