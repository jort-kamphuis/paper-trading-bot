"""Begin de meting helemaal opnieuw.

Dit wist alle portefeuilles, posities, trades, dagwaardes en de voortgang.
De koersen en het nieuws blijven staan, want die kosten tijd om op te halen.

Je hebt dit bijna nooit nodig. Gebruik het alleen als je echt opnieuw wilt
beginnen, bijvoorbeeld omdat je de instellingen flink hebt veranderd. Alles
wat je tot nu toe hebt gemeten, ben je daarna kwijt.

Gebruik:
    python scripts/herstart_meting.py          # vraagt om bevestiging
    python scripts/herstart_meting.py --ja     # zonder vragen
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import database
from bot.opzet import SLEUTEL_START


def main() -> int:
    p = argparse.ArgumentParser(description="De meting opnieuw beginnen")
    p.add_argument("--ja", action="store_true", help="niet om bevestiging vragen")
    p.add_argument("--db", default=None, help="pad naar de database")
    args = p.parse_args()

    con = database.verbind(args.db)

    start = database.lees_instelling(con, SLEUTEL_START)
    aantal_trades = con.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"]
    aantal_dagen = con.execute(
        "SELECT COUNT(DISTINCT datum) n FROM waarde"
    ).fetchone()["n"]

    if start is None and aantal_trades == 0:
        print("Er is nog niets te wissen.")
        con.close()
        return 0

    print("Dit wordt gewist:")
    print(f"  meting gestart op : {start}")
    print(f"  trades            : {aantal_trades}")
    print(f"  gemeten dagen     : {aantal_dagen}")
    print("De koersen blijven staan.")

    if not args.ja:
        antwoord = input("\nWeet je het zeker? Typ 'ja' om door te gaan: ").strip().lower()
        if antwoord != "ja":
            print("Niets gewist.")
            con.close()
            return 1

    database.leeg_handelsdata(con)
    con.execute("DELETE FROM instellingen WHERE sleutel = ?", (SLEUTEL_START,))
    con.commit()
    print("\nGewist. Draai nu: python scripts/dagelijks.py")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
