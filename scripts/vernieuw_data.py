"""Haal de koersen op en zet ze in de database.

Gebruik:
    python scripts/vernieuw_data.py              # vul aan wat ontbreekt
    python scripts/vernieuw_data.py --jaren 3    # haal 3 jaar geschiedenis op
    python scripts/vernieuw_data.py --alles      # alles opnieuw ophalen
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import database
from bot.data import koersen as mod_koersen
from bot.data import wisselkoers as mod_wisselkoers


def main() -> int:
    p = argparse.ArgumentParser(description="Koersen ophalen en opslaan")
    p.add_argument("--jaren", type=float, default=3.0, help="hoeveel jaar geschiedenis")
    p.add_argument("--alles", action="store_true", help="alles opnieuw ophalen")
    p.add_argument("--db", default=None, help="pad naar de database")
    args = p.parse_args()

    cfg = conf.laad()
    con = database.verbind(args.db)

    aandelen = conf.aandelen_symbolen(cfg)
    crypto = conf.crypto_symbolen(cfg)
    start_vol = mod_koersen.standaard_start(args.jaren)
    morgen = (date.today() + timedelta(days=1)).isoformat()

    print(f"Database: {args.db or conf.DB_PAD}")
    print(f"Periode: vanaf {start_vol}\n")

    # ---- aandelen en ETF's ----
    print(f"Aandelen en ETF's ophalen ({len(aandelen)} symbolen)...")
    start = start_vol
    if not args.alles:
        # Begin bij de oudste 'laatste datum', zodat geen enkel symbool gaten houdt.
        laatsten = [database.laatste_koersdatum(con, s) for s in aandelen]
        if all(laatsten) and laatsten:
            oudste = min(d for d in laatsten if d)
            start = max(start_vol, (date.fromisoformat(oudste) - timedelta(days=5)).isoformat())
    rijen, redenen = mod_koersen.haal_aandelen_met_herkansing(aandelen, start=start, eind=morgen)
    aantal = database.bewaar_koersen(con, rijen)
    gevonden = sorted({r["symbool"] for r in rijen})
    print(f"  {aantal} dagkoersen opgeslagen voor {len(gevonden)} symbolen")
    for symbool, reden in redenen.items():
        print(f"  LET OP: geen data voor {symbool}, ook niet na herkansing ({reden})")

    # ---- crypto ----
    print(f"\nCrypto ophalen ({len(crypto)} paren)...")
    start_crypto = start_vol
    if not args.alles:
        laatsten = [database.laatste_koersdatum(con, s) for s in crypto]
        if all(laatsten) and laatsten:
            oudste = min(d for d in laatsten if d)
            start_crypto = max(
                start_vol, (date.fromisoformat(oudste) - timedelta(days=5)).isoformat()
            )
    rijen, beurzen = mod_koersen.haal_crypto(
        crypto, start=start_crypto, beurzen=cfg["crypto"]["beurzen"]
    )
    aantal = database.bewaar_koersen(con, rijen)
    print(f"  {aantal} dagkoersen opgeslagen")
    for paar, beurs in sorted(beurzen.items()):
        print(f"    {paar:12s} via {beurs}")
    mist = [c for c in crypto if c not in beurzen]
    if mist:
        print(f"  LET OP: geen data voor {mist}")

    # ---- wisselkoers ----
    print("\nEUR/USD-wisselkoers ophalen...")
    rijen = mod_wisselkoers.haal_eurusd(
        cfg["wisselkoers"]["symbool"], start=start_vol, eind=morgen
    )
    aantal = database.bewaar_wisselkoersen(con, rijen)
    print(f"  {aantal} dagen opgeslagen")

    # ---- onaffe dagen weg ----
    weg = database.verwijder_onaffe_dagen(
        con,
        aandelen,
        crypto,
        onaf_aandelen=mod_koersen.eerste_onaffe_dag_aandelen(),
        onaf_crypto=mod_koersen.eerste_onaffe_dag_crypto(),
    )
    if weg:
        print(f"\n{weg} koersen van een dag die nog niet af was, weggehaald.")

    # ---- overzicht ----
    print("\nIn de database:")
    rij = con.execute(
        "SELECT COUNT(*) n, COUNT(DISTINCT symbool) s, MIN(datum) van, MAX(datum) tot FROM koersen"
    ).fetchone()
    print(f"  {rij['n']} dagkoersen, {rij['s']} symbolen, van {rij['van']} tot {rij['tot']}")
    rij = con.execute("SELECT COUNT(*) n, MAX(datum) tot FROM wisselkoersen").fetchone()
    print(f"  {rij['n']} wisselkoersen, laatste {rij['tot']}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
