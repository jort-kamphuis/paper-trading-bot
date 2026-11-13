"""Zijn de koersen compleet genoeg om op te handelen?

Ontbreekt er een symbool, of mist een aandeel de koers van een beursdag, dan
zou de bot stilletjes op een gat handelen. Momentum maakt dan een ranglijst
zonder dat symbool, en een positie erin wordt op de inkoopprijs gewaardeerd in
plaats van op de echte koers. Dat zijn fouten die je achteraf niet meer ziet.

Daarom: liever een nacht niet handelen. De motor haalt een gemiste dag de
volgende nacht vanzelf in, met complete koersen.
"""

from __future__ import annotations

from bot import config as conf
from bot.markt import Markt


def _verdachte_sprong(markt: Markt, symbool: str, dag: str) -> str | None:
    """Een aandeel dat in een dag halveert of verdubbelt, zonder splitsing: dat klopt niet.

    Zo'n sprong is bij grote aandelen en indexfondsen bijna altijd een fout in de
    data, of een splitsing die (nog) niet in de data staat. Daar handelen we niet op.
    """
    nu = markt.bar(symbool, dag)
    if nu is None:
        return None
    gisteren = markt.slot_op_of_voor(symbool, _dag_ervoor(dag))
    if not gisteren or gisteren <= 0:
        return None
    verhouding = nu.slot / gisteren
    if (verhouding < 0.5 or verhouding > 2.0) and not nu.splitsing:
        return (
            f"{symbool}: koers van {gisteren:.2f} naar {nu.slot:.2f} op {dag} zonder splitsing "
            f"in de data; verdacht"
        )
    return None


def _dag_ervoor(dag: str) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(dag) - timedelta(days=1)).isoformat()


def controleer(markt: Markt, cfg: dict, dagen: list[str]) -> list[str]:
    """Geef een lijst met problemen; leeg betekent: compleet."""
    problemen: list[str] = []
    aandelen = conf.aandelen_symbolen(cfg)
    crypto = conf.crypto_symbolen(cfg)

    for symbool in aandelen + crypto:
        if not markt.heeft(symbool):
            problemen.append(f"{symbool}: helemaal geen koersen")

    if not dagen:
        return problemen

    kalender = cfg["rapport"]["kalender_symbool"]
    beursdagen = set(markt.handelsdagen_van(kalender))
    cryptodagen: set[str] = set()
    for symbool in crypto:
        cryptodagen.update(markt.handelsdagen_van(symbool))

    for dag in dagen:
        if dag in beursdagen:
            for symbool in aandelen:
                if markt.heeft(symbool) and markt.bar(symbool, dag) is None:
                    problemen.append(f"{symbool}: geen koers op {dag}, terwijl {kalender} die dag wel handelde")
                    continue
                sprong = _verdachte_sprong(markt, symbool, dag)
                if sprong:
                    problemen.append(sprong)
        if dag in cryptodagen:
            for symbool in crypto:
                if markt.heeft(symbool) and markt.bar(symbool, dag) is None:
                    problemen.append(f"{symbool}: geen koers op {dag}")
    return problemen
