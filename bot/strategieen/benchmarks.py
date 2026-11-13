"""De meetlat: kopen en vasthouden.

Deze "strategie" koopt op de eerste dag een keer, met het hele startkapitaal,
en doet daarna nooit meer iets. Dat is precies waar de andere strategieën het
van moeten winnen.

Let op: hier geldt de grens van maximaal 100 euro per trade met opzet niet.
Anders zou de meetlat voor 900 van de 1000 euro in kas blijven zitten en meet
je niet wat "de markt kopen en vasthouden" oplevert. Dat verschil staat in de
README en in het weekrapport.
"""

from __future__ import annotations

from bot.markt import Markt
from bot.portefeuille import Positie
from bot.strategieen.basis import Order, Strategie


class KoopEnHoud(Strategie):
    naam = "koop_en_houd"
    volledig_kapitaal = True

    def __init__(self, symbool: str, instellingen: dict | None = None):
        super().__init__(instellingen or {})
        self.symbool = symbool
        self.naam = f"benchmark_{symbool.split('/')[0].lower()}"

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        if posities:
            return []  # al gekocht: niets meer doen
        if markt.bar(self.symbool, datum) is None:
            return []
        return [
            Order(
                symbool=self.symbool,
                actie="KOOP",
                reden=f"{self.symbool} kopen en vasthouden, de meetlat",
            )
        ]
