"""De spelregels van de meting: de meetmomenten van de hoofdvraag en de regel voor 'winnaars'.

Vooraf vastgelegd (README, config.yaml onder 'meting'):

1. De hoofdvraag wordt op vier vaste momenten bekeken (na 3, 6, 9 en 12 maanden).
   Wie vier keer kijkt met telkens de grens van 0,05, heeft samen veel meer dan 5%
   kans op vals alarm. Daarom geldt per moment een strengere grens, volgens
   O'Brien-Fleming: vroeg heel streng, aan het eind bijna de gewone grens
   (0,00026 / 0,0071 / 0,0226 / 0,0415, eenzijdig). Samen is de kans op een
   onterecht "ja" dan ongeveer 5%; tests/test_meting.py controleert dat met een
   simulatie op de echte toets. Het eerste moment waarop de grens gehaald wordt,
   beslist: dan is het antwoord "ja" en dat blijft zo. Is na het laatste moment
   geen enkele grens gehaald, dan is het antwoord "nee".

2. Alle andere portefeuilles zijn verkennend. Met tientallen portefeuilles haalt er
   door toeval altijd wel een de grens van 0,05. Daarom telt een "winnaar" alleen
   als (a) de p-waarde in een periode onder 0,05 gedeeld door het aantal
   verkennende portefeuilles ligt, EN (b) dat in een latere, nieuwe periode nog een
   keer gebeurt. Een keer is "kandidaat", twee keer "winnaar". De periodes zijn de
   kwartalen tussen de meetmomenten.

3. Kas levert 0% op.

Dit bestand rekent alleen; het verandert niets aan een portefeuille.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from bot import rapport


def momenten(cfg: dict) -> list[dict]:
    """De meetmomenten: datum en de grens voor de p-waarde, op volgorde."""
    m = cfg.get("meting") or {}
    lijst = [
        {"datum": str(x["datum"]), "grens_p": float(x["grens_p"])}
        for x in (m.get("meetmomenten") or [])
    ]
    if not lijst and m.get("einde"):
        lijst = [{"datum": str(m["einde"]), "grens_p": 0.05}]
    return sorted(lijst, key=lambda x: x["datum"])


def periodes(cfg: dict) -> list[tuple[str, str]]:
    """De periodes tussen de start en de meetmomenten: (van, tot en met)."""
    m = cfg.get("meting") or {}
    van = str(m.get("start") or "")
    uit = []
    for moment in momenten(cfg):
        uit.append((van, moment["datum"]))
        van = moment["datum"]
    return uit


def hoofdvraag_stand(
    con: sqlite3.Connection, cfg: dict, pf: str, meetlat: str, laatste_data: str, kal: list[str]
) -> dict:
    """De stand van de hoofdvraag: per meetmoment de uitkomst, en het label.

    Een moment telt pas als de koers van die dag er is, en er wordt gerekend tot
    en met die dag, niet verder. Tussen de momenten blijft het label 'nog niet te
    zeggen', ook bij een lage p-waarde.
    """
    lijst = []
    label = None
    for nummer, moment in enumerate(momenten(cfg), 1):
        bereikt = bool(laatste_data) and laatste_data >= moment["datum"]
        item = {"nummer": nummer, "datum": moment["datum"], "grens_p": moment["grens_p"], "bereikt": bereikt,
                "verschil_pp": None, "p_waarde": None, "gehaald": None, "beslissend": False}
        if bereikt:
            v = rapport.vergelijk_even_belegd(con, pf, meetlat, moment["datum"], kal)
            if v is not None:
                item["verschil_pp"], item["p_waarde"] = v.verschil_pp, v.p_waarde
            item["gehaald"] = bool(
                v is not None and v.verschil_pp > 0 and v.p_waarde is not None and v.p_waarde <= moment["grens_p"]
            )
            if item["gehaald"] and label is None:
                label = "ja"
                item["beslissend"] = True
        lijst.append(item)
    if label is None:
        label = "nee" if lijst and all(x["bereikt"] for x in lijst) else "nog niet te zeggen"
    volgende = next((x["datum"] for x in lijst if not x["bereikt"]), None)
    return {"label": label, "momenten": lijst, "volgende": volgende if label != "ja" else None}


def verkennend(
    con: sqlite3.Connection, cfg: dict, tot: str, kal: list[str], hoofd: str, ai: set[str]
) -> dict:
    """De spelregel voor de verkennende portefeuilles: kandidaat of winnaar.

    Per periode de vergelijking met de eigen meetlat, even belegd. De grens is
    0,05 gedeeld door het aantal verkennende portefeuilles. Alleen afgeronde
    periodes tellen voor 'kandidaat' en 'winnaar'; de lopende periode is een tussenstand.
    """
    namen = [
        r["naam"] for r in con.execute("SELECT naam FROM portefeuilles WHERE actief = 1 ORDER BY naam")
        if r["naam"] not in rapport.MEETLATTEN and r["naam"] != hoofd
    ]
    bestaand = {r["naam"] for r in con.execute("SELECT naam FROM portefeuilles")}
    basis = float((cfg.get("meting") or {}).get("verkennend_p", 0.05))
    grens = basis / len(namen) if namen else None
    uit: dict = {"aantal": len(namen), "grens_p": grens, "portefeuilles": {}}
    for naam in namen:
        meetlat = rapport.eigen_meetlat(naam, ai, bestaand)
        per = []
        for van, einde in periodes(cfg):
            if not van or tot < van:
                continue
            af = tot >= einde
            v = rapport.vergelijk_even_belegd(con, naam, meetlat, min(tot, einde), kal, van=van) if meetlat else None
            gehaald = bool(
                v is not None and grens is not None and v.verschil_pp > 0
                and v.p_waarde is not None and v.p_waarde <= grens
            )
            per.append({"van": van, "tot": einde, "af": af,
                        "verschil_pp": v.verschil_pp if v else None, "p_waarde": v.p_waarde if v else None,
                        "gehaald": gehaald})
        keren = sum(1 for p in per if p["af"] and p["gehaald"])
        status = "winnaar" if keren >= 2 else "kandidaat" if keren == 1 else ""
        uit["portefeuilles"][naam] = {"meetlat": meetlat, "periodes": per, "status": status}
    return uit


def verkennend_tekst(v: dict) -> str:
    from bot.tekst import getal

    if not v["aantal"]:
        return ""
    regels = [
        f"Spelregels (verkennend): {v['aantal']} portefeuilles naast de hoofdvraag. Een winnaar telt alleen bij "
        f"een p-waarde van {getal(v['grens_p'], 4)} of lager (0,05 gedeeld door {v['aantal']}) in een afgeronde "
        f"periode, en nog een keer in een latere periode."
    ]
    winnaars = [n for n, p in v["portefeuilles"].items() if p["status"] == "winnaar"]
    kandidaten = [n for n, p in v["portefeuilles"].items() if p["status"] == "kandidaat"]
    regels.append("  Winnaars: " + (", ".join(winnaars) if winnaars else "geen") + ". Kandidaten (een keer gehaald): "
                  + (", ".join(kandidaten) if kandidaten else "geen") + ".")
    return "\n".join(regels)


# ------------------------------------------------- credits per kwartaal


def credits_herinnering(con: sqlite3.Connection, cfg: dict, vandaag: date) -> str | None:
    """Een herinnering om credits voor het volgende kwartaal te kopen, of None.

    De credits worden per kwartaal gekocht, met automatisch opwaarderen uit. In de
    twee weken voor een meetmoment (behalve het laatste) geeft dit een keer een
    bericht, met het bedrag voor het volgende kwartaal uit de kosten die tot nu
    toe echt gemeten zijn. Alleen in de echte meting. Het koopt niets.
    """
    from bot import database
    from bot.ai.kostenprognose import maak_prognose
    from bot.tekst import datum_lang, dollar, getal

    if cfg.get("modus") != "echt":
        return None
    m = cfg.get("meting") or {}
    dagen = int(m.get("credits_herinnering_dagen", 14))
    marge = float(m.get("credits_marge_pct", 20))
    lijst = momenten(cfg)
    for moment in lijst[:-1]:
        op = date.fromisoformat(moment["datum"])
        if not (op - timedelta(days=dagen) <= vandaag < op):
            continue
        sleutel = f"credits_herinnering_{moment['datum']}"
        if database.lees_instelling(con, sleutel) is not None:
            return None
        database.zet_instelling(con, sleutel, "verstuurd")
        tekst = (
            f"Herinnering: op {datum_lang(moment['datum'])} begint het volgende kwartaal van de meting. "
            f"Koop voor die tijd de credits voor dat kwartaal in de Anthropic-console, met automatisch "
            f"opwaarderen uit."
        )
        prognose = maak_prognose(con, cfg)
        if prognose.maand_usd is not None:
            kwartaal = 3.0 * prognose.maand_usd
            tekst += (
                f" Uit de gemeten kosten van {prognose.rondes} nacht(en): ongeveer "
                f"{dollar(prognose.maand_usd)} per maand, dus {dollar(kwartaal)} voor een kwartaal, "
                f"met {getal(marge, 0)}% marge {dollar(kwartaal * (1 + marge / 100.0))}. "
                f"Kijk in de console wat er nog over is en vul aan tot dat bedrag."
            )
        else:
            tekst += " Er zijn nog geen gemeten kosten om een bedrag uit te rekenen; kijk in de console."
        return tekst
    return None
