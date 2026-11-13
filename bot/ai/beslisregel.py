"""De beslisregel van de AI-portefeuilles. Dit is code, geen model.

Vastgelegd voor de start van de meting (config.yaml, ai.beslisregel):

Verkopen
* als de kans onder 0,50 zakt;
* of als de houdtijd (14 kalenderdagen sinds de aankoop of de laatste
  herbevestiging) om is, tenzij het symbool opnieuw aan de koopregel voldoet.
  Dan telt dat als herbevestiging en begint de houdtijd opnieuw;
* en ook zonder nieuwe beoordeling (budget op, fout): na de houdtijd verkopen.

Kopen
* alleen bij een kans van 0,55 of meer EN een verwacht extra rendement van
  0,5% of meer (ruim boven de kosten van ongeveer 0,3% heen en terug);
* eerst alle symbolen beoordelen, dan de beste kiezen: gerangschikt op de kans,
  dan op het verwachte extra rendement. Zo wint niet het symbool dat toevallig
  vooraan in de lijst staat. Op de kans en niet op de verwachting: bij SPY en BTC
  (tegen kas) zit in de verwachting de hele marktstijging, bij de rest alleen het
  verschil met SPY of BTC; de kans is bij beide vragen even groot te krijgen;
* niet als het beoordelen die nacht niet compleet was (budget op of fouten): dan
  zou de keuze scheef zijn naar de symbolen die wel beoordeeld zijn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from bot.ai.analist import Beoordeling
from bot.portefeuille import Positie
from bot.strategieen.basis import Order
from bot.tekst import getal


@dataclass
class Uitkomst:
    orders: list[Order] = field(default_factory=list)
    herbevestigd: list[str] = field(default_factory=list)


def _houdtijd_om(positie: Positie, datum: str, dagen: int) -> bool:
    sinds = positie.bevestigd_op or positie.gekocht_op
    return (date.fromisoformat(datum) - date.fromisoformat(sinds)).days >= dagen


def voldoet_aan_koopregel(b: Beoordeling | None, regels: dict) -> bool:
    return (
        b is not None and b.bruikbaar
        and b.kans >= float(regels["koop_kans"])
        and b.verwacht_pct >= float(regels["koop_verwacht_pct"])
    )


def kies(
    beoordelingen: dict[str, Beoordeling],
    posities: dict[str, Positie],
    wachtende_kopen: set[str],
    wachtende_verkopen: set[str],
    datum: str,
    regels: dict,
    max_posities: int,
    kopen_toegestaan: bool = True,
) -> Uitkomst:
    uit = Uitkomst()
    houdtijd = int(regels["houdtijd_kalenderdagen"])
    verkoop_onder = float(regels["verkoop_kans_onder"])

    # ---- verkopen ----
    verkocht: set[str] = set()
    for symbool, positie in posities.items():
        if symbool in wachtende_verkopen:
            continue
        b = beoordelingen.get(symbool)
        om = _houdtijd_om(positie, datum, houdtijd)
        reden = None
        if b is None or not b.bruikbaar:
            if om:
                reden = f"houdtijd van {houdtijd} dagen om, en geen nieuwe beoordeling"
        elif b.kans < verkoop_onder:
            reden = f"kans gezakt naar {getal(b.kans, 2)} (onder {getal(verkoop_onder, 2)}). {b.onderbouwing}"
        elif om:
            if voldoet_aan_koopregel(b, regels):
                uit.herbevestigd.append(symbool)
            else:
                reden = (
                    f"houdtijd van {houdtijd} dagen om en niet opnieuw bevestigd "
                    f"(kans {getal(b.kans, 2)}, verwacht {getal(b.verwacht_pct, 1, teken=True)}%)"
                )
        if reden:
            uit.orders.append(
                Order(symbool=symbool, actie="VERKOOP", reden=reden,
                      zekerheid=b.kans if b and b.bruikbaar else None)
            )
            verkocht.add(symbool)

    if not kopen_toegestaan:
        return uit

    # ---- kopen ----
    bezet = (set(posities) - wachtende_verkopen - verkocht) | wachtende_kopen
    vrij = max(0, max_posities - len(bezet))
    kandidaten = [
        b for s, b in beoordelingen.items()
        if s not in bezet and s not in verkocht and voldoet_aan_koopregel(b, regels)
    ]
    kandidaten.sort(key=lambda b: (b.kans, b.verwacht_pct), reverse=True)
    for b in kandidaten[:vrij]:
        uit.orders.append(
            Order(
                symbool=b.symbool,
                actie="KOOP",
                reden=f"kans {getal(b.kans, 2)}, verwacht {getal(b.verwacht_pct, 1, teken=True)}% tegenover {b.referentie}. {b.onderbouwing}",
                zekerheid=b.kans,
            )
        )
    return uit
