"""Bouw het dashboard: de statische site plus de resultaten als JSON.

Verandert niets aan de meting. Een oudere database krijgt bij het openen wel de
nieuwste kolommen erbij (zoals elke ronde dat doet); de inhoud blijft gelijk.

Op GitHub draait dit elke nacht na de ronde, VOOR de database klein wordt
gemaakt (dan zijn alle koersen er nog). De map _site gaat naar GitHub Pages.

    python scripts/exporteer_dashboard.py                  # zoals op GitHub
    python scripts/exporteer_dashboard.py --uit _site      # andere map

Voor een voorvertoning op een kopie:
    python scripts/exporteer_dashboard.py --koersen-db kopie.db --proef-db kopie.db \\
        --echt-db kopie.db --toon-echt --label "Voorvertoning met simulatiegegevens"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import dashboard, database, modus


def main() -> int:
    p = argparse.ArgumentParser(description="Bouw het dashboard")
    p.add_argument("--uit", default=str(conf.PROJECT / "_site"))
    p.add_argument("--koersen-db", default=None, help="database met alle koersen (standaard: de actieve)")
    p.add_argument("--echt-db", default=None, help="standaard data/trading.db")
    p.add_argument("--proef-db", default=None, help="standaard de proefweekdatabase, als die er is")
    p.add_argument("--toon-echt", action="store_true", help="de echte weergave ook tijdens de proefweek tonen (voorvertoning)")
    p.add_argument("--label", default="", help="een balk bovenaan, bijvoorbeeld voor een voorvertoning")
    args = p.parse_args()

    cfg = modus.laad()
    overlay = modus.lees_proefweek()
    echt_pad = Path(args.echt_db) if args.echt_db else conf.DB_PAD
    proef_pad = Path(args.proef_db) if args.proef_db else (
        conf.PROJECT / overlay["proefweek"]["database"] if overlay else None
    )
    koers_pad = Path(args.koersen_db) if args.koersen_db else modus.database_pad(cfg)

    koers_con = database.verbind(koers_pad)
    con_echt = database.verbind(echt_pad) if echt_pad.exists() else None
    con_proef = database.verbind(proef_pad) if proef_pad and proef_pad.exists() else None
    # Tijdens de proefweek is trading.db nog niet de echte meting.
    echt_beschikbaar = args.toon_echt or cfg["modus"] == "echt"

    meta = dashboard.exporteer(
        Path(args.uit), koers_con, con_echt, con_proef, echt_beschikbaar, label=args.label,
    )
    print(f"Dashboard gebouwd in {args.uit} (koersen tot {meta['koersen_tot']}, "
          f"weergaven: {', '.join(k for k, v in meta['weergaven'].items() if v['beschikbaar'])}).")
    for c in (koers_con, con_echt, con_proef):
        if c is not None:
            c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
