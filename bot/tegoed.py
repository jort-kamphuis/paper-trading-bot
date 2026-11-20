"""Het gratis API-tegoed: wat de bot ervan weet, en wat dat betekent voor het budget.

De bot kan het saldo NIET zelf opvragen: de API heeft daar geen ingang voor. Wat
hier staat, komt uit config.yaml (onder 'tegoed'), met de hand overgenomen uit de
console van Anthropic: per tegoed het bedrag, de dag van toekenning en de dag
waarop het verloopt (00:00 UTC op die dag; op die dag zelf is het dus niet meer
geldig).

Twee dingen hangen ervan af:

1. Het budget. Zolang er volgens config.yaml geldig gratis tegoed is, geldt het
   maandbudget (ai.budget.max_usd_per_maand). Is het laatste bekende tegoed
   verlopen en is er geen nieuw ingevoerd, dan geldt
   ai.budget.zonder_gratis_tegoed_usd, als dat lager is. Wat in dezelfde maand al
   is uitgegeven op dagen dat het tegoed nog geldig was, telt daar niet in mee: dat
   was gratis. Portefeuilles met 'alleen_met_gratis_tegoed' doen dan niet meer mee;
   ai_analist, die de kansen voor de hoofdvraag levert, loopt door.
   Sinds 18 november 2026 zijn beide bedragen gelijk ($35) en heeft geen enkele
   portefeuille 'alleen_met_gratis_tegoed' (ai_opus zit niet in de echte meting).
   Het verlopen van het tegoed verandert dan niets aan de ronde; de melding blijft,
   want vanaf dat moment betaalt de gebruiker zelf.

2. Het weekrapport laat zien welk tegoed er bekend is, wanneer het verloopt en wat
   de bot sindsdien heeft uitgegeven. Dat is een schatting: de bot telt alleen wat
   hij zelf in deze database heeft geboekt.

Staat er in de instellingen helemaal geen 'tegoed', dan gebeurt hier niets: het
budget is dan gewoon het maandbudget.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Tegoed:
    bedrag_usd: float
    toegekend: date
    verloopt: date  # 00:00 UTC op deze dag: de laatste geldige dag is de dag ervoor

    def geldig_op(self, dag: date) -> bool:
        return self.toegekend <= dag < self.verloopt


def ingesteld(cfg: dict) -> bool:
    """Staat er een lijst met gratis tegoed in de instellingen (ook als die leeg is)?"""
    return isinstance((cfg.get("tegoed") or {}).get("gratis"), list)


def lijst(cfg: dict) -> list[Tegoed]:
    """De bekende tegoeden, op volgorde van verlopen."""
    uit = [
        Tegoed(float(t["bedrag_usd"]), date.fromisoformat(str(t["toegekend"])), date.fromisoformat(str(t["verloopt"])))
        for t in ((cfg.get("tegoed") or {}).get("gratis") or [])
    ]
    return sorted(uit, key=lambda t: (t.verloopt, t.toegekend))


def is_geldig(cfg: dict, dag: date) -> bool:
    """Is er op deze dag gratis tegoed? Zonder 'tegoed' in de instellingen: ja (dan speelt dit niet)."""
    if not ingesteld(cfg):
        return True
    return any(t.geldig_op(dag) for t in lijst(cfg))


def laatste_verloop(cfg: dict) -> date | None:
    alle = lijst(cfg)
    return max(t.verloopt for t in alle) if alle else None


def terugval_usd(cfg: dict) -> float:
    budget = cfg["ai"]["budget"]
    return float(budget.get("zonder_gratis_tegoed_usd", budget["max_usd_per_maand"]))


def budget_plafond(con: sqlite3.Connection, cfg: dict, dag: date) -> float:
    """Het maandbudget dat op deze dag geldt.

    Met geldig gratis tegoed: het gewone maandbudget. Zonder: het lagere bedrag, plus
    wat deze maand al is uitgegeven op dagen dat het tegoed nog geldig was. Zo blijft
    wat de gebruiker zelf betaalt onder het lagere bedrag, en raakt het budget niet in
    een klap 'op' doordat het gratis deel van de maand al meer kostte. Het wordt nooit
    meer dan het gewone maandbudget: zijn beide bedragen gelijk, dan is het plafond
    altijd dat ene bedrag.
    """
    gewoon = float(cfg["ai"]["budget"]["max_usd_per_maand"])
    if is_geldig(cfg, dag):
        return gewoon
    laag = min(gewoon, terugval_usd(cfg))
    maand = dag.strftime("%Y-%m")
    gratis = 0.0
    alle = lijst(cfg)
    for rij in con.execute(
        "SELECT datum, COALESCE(SUM(kosten_usd), 0) s FROM ai_kosten WHERE maand = ? GROUP BY datum", (maand,)
    ):
        try:
            geboekt = date.fromisoformat(str(rij["datum"])[:10])
        except ValueError:
            continue
        if any(t.geldig_op(geboekt) for t in alle):
            gratis += float(rij["s"])
    return min(gewoon, laag + gratis)


def afhankelijk(cfg: dict) -> list[str]:
    """De AI-portefeuilles die alleen draaien zolang er gratis tegoed is."""
    return [
        str(p["naam"]) for p in ((cfg.get("ai") or {}).get("portefeuilles") or [])
        if isinstance(p, dict) and p.get("alleen_met_gratis_tegoed")
    ]


def _gevolg(cfg: dict, nu: bool) -> str:
    """Wat er zonder gratis tegoed verandert: een hele zin, met hoofdletter en zonder punt.

    'nu': het is al zover (tegenwoordige tijd); anders: het komt eraan.
    """
    from bot.tekst import dollar

    laag, gewoon = terugval_usd(cfg), float(cfg["ai"]["budget"]["max_usd_per_maand"])
    namen = afhankelijk(cfg)
    if laag < gewoon:
        zin = (f"Het AI-budget is teruggevallen van {dollar(gewoon)} naar {dollar(laag)} per maand" if nu
               else f"Het AI-budget valt dan terug van {dollar(gewoon)} naar {dollar(laag)} per maand")
    else:
        zin = (f"Het AI-budget blijft {dollar(gewoon)} per maand" if nu
               else f"Het AI-budget blijft dan {dollar(gewoon)} per maand")
    if namen:
        lijst_ = ", ".join(namen)
        zin += (f": {lijst_} doet niet meer mee, ai_analist loopt door" if nu
                else f" ({lijst_} stopt, ai_analist loopt door)")
    elif laag >= gewoon:
        zin += " en aan de meting verandert niets"
    return zin


def mag_draaien(cfg: dict, portefeuille: dict, dag: date) -> bool:
    """Mag deze AI-portefeuille vandaag beoordelen? Alleen 'alleen_met_gratis_tegoed' hangt van het tegoed af."""
    return not portefeuille.get("alleen_met_gratis_tegoed") or is_geldig(cfg, dag)


def _datum(d: date) -> str:
    from bot.tekst import datum_lang

    return datum_lang(d.isoformat())


def uitgegeven_sinds(con: sqlite3.Connection, van: date, tot: date) -> float:
    return float(con.execute(
        "SELECT COALESCE(SUM(kosten_usd), 0) s FROM ai_kosten WHERE datum >= ? AND datum < ?",
        (van.isoformat(), tot.isoformat()),
    ).fetchone()["s"])


def als_tekst(con: sqlite3.Connection, cfg: dict, vandaag: date) -> str:
    """De regels voor het weekrapport: welk gratis tegoed is er, tot wanneer, en wat is er naar schatting over."""
    from bot.tekst import dollar

    if not ingesteld(cfg):
        return ""
    alle = lijst(cfg)
    geldig = [t for t in alle if t.geldig_op(vandaag)]
    kop = "Gratis API-tegoed (uit config.yaml; de bot kan het saldo niet zelf opvragen):"
    if not geldig:
        laatste = laatste_verloop(cfg)
        return "\n".join([
            kop,
            ("  Er is geen geldig gratis tegoed bekend"
             + (f": het laatste verliep op {_datum(laatste)}." if laatste else ".")),
            f"  Het AI-budget is {dollar(budget_plafond(con, cfg, vandaag))} voor deze maand "
            f"({dollar(terugval_usd(cfg))} per maand zonder gratis tegoed)"
            + (f" en {', '.join(afhankelijk(cfg))} doet niet mee" if afhankelijk(cfg) else "")
            + ". Je betaalt de AI-kosten nu zelf. "
            "Staat er in de console wel nieuw tegoed, laat het dan in config.yaml zetten.",
        ])
    regels = [kop]
    vorige_verloop: date | None = None
    for t in alle:
        if t.geldig_op(vandaag):
            # Het vorige tegoed gaat eerst op; dit tegoed telt vanaf zijn toekenning of vanaf
            # het moment dat het vorige verliep, wat het laatst is.
            vanaf = max(t.toegekend, vorige_verloop) if vorige_verloop else t.toegekend
            gebruikt = uitgegeven_sinds(con, vanaf, vandaag + timedelta(days=1))
            over = max(0.0, t.bedrag_usd - gebruikt)
            dagen = (t.verloopt - vandaag).days
            regels.append(
                f"  {dollar(t.bedrag_usd)}, toegekend op {_datum(t.toegekend)}, verloopt op {_datum(t.verloopt)} "
                f"om 00:00 UTC (nog {dagen} dag{'en' if dagen != 1 else ''}). Sinds {_datum(vanaf)} heeft de bot "
                f"{dollar(gebruikt)} geboekt in deze database; naar schatting is er nog ongeveer {dollar(over)} over."
            )
        vorige_verloop = t.verloopt
    regels.append(
        "  Kijk rond de vervaldatum in de console of er nieuw tegoed is bijgekomen. Zo ja: laat het in "
        f"config.yaml zetten. Zo nee: dan betaal je vanaf die dag zelf. {_gevolg(cfg, nu=False)}."
    )
    return "\n".join(regels)


def melding(con: sqlite3.Connection, cfg: dict, vandaag: date) -> str | None:
    """Een bericht voor Telegram, hooguit een keer per gebeurtenis:

    * een paar dagen voor het laatste bekende tegoed verloopt (tegoed.waarschuw_dagen_vooraf);
    * zodra het verlopen is en er geen nieuw tegoed is ingevoerd: het budget is teruggevallen.
    """
    from bot import database
    from bot.tekst import dollar

    if not ingesteld(cfg):
        return None
    laatste = laatste_verloop(cfg)
    if laatste is None:
        return None
    if not is_geldig(cfg, vandaag):
        sleutel = f"tegoed_verlopen_{laatste.isoformat()}"
        if database.lees_instelling(con, sleutel) is not None:
            return None
        database.zet_instelling(con, sleutel, "gemeld")
        return (
            f"LET OP: het gratis API-tegoed is volgens config.yaml verlopen op {_datum(laatste)} en er is geen "
            f"nieuw tegoed ingevoerd. {_gevolg(cfg, nu=True)}. Staat er in de console wel nieuw "
            f"tegoed, laat het dan in config.yaml zetten. Staat het er niet, dan betaal je vanaf nu zelf: zorg "
            f"dat er credits zijn, met automatisch opwaarderen uit."
        )
    dagen_vooraf = int((cfg.get("tegoed") or {}).get("waarschuw_dagen_vooraf", 5))
    if (laatste - vandaag).days <= dagen_vooraf:
        sleutel = f"tegoed_verloopt_{laatste.isoformat()}"
        if database.lees_instelling(con, sleutel) is not None:
            return None
        database.zet_instelling(con, sleutel, "gemeld")
        return (
            f"Herinnering: het gratis API-tegoed verloopt op {_datum(laatste)} om 00:00 UTC. Kijk in de console of "
            f"er nieuw tegoed is bijgekomen. Zo ja: laat het in config.yaml zetten, dan blijft alles zoals het is. "
            f"Zo nee: credits kopen is pas nodig voor die datum. {_gevolg(cfg, nu=False)}."
        )
    return None
