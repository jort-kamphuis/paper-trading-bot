"""De strategieën. Elk met een eigen portefeuille van nep-geld."""

from __future__ import annotations

from bot.strategieen.basis import DagContext, Order, Strategie
from bot.strategieen.benchmarks import GelijkVerdeeld, KoopEnHoud
from bot.strategieen.mean_reversion import MeanReversion
from bot.strategieen.momentum import Momentum
from bot.strategieen.trend import TrendVolgend

__all__ = [
    "DagContext",
    "Order",
    "Strategie",
    "Momentum",
    "MeanReversion",
    "TrendVolgend",
    "KoopEnHoud",
    "GelijkVerdeeld",
    "maak_strategieen",
]


def maak_strategieen(cfg: dict) -> list[Strategie]:
    """Maak alle regelstrategieën die in config.yaml op 'actief: true' staan.

    De meetlatten worden hier niet gemaakt; die maakt bot/opzet.py.
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
