"""Trend volgen: twee gemiddelden die elkaar kruisen.

De gedachte: zolang het 20-daags gemiddelde boven het 100-daags gemiddelde
ligt, is de trend omhoog. Kopen op het moment dat dat gebeurt, verkopen als
het omdraait.
"""

from __future__ import annotations

from bot.markt import Markt
from bot.portefeuille import Positie
from bot.strategieen.basis import Order, Strategie


class TrendVolgend(Strategie):
    naam = "trend"

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        snel = int(self.instellingen["snelle_ma"])
        langzaam = int(self.instellingen["langzame_ma"])
        veld_snel, veld_langzaam = f"ma{snel}", f"ma{langzaam}"

        orders: list[Order] = []

        for symbool in symbolen:
            if markt.bar(symbool, datum) is None:
                continue
            ma_snel = markt.kengetal(symbool, datum, veld_snel)
            ma_langzaam = markt.kengetal(symbool, datum, veld_langzaam)
            if ma_snel is None or ma_langzaam is None:
                continue
            vorige_snel = markt.kengetal_vorige_dag(symbool, datum, veld_snel)
            vorige_langzaam = markt.kengetal_vorige_dag(symbool, datum, veld_langzaam)

            heeft = symbool in posities

            if not heeft:
                # Kopen op het moment van kruisen omhoog.
                if vorige_snel is None or vorige_langzaam is None:
                    continue
                if vorige_snel <= vorige_langzaam and ma_snel > ma_langzaam:
                    orders.append(
                        Order(
                            symbool=symbool,
                            actie="KOOP",
                            reden=(
                                f"{snel}-daags gemiddelde kruist omhoog door het "
                                f"{langzaam}-daags gemiddelde"
                            ),
                        )
                    )
            else:
                # Verkopen zodra de trend omdraait.
                if ma_snel < ma_langzaam:
                    orders.append(
                        Order(
                            symbool=symbool,
                            actie="VERKOOP",
                            reden=(
                                f"{snel}-daags gemiddelde is onder het "
                                f"{langzaam}-daags gemiddelde gezakt"
                            ),
                        )
                    )

        return orders
