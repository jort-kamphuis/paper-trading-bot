"""Hulpstukken voor de tests: een verzonnen markt zonder internet."""

from __future__ import annotations

import copy
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import database
from bot.data.wisselkoers import Wisselkoers
from bot.markt import Markt

INSTELLINGEN = {
    "start_kapitaal_usd": 1000.0,
    "max_posities": 10,
    "positie_pct": 10.0,
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


def alle_dagen(aantal: int, start: str = "2025-01-01") -> list[str]:
    """Een lijst opeenvolgende kalenderdagen (zoals crypto handelt)."""
    dag = date.fromisoformat(start)
    return [(dag + timedelta(days=i)).isoformat() for i in range(aantal)]


def maak_df(slotkoersen: list[float], datums: list[str], dividend=None, splitsing=None,
            open_=None, volume=None) -> pd.DataFrame:
    n = min(len(slotkoersen), len(datums))
    df = pd.DataFrame(
        {
            "open": (open_ or slotkoersen)[:n],
            "hoog": slotkoersen[:n],
            "laag": slotkoersen[:n],
            "slot": slotkoersen[:n],
            "volume": (volume or [1000.0] * n)[:n],
            "valuta": ["USD"] * n,
            "dividend": (dividend or [0.0] * n)[:n],
            "splitsing": (splitsing or [0.0] * n)[:n],
        },
        index=datums[:n],
    )
    df.index.name = "datum"
    return df


def maak_markt(koersen_per_symbool: dict[str, list[float]], datums: list[str]) -> Markt:
    """Bouw een Markt uit losse slotkoersen. De openingskoers is de slotkoers."""
    return Markt({s: maak_df(k, datums) for s, k in koersen_per_symbool.items()})


@pytest.fixture
def con():
    verbinding = database.verbind(":memory:")
    yield verbinding
    verbinding.close()


@pytest.fixture
def vaste_wisselkoers():
    """Een vaste koers van 1,10 dollar per euro (alleen voor de weergave)."""
    return Wisselkoers({"2020-01-01": 1.10})


@pytest.fixture
def cfg_min():
    """De echte instellingen uit config.yaml, met de gewone geldregels."""
    cfg = copy.deepcopy(conf.laad())
    cfg["algemeen"] = dict(INSTELLINGEN)
    return cfg
