"""Wat kost de AI-analist echt, en past dat in een maand?

Dit rekent met wat er werkelijk is betaald, niet met een aanname. De basis is
de prijs per "symbool-dag": een symbool dat op een dag door de AI-analist is
beoordeeld. Aandelen en crypto houden we apart, want aandelen handelen ongeveer
21 dagen per maand en crypto elke dag.

Dit bestand verandert niets aan de instellingen. Past het niet, dan stelt het
aanpassingen voor die de hele maand gelijk blijven, met wat ze zouden schelen.
Kiezen doet de gebruiker.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from bot.config import is_crypto

# Gemiddeld aantal handelsdagen per maand. Benadering: de Amerikaanse beurs is
# ongeveer 252 dagen per jaar open, crypto elke dag.
BEURSDAGEN_PER_MAAND = 252 / 12
CRYPTODAGEN_PER_MAAND = 365.25 / 12
WEKEN_PER_MAAND = 365.25 / 12 / 7

# Zo heet een besluit waarvoor geen enkele API-aanroep nodig was.
GEEN_KOERS = "geen koers op deze dag"



@dataclass
class Prognose:
    rondes: int
    uitgegeven_usd: float
    per_portefeuille: dict[str, float] = field(default_factory=dict)
    maand_usd: float | None = None
    budget_usd: float = 0.0
    voorstellen: list[str] = field(default_factory=list)
    uitleg: list[str] = field(default_factory=list)

    @property
    def past(self) -> bool | None:
        if self.maand_usd is None:
            return None
        return self.maand_usd <= self.budget_usd


def _betaalde_besluiten(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT portefeuille, symbool, datum, kosten_usd, overgeslagen, stappen
           FROM ai_besluiten
           WHERE COALESCE(overgeslagen, '') != ?""",
        (GEEN_KOERS,),
    ).fetchall()


def _gemiddelde(waarden: list[float]) -> float | None:
    return sum(waarden) / len(waarden) if waarden else None


