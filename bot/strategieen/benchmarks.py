"""De meetlatten: kopen en vasthouden.

Een meetlat koopt op de eerste beursdag een keer en doet daarna nooit meer
iets. Daar moeten de strategieën het van winnen.

* KoopEnHoud: het hele bedrag in een symbool (SPY, BTC).
* GelijkVerdeeld: het bedrag gelijk verdeeld over een lijst symbolen. Dat is de
  eerlijkste meetlat voor een strategie die uit diezelfde lijst kiest: wint de
  strategie hier niet van, dan voegde het kiezen niets toe.

Meetlatten hebben geen grens op het aantal posities en geen 10%-regel.
"""

from __future__ import annotations

from bot.markt import Markt
from bot.portefeuille import Positie
from bot.strategieen.basis import Order, Strategie


class KoopEnHoud(Strategie):
    naam = "koop_en_houd"
    max_posities = None

    def __init__(self, symbool: str, instellingen: dict | None = None):
        super().__init__(instellingen or {})
        self.symbool = symbool
        self.naam = f"benchmark_{symbool.split('/')[0].lower()}"

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        if posities or (self.dag and self.dag.wachtende_kopen):
            return []
        return [
            Order(
                symbool=self.symbool,
                actie="KOOP",
                reden=f"{self.symbool} kopen en vasthouden, de meetlat",
                gewicht=1.0,
            )
        ]


class GelijkVerdeeld(Strategie):
    naam = "gelijk_verdeeld"
    max_posities = None

    def __init__(self, naam: str, symbolen: list[str]):
        super().__init__({})
        self.naam = naam
        self.lijst = list(symbolen)

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        if posities or (self.dag and self.dag.wachtende_kopen):
            return []  # al gekocht (of onderweg): niets meer doen
        aanwezig = [s for s in self.lijst if markt.heeft(s)]
        if not aanwezig:
            return []
        gewicht = 1.0 / len(aanwezig)
        return [
            Order(
                symbool=s,
                actie="KOOP",
                reden=f"gelijk verdeeld over {len(aanwezig)} symbolen, kopen en vasthouden",
                gewicht=gewicht,
            )
            for s in aanwezig
        ]
