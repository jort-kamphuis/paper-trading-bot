"""Wat elke strategie gemeen heeft."""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.markt import Markt
from bot.portefeuille import Positie


@dataclass
class Order:
    """Een voorgenomen trade. Wordt uitgevoerd op de eerstvolgende handelsdag."""

    symbool: str
    actie: str  # KOOP of VERKOOP
    reden: str
    zekerheid: float | None = None
    # Welk deel van de portefeuille deze aankoop krijgt. Leeg = de standaard
    # (10%). De meetlatten zetten dit zelf (alles in een keer, of gelijk verdeeld).
    gewicht: float | None = None


@dataclass
class DagContext:
    """Wat een strategie over de eigen portefeuille op de beslisdag mag weten."""

    datum: str
    is_beursdag: bool = True
    wachtende_kopen: set[str] = field(default_factory=set)
    wachtende_verkopen: set[str] = field(default_factory=set)
    kas_usd: float = 0.0
    waarde_usd: float = 0.0
    max_posities: int | None = 10


class Strategie:
    """Een strategie beslist op basis van de data tot en met de beslisdag.

    Een strategie mag niets uitvoeren en niets opslaan: hij geeft alleen
    orders terug. Zo kan hij ook nooit per ongeluk in de toekomst kijken.
    """

    naam: str = "basis"
    # Alleen beslissen op dagen dat de Amerikaanse beurs open was (zie motor.py).
    alleen_beursdagen: bool = True
    # Hoeveel posities tegelijk. None = geen grens (de meetlatten).
    max_posities: int | None = 10

    def __init__(self, instellingen: dict):
        self.instellingen = dict(instellingen)
        # Door de motor gezet vlak voor elk besluit.
        self.dag: DagContext | None = None

    # ---------- hulpjes ----------

    def bezet(self, posities: dict[str, Positie]) -> set[str]:
        """Wat er op de beslisdag al vastligt: posities en orders die nog afgaan.

        Een koop die gisteren besloten is maar nog moet afgaan (bijvoorbeeld een
        aandeel in het weekend), telt al als plek. Anders wordt dezelfde plek
        twee keer weggegeven.
        """
        dag = self.dag or DagContext(datum="")
        return (set(posities) - dag.wachtende_verkopen) | dag.wachtende_kopen

    def vrije_plekken(self, posities: dict[str, Positie], na_verkopen: set[str] | None = None) -> int:
        grens = self.max_posities
        if grens is None:
            return 10**9
        bezet = self.bezet(posities) - (na_verkopen or set())
        return max(0, grens - len(bezet))

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
