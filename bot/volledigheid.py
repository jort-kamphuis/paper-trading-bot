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


def beurs_open_volgens_kalender(cfg: dict, dag: str) -> bool:
    """Was de NYSE op deze dag open? False voor weekend, feestdag of een jaar zonder kalender."""
    from datetime import date

    d = date.fromisoformat(dag)
    if d.weekday() >= 5:
        return False
    feestdagen = ((cfg.get("beurskalender") or {}).get("feestdagen") or {})
    lijst = feestdagen.get(d.year, feestdagen.get(str(d.year)))
    if lijst is None:
        return False  # geen kalender voor dit jaar: niet controleren
    return dag not in {str(x) for x in lijst}


def verdwenen_grens(cfg: dict) -> int:
    return int((cfg.get("beurskalender") or {}).get("verdwenen_na_beursdagen", 3))


def gemiste_beursdagen(markt: Markt, symbool: str, dag: str, kalender: str) -> int:
    """Hoeveel beursdagen (van de kalender-SPY) tot en met 'dag' achter elkaar zonder koers."""
    if not markt.heeft(symbool):
        return 0
    eigen = set(markt.handelsdagen_van(symbool))
    teller = 0
    for d in reversed([x for x in markt.handelsdagen_van(kalender) if x <= dag]):
        if d in eigen:
            break
        teller += 1
    return teller


def gat_lengte(markt: Markt, symbool: str, dag: str, kalender: str, tot: str | None = None) -> int:
    """Hoe lang het aaneengesloten gat is waar 'dag' in zit: beursdagen zonder koers, voor en na.

    'tot' is de laatste dag die meetelt (de laatste dag van de ronde); later telt niet.
    """
    if not markt.heeft(symbool):
        return 0
    eigen = set(markt.handelsdagen_van(symbool))
    beurs = markt.handelsdagen_van(kalender)
    if dag not in beurs or dag in eigen:
        return 0
    i = beurs.index(dag)
    lengte = 1
    j = i - 1
    while j >= 0 and beurs[j] not in eigen:
        lengte += 1
        j -= 1
    j = i + 1
    while j < len(beurs) and beurs[j] not in eigen and (tot is None or beurs[j] <= tot):
        lengte += 1
        j += 1
    return lengte


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

    verdwenen_na = verdwenen_grens(cfg)
    # Een dag zonder koers blokkeert niet als hij in een aaneengesloten gat van
    # zoveel beursdagen zit: dan is het aandeel 'verdwenen' en sluit de motor de
    # positie. Het hele gat telt, niet alleen tot de dag zelf of tot de laatste dag:
    # anders bleven de eerste gemiste dagen een probleem (review 3), of blokkeerde
    # een al verwerkte dag opnieuw zodra het aandeel terugkwam (eindreview).
    laatste = max(dagen)
    for dag in dagen:
        if beurs_open_volgens_kalender(cfg, dag) and dag not in beursdagen:
            problemen.append(
                f"{kalender}: geen koers op {dag}, terwijl de beurs volgens de NYSE-kalender open was"
            )
        if dag in beursdagen:
            for symbool in aandelen:
                if markt.heeft(symbool) and markt.bar(symbool, dag) is None:
                    if gat_lengte(markt, symbool, dag, kalender, tot=laatste) >= verdwenen_na:
                        continue  # verdwenen: de motor sluit de positie, de handel gaat door
                    problemen.append(f"{symbool}: geen koers op {dag}, terwijl {kalender} die dag wel handelde")
                    continue
                sprong = _verdachte_sprong(markt, symbool, dag)
                if sprong:
                    problemen.append(sprong)
        # Crypto handelt elke dag. Elke te verwerken dag moet dus een koers hebben
        # voor elk paar, ook als geen enkel paar die dag iets leverde (anders wordt
        # zo'n dag stil verwerkt op de koers van gisteren; review 3).
        for symbool in crypto:
            if markt.heeft(symbool) and markt.bar(symbool, dag) is None:
                problemen.append(f"{symbool}: geen koers op {dag}")
    return problemen
