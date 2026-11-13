"""Controleert dat de waarde van een portefeuille op gesloten dagen klopt.

Dit was een echte fout: op een dag dat de aandelenbeurs dicht was (crypto
handelt dan wel, dus die dag staat wel in de reeks) viel de waarde van een
aandelenpositie terug op de kostprijs. Daardoor leek de meetlat SPY precies
op het startbedrag te eindigen en klopten de grootste daling en de Sharpe
ratio niet.
"""

from __future__ import annotations

import pandas as pd
import pytest

from bot import statistiek
from bot.markt import Markt
from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN, werkdagen


def _markt_met_gat() -> tuple[Markt, list[str]]:
    """AAA handelt alleen door de week, BTC elke dag."""
    alle = ["2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05", "2025-01-06"]
    beursdagen = ["2025-01-02", "2025-01-03", "2025-01-06"]  # 4 en 5 jan = weekend

    df_aaa = pd.DataFrame(
        {
            "open": [100.0, 120.0, 130.0], "hoog": [100.0, 120.0, 130.0],
            "laag": [100.0, 120.0, 130.0], "slot": [100.0, 120.0, 130.0],
            "volume": [1.0] * 3, "valuta": ["USD"] * 3,
        },
        index=beursdagen,
    )
    df_aaa.index.name = "datum"
    df_btc = pd.DataFrame(
        {
            "open": [10.0] * 5, "hoog": [10.0] * 5, "laag": [10.0] * 5,
            "slot": [10.0] * 5, "volume": [1.0] * 5, "valuta": ["USD"] * 5,
        },
        index=alle,
    )
    df_btc.index.name = "datum"
    return Markt({"AAA": df_aaa, "BTC/USDT": df_btc}), alle


def test_koers_blijft_staan_op_een_gesloten_dag(vaste_wisselkoers):
    markt, alle = _markt_met_gat()
    # Zaterdag 4 januari: AAA handelt niet, maar is nog steeds 120 waard
    # (de slotkoers van vrijdag), niet 0 en niet de kostprijs.
    koersen = markt.slotkoersen_eur("2025-01-04", vaste_wisselkoers)
    assert "AAA" in koersen
    assert koersen["AAA"] == pytest.approx(120.0 / 1.10)


def test_portefeuillewaarde_valt_niet_terug_op_de_kostprijs(vaste_wisselkoers):
    markt, alle = _markt_met_gat()
    p = nieuwe_portefeuille("test", "test", INSTELLINGEN, gestart_op=alle[0])
    p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10)
    aantal = p.posities["AAA"].aantal
    kostprijs = p.posities["AAA"].kostprijs_eur

    # Op zaterdag staat AAA op 120: de positie is dan meer waard dan de kostprijs.
    koersen = markt.slotkoersen_eur("2025-01-04", vaste_wisselkoers)
    waarde = p.posities_waarde(koersen)
    assert waarde == pytest.approx(aantal * 120.0 / 1.10)
    assert waarde > kostprijs * 1.1, "de koerswinst mag in het weekend niet verdwijnen"


def test_geen_koers_voor_de_eerste_dag_van_een_symbool(vaste_wisselkoers):
    """Een symbool dat nog niet bestond, krijgt geen verzonnen koers."""
    df_oud = pd.DataFrame(
        {"open": [10.0], "hoog": [10.0], "laag": [10.0], "slot": [10.0],
         "volume": [1.0], "valuta": ["USD"]},
        index=["2025-01-02"],
    )
    df_oud.index.name = "datum"
    df_nieuw = pd.DataFrame(
        {"open": [50.0], "hoog": [50.0], "laag": [50.0], "slot": [50.0],
         "volume": [1.0], "valuta": ["USD"]},
        index=["2025-01-03"],
    )
    df_nieuw.index.name = "datum"
    markt = Markt({"OUD": df_oud, "NIEUW": df_nieuw})
    koersen = markt.slotkoersen_eur("2025-01-02", vaste_wisselkoers)
    assert "OUD" in koersen
    assert "NIEUW" not in koersen, "een symbool zonder koers mag er niet bij verzonnen worden"


def test_meetkalender_houdt_alleen_beursdagen_over():
    datums = ["2025-01-02", "2025-01-03", "2025-01-04", "2025-01-06"]
    waarden = [1000.0, 1010.0, 1005.0, 1020.0]
    kalender = ["2025-01-02", "2025-01-03", "2025-01-06"]
    d, w = statistiek.op_kalender(datums, waarden, kalender)
    assert d == kalender
    assert w == [1000.0, 1010.0, 1020.0]


def test_sharpe_van_aandelen_en_crypto_op_dezelfde_kalender():
    """Op een gemeenschappelijke kalender telt een vlak weekend niet mee."""
    beursdagen = werkdagen(100)
    alle_dagen = []
    from datetime import date, timedelta

    dag = date.fromisoformat(beursdagen[0])
    while dag.isoformat() <= beursdagen[-1]:
        alle_dagen.append(dag.isoformat())
        dag += timedelta(days=1)

    # Een aandelenportefeuille: beweegt alleen op beursdagen, vlak in het weekend.
    waarde_per_dag = {}
    stand = 1000.0
    for d in alle_dagen:
        if d in set(beursdagen):
            stand *= 1.001
        waarde_per_dag[d] = stand

    datums = list(waarde_per_dag)
    waarden = list(waarde_per_dag.values())
    _, op_beursdagen = statistiek.op_kalender(datums, waarden, beursdagen)

    # Op de beursdagenkalender zijn er geen nulrendementen van het weekend meer.
    r = statistiek.dagrendementen(op_beursdagen)
    assert len(r) == len(beursdagen) - 1
    assert all(abs(x - 0.001) < 1e-9 for x in r)
