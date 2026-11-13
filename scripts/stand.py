"""Laat zien hoe het ervoor staat. Verandert niets.

Gebruik:
    python scripts/stand.py            # overzicht van alle portefeuilles
    python scripts/stand.py --trades   # ook de laatste trades
    python scripts/stand.py --pf momentum
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import database, overzicht, statistiek
from bot.markt import Markt
from bot.opzet import SLEUTEL_START


def _reeks(con, portefeuille: str) -> tuple[list[str], list[float]]:
    rijen = con.execute(
        "SELECT datum, totaal_eur FROM waarde WHERE portefeuille = ? ORDER BY datum",
        (portefeuille,),
    ).fetchall()
    return [r["datum"] for r in rijen], [r["totaal_eur"] for r in rijen]


def main() -> int:
    p = argparse.ArgumentParser(description="De stand van de portefeuilles")
    p.add_argument("--trades", action="store_true", help="ook de laatste trades tonen")
    p.add_argument("--pf", default=None, help="alleen deze portefeuille")
    p.add_argument("--aantal", type=int, default=15, help="hoeveel trades tonen")
    p.add_argument("--db", default=None, help="pad naar de database")
    args = p.parse_args()

    cfg = conf.laad()
    con = database.verbind(args.db)

    start = database.lees_instelling(con, SLEUTEL_START)
    if start is None:
        print("De meting is nog niet begonnen. Draai eerst: python scripts/dagelijks.py")
        con.close()
        return 1

    rij = con.execute("SELECT MAX(datum) AS d FROM waarde").fetchone()
    laatste = rij["d"] if rij else None
    print(f"Meting gestart op {start}, laatste stand van {laatste}\n")

    # De meetkalender: de dagen dat de Amerikaanse beurs open was.
    kalender_symbool = cfg["rapport"]["kalender_symbool"]
    markt = Markt.uit_database(con, symbolen=[kalender_symbool])
    kalender = [d for d in markt.handelsdagen_van(kalender_symbool) if d >= start]

    standen = overzicht.portefeuille_standen(con)
    if args.pf:
        standen = [s for s in standen if s["portefeuille"] == args.pf]

    kop = (
        f"{'portefeuille':16s} {'totaal':>9s} {'kas':>8s} {'rend.':>8s} "
        f"{'trades':>7s} {'daling':>8s} {'sharpe':>7s}"
    )
    print(kop)
    print("-" * len(kop))
    for s in standen:
        naam = s["portefeuille"]
        datums, waarden = statistiek.op_kalender(*_reeks(con, naam), kalender)
        aantal = con.execute(
            "SELECT COUNT(*) n FROM trades WHERE portefeuille = ?", (naam,)
        ).fetchone()["n"]
        daling = statistiek.max_daling_pct(waarden) if waarden else 0.0
        sh = statistiek.sharpe(waarden, datums) if waarden else None
        print(
            f"{naam:16s} {s['totaal_eur']:9.2f} {s['kas_eur']:8.2f} "
            f"{s['rendement_pct']:+7.1f}% {aantal:7d} {daling:7.1f}% "
            f"{(f'{sh:.2f}' if sh is not None else '-'):>7s}"
        )

    # ---- open posities ----
    posities = overzicht.open_posities(con, args.pf)
    print(f"\nOpen posities ({len(posities)}):")
    if posities:
        for pos in posities:
            print(
                f"  {pos['portefeuille']:16s} {pos['symbool']:10s} "
                f"ingelegd EUR {pos['kostprijs_eur']:7.2f}  sinds {pos['gekocht_op']}"
            )
    else:
        print("  geen")

    # ---- orders die klaarstaan ----
    wachtend = overzicht.openstaande_orders(con)
    if args.pf:
        wachtend = [w for w in wachtend if w["portefeuille"] == args.pf]
    print(f"\nKlaar voor de volgende handelsdag ({len(wachtend)}):")
    if wachtend:
        for w in wachtend:
            print(
                f"  {w['portefeuille']:16s} {w['actie']:8s} {w['symbool']:10s} "
                f"(besloten op {w['besluit_datum']})"
            )
    else:
        print("  geen")

    # ---- trades ----
    if args.trades:
        vraag = "SELECT * FROM trades"
        params: tuple = ()
        if args.pf:
            vraag += " WHERE portefeuille = ?"
            params = (args.pf,)
        vraag += " ORDER BY datum DESC, id DESC LIMIT ?"
        params = params + (args.aantal,)
        rijen = con.execute(vraag, params).fetchall()
        print(f"\nLaatste {len(rijen)} trades:")
        for t in rijen:
            resultaat = ""
            if t["actie"] == "VERKOOP" and t["resultaat_eur"] is not None:
                teken = "+" if t["resultaat_eur"] >= 0 else ""
                resultaat = f"  {teken}{t['resultaat_eur']:.2f} euro"
            print(
                f"  {t['datum']}  {t['portefeuille']:16s} {t['actie']:8s} "
                f"{t['symbool']:10s} EUR {t['bedrag_eur']:7.2f}{resultaat}"
            )
            if t["reden"]:
                print(f"      {t['reden']}")

    # ---- is het al iets? ----
    rap = cfg["rapport"]
    print("\nIs dit al iets, of kan het nog geluk zijn?")
    for s in standen:
        naam = s["portefeuille"]
        if naam.startswith("benchmark_"):
            continue
        resultaten = [
            r["resultaat_eur"]
            for r in con.execute(
                """SELECT resultaat_eur FROM trades
                   WHERE portefeuille = ? AND actie = 'VERKOOP'
                     AND resultaat_eur IS NOT NULL""",
                (naam,),
            )
        ]
        oordeel = statistiek.beoordeel(
            resultaten,
            min_trades=int(rap["min_trades_voor_uitspraak"]),
            betrouwbaarheid=float(rap["betrouwbaarheid"]),
            onderscheidend_vermogen=float(rap["onderscheidend_vermogen"]),
        )
        print(f"  {naam}: {oordeel.uitleg}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
