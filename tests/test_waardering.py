"""Waardering op gesloten dagen, de meetkalender, en dividend en splitsingen in de motor.

Twee echte fouten zitten hier achter:
* Op een dag dat de aandelenbeurs dicht was (crypto handelt dan wel) viel de
  waarde van een aandelenpositie terug op de kostprijs.
* Yahoo past oude koersen achteraf aan na een splitsing. Een positie die het
  oude aantal stukken onthoudt, lijkt dan 90% te zijn gekelderd (NFLX splitste
  op 17 november 2025 10:1).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from bot import statistiek
from bot.markt import Markt
from bot.motor import Motor
from bot.portefeuille import nieuwe_portefeuille
from bot.strategieen.basis import Order, Strategie
from tests.conftest import INSTELLINGEN, maak_df, werkdagen


def _markt_met_gat() -> Markt:
    beursdagen = ["2025-01-02", "2025-01-03", "2025-01-06"]  # 4 en 5 jan = weekend
    alle = ["2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05", "2025-01-06"]
    return Markt({
        "AAA": maak_df([100.0, 120.0, 130.0], beursdagen),
        "BTC/USDT": maak_df([10.0] * 5, alle),
    })


def test_koers_blijft_staan_op_een_gesloten_dag():
    koersen = _markt_met_gat().slotkoersen_usd("2025-01-04")
    assert koersen["AAA"] == pytest.approx(120.0), "de slotkoers van vrijdag, in dollars"


def test_portefeuillewaarde_valt_niet_terug_op_de_kostprijs():
    markt = _markt_met_gat()
    p = nieuwe_portefeuille("t", "t", INSTELLINGEN)
    p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10, bedrag_usd=100.0)
    waarde = p.posities_waarde(markt.slotkoersen_usd("2025-01-04"))
    assert waarde == pytest.approx(p.posities["AAA"].aantal * 120.0)


def test_geen_koers_voor_de_eerste_dag_van_een_symbool():
    markt = Markt({"OUD": maak_df([10.0], ["2025-01-02"]), "NIEUW": maak_df([50.0], ["2025-01-03"])})
    koersen = markt.slotkoersen_usd("2025-01-02")
    assert "OUD" in koersen and "NIEUW" not in koersen


def test_meetkalender_houdt_alleen_beursdagen_over():
    d, w = statistiek.op_kalender(
        ["2025-01-02", "2025-01-03", "2025-01-04", "2025-01-06"],
        [1000.0, 1010.0, 1005.0, 1020.0],
        ["2025-01-02", "2025-01-03", "2025-01-06"],
    )
    assert w == [1000.0, 1010.0, 1020.0]


# ------------------------------------------------- dividend en splitsingen in de motor


class KoopOpDag(Strategie):
    naam = "koper"

    def __init__(self, dag: str):
        super().__init__({})
        self.koopdag = dag

    def besluit(self, datum, markt, symbolen, posities):
        if datum == self.koopdag and not posities:
            return [Order(symbool="AAA", actie="KOOP", reden="test")]
        return []


def _draai(markt, dagen, cfg_min, con, wk):
    motor = Motor(con, cfg_min, markt, wk)
    motor.voeg_toe(KoopOpDag(dagen[0]), ["AAA"], gestart_op=dagen[0])
    motor.draai_periode(dagen[0], dagen[-1])
    return motor


def test_splitsing_live_aantal_gaat_omhoog_waarde_blijft(con, cfg_min, vaste_wisselkoers):
    """Live: gekocht tegen de oude prijs, daarna splitsing en Yahoo past de geschiedenis aan."""
    dagen = werkdagen(6)
    # Na de splitsing op dag 4 staat in de data alles al gedeeld door 10, ook de
    # koersen van voor de splitsing. Maar de bot kocht op dag 2 tegen de oude prijs
    # van 1000; dat simuleren we door de koopprijs na het kopen te zetten.
    markt = Markt({"AAA": maak_df([100.0] * 6, dagen, splitsing=[0, 0, 0, 10.0, 0, 0])})
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KoopOpDag(dagen[0]), ["AAA"], gestart_op=dagen[0])
    motor.draai_periode(dagen[0], dagen[1])  # gekocht op dag 2
    pf = motor.deelnemers[0].portefeuille
    positie = pf.posities["AAA"]
    # Wat er live gebeurde: gekocht tegen 1000 (oud), dus een tiende van het aantal.
    positie.koopprijs = 1000.0 * 1.0005
    positie.aantal = positie.aantal / 10
    pf.bewaar(con)
    con.commit()
    aantal_voor = positie.aantal

    motor.draai_periode(dagen[2], dagen[-1])
    pf = motor.deelnemers[0].portefeuille
    assert pf.posities["AAA"].aantal == pytest.approx(aantal_voor * 10), "de splitsing is niet verwerkt"
    waarde = con.execute(
        "SELECT posities_usd FROM waarde WHERE portefeuille='koper' AND datum=?", (dagen[-1],)
    ).fetchone()["posities_usd"]
    assert waarde == pytest.approx(aantal_voor * 10 * 100.0), "nep-crash na de splitsing"


def test_splitsing_in_de_backtest_niet_dubbel(con, cfg_min, vaste_wisselkoers):
    """In de backtest is alles al aangepast; dan mag het aantal NIET nog eens omhoog."""
    dagen = werkdagen(6)
    markt = Markt({"AAA": maak_df([100.0] * 6, dagen, splitsing=[0, 0, 0, 10.0, 0, 0])})
    motor = _draai(markt, dagen, cfg_min, con, vaste_wisselkoers)
    assert con.execute("SELECT COUNT(*) n FROM uitkeringen WHERE soort='splitsing'").fetchone()["n"] == 0
    pf = motor.deelnemers[0].portefeuille
    assert pf.posities["AAA"].aantal == pytest.approx(100.0 / (100.0 * 1.0005))


def test_onduidelijke_splitsing_wordt_gemeld(con, cfg_min, vaste_wisselkoers):
    dagen = werkdagen(6)
    markt = Markt({"AAA": maak_df([100.0] * 6, dagen, splitsing=[0, 0, 0, 10.0, 0, 0])})
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KoopOpDag(dagen[0]), ["AAA"], gestart_op=dagen[0])
    motor.draai_periode(dagen[0], dagen[1])
    motor.deelnemers[0].portefeuille.posities["AAA"].koopprijs = 400.0  # past bij geen van beide
    motor.draai_periode(dagen[2], dagen[-1])
    assert motor.problemen and "splitsing" in motor.problemen[0]


def test_dividend_alleen_als_je_het_aandeel_voor_de_exdatum_had(con, cfg_min, vaste_wisselkoers):
    dagen = werkdagen(6)
    # Dividend op dag 2 (de dag waarop gekocht wordt, dus geen recht) en op dag 5.
    markt = Markt({"AAA": maak_df([100.0] * 6, dagen, dividend=[0, 1.0, 0, 0, 0.5, 0])})
    motor = _draai(markt, dagen, cfg_min, con, vaste_wisselkoers)
    rijen = con.execute("SELECT datum, waarde FROM uitkeringen WHERE soort='dividend'").fetchall()
    assert [(r["datum"], r["waarde"]) for r in rijen] == [(dagen[4], 0.5)]
    pf = motor.deelnemers[0].portefeuille
    assert pf.posities["AAA"].dividend_usd == pytest.approx(pf.posities["AAA"].aantal * 0.5)


def test_dividend_ook_voor_de_meetlat(con, cfg_min, vaste_wisselkoers):
    """De meetlat SPY krijgt zijn dividend. Zonder dat zou hij oneerlijk achterlopen."""
    from bot.strategieen.benchmarks import KoopEnHoud

    dagen = werkdagen(8)
    markt = Markt({"SPY": maak_df([500.0] * 8, dagen, dividend=[0, 0, 0, 0, 2.0, 0, 0, 0])})
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KoopEnHoud("SPY"), ["SPY"], gestart_op=dagen[0])
    motor.draai_periode(dagen[0], dagen[-1])
    rij = con.execute("SELECT COUNT(*) n, SUM(bedrag_usd) s FROM uitkeringen").fetchone()
    assert rij["n"] == 1 and rij["s"] > 3.5
