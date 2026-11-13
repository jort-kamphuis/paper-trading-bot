"""Momentum: koop wat het de laatste maanden het beste deed.

De gedachte: koersen die sterk gestegen zijn, stijgen vaak nog even door.
We vergelijken alle symbolen met elkaar en kopen de sterkste.

Twee keuzes die het eerlijk houden:
* De terugblik is in kalenderdagen (90), voor aandelen en crypto gelijk.
* We rangschikken op rendement gedeeld door de schommeling. Anders staat
  vanzelf bovenaan wat het wildst beweegt, en dat is meestal crypto.
"""

from __future__ import annotations

from bot.markt import Markt
from bot.portefeuille import Positie
from bot.strategieen.basis import Order, Strategie
from bot.tekst import getal


class Momentum(Strategie):
    naam = "momentum"

    def __init__(self, instellingen: dict):
        super().__init__(instellingen)
        self.max_posities = int(self.instellingen.get("aantal_posities", 10))

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        terugblik = int(self.instellingen["terugblik_kalenderdagen"])
        buiten_top = int(self.instellingen["verkoop_buiten_top"])

        scores: list[tuple[str, float, float]] = []
        for symbool in symbolen:
            score = markt.gecorrigeerd_rendement(symbool, datum, terugblik)
            rend = markt.kalender_rendement(symbool, datum, terugblik)
            if score is None or rend is None:
                continue
            scores.append((symbool, score, rend))
        if not scores:
            return []

        scores.sort(key=lambda x: x[1], reverse=True)
        rang = {s: i + 1 for i, (s, _, _) in enumerate(scores)}
        info = {s: (sc, r) for s, sc, r in scores}
        dag = self.dag
        wacht_verkoop = dag.wachtende_verkopen if dag else set()

        orders: list[Order] = []
        verkocht: set[str] = set()
        for symbool in posities:
            if symbool in wacht_verkoop:
                continue
            plek = rang.get(symbool)
            if plek is None or plek > buiten_top:
                waarom = "geen rendement te berekenen" if plek is None else (
                    f"uit de top {buiten_top} gevallen (plek {plek} van {len(scores)})"
                )
                orders.append(Order(symbool=symbool, actie="VERKOOP", reden=waarom))
                verkocht.add(symbool)

        vrij = self.vrije_plekken(posities, na_verkopen=verkocht)
        bezet = self.bezet(posities)
        for symbool, score, rend in scores[: self.max_posities]:
            if vrij <= 0:
                break
            if symbool in bezet or score <= 0:
                continue
            orders.append(
                Order(
                    symbool=symbool,
                    actie="KOOP",
                    reden=(
                        f"plek {rang[symbool]} van {len(scores)}: {terugblik} dagen "
                        f"{getal(rend, 1, teken=True)}%, gecorrigeerd voor schommeling {getal(score, 2)}"
                    ),
                )
            )
            vrij -= 1
        return orders
