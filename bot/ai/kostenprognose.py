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
