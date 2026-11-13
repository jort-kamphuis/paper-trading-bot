"""De tabel met kengetallen die de AI per symbool te zien krijgt.

De code rekent dit zelf uit, uit de koersen tot en met de beslisdag. Er zit dus
geen model tussen dat cijfers kan verkeerd lezen of verzinnen, en de dure rollen
krijgen precies dezelfde getallen.

Periodes zijn in kalenderdagen, zodat aandelen en crypto over dezelfde periode
worden gemeten. Elk symbool wordt ook vergeleken met zijn referentie: SPY voor
aandelen en indexfondsen, BTC voor crypto. Dat is waar de AI tegen voorspelt.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from bot.config import is_crypto
from bot.markt import Markt

PERIODES = (7, 28, 90, 180)


def referentie(symbool: str) -> str:
    return "BTC/USDT" if is_crypto(symbool) else "SPY"


def _r(x: float | None, d: int = 1) -> float | None:
    return None if x is None else round(x, d)


def _tov(slot: float | None, ref: float | None) -> float | None:
    if slot is None or not ref:
        return None
    return 100.0 * (slot / ref - 1.0)


def tabel(
    markt: Markt, symbool: str, datum: str, con: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    """Alle kengetallen van een symbool op de beslisdag. None als er geen koers is."""
    slot = markt.slot_op_of_voor(symbool, datum)
    if slot is None:
        return None
    ref = referentie(symbool)
    t: dict[str, Any] = {"symbool": symbool, "referentie": ref, "slotkoers_usd": round(slot, 4)}

    for d in PERIODES:
        eigen = markt.kalender_rendement(symbool, datum, d)
        tegen = markt.kalender_rendement(ref, datum, d) if ref != symbool else None
        t[f"rendement_{d}d_pct"] = _r(eigen)
        if ref != symbool:
            t[f"rendement_{d}d_min_{ref.split('/')[0]}_pct"] = (
                _r(eigen - tegen) if eigen is not None and tegen is not None else None
            )

    t["schommeling_per_dag_28d_pct"] = _r(markt.kalender_schommeling(symbool, datum, 28), 2)
    t["afstand_tot_jaarhoog_pct"] = _r(_tov(slot, markt.kalender_hoogste(symbool, datum, 365)))
    t["koers_tov_ma200_pct"] = _r(_tov(slot, markt.kengetal(symbool, datum, "ma200")))
    t["rsi14"] = _r(markt.kengetal(symbool, datum, "rsi14"))
    t["volume_tov_normaal"] = _r(markt.volume_verhouding(symbool, datum, 50), 2)

    # De stand van de markt als geheel.
    for naam, s in (("SPY", "SPY"), ("BTC", "BTC/USDT")):
        t[f"markt_{naam}_tov_ma200_pct"] = _r(
            _tov(markt.slot_op_of_voor(s, datum), markt.kengetal(s, datum, "ma200"))
            if markt.heeft(s) and markt.is_handelsdag(s, datum) else None
        )
        t[f"markt_{naam}_rendement_28d_pct"] = _r(markt.kalender_rendement(s, datum, 28))

    if con is not None and not is_crypto(symbool):
        from bot.data.kwartaalcijfers import bekend_op

        cijfers = bekend_op(con, symbool, datum)
        if cijfers:
            t["volgende_kwartaalcijfers"] = cijfers
            t["dagen_tot_kwartaalcijfers"] = (date.fromisoformat(cijfers) - date.fromisoformat(datum)).days
    return t


UITLEG = {
    "slotkoers_usd": "slotkoers (dollar)",
    "schommeling_per_dag_28d_pct": "gemiddelde dagbeweging, 28 dagen (%)",
    "afstand_tot_jaarhoog_pct": "afstand tot het hoogste punt van het jaar (%)",
    "koers_tov_ma200_pct": "koers ten opzichte van het 200-daags gemiddelde (%)",
    "rsi14": "RSI 14 dagen (0-100)",
    "volume_tov_normaal": "volume ten opzichte van normaal (1 = normaal)",
}


def als_tekst(t: dict[str, Any]) -> str:
    """De tabel als leesbare regels, met '-' waar iets niet te berekenen was."""
    regels = []
    for sleutel, waarde in t.items():
        if sleutel == "symbool":
            continue
        label = UITLEG.get(sleutel, sleutel.replace("_", " "))
        regels.append(f"  {label}: {'-' if waarde is None else waarde}")
    return "\n".join(regels)
