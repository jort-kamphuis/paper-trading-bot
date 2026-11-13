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
from bot.markt import Markt
from bot.motor import Motor
from bot.strategieen import maak_strategieen
from bot.strategieen.benchmarks import KoopEnHoud
from bot.data.wisselkoers import Wisselkoers


def _reeks(con, portefeuille: str) -> tuple[list[str], list[float]]:
    rijen = con.execute(
        "SELECT datum, totaal_eur FROM waarde WHERE portefeuille = ? ORDER BY datum",
        (portefeuille,),
    ).fetchall()
    return [r["datum"] for r in rijen], [r["totaal_eur"] for r in rijen]


def _resultaten(con, portefeuille: str) -> list[float]:
    return [
        r["resultaat_eur"]
        for r in con.execute(
            """SELECT resultaat_eur FROM trades
               WHERE portefeuille = ? AND actie = 'VERKOOP' AND resultaat_eur IS NOT NULL
               ORDER BY datum""",
            (portefeuille,),
        )
    ]


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest van de regelstrategieën")
    p.add_argument("--jaren", type=float, default=None, help="hoeveel jaar terug")
    p.add_argument("--db", default=None, help="database met de koersen")
    args = p.parse_args()

    cfg = conf.laad()
    jaren = args.jaren if args.jaren is not None else cfg["backtest"]["jaren_terug"]

    # De koersen staan in de gewone database; de uitkomst van de backtest komt
    # in een eigen bestand, zodat de echte resultaten nooit vervuild raken.
    bron = database.verbind(args.db)
    doel_pad = conf.PROJECT / "data" / "backtest.db"
    if doel_pad.exists():
        doel_pad.unlink()
    con = database.verbind(doel_pad)

    # Koersen en wisselkoersen overnemen.
    for rij in bron.execute("SELECT * FROM koersen"):
        con.execute(
            """INSERT OR REPLACE INTO koersen
               (symbool, datum, open, hoog, laag, slot, volume, valuta, bron)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                rij["symbool"], rij["datum"], rij["open"], rij["hoog"], rij["laag"],
                rij["slot"], rij["volume"], rij["valuta"], rij["bron"],
            ),
        )
    for rij in bron.execute("SELECT * FROM wisselkoersen"):
        con.execute(
            "INSERT OR REPLACE INTO wisselkoersen (datum, eurusd) VALUES (?,?)",
            (rij["datum"], rij["eurusd"]),
        )
    con.commit()
    bron.close()

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
    motor = Motor(con, cfg, markt, wk)

    symbolen = conf.alle_symbolen(cfg)
    aanwezig = [s for s in symbolen if markt.heeft(s)]
    ontbreekt = [s for s in symbolen if not markt.heeft(s)]
    if ontbreekt:
        print(f"Geen koersen voor: {ontbreekt}\n")

    for strategie in maak_strategieen(cfg):
        motor.voeg_toe(strategie, aanwezig, gestart_op=begin)
    if cfg["strategieen"]["benchmarks"]["actief"]:
        for symbool in cfg["strategieen"]["benchmarks"]["posities"]:
            if markt.heeft(symbool):
                motor.voeg_toe(KoopEnHoud(symbool), [symbool], gestart_op=begin)
            else:
                print(f"Meetlat {symbool} overgeslagen: geen koersen")

    print(f"{len(motor.deelnemers)} portefeuilles, {len(aanwezig)} symbolen. Draaien...")
    dagen = motor.draai_periode(begin, eind, stil=False)
    print(f"\n{dagen} handelsdagen verwerkt.\n")

    # ---- uitkomst ----
    start_kapitaal = float(cfg["algemeen"]["start_kapitaal_eur"])
    rap = cfg["rapport"]

    # Alle risicocijfers op dezelfde meetkalender: de dagen dat de Amerikaanse
    # beurs open was. Zie statistiek.op_kalender voor het waarom.
    kalender = markt.handelsdagen_van("SPY") or markt.datums
    kalender = [d for d in kalender if begin <= d <= eind]
    print(f"Meetkalender: {len(kalender)} beursdagen\n")

    benchmark_reeksen: dict[str, tuple[list[str], list[float]]] = {}
    for deelnemer in motor.deelnemers:
        if isinstance(deelnemer.strategie, KoopEnHoud):
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
        if isinstance(deelnemer.strategie, KoopEnHoud):
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
            print(f"    gemiddeld resultaat per afgeronde trade: {oordeel.gemiddelde:+.2f} euro")

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
