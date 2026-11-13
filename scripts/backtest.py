"""Backtest over het afgelopen jaar, alleen voor de regelstrategieën.

LET OP, EN DIT IS BELANGRIJK:
Een backtest is GEEN bewijs dat een strategie werkt. Je kijkt naar het
verleden met regels die je nu kiest, en dat ziet er bijna altijd beter uit dan
wat je vooruit haalt. Hij is hier voor een ander doel: controleren of de motor
klopt, en zien hoeveel trades een strategie ongeveer maakt. De echte test
begint vanaf vandaag, met geld dat vooruit wordt ingezet.

De AI-analist doet niet mee in de backtest: die zou nieuwskoppen uit het
verleden moeten lezen die we nu niet meer op dezelfde manier kunnen ophalen,
en dan weet je nooit of hij toch iets van later heeft meegekregen.

Gebruik:
    python scripts/backtest.py
    python scripts/backtest.py --jaren 2
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as conf
from bot import database, statistiek
from bot.data.wisselkoers import Wisselkoers
from bot.markt import Markt
from bot.opzet import bouw_motor
from bot.strategieen.benchmarks import GelijkVerdeeld, KoopEnHoud

MEETLAT = (KoopEnHoud, GelijkVerdeeld)


def _reeks(con, portefeuille: str) -> tuple[list[str], list[float]]:
    rijen = con.execute(
        "SELECT datum, totaal_usd FROM waarde WHERE portefeuille = ? ORDER BY datum",
        (portefeuille,),
    ).fetchall()
    return [r["datum"] for r in rijen], [r["totaal_usd"] for r in rijen]


def _resultaten(con, portefeuille: str) -> list[float]:
    return [
        r["resultaat_usd"]
        for r in con.execute(
            """SELECT resultaat_usd FROM trades
               WHERE portefeuille = ? AND actie = 'VERKOOP' AND resultaat_usd IS NOT NULL
               ORDER BY datum""",
            (portefeuille,),
        )
    ]


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest van de regelstrategieën")
    p.add_argument("--jaren", type=float, default=None, help="hoeveel jaar terug")
    # (de koersen staan in data/backtest.db; met --vernieuw worden ze opnieuw opgehaald)
    p.add_argument("--vernieuw", action="store_true", help="de koersen opnieuw ophalen")
    args = p.parse_args()

    cfg = conf.laad()
    jaren = args.jaren if args.jaren is not None else cfg["backtest"]["jaren_terug"]

    # De backtest heeft een eigen database, met eigen koersen. De echte
    # database (data/trading.db) staat in git en wordt alleen door de
    # nachtelijke ronde op GitHub bijgewerkt. Zou de backtest daar koersen in
    # zetten, dan botst dat bij de volgende 'git pull'.
    doel_pad = conf.PROJECT / "data" / "backtest.db"
    con = database.verbind(doel_pad)
    heeft_koersen = con.execute("SELECT COUNT(*) n FROM koersen").fetchone()["n"] > 0
    if args.vernieuw or not heeft_koersen:
        print("Koersen voor de backtest ophalen (eenmalig, ongeveer een minuut)...")
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "vernieuw_data.py"),
                "--jaren", str(max(3.0, float(jaren) + 1.0)),
                "--db", str(doel_pad),
                "--alles",  # ook oudere dagen opnieuw, met dividend en splitsingen
            ],
            check=True,
        )
        con.close()
        con = database.verbind(doel_pad)
        print()

    markt = Markt.uit_database(con)
    if not markt.datums:
        print("Geen koersen in de database. Draai eerst: python scripts/vernieuw_data.py")
        return 1
    wk = Wisselkoers.uit_database(con)
    if len(wk) == 0:
        print("Geen wisselkoersen in de database. Draai eerst: python scripts/vernieuw_data.py")
        return 1

    eind = markt.datums[-1]
    begin = (date.fromisoformat(eind) - timedelta(days=int(365.25 * jaren))).isoformat()
    begin = max(begin, markt.datums[0])

    # Controle: is er genoeg geschiedenis vooraf voor het 100-daags gemiddelde?
    opwarm = int(cfg["backtest"]["opwarm_dagen"])
    dagen_voor_begin = sum(1 for d in markt.datums if d < begin)
    print(f"Backtest van {begin} tot {eind}")
    print(f"Geschiedenis vooraf: {dagen_voor_begin} dagen (nodig: ongeveer {opwarm})")
    if dagen_voor_begin < opwarm:
        print(
            f"  LET OP: te weinig geschiedenis vooraf. De trendstrategie kan dan in\n"
            f"  het begin nog niets doen. Haal meer jaren op met --jaren bij vernieuw_data."
        )
    print()

    database.leeg_handelsdata(con)
    # Precies dezelfde opstelling als de dagelijkse ronde, zodat de backtest
    # niet per ongeluk andere regels hanteert.
    motor = bouw_motor(con, cfg, markt, wk, begin=begin, stil=False)

    aanwezig = [s for s in conf.alle_symbolen(cfg) if markt.heeft(s)]
    print(f"{len(motor.deelnemers)} portefeuilles, {len(aanwezig)} symbolen. Draaien...")
    dagen = motor.draai_periode(begin, eind, stil=False)
    print(f"\n{dagen} handelsdagen verwerkt.\n")

    # ---- uitkomst ----
    start_kapitaal = float(cfg["algemeen"]["start_kapitaal_usd"])
    rap = cfg["rapport"]

    # Alle risicocijfers op dezelfde meetkalender: de dagen dat de Amerikaanse
    # beurs open was. Zie statistiek.op_kalender voor het waarom.
    kalender = markt.handelsdagen_van(cfg["rapport"]["kalender_symbool"]) or markt.datums
    kalender = [d for d in kalender if begin <= d <= eind]
    print(f"Meetkalender: {len(kalender)} beursdagen\n")

    benchmark_reeksen: dict[str, tuple[list[str], list[float]]] = {}
    for deelnemer in motor.deelnemers:
        if isinstance(deelnemer.strategie, MEETLAT):
            naam = deelnemer.portefeuille.naam
            benchmark_reeksen[naam] = statistiek.op_kalender(*_reeks(con, naam), kalender)

    kop = f"{'strategie':16s} {'eind':>9s} {'rend.':>8s} {'trades':>7s} {'winst%':>7s} {'daling':>8s} {'sharpe':>7s}"
    print(kop)
    print("-" * len(kop))

    regels = []
    for deelnemer in motor.deelnemers:
        naam = deelnemer.portefeuille.naam
        datums, waarden = statistiek.op_kalender(*_reeks(con, naam), kalender)
        if not waarden:
            continue
        resultaten = _resultaten(con, naam)
        aantal_trades = con.execute(
            "SELECT COUNT(*) n FROM trades WHERE portefeuille = ?", (naam,)
        ).fetchone()["n"]
        rend = statistiek.rendement_pct(start_kapitaal, waarden[-1])
        winst = statistiek.win_pct(resultaten)
        daling = statistiek.max_daling_pct(waarden)
        sh = statistiek.sharpe(waarden, datums)
        print(
            f"{naam:16s} {waarden[-1]:9.2f} {rend:+7.1f}% {aantal_trades:7d} "
            f"{(f'{winst:.0f}%' if winst is not None else '-'):>7s} "
            f"{daling:7.1f}% {(f'{sh:.2f}' if sh is not None else '-'):>7s}"
        )
        regels.append((deelnemer, naam, datums, waarden, resultaten))

    # ---- hoe zeker is dit? ----
    print("\nIs dit al iets, of kan het nog geluk zijn?")
    print("(onthoud: dit is een backtest, dus ook een nette uitkomst bewijst niets)\n")
    for deelnemer, naam, datums, waarden, resultaten in regels:
        if isinstance(deelnemer.strategie, MEETLAT):
            continue
        oordeel = statistiek.beoordeel(
            resultaten,
            min_trades=int(rap["min_trades_voor_uitspraak"]),
            betrouwbaarheid=float(rap["betrouwbaarheid"]),
            onderscheidend_vermogen=float(rap["onderscheidend_vermogen"]),
        )
        print(f"  {naam}:")
        print(f"    {oordeel.uitleg}")
        if oordeel.aantal:
            print(f"    gemiddeld resultaat per afgeronde trade: {oordeel.gemiddelde:+.2f} dollar")

        for bnaam, (bdatums, bwaarden) in benchmark_reeksen.items():
            p = statistiek.vergelijk_reeksen(
                list(statistiek.dagrendementen(waarden)),
                list(statistiek.dagrendementen(bwaarden)),
            )
            bench_rend = statistiek.rendement_pct(start_kapitaal, bwaarden[-1]) if bwaarden else 0.0
            eigen_rend = statistiek.rendement_pct(start_kapitaal, waarden[-1])
            verschil = eigen_rend - bench_rend
            tekst = f"    tegen {bnaam}: {verschil:+.1f} procentpunt"
            if p is not None:
                tekst += f" (p-waarde {p:.3f}{', betekenisvol' if p <= 0.05 else ', nog geen bewijs'})"
            print(tekst)
        print()

    print(f"Opgeslagen in: {doel_pad}")
    print(
        "\nNogmaals: een backtest is geen bewijs. Hij laat zien dat de motor werkt\n"
        "en hoeveel trades elke strategie ongeveer maakt."
    )
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
