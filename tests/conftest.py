"""Hulpstukken voor de tests: een verzonnen markt zonder internet."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import database
from bot.data.wisselkoers import Wisselkoers
from bot.markt import Markt

INSTELLINGEN = {
    "start_kapitaal_eur": 1000.0,
    "max_per_trade_eur": 100.0,
    "kosten_pct": 0.1,
    "slippage_pct": 0.05,
}


def werkdagen(aantal: int, start: str = "2025-01-01") -> list[str]:
    """Een lijst datums, zaterdag en zondag overgeslagen."""
    dag = date.fromisoformat(start)
    uit: list[str] = []
    while len(uit) < aantal:
        if dag.weekday() < 5:
            uit.append(dag.isoformat())
        dag += timedelta(days=1)
    return uit


def maak_markt(koersen_per_symbool: dict[str, list[float]], datums: list[str]) -> Markt:
    """Bouw een Markt uit losse slotkoersen. De openingskoers is de slotkoers."""
    per_symbool: dict[str, pd.DataFrame] = {}
    for symbool, slotkoersen in koersen_per_symbool.items():
        n = min(len(slotkoersen), len(datums))
        df = pd.DataFrame(
            {
                "open": slotkoersen[:n],
                "hoog": slotkoersen[:n],
                "laag": slotkoersen[:n],
                "slot": slotkoersen[:n],
                "volume": [1000.0] * n,
                "valuta": ["USD"] * n,
            },
            index=datums[:n],
        )
        df.index.name = "datum"
        per_symbool[symbool] = df
    return Markt(per_symbool)


@pytest.fixture
def con():
    verbinding = database.verbind(":memory:")
    yield verbinding
    verbinding.close()


@pytest.fixture
def vaste_wisselkoers():
    """Een vaste koers van 1,10 dollar per euro, zodat je het kunt narekenen."""
    return Wisselkoers({"2020-01-01": 1.10})


@pytest.fixture
def cfg_min():
    """Een kleine configuratie voor de tests."""
    return {
        "algemeen": dict(INSTELLINGEN),
        "strategieen": {
            "momentum": {
                "actief": True, "terugblik_dagen": 60, "aantal_posities": 8,
                "verkoop_buiten_top": 16, "min_stijging_pct": 2.0,
            },
            "mean_reversion": {
                "actief": True, "rsi_periode": 14, "koop_onder": 30,
                "verkoop_boven": 55, "max_dagen_vasthouden": 20,
            },
            "trend": {"actief": True, "snelle_ma": 20, "langzame_ma": 100},
            "benchmarks": {"actief": True, "posities": ["SPY"]},
        },
        "rapport": {
            "kalender_symbool": "SPY",
            "min_trades_voor_uitspraak": 30,
            "betrouwbaarheid": 0.95,
            "onderscheidend_vermogen": 0.80,
        },
    }
