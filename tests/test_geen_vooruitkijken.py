"""Controleert dat geen enkele strategie in de toekomst kan kijken.

Twee dingen worden getest:
1. De kengetallen op dag T zijn hetzelfde, of je ze nu uitrekent over de hele
   reeks of over de reeks die op dag T stopt. Dat moet, want dan mag je ze
   vooraf in een keer uitrekenen.
2. Een besluit van dag T wordt uitgevoerd op de openingskoers van de
   eerstvolgende handelsdag, nooit op een koers van dag T zelf.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot import indicatoren
from bot.markt import Markt
from bot.motor import Motor
from bot.strategieen.basis import Order, Strategie
from tests.conftest import maak_markt, werkdagen


def _reeks(n: int = 200, zaad: int = 7) -> pd.Series:
    generator = np.random.default_rng(zaad)
    stappen = generator.normal(0, 1.5, n)
    return pd.Series(100.0 + np.cumsum(stappen))


@pytest.mark.parametrize("afkap", [50, 100, 150, 199])
def test_kengetallen_veranderen_niet_door_latere_data(afkap):
    reeks = _reeks()
    kort = reeks.iloc[: afkap + 1]

    for naam, functie in [
        ("ma20", lambda s: indicatoren.voortschrijdend_gemiddelde(s, 20)),
        ("ma100", lambda s: indicatoren.voortschrijdend_gemiddelde(s, 100)),
        ("rsi14", lambda s: indicatoren.rsi(s, 14)),
        ("rend60", lambda s: indicatoren.rendement_over(s, 60)),
    ]:
        vol = functie(reeks).iloc[afkap]
        deel = functie(kort).iloc[afkap]
        if pd.isna(vol) and pd.isna(deel):
            continue
        assert vol == pytest.approx(deel, rel=1e-12, abs=1e-12), (
            f"{naam} op plek {afkap} verandert door latere data: {vol} tegen {deel}"
        )


def test_tot_en_met_geeft_nooit_latere_dagen():
    datums = werkdagen(10)
    markt = maak_markt({"AAA": [10.0] * 10}, datums)
    deel = markt.tot_en_met("AAA", datums[4])
    assert list(deel.index) == datums[:5]
    assert datums[5] not in deel.index


class KoopOpDagTwee(Strategie):
    """Een teststrategie die precies een keer koopt, op de derde dag."""

    naam = "test_koop"

    def __init__(self, koopdatum: str):
        super().__init__({})
        self.koopdatum = koopdatum
        self.gezien: list[str] = []

    def besluit(self, datum, markt, symbolen, posities):
        self.gezien.append(datum)
        if datum == self.koopdatum and not posities:
            return [Order(symbool="AAA", actie="KOOP", reden="test")]
        return []


def test_order_wordt_op_de_volgende_dag_uitgevoerd(con, cfg_min, vaste_wisselkoers):
    datums = werkdagen(6)
    # De koers springt op dag 4 (plek 3) omhoog. Wie op dag 3 besluit, moet
    # die sprong betalen, niet de oude koers.
    koersen = [100.0, 100.0, 100.0, 200.0, 200.0, 200.0]
    markt = maak_markt({"AAA": koersen}, datums)

    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    strategie = KoopOpDagTwee(koopdatum=datums[2])
    motor.voeg_toe(strategie, ["AAA"])
    motor.draai_periode(datums[0], datums[-1])

    trades = con.execute("SELECT * FROM trades ORDER BY id").fetchall()
    assert len(trades) == 1, "er had precies een trade moeten zijn"
    trade = trades[0]
    assert trade["besluit_datum"] == datums[2]
    assert trade["datum"] == datums[3], "de trade moet op de dag na het besluit vallen"

    # De prijs is de openingskoers van dag 4 plus slippage.
    verwacht_lokaal = 200.0 * (1 + 0.05 / 100)
    assert trade["prijs_lokaal"] == pytest.approx(verwacht_lokaal)
    assert trade["prijs_eur"] == pytest.approx(verwacht_lokaal / 1.10)


def test_strategie_ziet_alleen_dagen_tot_en_met_vandaag(con, cfg_min, vaste_wisselkoers):
    datums = werkdagen(5)
    markt = maak_markt({"AAA": [10.0] * 5}, datums)
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    strategie = KoopOpDagTwee(koopdatum="nooit")
    motor.voeg_toe(strategie, ["AAA"])
    motor.draai_periode(datums[0], datums[-1])
    assert strategie.gezien == datums


def test_order_vervalt_als_de_beurs_lang_dicht_blijft(con, cfg_min, vaste_wisselkoers):
    """Een order die dagen blijft hangen, moet vervallen in plaats van later afgaan."""
    # AAA handelt op dag 1 en daarna pas 20 dagen later.
    datums_aaa = [werkdagen(1)[0], werkdagen(30)[-1]]
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0], "hoog": [100.0, 100.0], "laag": [100.0, 100.0],
            "slot": [100.0, 100.0], "volume": [1.0, 1.0], "valuta": ["USD", "USD"],
        },
        index=datums_aaa,
    )
    df.index.name = "datum"
    # BBB handelt elke dag, zodat de motor dagen heeft om te draaien.
    alle = werkdagen(30)
    df_b = pd.DataFrame(
        {
            "open": [50.0] * 30, "hoog": [50.0] * 30, "laag": [50.0] * 30,
            "slot": [50.0] * 30, "volume": [1.0] * 30, "valuta": ["USD"] * 30,
        },
        index=alle,
    )
    df_b.index.name = "datum"
    markt = Markt({"AAA": df, "BBB": df_b})

    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(KoopOpDagTwee(koopdatum=alle[0]), ["AAA"])
    motor.draai_periode(alle[0], alle[-1])

    trades = con.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"]
    assert trades == 0, "de order had moeten vervallen, niet 20 dagen later afgaan"
