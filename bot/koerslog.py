"""Het koerslog: waren de koersen van de laatste beursdag er al toen de ronde ze ophaalde?

Op 17 november 2026 leverde Yahoo om 01:42 UTC, bijna vijf uur na het sluiten van
de beurs, nog geen enkele koers van die dag. De bot handelde toen terecht niet.
Om te zien of dat een incident was of een patroon, legt elke ronde hier vast: het
tijdstip van ophalen (UTC), welke beursdag af had moeten zijn, en voor hoeveel
aandelen en fondsen die dag er al was.

Wanneer Yahoo een koers publiceert, is niet te zien. Wel of hij er was op het
moment van ophalen: bij de hoofdronde (rond 04:00 UTC) en, als die niet kon
handelen, bij de herkansing (rond 08:00 UTC).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

from bot import config as conf
from bot.data.koersen import eerste_onaffe_dag_aandelen
from bot.volledigheid import beurs_open_volgens_kalender


def verwachte_beursdag(cfg: dict, nu: datetime | None = None) -> str | None:
    """De laatste dag dat de NYSE open was en waarvan de slotkoers nu vast hoort te staan."""
    nu = nu or datetime.now(timezone.utc)
    dag = date.fromisoformat(eerste_onaffe_dag_aandelen(nu)) - timedelta(days=1)
    for _ in range(10):
        if beurs_open_volgens_kalender(cfg, dag.isoformat()):
            return dag.isoformat()
        dag -= timedelta(days=1)
    return None  # geen beurskalender voor dit jaar: niets te zeggen


def leg_vast(con: sqlite3.Connection, cfg: dict, ronde: str, nu: datetime | None = None) -> dict | None:
    """Schrijf een regel in het koerslog, direct na het ophalen. Geeft de regel terug (of None)."""
    nu = nu or datetime.now(timezone.utc)
    beursdag = verwachte_beursdag(cfg, nu)
    if beursdag is None:
        return None
    symbolen = conf.aandelen_symbolen(cfg)
    plekken = ",".join("?" * len(symbolen))
    aanwezig = con.execute(
        f"SELECT COUNT(DISTINCT symbool) n FROM koersen WHERE datum = ? AND symbool IN ({plekken})",
        (beursdag, *symbolen),
    ).fetchone()["n"]
    regel = {
        "gevraagd_op": nu.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ronde": ronde, "beursdag": beursdag, "aanwezig": int(aanwezig), "verwacht": len(symbolen),
    }
    con.execute(
        "INSERT INTO koerslog (gevraagd_op, ronde, beursdag, aanwezig, verwacht) VALUES (?,?,?,?,?)",
        (regel["gevraagd_op"], ronde, beursdag, regel["aanwezig"], regel["verwacht"]),
    )
    con.commit()
    return regel


def als_regel(r: dict | sqlite3.Row) -> str:
    """Een regel van het koerslog als tekst."""
    tijd = str(r["gevraagd_op"])
    oordeel = (
        "compleet" if r["aanwezig"] >= r["verwacht"]
        else "ONTBREEKT" if r["aanwezig"] == 0 else "ONVOLLEDIG"
    )
    return (
        f"{tijd[:10]} om {tijd[11:16]} UTC ({r['ronde']}): beursdag {r['beursdag']}, "
        f"{r['aanwezig']} van {r['verwacht']} koersen, {oordeel}"
    )


def samenvatting(con: sqlite3.Connection, laatste: int = 30) -> list[str]:
    """De laatste regels van het koerslog, oud naar nieuw, met een telling erboven."""
    rijen = con.execute(
        "SELECT * FROM koerslog ORDER BY gevraagd_op DESC, id DESC LIMIT ?", (laatste,)
    ).fetchall()
    if not rijen:
        return []
    hoofd = [r for r in rijen if r["ronde"] == "hoofd"]
    te_laat = [r for r in hoofd if r["aanwezig"] < r["verwacht"]]
    kop = (
        f"Koersen van Yahoo bij het ophalen ({len(rijen)} rondes gelogd): in {len(te_laat)} van de "
        f"{len(hoofd)} hoofdrondes was de laatste beursdag er nog niet (helemaal)."
    )
    return [kop] + [f"  {als_regel(r)}" for r in reversed(rijen)]
