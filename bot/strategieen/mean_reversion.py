"""Mean reversion met de RSI: koop wat te hard gedaald is.

De gedachte: na een flinke daling veert een koers vaak een stukje terug.
De RSI meet hoe fors de daling of stijging was (0 tot 100).
"""

from __future__ import annotations

from bot.markt import Markt
from bot.portefeuille import Positie
from bot.strategieen.basis import Order, Strategie


class MeanReversion(Strategie):
    naam = "mean_reversion"

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        periode = int(self.instellingen["rsi_periode"])
        koop_onder = float(self.instellingen["koop_onder"])
        verkoop_boven = float(self.instellingen["verkoop_boven"])
        max_dagen = int(self.instellingen["max_dagen_vasthouden"])
        veld = f"rsi{periode}"

        orders: list[Order] = []

        # Verkopen: hersteld, of te lang vastgehouden.
        for symbool, positie in posities.items():
            if markt.bar(symbool, datum) is None:
                continue
            rsi_nu = markt.kengetal(symbool, datum, veld)
            dagen_vast = markt.beursdagen_tussen(symbool, positie.gekocht_op, datum)
            if rsi_nu is not None and rsi_nu > verkoop_boven:
                orders.append(
                    Order(
                        symbool=symbool,
                        actie="VERKOOP",
                        reden=f"RSI hersteld naar {rsi_nu:.0f} (boven {verkoop_boven:.0f})",
                    )
                )
            elif dagen_vast >= max_dagen:
                orders.append(
                    Order(
                        symbool=symbool,
                        actie="VERKOOP",
                        reden=f"{dagen_vast} beursdagen vastgehouden, de grens is {max_dagen}",
                    )
                )

        # Kopen: fors gedaald. De laagste RSI eerst, want die is het meest gedaald.
        kandidaten: list[tuple[str, float]] = []
        for symbool in symbolen:
            if symbool in posities:
                continue
            if markt.bar(symbool, datum) is None:
                continue
            rsi_nu = markt.kengetal(symbool, datum, veld)
            if rsi_nu is None or rsi_nu >= koop_onder:
                continue
            kandidaten.append((symbool, rsi_nu))

        kandidaten.sort(key=lambda x: x[1])
        for symbool, rsi_nu in kandidaten:
            orders.append(
                Order(
                    symbool=symbool,
                    actie="KOOP",
                    reden=f"RSI {rsi_nu:.0f}, onder de grens van {koop_onder:.0f}",
                )
            )

        return orders
