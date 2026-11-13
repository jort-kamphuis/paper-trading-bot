"""Het verslag van de proefweek: hoe vaak handelde de AI, en wat kostte het?

Verandert niets. Haal eerst de laatste stand op met 'git pull'.

    python scripts/proefweek_verslag.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import database, modus


def verslag(con: sqlite3.Connection) -> str:
    regels: list[str] = ["Proefweek: hoe vaak handelde de AI?", ""]

    rondes = con.execute("SELECT COUNT(DISTINCT datum) n FROM ai_besluiten").fetchone()["n"]
    regels.append(f"Rondes met AI-besluiten: {rondes}")
    regels.append("")

    portefeuilles = [
        r["portefeuille"]
        for r in con.execute("SELECT DISTINCT portefeuille FROM ai_besluiten ORDER BY portefeuille")
    ]
    for pf in portefeuilles:
        rijen = con.execute(
            "SELECT actie, zekerheid, onderbouwing, overgeslagen, stappen, kosten_usd "
            "FROM ai_besluiten WHERE portefeuille = ?",
            (pf,),
        ).fetchall()
        beoordeeld = [r for r in rijen if (r["overgeslagen"] or "") != "geen koers op deze dag"]
        acties = Counter(r["actie"] for r in beoordeeld)
        onder_grens = sum(1 for r in beoordeeld if "onder de grens" in (r["onderbouwing"] or ""))
        harde_regel = sum(
            1 for r in beoordeeld
            if any(t in (r["onderbouwing"] or "") for t in ("hebben we al", "hebben we niet", "Te weinig kas"))
        )
        zonder_debat = sum(1 for r in beoordeeld if "debat overgeslagen" in (r["overgeslagen"] or ""))
        mislukt = sum(1 for r in beoordeeld if (r["overgeslagen"] or "").startswith(("antwoord", "risk", "technisch")))

        # Wat wilde de risk manager zelf, voor de harde controles?
        gewild = Counter()
        zekerheden: list[float] = []
        for r in beoordeeld:
            try:
                risk = (json.loads(r["stappen"] or "{}") or {}).get("risk") or {}
            except json.JSONDecodeError:
                risk = {}
            if risk:
                gewild[str(risk.get("actie", "?")).upper()] += 1
                try:
                    zekerheden.append(float(risk.get("zekerheid", 0)))
                except (TypeError, ValueError):
                    pass

        kosten = sum(float(r["kosten_usd"] or 0) for r in rijen)
        regels.append(pf)
        regels.append(f"  beoordeeld: {len(beoordeeld)} symbool-dagen, kosten ${kosten:.2f}")
        regels.append(
            f"  uitkomst: {acties.get('KOOP', 0)} koop, {acties.get('VERKOOP', 0)} verkoop, "
            f"{acties.get('NIETS', 0)} niets"
        )
        regels.append(f"  zonder debat (beide analisten neutraal): {zonder_debat}")
        if gewild:
            regels.append(
                f"  wat de risk manager zelf koos: {gewild.get('KOOP', 0)} koop, "
                f"{gewild.get('VERKOOP', 0)} verkoop, {gewild.get('NIETS', 0)} niets"
            )
        regels.append(f"  tegengehouden door de grens van 0,6: {onder_grens}")
        regels.append(f"  tegengehouden door een harde regel (al in bezit, geen kas): {harde_regel}")
        if mislukt:
            regels.append(f"  onbruikbare antwoorden (afgekapt, geweigerd, onleesbaar): {mislukt}")
        if zekerheden:
            zekerheden.sort()
            midden = zekerheden[len(zekerheden) // 2]
            boven = sum(1 for z in zekerheden if z >= 0.6)
            regels.append(
                f"  zekerheid van de risk manager: mediaan {midden:.2f}, "
                f"{boven} van de {len(zekerheden)} op 0,6 of hoger"
            )
        regels.append("")

    kosten = con.execute(
        "SELECT rol, model, SUM(kosten_usd) usd FROM ai_kosten GROUP BY rol, model ORDER BY usd DESC"
    ).fetchall()
    totaal = sum(float(r["usd"] or 0) for r in kosten)
    regels.append(f"Totale kosten van de proefweek: ${totaal:.2f}")
    for r in kosten:
        regels.append(f"  {r['rol']:10s} {r['model']:20s} ${float(r['usd'] or 0):.3f}")
    return "\n".join(regels)


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
