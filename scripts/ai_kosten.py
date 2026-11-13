"""Wat kost de AI-analist echt, en past het binnen het maandbudget?

Verandert niets. Past het niet, dan krijg je voorstellen te zien die de hele
maand gelijk houden, met wat ze zouden schelen.

Gebruik:
    python scripts/ai_kosten.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import database
from bot.ai.budget import Budget
from bot.ai.kostenprognose import als_tekst, maak_prognose


def main() -> int:
    from bot import modus

    cfg = modus.laad()
    con = database.verbind(modus.database_pad(cfg))
    if cfg["modus"] == "proefweek":
        print(f"{cfg['label']} (data/proefweek.db)\n")
    print(Budget(con, cfg).samenvatting())
    print()
    print(als_tekst(maak_prognose(con, cfg)))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
