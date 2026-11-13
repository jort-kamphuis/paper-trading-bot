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

import json
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

# Een "sterk signaal" voor de voorstel-berekening: een analist die niet
# neutraal is en minstens deze zekerheid heeft.
STERK = 0.7


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
    n_crypto = sum(1 for s in ai_lijst if is_crypto(s))
    n_aandeel = len(ai_lijst) - n_crypto

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

    totaal = 0.0
    for pf, soorten in sorted(per_pf.items()):
        alles = soorten["crypto"] + soorten["aandeel"]
        algemeen = _gemiddelde(alles) or 0.0
        per_aandeel = _gemiddelde(soorten["aandeel"])
        per_crypto = _gemiddelde(soorten["crypto"])
        maand = (per_aandeel if per_aandeel is not None else algemeen) * n_aandeel * BEURSDAGEN_PER_MAAND
        maand += (per_crypto if per_crypto is not None else algemeen) * n_crypto * CRYPTODAGEN_PER_MAAND
        prognose.per_portefeuille[pf] = maand
        totaal += maand
        prognose.uitleg.append(
            f"{pf}: gemiddeld ${algemeen:.4f} per symbool-dag "
            f"({len(alles)} gemeten), naar schatting ${maand:.2f} per maand"
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

    # ---- het past niet: wat zou helpen? ----
    tekort = totaal - budget

    # 1. De controleportefeuille uitzetten.
    controle = {pf: m for pf, m in prognose.per_portefeuille.items() if pf.startswith("ai_controle")}
    if controle:
        bespaard = sum(controle.values())
        prognose.voorstellen.append(
            f"Controleportefeuille uit: scheelt ${bespaard:.2f} per maand, maar dan weet je "
            f"niet meer of het duurdere model zijn geld waard is."
        )

    # 2. Minder symbolen voor de AI.
    per_symbool = totaal / max(1, len(ai_lijst))
    weg = int(-(-tekort // per_symbool)) if per_symbool > 0 else 0
    if 0 < weg < len(ai_lijst):
        prognose.voorstellen.append(
            f"Minder symbolen voor de AI: ongeveer {weg} minder (van {len(ai_lijst)} naar "
            f"{len(ai_lijst) - weg}). Elk symbool kost gemiddeld ${per_symbool:.2f} per maand. "
            f"Wel minder trades, dus nog langer wachten op een uitspraak."
        )

    # 3. Het debat alleen bij een sterk signaal.
    # De analisten draaien hoe dan ook; alleen het debat (bull, bear, risk)
    # valt weg. Wat de analisten per besluit kosten, rekenen we uit hun eigen
    # boekingen, zodat dat niet als besparing meetelt.
    analisten = con.execute(
        "SELECT COALESCE(SUM(kosten_usd), 0) s FROM ai_kosten WHERE rol IN ('technisch', 'nieuws')"
    ).fetchone()["s"]
    analist_per_besluit = float(analisten) / len(besluiten) if besluiten else 0.0

    gedebatteerd, zou_blijven, bespaard = 0, 0, 0.0
    for b in besluiten:
        if b["overgeslagen"]:
            continue  # er was geen debat
        try:
            stappen = json.loads(b["stappen"] or "{}")
        except json.JSONDecodeError:
            continue
        gedebatteerd += 1
        sterk = any(
            (stappen.get(r) or {}).get("signaal") not in (None, "neutraal")
            and float((stappen.get(r) or {}).get("zekerheid") or 0) >= STERK
            for r in ("technisch", "nieuws")
        )
        if sterk:
            zou_blijven += 1
        else:
            bespaard += max(0.0, float(b["kosten_usd"] or 0.0) - analist_per_besluit)
    gemeten = sum(float(b["kosten_usd"] or 0.0) for b in besluiten)
    if gedebatteerd and gemeten > 0:
        aandeel = bespaard / gemeten
        prognose.voorstellen.append(
            f"Debat alleen bij een sterk signaal (een analist met zekerheid {STERK} of meer): "
            f"scheelt naar schatting {100 * aandeel:.0f}%, ongeveer ${totaal * aandeel:.2f} per "
            f"maand. Maar dan was er bij maar {zou_blijven} van de {gedebatteerd} debatten "
            f"nog een debat geweest. Zonder debat beslist de AI altijd NIETS, dus hij zou dan "
            f"veel minder handelen en je meet een andere AI-analist dan nu."
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
