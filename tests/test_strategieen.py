"""Controleert of elke strategie doet wat zijn naam belooft."""

from __future__ import annotations

import numpy as np

from bot.strategieen.benchmarks import KoopEnHoud
from bot.strategieen.mean_reversion import MeanReversion
from bot.strategieen.momentum import Momentum
from bot.strategieen.trend import TrendVolgend
from bot.portefeuille import Positie
from tests.conftest import maak_markt, werkdagen


def _instellingen(cfg_min, naam):
    return cfg_min["strategieen"][naam]


def test_momentum_koopt_de_sterkste_stijgers(cfg_min):
    datums = werkdagen(120)
    # AAA stijgt hard, BBB daalt, CCC blijft vlak.
    koersen = {
        "AAA": [100.0 * (1.01**i) for i in range(120)],
        "BBB": [100.0 * (0.99**i) for i in range(120)],
        "CCC": [100.0] * 120,
    }
    markt = maak_markt(koersen, datums)
    strategie = Momentum(_instellingen(cfg_min, "momentum"))
    orders = strategie.besluit(datums[-1], markt, list(koersen), {})

    kopen = [o.symbool for o in orders if o.actie == "KOOP"]
    assert "AAA" in kopen, "de hardste stijger moet gekocht worden"
    assert "BBB" not in kopen, "een daler mag niet gekocht worden"
    assert "CCC" not in kopen, "een vlakke koers haalt de drempel van 2% niet"


def test_momentum_verkoopt_wat_uit_de_top_valt(cfg_min):
    datums = werkdagen(120)
    # 20 symbolen; het laatste daalt het hardst en moet eruit.
    koersen = {f"S{i:02d}": [100.0 * (1.0 + 0.002 * (20 - i)) ** j for j in range(120)]
               for i in range(20)}
    koersen["S19"] = [100.0 * (0.98**j) for j in range(120)]
    markt = maak_markt(koersen, datums)

    strategie = Momentum(_instellingen(cfg_min, "momentum"))
    posities = {"S19": Positie("S19", 1.0, 100.0, datums[0])}
    orders = strategie.besluit(datums[-1], markt, list(koersen), posities)

    verkopen = [o.symbool for o in orders if o.actie == "VERKOOP"]
    assert "S19" in verkopen


def test_mean_reversion_koopt_bij_lage_rsi(cfg_min):
    datums = werkdagen(60)
    # Een koers die elke dag daalt, heeft een zeer lage RSI.
    koersen = {"AAA": [100.0 * (0.98**i) for i in range(60)]}
    markt = maak_markt(koersen, datums)
    strategie = MeanReversion(_instellingen(cfg_min, "mean_reversion"))
    orders = strategie.besluit(datums[-1], markt, ["AAA"], {})

    assert [o.actie for o in orders] == ["KOOP"]
    assert "RSI" in orders[0].reden


def test_mean_reversion_koopt_niet_bij_hoge_rsi(cfg_min):
    datums = werkdagen(60)
    koersen = {"AAA": [100.0 * (1.02**i) for i in range(60)]}
    markt = maak_markt(koersen, datums)
    strategie = MeanReversion(_instellingen(cfg_min, "mean_reversion"))
    orders = strategie.besluit(datums[-1], markt, ["AAA"], {})
    assert orders == []


def test_mean_reversion_verkoopt_na_herstel(cfg_min):
    datums = werkdagen(60)
    # Eerst hard omlaag, daarna hard omhoog: de RSI loopt weer op.
    koersen = {"AAA": [100.0 * (0.97**i) for i in range(40)]
                      + [100.0 * (0.97**39) * (1.05**i) for i in range(1, 21)]}
    markt = maak_markt(koersen, datums)
    strategie = MeanReversion(_instellingen(cfg_min, "mean_reversion"))
    posities = {"AAA": Positie("AAA", 1.0, 100.0, datums[45])}
    orders = strategie.besluit(datums[-1], markt, ["AAA"], posities)
    assert [o.actie for o in orders] == ["VERKOOP"]


def test_mean_reversion_verkoopt_na_te_lang_vasthouden(cfg_min):
    datums = werkdagen(60)
    koersen = {"AAA": [100.0 * (0.98**i) for i in range(60)]}
    markt = maak_markt(koersen, datums)
    strategie = MeanReversion(_instellingen(cfg_min, "mean_reversion"))
    # 30 beursdagen geleden gekocht, de grens is 20.
    posities = {"AAA": Positie("AAA", 1.0, 100.0, datums[29])}
    orders = strategie.besluit(datums[-1], markt, ["AAA"], posities)
    verkopen = [o for o in orders if o.actie == "VERKOOP"]
    assert verkopen, "na de maximale periode moet er verkocht worden"
    assert "beursdagen" in verkopen[0].reden


def test_trend_koopt_op_het_moment_van_kruisen(cfg_min):
    datums = werkdagen(200)
    # Eerst lang dalen, dan stijgen: ergens kruist het snelle gemiddelde omhoog.
    omlaag = [100.0 * (0.995**i) for i in range(120)]
    omhoog = [omlaag[-1] * (1.02**i) for i in range(1, 81)]
    markt = maak_markt({"AAA": omlaag + omhoog}, datums)
    strategie = TrendVolgend(_instellingen(cfg_min, "trend"))

    koopdagen = [
        d for d in datums[100:]
        if any(o.actie == "KOOP" for o in strategie.besluit(d, markt, ["AAA"], {}))
    ]
    assert len(koopdagen) == 1, f"er moet precies een kruismoment zijn, gevonden: {koopdagen}"


def test_trend_verkoopt_als_de_trend_omdraait(cfg_min):
    datums = werkdagen(200)
    omhoog = [100.0 * (1.005**i) for i in range(120)]
    omlaag = [omhoog[-1] * (0.97**i) for i in range(1, 81)]
    markt = maak_markt({"AAA": omhoog + omlaag}, datums)
    strategie = TrendVolgend(_instellingen(cfg_min, "trend"))
    posities = {"AAA": Positie("AAA", 1.0, 100.0, datums[0])}
    orders = strategie.besluit(datums[-1], markt, ["AAA"], posities)
    assert [o.actie for o in orders] == ["VERKOOP"]


def test_koop_en_houd_koopt_een_keer(cfg_min):
    datums = werkdagen(10)
    markt = maak_markt({"SPY": [100.0] * 10}, datums)
    strategie = KoopEnHoud("SPY")

    eerste = strategie.besluit(datums[0], markt, ["SPY"], {})
    assert [o.actie for o in eerste] == ["KOOP"]

    # Zodra er een positie is, doet de meetlat niets meer.
    posities = {"SPY": Positie("SPY", 10.0, 1000.0, datums[0])}
    assert strategie.besluit(datums[5], markt, ["SPY"], posities) == []


def test_strategie_doet_niets_zonder_genoeg_geschiedenis(cfg_min):
    datums = werkdagen(5)
    markt = maak_markt({"AAA": [100.0, 101.0, 102.0, 103.0, 104.0]}, datums)
    for strategie in (
        Momentum(_instellingen(cfg_min, "momentum")),
        MeanReversion(_instellingen(cfg_min, "mean_reversion")),
        TrendVolgend(_instellingen(cfg_min, "trend")),
    ):
        assert strategie.besluit(datums[-1], markt, ["AAA"], {}) == [], (
            f"{type(strategie).__name__} handelt zonder genoeg geschiedenis"
        )
