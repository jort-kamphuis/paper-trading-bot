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
        if dag in cryptodagen:
            for symbool in crypto:
                if markt.heeft(symbool) and markt.bar(symbool, dag) is None:
                    problemen.append(f"{symbool}: geen koers op {dag}")
    return problemen
