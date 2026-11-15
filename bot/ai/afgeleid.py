"""Afgeleide AI-portefeuilles: ze handelen op kansen die al gemaakt zijn, zonder de API.

Elke nacht geeft de AI per symbool een kans. De gewone AI-portefeuilles kopen
alleen bij een kans van 0,55 of meer; in de eerste proefnacht haalde geen enkel
symbool dat. Deze portefeuilles gebruiken dezelfde kansen anders. Ze kosten niets
extra: ze lezen wat er die nacht al in de tabel ai_besluiten staat.

* rangorde: altijd belegd in de 10 symbolen met de grootste voorsprong (kans min
  de basiskans van dat symbool), ook als die voorsprong nul of negatief is. Dit is
  de portefeuille van de hoofdvraag: zit er informatie in de RANGORDE van de AI?
* rangorde_omgekeerd: precies het spiegelbeeld, de 10 met de kleinste voorsprong.
  De controle: als de rangorde iets betekent, hoort deze het slechter te doen.
* consensus: koopt alleen wat alle bronnen (debat, enkele aanroep, Haiku) boven de
  basiskans zetten. Niet altijd belegd.

Voor alle drie: 10% per positie, hooguit 10 posities, hooguit 2 crypto, en wisselen
alleen na de houdtijd van 14 kalenderdagen. Staat een positie dan nog in de
doelgroep, dan blijft hij (herbevestigd); anders gaat hij eruit.

Bij een gelijke voorsprong (het model geeft kansen in stappen van 0,01, dus dat
komt vaak voor) beslist de verwachting gedeeld door de dagschommeling, en daarna
de naam van het symbool. Zo is de keuze altijd dezelfde bij dezelfde kansen. Die
tweede sleutel is ook een signaal van de AI: de verwachting komt uit hetzelfde
antwoord als de kans; alleen de dagschommeling rekent de code zelf uit.

In een nacht waarin een bron niet alles kon beoordelen, wordt er niet gekocht:
dan zou de rangorde scheef zijn naar wat toevallig wel beoordeeld is.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from bot.ai import beslisregel
from bot.ai.analist import Beoordeling
from bot.ai.kostenprognose import GEEN_KOERS
from bot.config import is_crypto
from bot.markt import Markt
from bot.portefeuille import Portefeuille, Positie
from bot.strategieen.basis import Order
from bot.tekst import getal

RANGORDE = "rangorde"
OMGEKEERD = "rangorde_omgekeerd"
CONSENSUS = "consensus"
SOORTEN = (RANGORDE, OMGEKEERD, CONSENSUS)


def afgeleide_portefeuilles(cfg: dict) -> list[dict]:
    """De afgeleide portefeuilles uit de instellingen: naam, soort en de bron(nen) van de kansen."""
    if not cfg.get("ai", {}).get("actief"):
        return []
    uit = []
    for p in cfg["ai"].get("afgeleid") or []:
        soort = str(p["soort"])
        if soort not in SOORTEN:
            raise ValueError(f"onbekende soort afgeleide portefeuille: {soort}")
        bronnen = [str(b) for b in (p.get("bronnen") or [p["bron"]])]
        uit.append({"naam": str(p["naam"]), "soort": soort, "bronnen": bronnen})
    return uit


@dataclass
class Uitkomst:
    portefeuille: str = ""
    orders: list[Order] = field(default_factory=list)
    herbevestigd: list[str] = field(default_factory=list)
    compleet: bool = True
    overgeslagen: str | None = None


def voorsprong(b: Beoordeling, regels: dict, basiskansen: dict | None) -> float:
    """De kans min de basiskans van dat symbool (de basiskans die het model zelf zag)."""
    return float(b.kans) - beslisregel.verkoopgrens(b, regels, basiskansen)


def rangschik(
    beoordelingen: dict[str, Beoordeling], regels: dict, basiskansen: dict | None, omgekeerd: bool = False
) -> list[tuple[str, float]]:
    """Alle bruikbare symbolen op volgorde: de grootste voorsprong eerst (of de kleinste, omgekeerd)."""
    teken = -1.0 if omgekeerd else 1.0
    rijen = [
        (s, round(voorsprong(b, regels, basiskansen), 6), beslisregel._verwacht_per_schommeling(b))
        for s, b in beoordelingen.items() if b.bruikbaar
    ]
    rijen.sort(key=lambda r: (-teken * r[1], -teken * r[2], r[0]))
    return [(s, v) for s, v, _ in rijen]


def _doelgroep(volgorde: list[str], max_posities: int, max_crypto: int | None) -> list[str]:
    """De eerste 'max_posities' van de volgorde, met hooguit 'max_crypto' cryptosymbolen."""
    uit: list[str] = []
    crypto = 0
    for s in volgorde:
        if len(uit) >= max_posities:
            break
        if is_crypto(s):
            if max_crypto is not None and crypto >= int(max_crypto):
                continue
            crypto += 1
        uit.append(s)
    return uit


def _houdtijd_om(positie: Positie, datum: str, dagen: int) -> bool:
    sinds = positie.bevestigd_op or positie.gekocht_op
    return (date.fromisoformat(datum) - date.fromisoformat(sinds)).days >= dagen


def _wissel(
    volgorde: list[str],
    redenen: dict[str, str],
    posities: dict[str, Positie],
    wachtende_kopen: set[str],
    wachtende_verkopen: set[str],
    datum: str,
    regels: dict,
    max_posities: int,
    kopen_toegestaan: bool,
    altijd_vol: bool,
) -> Uitkomst:
    """Het gedeelde deel: verkopen na de houdtijd, en vrije plekken vullen in de volgorde."""
    uit = Uitkomst()
    houdtijd = int(regels["houdtijd_kalenderdagen"])
    max_crypto = regels.get("max_crypto")
    doel = set(_doelgroep(volgorde, max_posities, max_crypto))

    verkocht: set[str] = set()
    for symbool, positie in posities.items():
        if symbool in wachtende_verkopen or not _houdtijd_om(positie, datum, houdtijd):
            continue
        if symbool in doel:
            uit.herbevestigd.append(symbool)
        else:
            waarom = "staat niet meer in de doelgroep" if symbool in redenen else "geen bruikbare beoordeling"
            uit.orders.append(Order(symbool=symbool, actie="VERKOOP",
                                    reden=f"houdtijd van {houdtijd} dagen om en {waarom}"))
            verkocht.add(symbool)

    if not kopen_toegestaan:
        return uit

    bezet = (set(posities) - wachtende_verkopen - verkocht) | wachtende_kopen
    vrij = max(0, max_posities - len(bezet))
    crypto = sum(1 for s in bezet if is_crypto(s))
    for symbool in volgorde:
        if vrij <= 0:
            break
        if symbool in bezet or symbool in verkocht:
            continue
        if not altijd_vol and symbool not in doel:
            continue
        if is_crypto(symbool):
            if max_crypto is not None and crypto >= int(max_crypto):
                continue
            crypto += 1
        uit.orders.append(Order(symbool=symbool, actie="KOOP", reden=redenen[symbool]))
        vrij -= 1
    return uit


def kies_rangorde(
    beoordelingen: dict[str, Beoordeling],
    posities: dict[str, Positie],
    wachtende_kopen: set[str],
    wachtende_verkopen: set[str],
    datum: str,
    regels: dict,
    max_posities: int,
    basiskansen: dict | None,
    omgekeerd: bool = False,
    kopen_toegestaan: bool = True,
) -> Uitkomst:
    """Altijd belegd in de top van de rangorde; wisselen alleen na de houdtijd."""
    rang = rangschik(beoordelingen, regels, basiskansen, omgekeerd=omgekeerd)
    volgorde = [s for s, _ in rang]
    soort = "omgekeerde rangorde" if omgekeerd else "rangorde"
    redenen = {}
    for plek, (s, v) in enumerate(rang, 1):
        b = beoordelingen[s]
        basis = beslisregel.verkoopgrens(b, regels, basiskansen)
        redenen[s] = (
            f"{soort}: plek {plek} van {len(rang)}, kans {getal(b.kans, 2)} tegen een basiskans van "
            f"{getal(basis, 2)} (verschil {getal(v, 2, teken=True)})"
        )
    return _wissel(volgorde, redenen, posities, wachtende_kopen, wachtende_verkopen, datum, regels,
                   max_posities, kopen_toegestaan, altijd_vol=True)


def kies_consensus(
    per_bron: dict[str, dict[str, Beoordeling]],
    posities: dict[str, Positie],
    wachtende_kopen: set[str],
    wachtende_verkopen: set[str],
    datum: str,
    regels: dict,
    max_posities: int,
    basiskansen: dict | None,
    kopen_toegestaan: bool = True,
) -> Uitkomst:
    """Alleen kopen wat ELKE bron boven de basiskans zet; de sterkste gemiddelde voorsprong eerst."""
    bronnen = list(per_bron)
    kandidaten: list[tuple[str, float]] = []
    for symbool in sorted(set().union(*[set(d) for d in per_bron.values()]) if per_bron else set()):
        verschillen = []
        for bron in bronnen:
            b = per_bron[bron].get(symbool)
            if b is None or not b.bruikbaar:
                break
            v = voorsprong(b, regels, basiskansen)
            if v <= 1e-9:
                break
            verschillen.append(v)
        else:
            kandidaten.append((symbool, sum(verschillen) / len(verschillen)))
    kandidaten.sort(key=lambda r: (-round(r[1], 6), r[0]))
    volgorde = [s for s, _ in kandidaten]
    redenen = {
        s: f"consensus: alle {len(bronnen)} bronnen boven de basiskans, gemiddeld {getal(v, 2, teken=True)}"
        for s, v in kandidaten
    }
    return _wissel(volgorde, redenen, posities, wachtende_kopen, wachtende_verkopen, datum, regels,
                   max_posities, kopen_toegestaan, altijd_vol=False)


# ------------------------------------------------------------------ de ronde


def _lees_bron(con: sqlite3.Connection, bron: str, datum: str, symbolen: list[str]) -> tuple[dict[str, Beoordeling], bool]:
    """De beoordelingen van een bron voor deze dag, en of die bron compleet was."""
    from bot.ai.ronde import _lees_beoordeling

    uit: dict[str, Beoordeling] = {}
    compleet = True
    for symbool in symbolen:
        b = _lees_beoordeling(con, bron, symbool, datum)
        if b is None:
            compleet = False
            continue
        if b.probleem == GEEN_KOERS:
            continue  # geen koers die dag: geen beoordeling nodig, wel compleet
        if not b.bruikbaar:
            compleet = False
            continue
        uit[symbool] = b
    return uit, compleet


def werk_bij(
    con: sqlite3.Connection, cfg: dict, markt: Markt, afgeleide: dict, datum: str, symbolen: list[str],
    afbouwen: bool = False,
) -> Uitkomst:
    """Laat een afgeleide portefeuille kiezen op de kansen van deze dag. Roept de API niet aan.

    'afbouwen': de meting is voorbij en de AI beoordeelt niets meer. Alleen dan
    worden posities zonder beoordeling verkocht zodra hun houdtijd om is. Tijdens
    de meting betekent een nacht zonder beoordelingen (tegoed op, storing) dat er
    niets verandert: anders zou de portefeuille van de hoofdvraag leeglopen.
    """
    naam = afgeleide["naam"]
    kalender = (cfg.get("rapport") or {}).get("kalender_symbool", "SPY")
    if markt.heeft(kalender) and not markt.is_handelsdag(kalender, datum):
        return Uitkomst(portefeuille=naam, overgeslagen=f"{datum} was geen beursdag")
    pf = Portefeuille.uit_database(con, naam, cfg["algemeen"])
    if pf is None:
        return Uitkomst(portefeuille=naam, overgeslagen=f"portefeuille {naam} bestaat nog niet")

    regels = cfg["ai"]["beslisregel"]
    basiskansen = cfg["ai"].get("basiskansen")
    per_bron: dict[str, dict[str, Beoordeling]] = {}
    compleet = True
    for bron in afgeleide["bronnen"]:
        per_bron[bron], ok = _lees_bron(con, bron, datum, symbolen)
        compleet = compleet and ok and bool(per_bron[bron])

    if not compleet and (any(per_bron.values()) or not afbouwen):
        # Een deel is beoordeeld en een deel niet (dan is de rangorde scheef), of er is
        # tijdens de meting niets beoordeeld: vannacht niets doen. Alleen na de
        # einddatum lopen de posities hieronder af na hun houdtijd.
        return Uitkomst(portefeuille=naam, compleet=False,
                        overgeslagen="niet alle symbolen zijn beoordeeld; vannacht geen wijzigingen")

    wachtend = con.execute("SELECT symbool, actie FROM wachtrij WHERE portefeuille = ?", (naam,)).fetchall()
    gedeeld = dict(
        posities=pf.posities,
        wachtende_kopen={r["symbool"] for r in wachtend if r["actie"] == "KOOP"},
        wachtende_verkopen={r["symbool"] for r in wachtend if r["actie"] == "VERKOOP"},
        datum=datum, regels=regels, max_posities=pf.max_posities or 10,
        basiskansen=basiskansen, kopen_toegestaan=compleet,
    )
    if afgeleide["soort"] == CONSENSUS:
        keuze = kies_consensus(per_bron, **gedeeld)
    else:
        keuze = kies_rangorde(per_bron[afgeleide["bronnen"][0]], omgekeerd=afgeleide["soort"] == OMGEKEERD, **gedeeld)

    for order in keuze.orders:
        con.execute(
            """INSERT INTO wachtrij (portefeuille, symbool, actie, reden, zekerheid, besluit_datum)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(portefeuille, symbool, actie, besluit_datum) DO NOTHING""",
            (naam, order.symbool, order.actie, order.reden[:1000], order.zekerheid, datum),
        )
    for symbool in keuze.herbevestigd:
        con.execute("UPDATE posities SET bevestigd_op=? WHERE portefeuille=? AND symbool=?", (datum, naam, symbool))
    con.commit()
    keuze.portefeuille = naam
    keuze.compleet = compleet
    return keuze
