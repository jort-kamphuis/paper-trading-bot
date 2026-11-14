"""Het verslag van de proefweek: welke kansen gaf de AI, hoe vaak zou hij handelen, wat kost het?

Verandert niets. Haal eerst de laatste stand op met 'git pull'.

    python scripts/proefweek_verslag.py

Dit is de basis voor twee keuzes die jij maakt (de bot past zelf niets aan):
* welke symbolen de AI in de echte meting volgt (de maandschatting per aantal);
* of de drempels (kans 0,55 en verwacht 0,5%) passen bij de kansen die de AI geeft.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import database, modus
from bot.ai import kenmerken
from bot.ai.kostenprognose import BEURSDAGEN_PER_MAAND, GEEN_KOERS
from bot.tekst import getal

AANTALLEN = (15, 30, 46)


def _mediaan(waarden: list[float]) -> float | None:
    if not waarden:
        return None
    w = sorted(waarden)
    n = len(w)
    return w[n // 2] if n % 2 else (w[n // 2 - 1] + w[n // 2]) / 2


def verslag(con: sqlite3.Connection, regels_cfg: dict | None = None) -> str:
    regels_cfg = regels_cfg or conf.laad()["ai"]["beslisregel"]
    koop_kans = float(regels_cfg["koop_kans"])
    koop_verwacht = float(regels_cfg["koop_verwacht_pct"])
    basiskansen = conf.laad()["ai"].get("basiskansen")

    uit: list[str] = ["Proefweek: wat deed de AI?", ""]
    rondes = con.execute("SELECT COUNT(DISTINCT datum) n FROM ai_besluiten").fetchone()["n"]
    uit.append(f"Rondes met AI-beoordelingen: {rondes}")
    uit.append(
        f"Drempels: kopen bij kans {getal(koop_kans, 2)} of meer en verwacht "
        f"{getal(koop_verwacht, 1)}% of meer; verkopen onder de basiskans van het symbool."
    )
    uit.append("")

    portefeuilles = [
        r["portefeuille"]
        for r in con.execute("SELECT DISTINCT portefeuille FROM ai_besluiten ORDER BY portefeuille")
    ]
    for pf in portefeuilles:
        rijen = con.execute(
            "SELECT symbool, actie, kans, verwacht_pct, overgeslagen, kosten_usd FROM ai_besluiten WHERE portefeuille = ?",
            (pf,),
        ).fetchall()
        beoordeeld = [r for r in rijen if (r["overgeslagen"] or "") != GEEN_KOERS]
        bruikbaar = [r for r in beoordeeld if r["kans"] is not None and r["verwacht_pct"] is not None]
        acties = Counter(r["actie"] for r in beoordeeld)
        kosten = sum(float(r["kosten_usd"] or 0) for r in beoordeeld)
        per_symbool_dag = kosten / len(beoordeeld) if beoordeeld else 0.0

        uit.append(pf)
        uit.append(
            f"  beoordeeld: {len(beoordeeld)} symbool-dagen, kosten ${getal(kosten, 2)} "
            f"(${getal(per_symbool_dag, 4)} per symbool-dag)"
        )
        uit.append(
            f"  uitkomst: {acties.get('KOOP', 0)} koop, {acties.get('VERKOOP', 0)} verkoop, "
            f"{acties.get('NIETS', 0)} niets"
        )
        onbruikbaar = len(beoordeeld) - len(bruikbaar)
        if onbruikbaar:
            redenen = Counter((r["overgeslagen"] or "onbekend")[:60] for r in beoordeeld
                              if r["kans"] is None or r["verwacht_pct"] is None)
            uit.append(f"  zonder bruikbare kans: {onbruikbaar} ({', '.join(f'{k}: {v}' for k, v in redenen.most_common(3))})")
        if bruikbaar:
            kansen = [float(r["kans"]) for r in bruikbaar]
            verwacht = [float(r["verwacht_pct"]) for r in bruikbaar]
            beide = sum(1 for r in bruikbaar if r["kans"] >= koop_kans and r["verwacht_pct"] >= koop_verwacht)
            alleen_kans = sum(1 for k in kansen if k >= koop_kans)
            onder = sum(1 for r in bruikbaar if r["kans"] < (kenmerken.basiskans(r["symbool"], basiskansen) or 0.5))
            uit.append(
                f"  kans: mediaan {getal(_mediaan(kansen), 2)}, laagste {getal(min(kansen), 2)}, "
                f"hoogste {getal(max(kansen), 2)}"
            )
            uit.append(
                f"  verwacht extra rendement: mediaan {getal(_mediaan(verwacht), 1, teken=True)}%, "
                f"van {getal(min(verwacht), 1, teken=True)}% tot {getal(max(verwacht), 1, teken=True)}%"
            )
            uit.append(
                f"  haalt beide koopdrempels: {beide} van {len(bruikbaar)} "
                f"(alleen de kans: {alleen_kans}); kans onder de eigen basiskans: {onder}"
            )
        if per_symbool_dag:
            schatting = ", ".join(
                f"{n} symbolen ${getal(per_symbool_dag * n * BEURSDAGEN_PER_MAAND, 2)}" for n in AANTALLEN
            )
            uit.append(f"  per maand (21 beursdagen) bij: {schatting}")
        uit.append("")

    # Het bedrag voor de hele meting, om de credits in een keer te kopen.
    from bot.ai import kostenprognose

    normaal = conf.laad()
    per_aantal = [(n, kostenprognose.bedrag_hele_meting(con, normaal, symbolen=n)) for n in AANTALLEN]
    if per_aantal[0][1].totaal_usd is not None:
        dagen = per_aantal[0][1].beursdagen
        uit.append(f"Hele meting ({dagen} beursdagen, drie AI-portefeuilles en de weekrapporten samen):")
        for n, b in per_aantal:
            uit.append(f"  {n} symbolen: ${getal(b.totaal_usd, 2)}, met 20% marge ${getal(b.totaal_usd * 1.2, 2)}")
        uit.append("")

    from bot.ai import kalibratie

    uit.append(kalibratie.als_tekst(con))
    uit.append("")

    kosten = con.execute(
        "SELECT rol, model, SUM(kosten_usd) usd FROM ai_kosten GROUP BY rol, model ORDER BY usd DESC"
    ).fetchall()
    totaal = sum(float(r["usd"] or 0) for r in kosten)
    uit.append(f"Totale kosten van de proefweek: ${getal(totaal, 2)}")
    for r in kosten:
        uit.append(f"  {r['rol']:10s} {r['model']:20s} ${getal(float(r['usd'] or 0), 3)}")
    uit.append("")
    uit.append("Nep-geld. Dit verslag verandert niets; de keuzes maak jij.")
    return "\n".join(uit)


def main() -> int:
    overlay = modus.lees_proefweek()
    if not overlay:
        print("Er is geen proefweek ingesteld.")
        return 1
    pad = conf.PROJECT / overlay["proefweek"]["database"]
    if not pad.exists():
        print(f"{pad.name} bestaat nog niet. Haal de laatste stand op met 'git pull'.")
        return 1
    con = database.verbind(pad)
    print(verslag(con))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
