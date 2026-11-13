"""Controleert of elke strategie doet wat zijn naam belooft, met de regels van nu."""

from __future__ import annotations

import numpy as np

from bot.portefeuille import Positie
from bot.strategieen.basis import DagContext
from bot.strategieen.benchmarks import GelijkVerdeeld, KoopEnHoud
from bot.strategieen.mean_reversion import MeanReversion
from bot.strategieen.momentum import Momentum
from bot.strategieen.trend import TrendVolgend
from tests.conftest import alle_dagen, maak_markt, werkdagen


def _inst(cfg_min, naam):
    return cfg_min["strategieen"][naam]


def _ruis(n: int, zaad: int, sterkte: float = 0.003) -> np.ndarray:
    """Een beetje vaste ruis, zodat de schommeling nooit nul is."""
    return 1.0 + np.random.default_rng(zaad).normal(0, sterkte, n)


def _reeks(n: int, groei: float, zaad: int, start: float = 100.0) -> list[float]:
    return list(start * (groei ** np.arange(n)) * _ruis(n, zaad))


def _positie(symbool: str, gekocht_op: str) -> Positie:
    return Positie(symbool, 1.0, 100.0, gekocht_op, koopprijs=100.0)


# ------------------------------------------------------------------ momentum


def test_momentum_koopt_de_sterkste_stijgers(cfg_min):
    datums = werkdagen(120)
    koersen = {"AAA": _reeks(120, 1.01, 1), "BBB": _reeks(120, 0.99, 2), "CCC": _reeks(120, 1.0, 3)}
    markt = maak_markt(koersen, datums)
    orders = Momentum(_inst(cfg_min, "momentum")).besluit(datums[-1], markt, list(koersen), {})
    kopen = [o.symbool for o in orders if o.actie == "KOOP"]
    assert "AAA" in kopen
    assert "BBB" not in kopen, "een daler (score onder nul) wordt niet gekocht"


def test_momentum_corrigeert_voor_schommeling(cfg_min):
    """Iets dat iets minder stijgt maar veel rustiger, gaat voor op een wilde stijger."""
    datums = werkdagen(120)
    rustig = _reeks(120, 1.004, 4, start=100.0)
    wild = list(np.array(_reeks(120, 1.005, 5)) * _ruis(120, 6, sterkte=0.05))
    markt = maak_markt({"RUST": rustig, "WILD": wild}, datums)
    m = Momentum({**_inst(cfg_min, "momentum"), "aantal_posities": 1})
    orders = m.besluit(datums[-1], markt, ["RUST", "WILD"], {})
    assert [o.symbool for o in orders] == ["RUST"]


def test_momentum_verkoopt_wat_uit_de_top_valt(cfg_min):
    datums = werkdagen(120)
    koersen = {f"S{i:02d}": _reeks(120, 1.0 + 0.0005 * (25 - i), 10 + i) for i in range(24)}
    koersen["S23"] = _reeks(120, 0.98, 99)
    markt = maak_markt(koersen, datums)
    posities = {"S23": _positie("S23", datums[0])}
    orders = Momentum(_inst(cfg_min, "momentum")).besluit(datums[-1], markt, list(koersen), posities)
    assert any(o.symbool == "S23" and o.actie == "VERKOOP" for o in orders)


def test_momentum_houdt_vast_binnen_de_top_20(cfg_min):
    datums = werkdagen(120)
    koersen = {f"S{i:02d}": _reeks(120, 1.0 + 0.0005 * (25 - i), 10 + i) for i in range(24)}
    markt = maak_markt(koersen, datums)
    # Plek 15 of zo: niet in de top 10, wel in de top 20. Niet verkopen.
    posities = {"S14": _positie("S14", datums[0])}
    orders = Momentum(_inst(cfg_min, "momentum")).besluit(datums[-1], markt, list(koersen), posities)
    assert not any(o.symbool == "S14" and o.actie == "VERKOOP" for o in orders)


def test_momentum_telt_wachtende_kopen_als_bezet(cfg_min):
    datums = werkdagen(120)
    koersen = {f"S{i:02d}": _reeks(120, 1.003 + 0.0002 * i, 30 + i) for i in range(15)}
    markt = maak_markt(koersen, datums)
    m = Momentum(_inst(cfg_min, "momentum"))
    m.dag = DagContext(datum=datums[-1], wachtende_kopen={f"X{i}" for i in range(8)})
    orders = m.besluit(datums[-1], markt, list(koersen), {})
    assert len([o for o in orders if o.actie == "KOOP"]) == 2, "10 plekken, 8 al onderweg"