def maak_prognose(con: sqlite3.Connection, cfg: dict) -> Prognose:
    budget = float(cfg["ai"]["budget"]["max_usd_per_maand"])
    ai_lijst = list(cfg.get("ai_watchlist", []))

    rondes = con.execute("SELECT COUNT(DISTINCT datum) n FROM ai_besluiten").fetchone()["n"]
    uitgegeven = float(
        con.execute("SELECT COALESCE(SUM(kosten_usd), 0) s FROM ai_kosten").fetchone()["s"]
    )
    prognose = Prognose(rondes=rondes, uitgegeven_usd=uitgegeven, budget_usd=budget)

    besluiten = _betaalde_besluiten(con)
    if not besluiten:
        prognose.uitleg.append("Nog geen betaalde AI-besluiten; er valt nog niets te meten.")
        return prognose

    # ---- per portefeuille: wat kost een symbool-dag? ----
    per_pf: dict[str, dict[str, list[float]]] = {}
    for b in besluiten:
        soort = "crypto" if is_crypto(b["symbool"]) else "aandeel"
        per_pf.setdefault(b["portefeuille"], {"crypto": [], "aandeel": []})[soort].append(
            float(b["kosten_usd"] or 0.0)
        )

    # De AI beslist alleen op beursdagen, dus ook crypto telt ongeveer 21 dagen
    # per maand.
    totaal = 0.0
    for pf, soorten in sorted(per_pf.items()):
        alles = soorten["crypto"] + soorten["aandeel"]
        per_symbool_dag = _gemiddelde(alles) or 0.0
        maand = per_symbool_dag * len(ai_lijst) * BEURSDAGEN_PER_MAAND
        prognose.per_portefeuille[pf] = maand
        totaal += maand
        prognose.uitleg.append(
            f"{pf}: gemiddeld ${per_symbool_dag:.4f} per symbool-dag "
            f"({len(alles)} gemeten), naar schatting ${maand:.2f} per maand bij {len(ai_lijst)} symbolen"
        )
    # Het weekrapport kost ook iets: een vraag per week.
    rapport = con.execute(
        """SELECT COUNT(DISTINCT datum) n, COALESCE(SUM(kosten_usd), 0) s
           FROM ai_kosten WHERE rol = 'rapport'"""
    ).fetchone()
    if rapport["n"]:
        per_rapport = float(rapport["s"]) / int(rapport["n"])
        per_maand = per_rapport * WEKEN_PER_MAAND
        totaal += per_maand
        prognose.uitleg.append(
            f"weekrapport: gemiddeld ${per_rapport:.4f} per keer, ${per_maand:.2f} per maand"
        )
    prognose.maand_usd = totaal

    if prognose.past:
        return prognose

    # ---- het past niet: wat zou helpen? Niets wordt hier aangepast. ----
    tekort = totaal - budget
    for voorvoegsel, wat, nadeel in (
        ("ai_controle", "Controleportefeuille uit", "dan weet je niet meer of het duurdere model zijn geld waard is"),
        ("ai_enkel", "ai_enkel uit", "dan weet je niet meer of het debat iets toevoegt"),
    ):
        deel = sum(m for p, m in prognose.per_portefeuille.items() if p.startswith(voorvoegsel))
        if deel:
            prognose.voorstellen.append(f"{wat}: scheelt ${deel:.2f} per maand, maar {nadeel}.")

    per_symbool = totaal / max(1, len(ai_lijst))
    weg = int(-(-tekort // per_symbool)) if per_symbool > 0 else 0
    if 0 < weg < len(ai_lijst):
        prognose.voorstellen.append(
            f"Minder symbolen voor de AI: ongeveer {weg} minder (van {len(ai_lijst)} naar "
            f"{len(ai_lijst) - weg}). Elk symbool kost gemiddeld ${per_symbool:.2f} per maand. "
            f"Dat moet vastliggen voor de start; tijdens de meting verandert de lijst niet."
        )
    return prognose


def als_tekst(prognose: Prognose, min_rondes: int = 3) -> str:
    regels = [
        f"AI-kosten tot nu toe: ${prognose.uitgegeven_usd:.4f} over {prognose.rondes} ronde(s).",
        *[f"  {u}" for u in prognose.uitleg],
    ]
    if prognose.maand_usd is None:
        return "\n".join(regels)

    regels.append(
        f"Verwachting voor een hele maand: ${prognose.maand_usd:.2f} "
        f"(budget ${prognose.budget_usd:.2f})."
    )
    if prognose.rondes < min_rondes:
        regels.append(
            f"Dit is nog gebaseerd op {prognose.rondes} ronde(s). Na {min_rondes} rondes "
            f"is de schatting betrouwbaar genoeg om op te beslissen."
        )
    if prognose.past:
        marge = prognose.budget_usd - prognose.maand_usd
        regels.append(f"Dat past, met ${marge:.2f} marge.")
    else:
        regels.append("Dat past NIET. Er is niets aangepast; mogelijke aanpassingen:")
        for i, v in enumerate(prognose.voorstellen, 1):
            regels.append(f"  {i}. {v}")
    return "\n".join(regels)


# ------------------------------------------------- de hele meting in een keer
#
# De credits worden in een keer gekocht voor de hele meting, met automatisch
# opwaarderen uit. Dit rekent uit hoeveel dat is, uit wat de proefnachten echt
# kostten. Het verandert niets; kopen doet de gebruiker zelf.

# Welke proefportefeuille de kosten meet van welke portefeuille in de echte meting.
PROEF_NAAR_ECHT = {
    "proef_ai_mix": "ai_analist",
    "proef_ai_haiku": "ai_controle_haiku",
    "proef_ai_enkel": "ai_enkel",
}


def beursdagen_in_meting(cfg: dict) -> int:
    """Het aantal dagen dat de NYSE open is, van de start tot en met de einddatum."""
    from datetime import date, timedelta

    from bot.volledigheid import beurs_open_volgens_kalender

    m = cfg.get("meting") or {}
    dag, einde = date.fromisoformat(str(m["start"])), date.fromisoformat(str(m["einde"]))
    n = 0
    while dag <= einde:
        n += beurs_open_volgens_kalender(cfg, dag.isoformat())
        dag += timedelta(days=1)
    return n


def _rapportdagen_in_meting(cfg: dict) -> int:
    from datetime import date, timedelta

    m = cfg.get("meting") or {}
    dag, einde = date.fromisoformat(str(m["start"])), date.fromisoformat(str(m["einde"]))
    weekdag = int(cfg["rapport"]["weekdag"])
    n = 0
    while dag <= einde:
        n += dag.weekday() == weekdag
        dag += timedelta(days=1)
    return n


@dataclass
class MetingBedrag:
    symbolen: int
    beursdagen: int
    rondes: int
    per_symbool_dag: dict[str, float] = field(default_factory=dict)  # per portefeuille van de echte meting
    per_portefeuille: dict[str, float] = field(default_factory=dict)
    rapporten_usd: float = 0.0
    totaal_usd: float | None = None


def bedrag_hele_meting(con_proef: sqlite3.Connection, cfg: dict, symbolen: int | None = None) -> MetingBedrag:
    """Wat de hele meting kost, uit de gemeten kosten van de proefnachten.

    'cfg' zijn de gewone instellingen (config.yaml): de gekozen AI-lijst telt.
    'symbolen' rekent het voor een ander aantal uit (voor het proefweekverslag).
    """
    n = symbolen if symbolen is not None else len(cfg.get("ai_watchlist", []))
    dagen = beursdagen_in_meting(cfg)
    rondes = con_proef.execute(
        "SELECT COUNT(DISTINCT datum) n FROM ai_besluiten WHERE COALESCE(overgeslagen, '') != ?", (GEEN_KOERS,)
    ).fetchone()["n"]
    uit = MetingBedrag(symbolen=n, beursdagen=dagen, rondes=rondes)
    actief = {str(p["naam"]) for p in (cfg.get("ai") or {}).get("portefeuilles") or []}
    for proef, echt in PROEF_NAAR_ECHT.items():
        if echt not in actief:
            continue
        rij = con_proef.execute(
            """SELECT COUNT(*) n, COALESCE(SUM(kosten_usd), 0) s FROM ai_besluiten
               WHERE portefeuille = ? AND COALESCE(overgeslagen, '') != ?""",
            (proef, GEEN_KOERS),
        ).fetchone()
        if not rij["n"]:
            continue
        per = float(rij["s"]) / int(rij["n"])
        uit.per_symbool_dag[echt] = per
        uit.per_portefeuille[echt] = per * n * dagen
    rapport = con_proef.execute(
        "SELECT COUNT(DISTINCT datum) n, COALESCE(SUM(kosten_usd), 0) s FROM ai_kosten WHERE rol = 'rapport'"
    ).fetchone()
    if rapport["n"]:
        uit.rapporten_usd = float(rapport["s"]) / int(rapport["n"]) * _rapportdagen_in_meting(cfg)
    if "ai_analist" in uit.per_portefeuille:
        uit.totaal_usd = sum(uit.per_portefeuille.values()) + uit.rapporten_usd
    return uit


def bedrag_als_tekst(b: MetingBedrag, cfg: dict, marge_pct: float = 20.0) -> str:
    from bot.tekst import dollar, getal

    m = cfg.get("meting") or {}
    if b.totaal_usd is None:
        return (
            "Het bedrag voor de hele meting is nog niet te berekenen: er zijn nog geen gemeten "
            "kosten van de proefnachten. Kijk in het proefweekverslag (scripts/proefweek_verslag.py)."
        )
    regels = [
        f"Credits voor de hele meting ({m.get('start')} tot en met {m.get('einde')}, {b.beursdagen} beursdagen), "
        f"uit de gemeten kosten van {b.rondes} proefnacht(en) en de gekozen AI-lijst ({b.symbolen} symbolen):"
    ]
    for naam, bedrag in b.per_portefeuille.items():
        regels.append(f"  {naam}: {dollar(b.per_symbool_dag[naam], 4)} per symbool per dag, samen {dollar(bedrag)}")
    if b.rapporten_usd:
        regels.append(f"  weekrapporten: {dollar(b.rapporten_usd)}")
    ontbreekt = [e for e in PROEF_NAAR_ECHT.values()
                 if e in {str(p['naam']) for p in cfg['ai']['portefeuilles']} and e not in b.per_portefeuille]
    if ontbreekt:
        regels.append(f"  LET OP: nog geen gemeten kosten voor {', '.join(ontbreekt)}; die zitten er niet in.")
    regels.append(
        f"Totaal: {dollar(b.totaal_usd)}. Met {getal(marge_pct, 0)}% marge: "
        f"{dollar(b.totaal_usd * (1 + marge_pct / 100.0))}."
    )
    maanden = max(1.0, b.beursdagen / BEURSDAGEN_PER_MAAND)
    per_maand = b.totaal_usd / maanden
    budget = float(cfg["ai"]["budget"]["max_usd_per_maand"])
    regels.append(
        f"Dat is ongeveer {dollar(per_maand)} per maand; het budget in de bot staat op {dollar(budget)} per maand"
        + (". LET OP: dat past niet, de AI zou dan halverwege een maand stoppen met kopen." if per_maand > budget else ".")
    )
    return "\n".join(regels)
