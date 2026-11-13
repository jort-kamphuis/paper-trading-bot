"""Mean reversion: koop een korte dip in een stijgende markt.

Dit is de veelgebruikte variant van Larry Connors, met zijn gangbare
instellingen (niet afgesteld op onze backtest):
* kopen als de RSI over 2 dagen onder 10 zakt (een korte, felle dip),
  maar alleen als de koers boven het 200-daags gemiddelde staat (de grote
  trend is omhoog);
* verkopen zodra de koers boven het 5-daags gemiddelde sluit (de dip is voorbij),
  of na 14 kalenderdagen.

Extra: hooguit 2 cryptoposities tegelijk. Cryptomunten bewegen vaak samen, dus
meer zou vooral een grote gok op een richting zijn.
"""

from __future__ import annotations

from datetime import date

from bot.config import is_crypto
from bot.markt import Markt
from bot.portefeuille import Positie
from bot.strategieen.basis import Order, Strategie
from bot.tekst import getal


class MeanReversion(Strategie):
    naam = "mean_reversion"

    def __init__(self, instellingen: dict):
        super().__init__(instellingen)
        self.max_posities = int(self.instellingen.get("aantal_posities", 10))

    def besluit(
        self, datum: str, markt: Markt, symbolen: list[str], posities: dict[str, Positie]
    ) -> list[Order]:
        rsi_naam = f"rsi{int(self.instellingen['rsi_periode'])}"
        koop_onder = float(self.instellingen["koop_onder"])
        trend_naam = f"ma{int(self.instellingen['trendfilter_ma'])}"
        uit_naam = f"ma{int(self.instellingen['verkoop_boven_ma'])}"
        max_dagen = int(self.instellingen["max_kalenderdagen"])
        max_crypto = int(self.instellingen["max_crypto"])
        dag = self.dag
        wacht_verkoop = dag.wachtende_verkopen if dag else set()

        orders: list[Order] = []
        verkocht: set[str] = set()
        for symbool, positie in posities.items():
            if symbool in wacht_verkoop:
                continue
            slot = markt.slot_op_of_voor(symbool, datum)
            ma_uit = markt.kengetal(symbool, datum, uit_naam)
            dagen = (date.fromisoformat(datum) - date.fromisoformat(positie.gekocht_op)).days
            reden = None
            if slot is not None and ma_uit is not None and slot > ma_uit:
                reden = f"koers {getal(slot, 2)} boven het {uit_naam[2:]}-daags gemiddelde: dip voorbij"
            elif dagen >= max_dagen:
                reden = f"{dagen} kalenderdagen vastgehouden, de grens is {max_dagen}"
            if reden:
                orders.append(Order(symbool=symbool, actie="VERKOOP", reden=reden))
                verkocht.add(symbool)

        bezet = self.bezet(posities)
        crypto_bezet = sum(1 for s in bezet - verkocht if is_crypto(s))
        kandidaten: list[tuple[str, float]] = []
        for symbool in symbolen:
            if symbool in bezet or markt.bar(symbool, datum) is None:
                continue
            rsi = markt.kengetal(symbool, datum, rsi_naam)
            trend = markt.kengetal(symbool, datum, trend_naam)
            slot = markt.slot_op_of_voor(symbool, datum)
            if rsi is None or trend is None or slot is None:
                continue
            if rsi < koop_onder and slot > trend:
                kandidaten.append((symbool, rsi))

        kandidaten.sort(key=lambda x: x[1])  # de felste dip eerst
        vrij = self.vrije_plekken(posities, na_verkopen=verkocht)
        for symbool, rsi in kandidaten:
            if vrij <= 0:
                break
            if is_crypto(symbool):
                if crypto_bezet >= max_crypto:
                    continue
                crypto_bezet += 1
            orders.append(
                Order(
                    symbool=symbool,
                    actie="KOOP",
                    reden=f"RSI({rsi_naam[3:]}) {getal(rsi, 0)} onder {getal(koop_onder, 0)}, boven het {trend_naam[2:]}-daags gemiddelde",
                )
            )
            vrij -= 1
        return orders
