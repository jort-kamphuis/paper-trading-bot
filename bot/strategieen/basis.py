"""Wat elke strategie gemeen heeft."""

from __future__ import annotations

from dataclasses import dataclass

from bot.markt import Markt
from bot.portefeuille import Positie


@dataclass
class Order:
    """Een voorgenomen trade. Wordt uitgevoerd op de eerstvolgende beursdag."""

    symbool: str
    actie: str  # KOOP of VERKOOP
    reden: str
    zekerheid: float | None = None


class Strategie:
    """Een strategie beslist op basis van de data tot en met de beslisdag.

    Een strategie mag niets uitvoeren en niets opslaan: hij geeft alleen
    orders terug. Zo kan hij ook nooit per ongeluk in de toekomst kijken.
    """

    naam: str = "basis"
    # Welke symbolen deze strategie volgt: 'alles' of 'ai'.
    lijst: str = "alles"
    # Normaal geldt de grens van max_per_trade_eur. Alleen de meetlat
    # (kopen en vasthouden) legt het hele startkapitaal in een keer in.
    volledig_kapitaal: bool = False

    def __init__(self, instellingen: dict):
        self.instellingen = dict(instellingen)

    def besluit(
        self,
        datum: str,
        markt: Markt,
        symbolen: list[str],
        posities: dict[str, Positie],
    ) -> list[Order]:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.naam}>"
