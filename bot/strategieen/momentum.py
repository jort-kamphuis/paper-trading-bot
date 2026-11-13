"""Momentum: koop wat het de laatste maanden het beste deed.

De gedachte: koersen die sterk gestegen zijn, stijgen vaak nog even door.
We vergelijken alle symbolen met elkaar en kopen de sterkste.
"""

from __future__ import annotations

from bot.markt import Markt
from bot.portefeuille import Positie
from bot.strategieen.basis import Order, Strategie


class Momentum(Strategie):
    naam = "momentum"

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        terugblik = int(self.instellingen["terugblik_dagen"])
        aantal = int(self.instellingen["aantal_posities"])
        buiten_top = int(self.instellingen["verkoop_buiten_top"])
        min_stijging = float(self.instellingen["min_stijging_pct"])

        # Rangschik alle symbolen op hun stijging over de terugblikperiode.
        scores: list[tuple[str, float]] = []
        for symbool in symbolen:
            if markt.bar(symbool, datum) is None:
                continue  # dit symbool handelde vandaag niet
            rendement = markt.kengetal(symbool, datum, f"rend{terugblik}")
            if rendement is None:
                continue
            scores.append((symbool, rendement))

        if not scores:
            return []

        scores.sort(key=lambda x: x[1], reverse=True)
        rang = {symbool: i + 1 for i, (symbool, _) in enumerate(scores)}
        per_symbool = dict(scores)

        orders: list[Order] = []

        # Eerst verkopen: alles wat uit de ruimere top is gevallen.
        for symbool in list(posities):
            plek = rang.get(symbool)
            if plek is None:
                continue  # geen koers vandaag; morgen opnieuw kijken
            if plek > buiten_top:
                orders.append(
                    Order(
                        symbool=symbool,
                        actie="VERKOOP",
                        reden=(
                            f"uit de top {buiten_top} gevallen (plek {plek} van {len(scores)}, "
                            f"{terugblik}-daags rendement {per_symbool[symbool]:+.1f}%)"
                        ),
                    )
                )

        # Dan kopen: de sterkste namen die we nog niet hebben.
        verkocht = {o.symbool for o in orders}
        plekken_vrij = aantal - (len(posities) - len(verkocht))
        for symbool, rendement in scores[:aantal]:
            if plekken_vrij <= 0:
                break
            if symbool in posities or symbool in verkocht:
                continue
            if rendement < min_stijging:
                continue
            orders.append(
                Order(
                    symbool=symbool,
                    actie="KOOP",
                    reden=(
                        f"plek {rang[symbool]} van {len(scores)} op {terugblik}-daags "
                        f"rendement ({rendement:+.1f}%)"
                    ),
                )
            )
            plekken_vrij -= 1

        return orders