# ------------------------------------------------------------ mean reversion


def _dip_na_stijging(n: int = 230) -> list[float]:
    """Lang omhoog (boven het 200-daags gemiddelde), dan twee felle dalers."""
    k = _reeks(n - 2, 1.003, 7)
    return k + [k[-1] * 0.96, k[-1] * 0.96 * 0.96]


def test_mean_reversion_koopt_een_dip_in_een_stijgende_markt(cfg_min):
    datums = werkdagen(230)
    markt = maak_markt({"AAA": _dip_na_stijging()}, datums)
    orders = MeanReversion(_inst(cfg_min, "mean_reversion")).besluit(datums[-1], markt, ["AAA"], {})
    assert [o.actie for o in orders] == ["KOOP"]
    assert "RSI(2)" in orders[0].reden


def test_mean_reversion_koopt_geen_dip_in_een_dalende_markt(cfg_min):
    datums = werkdagen(230)
    k = _reeks(228, 0.998, 8)
    markt = maak_markt({"AAA": k + [k[-1] * 0.96, k[-1] * 0.92]}, datums)
    orders = MeanReversion(_inst(cfg_min, "mean_reversion")).besluit(datums[-1], markt, ["AAA"], {})
    assert orders == [], "onder het 200-daags gemiddelde: geen koop"


def test_mean_reversion_verkoopt_boven_het_5_daags_gemiddelde(cfg_min):
    datums = werkdagen(230)
    k = _dip_na_stijging(228)
    markt = maak_markt({"AAA": k + [k[-1] * 1.05, k[-1] * 1.10]}, datums)
    posities = {"AAA": _positie("AAA", datums[-3])}
    orders = MeanReversion(_inst(cfg_min, "mean_reversion")).besluit(datums[-1], markt, ["AAA"], posities)
    assert [o.actie for o in orders] == ["VERKOOP"]
    assert "dip voorbij" in orders[0].reden


def test_mean_reversion_verkoopt_na_14_kalenderdagen(cfg_min):
    datums = werkdagen(230)
    k = _reeks(230, 0.999, 9)  # blijft onder het 5-daags gemiddelde hangen
    k = k[:-1] + [k[-2] * 0.97]
    markt = maak_markt({"AAA": k}, datums)
    posities = {"AAA": _positie("AAA", datums[-12])}  # 12 beursdagen = 16 kalenderdagen
    orders = MeanReversion(_inst(cfg_min, "mean_reversion")).besluit(datums[-1], markt, ["AAA"], posities)
    assert orders and orders[0].actie == "VERKOOP"
    assert "kalenderdagen" in orders[0].reden


def test_mean_reversion_hooguit_twee_crypto(cfg_min):
    datums = alle_dagen(320)
    koersen = {}
    for i, munt in enumerate(["AAA/USDT", "BBB/USDT", "CCC/USDT", "DDD/USDT"]):
        k = _reeks(318, 1.003, 40 + i)
        koersen[munt] = k + [k[-1] * 0.95, k[-1] * 0.95 * 0.95]
    markt = maak_markt(koersen, datums)
    orders = MeanReversion(_inst(cfg_min, "mean_reversion")).besluit(datums[-1], markt, list(koersen), {})
    assert len([o for o in orders if o.actie == "KOOP"]) == 2


# --------------------------------------------------------------------- trend


def test_trend_koopt_zolang_de_trend_omhoog_is(cfg_min):
    """Een toestandsregel: ook na de kruising nog kopen, niet alleen op die ene dag."""
    datums = werkdagen(300)
    omlaag = _reeks(150, 0.997, 11)
    omhoog = [omlaag[-1] * x for x in _reeks(150, 1.01, 12, start=1.0)]
    markt = maak_markt({"AAA": omlaag + omhoog}, datums)
    t = TrendVolgend(_inst(cfg_min, "trend"))
    koopdagen = [d for d in datums[200:] if t.besluit(d, markt, ["AAA"], {})]
    assert len(koopdagen) > 5, "elke dag met 50 boven 200 is een koopkans zolang er plek is"
    assert t.besluit(datums[160], markt, ["AAA"], {}) == [], "voor de kruising niet"


