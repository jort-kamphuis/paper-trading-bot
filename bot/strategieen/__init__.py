"""De strategieën. Elk met een eigen portefeuille van nep-geld."""

from __future__ import annotations

from bot.strategieen.basis import Order, Strategie
from bot.strategieen.benchmarks import KoopEnHoud
from bot.strategieen.mean_reversion import MeanReversion
from bot.strategieen.momentum import Momentum
from bot.strategieen.trend import TrendVolgend

__all__ = [
    "Order",
    "Strategie",
    "Momentum",
    "MeanReversion",
    "TrendVolgend",
    "KoopEnHoud",
    "maak_strategieen",
]


def maak_strategieen(cfg: dict) -> list[Strategie]:
    """Maak alle strategieën die in config.yaml op 'actief: true' staan.

    De benchmarks worden hier niet gemaakt: die krijgen elk een eigen
    portefeuille en worden door de motor apart aangemaakt.
    """
    s = cfg["strategieen"]
    uitkomst: list[Strategie] = []
    if s["momentum"]["actief"]:
        uitkomst.append(Momentum(s["momentum"]))
    if s["mean_reversion"]["actief"]:
        uitkomst.append(MeanReversion(s["mean_reversion"]))
    if s["trend"]["actief"]:
        uitkomst.append(TrendVolgend(s["trend"]))
    return uitkomst
