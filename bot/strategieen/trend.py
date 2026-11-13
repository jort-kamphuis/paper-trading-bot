"""Trend volgen: het 50-daags gemiddelde tegen het 200-daags gemiddelde.

De klassieke regel: zolang het 50-daags gemiddelde boven het 200-daags ligt,
is de trend omhoog en hoor je erin te zitten. Zakt het eronder, dan eruit.

Dit is een toestandsregel, geen moment: een symbool dat al in een opwaartse
trend zit, kan ook later nog gekocht worden. Zo gaat een signaal niet verloren
omdat er op de dag van de kruising geen plek of geen geld was. Zijn er meer
kandidaten dan plekken, dan gaan de sterkste trends voor (hoe ver het
50-daags gemiddelde boven het 200-daags ligt).
"""

from __future__ import annotations

from bot.markt import Markt
from bot.portefeuille import Positie
from bot.strategieen.basis import Order, Strategie
from bot.tekst import getal


class TrendVolgend(Strategie):
    naam = "trend"

    def __init__(self, instellingen: dict):
        super().__init__(instellingen)
        self.max_posities = int(self.instellingen.get("aantal_posities", 10))

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        snel = f"ma{int(self.instellingen['snelle_ma'])}"
        langzaam = f"ma{int(self.instellingen['langzame_ma'])}"
        dag = self.dag
        wacht_verkoop = dag.wachtende_verkopen if dag else set()

        orders: list[Order] = []
        verkocht: set[str] = set()
        for symbool in posities:
            if symbool in wacht_verkoop:
                continue
            a = markt.kengetal(symbool, datum, snel)
            b = markt.kengetal(symbool, datum, langzaam)
            if a is not None and b is not None and a < b:
                orders.append(
                    Order(
                        symbool=symbool,
                        actie="VERKOOP",
                        reden=f"{snel[2:]}-daags gemiddelde onder het {langzaam[2:]}-daags gezakt",
                    )
                )
                verkocht.add(symbool)

        bezet = self.bezet(posities)
        kandidaten: list[tuple[str, float]] = []
        for symbool in symbolen:
            if symbool in bezet or markt.bar(symbool, datum) is None:
                continue
            a = markt.kengetal(symbool, datum, snel)
            b = markt.kengetal(symbool, datum, langzaam)
            if a is None or b is None or b <= 0:
                continue
            if a > b:
                kandidaten.append((symbool, a / b - 1.0))

        kandidaten.sort(key=lambda x: x[1], reverse=True)
        vrij = self.vrije_plekken(posities, na_verkopen=verkocht)
        for symbool, sterkte in kandidaten[: max(0, vrij)]:
            orders.append(
                Order(
                    symbool=symbool,
                    actie="KOOP",
                    reden=(
                        f"{snel[2:]}-daags gemiddelde {getal(100 * sterkte, 1)}% boven het "
                        f"{langzaam[2:]}-daags: opwaartse trend"
                    ),
                )
            )
        return orders
