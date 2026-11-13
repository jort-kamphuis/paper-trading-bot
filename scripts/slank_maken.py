"""Maak de database klein genoeg om elke nacht in git te bewaren.

De database bevat de resultaten (portefeuilles, trades, waardes, AI-besluiten,
nieuws, kosten) en de koersgeschiedenis. Die laatste is bijna 5 MB en wordt bij
elke ronde toch opnieuw opgehaald. Bewaar je hem elke nacht in git, dan groeit
de repository in drie maanden met honderden MB's.

Daarom haalt dit script de koersen weg, op één symbool na: het symbool van de
meetkalender (SPY). Dat is nodig om de risicocijfers op de juiste dagen te
rekenen (zie statistiek.op_kalender). Alles wat met de meting zelf te maken
heeft, blijft staan.

Gebruik (door GitHub Actions, na de ronde):
    python scripts/slank_maken.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import database


def maak_slank(con, kalender_symbool: str) -> int:
    weg = con.execute(
        "DELETE FROM koersen WHERE symbool != ?", (kalender_symbool,)
    ).rowcount
    con.commit()
    con.execute("VACUUM")
    return weg


def main() -> int:
    cfg = conf.laad()
    pad = Path(sys.argv[1]) if len(sys.argv) > 1 else conf.DB_PAD
    voor = pad.stat().st_size if pad.exists() else 0
    con = database.verbind(pad)
    weg = maak_slank(con, cfg["rapport"]["kalender_symbool"])
    con.close()
    na = pad.stat().st_size
    print(f"{weg} koersen weggehaald; database van {voor / 1024:.0f} kB naar {na / 1024:.0f} kB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
