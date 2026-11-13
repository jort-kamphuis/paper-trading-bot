"""Controleert dat de bot niet op onvolledige koersen handelt."""

from __future__ import annotations

import pandas as pd

from bot import volledigheid
from bot.markt import Markt


def _df(datums: list[str]) -> pd.DataFrame:
    n = len(datums)
    df = pd.DataFrame(
        {"open": [1.0] * n, "hoog": [1.0] * n, "laag": [1.0] * n, "slot": [1.0] * n,
         "volume": [1.0] * n, "valuta": ["USD"] * n},
        index=datums,
    )
    df.index.name = "datum"
    return df


def _cfg():
    return {
        "watchlist": {"aandelen": ["AAPL", "ORCL"], "etfs": ["SPY"], "crypto": ["BTC/USDT"]},
        "rapport": {"kalender_symbool": "SPY"},
    }


BEURS = ["2026-11-11", "2026-11-12"]
ALLES = ["2026-11-11", "2026-11-12", "2026-11-13"]


def test_compleet_geeft_geen_problemen():
    markt = Markt({"AAPL": _df(BEURS), "ORCL": _df(BEURS), "SPY": _df(BEURS), "BTC/USDT": _df(ALLES)})
    assert volledigheid.controleer(markt, _cfg(), ALLES) == []


def test_een_heel_symbool_weg():
    markt = Markt({"AAPL": _df(BEURS), "SPY": _df(BEURS), "BTC/USDT": _df(ALLES)})
    problemen = volledigheid.controleer(markt, _cfg(), ALLES)
    assert any("ORCL" in p and "geen koersen" in p for p in problemen)


def test_aandeel_mist_een_beursdag():
    markt = Markt({"AAPL": _df(BEURS), "ORCL": _df(BEURS[:1]), "SPY": _df(BEURS), "BTC/USDT": _df(ALLES)})
    problemen = volledigheid.controleer(markt, _cfg(), ALLES)
    assert problemen == ["ORCL: geen koers op 2026-11-12, terwijl SPY die dag wel handelde"]


def test_weekend_is_geen_probleem_voor_aandelen():
    """Op een dag dat SPY niet handelde, hoeft een aandeel ook geen koers te hebben."""
    markt = Markt({"AAPL": _df(BEURS), "ORCL": _df(BEURS), "SPY": _df(BEURS), "BTC/USDT": _df(ALLES)})
    assert volledigheid.controleer(markt, _cfg(), ["2026-11-13"]) == []


def test_crypto_mist_een_dag():
    cfg = _cfg()
    cfg["watchlist"]["crypto"] = ["BTC/USDT", "ETH/USDT"]
    markt = Markt({
        "AAPL": _df(BEURS), "ORCL": _df(BEURS), "SPY": _df(BEURS),
        "BTC/USDT": _df(ALLES), "ETH/USDT": _df(ALLES[:2]),
    })
    problemen = volledigheid.controleer(markt, cfg, ALLES)
    assert problemen == ["ETH/USDT: geen koers op 2026-11-13"]