def test_trend_verkoopt_als_de_trend_omdraait(cfg_min):
    datums = werkdagen(300)
    omhoog = _reeks(200, 1.004, 13)
    omlaag = [omhoog[-1] * x for x in _reeks(100, 0.98, 14, start=1.0)]
    markt = maak_markt({"AAA": omhoog + omlaag}, datums)
    posities = {"AAA": _positie("AAA", datums[0])}
    orders = TrendVolgend(_inst(cfg_min, "trend")).besluit(datums[-1], markt, ["AAA"], posities)
    assert [o.actie for o in orders] == ["VERKOOP"]


def test_trend_sterkste_trend_eerst_bij_te_weinig_plek(cfg_min):
    datums = werkdagen(260)
    koersen = {"ZWAK": _reeks(260, 1.001, 15), "STERK": _reeks(260, 1.006, 16)}
    markt = maak_markt(koersen, datums)
    t = TrendVolgend({**_inst(cfg_min, "trend"), "aantal_posities": 1})
    orders = t.besluit(datums[-1], markt, list(koersen), {})
    assert [o.symbool for o in orders] == ["STERK"]


# ---------------------------------------------------------------- meetlatten


def test_koop_en_houd_koopt_een_keer_alles():
    datums = werkdagen(10)
    markt = maak_markt({"SPY": [100.0] * 10}, datums)
    s = KoopEnHoud("SPY")
    eerste = s.besluit(datums[0], markt, ["SPY"], {})
    assert [o.actie for o in eerste] == ["KOOP"] and eerste[0].gewicht == 1.0
    assert s.besluit(datums[5], markt, ["SPY"], {"SPY": _positie("SPY", datums[0])}) == []
    s.dag = DagContext(datum=datums[1], wachtende_kopen={"SPY"})
    assert s.besluit(datums[1], markt, ["SPY"], {}) == [], "niet nog eens kopen terwijl de eerste onderweg is"


def test_gelijk_verdeeld_verdeelt_over_wat_er_is():
    datums = werkdagen(5)
    markt = maak_markt({"A": [10.0] * 5, "B": [20.0] * 5, "C": [30.0] * 5}, datums)
    s = GelijkVerdeeld("gelijk_alle", ["A", "B", "C", "ONBEKEND"])
    orders = s.besluit(datums[0], markt, ["A", "B", "C"], {})
    assert sorted(o.symbool for o in orders) == ["A", "B", "C"]
    assert all(abs(o.gewicht - 1 / 3) < 1e-12 for o in orders)
    assert s.max_posities is None


def test_meetlat_belegt_alles_in_de_motor(con, cfg_min, vaste_wisselkoers):
    from bot.motor import Motor

    datums = werkdagen(5)
    markt = maak_markt({"A": [10.0] * 5, "B": [20.0] * 5}, datums)
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(GelijkVerdeeld("gelijk", ["A", "B"]), ["A", "B"], gestart_op=datums[0])
    motor.draai_periode(datums[0], datums[-1])
    pf = motor.deelnemers[0].portefeuille
    assert set(pf.posities) == {"A", "B"}
    assert pf.kas_usd < 5.0, "bijna alles belegd, op de kosten na"


# ---------------------------------------------------------------- algemeen


def test_strategie_doet_niets_zonder_genoeg_geschiedenis(cfg_min):
    datums = werkdagen(5)
    markt = maak_markt({"AAA": [100.0, 101.0, 102.0, 103.0, 104.0]}, datums)
    for strategie in (
        Momentum(_inst(cfg_min, "momentum")),
        MeanReversion(_inst(cfg_min, "mean_reversion")),
        TrendVolgend(_inst(cfg_min, "trend")),
    ):
        assert strategie.besluit(datums[-1], markt, ["AAA"], {}) == [], type(strategie).__name__


def test_geen_besluiten_in_het_weekend(con, cfg_min, vaste_wisselkoers):
    """Met SPY als kalender beslist een strategie alleen op beursdagen."""
    from bot.motor import Motor
    from bot.strategieen.basis import Strategie

    class Teller(Strategie):
        naam = "teller"

        def __init__(self):
            super().__init__({})
            self.dagen: list[str] = []

        def besluit(self, datum, markt, symbolen, posities):
            self.dagen.append(datum)
            return []

    beurs = werkdagen(10)
    alles = alle_dagen(14)
    from bot.markt import Markt
    from tests.conftest import maak_df

    markt = Markt({"SPY": maak_df([100.0] * 10, beurs), "BTC/USDT": maak_df([10.0] * 14, alles)})
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    t = Teller()
    motor.voeg_toe(t, ["BTC/USDT"], gestart_op=alles[0])
    motor.draai_periode(alles[0], alles[-1])
    assert t.dagen and set(t.dagen) <= set(beurs)
