"""Van de proefweek naar de echte meting.

Dit gebeurt een keer, vanzelf, in de eerste ronde na de proefweek. Of eerder,
als je het zelf start (scripts/omschakelen.py, dat de workflow op GitHub
start). Wat er gebeurt:

1. De echte database (trading.db) begint een nieuwe meting: alle portefeuilles
   beginnen op dezelfde dag met 1.000 dollar. De AI-kosten blijven staan.
2. De correctie uit config.proefweek.yaml wordt geboekt.
3. De proefweekdatabase wordt als afgesloten gemarkeerd, en blijft bewaard.
4. Vanaf dat moment gelden weer alleen de instellingen uit config.yaml.

Het gebeurt nooit twee keer: de echte database onthoudt dat het gedaan is.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from bot import database
from bot.opzet import SLEUTEL_START

SLEUTEL = "omgeschakeld_op"


def is_gedaan(con_echt: sqlite3.Connection) -> bool:
    return database.lees_instelling(con_echt, SLEUTEL) is not None


def is_nodig(con_echt: sqlite3.Connection, overlay: dict | None, vandaag: date) -> bool:
    """Vanzelf omschakelen: er was een proefweek, die is voorbij, en het is nog niet gedaan."""
    if not overlay or "proefweek" not in overlay or is_gedaan(con_echt):
        return False
    return vandaag > date.fromisoformat(str(overlay["proefweek"]["tot_en_met"]))


def voer_uit(
    con_echt: sqlite3.Connection,
    con_proef: sqlite3.Connection | None,
    overlay: dict | None,
    vandaag: date,
) -> str:
    if is_gedaan(con_echt):
        return f"De echte meting was al gestart op {database.lees_instelling(con_echt, SLEUTEL)}; niets gedaan."

    database.leeg_handelsdata(con_echt)
    con_echt.execute("DELETE FROM instellingen WHERE sleutel = ?", (SLEUTEL_START,))
    con_echt.commit()

    regels = [
        "De proefweek is voorbij. De echte meting begint nu, met de instellingen uit "
        "config.yaml. Alle portefeuilles beginnen op dezelfde dag met 1.000 dollar."
    ]
    omschakelen = ((overlay or {}).get("proefweek") or {}).get("omschakelen") or {}
    correctie = float(omschakelen.get("correctie_usd") or 0)
    if correctie:
        reden = str(omschakelen.get("correctie_reden") or "correctie")
        database.boek_correctie(con_echt, vandaag.isoformat(), correctie, reden)
        regels.append(f"Geboekt als correctie: ${correctie:.4f} ({reden}).")

    database.zet_instelling(con_echt, SLEUTEL, vandaag.isoformat())
    if con_proef is not None:
        database.zet_instelling(con_proef, "afgesloten_op", vandaag.isoformat())
        regels.append("De resultaten van de proefweek blijven bewaard in data/proefweek.db.")
    return "\n".join(regels)
