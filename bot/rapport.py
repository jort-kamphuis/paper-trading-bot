"""Het weekrapport: de cijfers per strategie, eerlijk opgeschreven.

Per portefeuille: rendement (totaal en deze week), het aantal trades en welk
deel winst gaf, de grootste daling, de Sharpe ratio, hoeveel van het geld
gemiddeld belegd was, de vergelijking met de meetlatten, en of het verschil al
iets betekent of nog geluk kan zijn, inclusief hoeveel trades er nog nodig zijn.

Alle risicocijfers worden gerekend op de meetkalender (de dagen dat de
Amerikaanse beurs open was), zie statistiek.op_kalender.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from bot import statistiek

# Waar elke strategie tegen wordt afgezet. gelijk_alle (gelijk verdeeld over
# alle symbolen) is de eerlijkste: wint een strategie daar niet van, dan voegde
# het kiezen niets toe. gelijk_ai bestaat alleen als de AI een deel volgt.
MEETLATTEN = ("benchmark_spy", "benchmark_btc", "gelijk_alle", "gelijk_ai")


@dataclass
class Vergelijking:
    tegen: str
    verschil_pp: float
    p_waarde: float | None


@dataclass
class Kerncijfers:
    portefeuille: str
    gestart_op: str
    waarde_usd: float
    rendement_pct: float
    rendement_week_pct: float | None
    trades: int
    afgerond: int
    win_pct: float | None
    max_daling_pct: float
    sharpe: float | None
    belegd_pct: float | None
    vergelijkingen: list[Vergelijking] = field(default_factory=list)
    oordeel: statistiek.Zekerheid | None = None
    is_meetlat: bool = False


def kalender(con: sqlite3.Connection, cfg: dict) -> list[str]:
    symbool = cfg["rapport"]["kalender_symbool"]
    return [
        r["datum"]
        for r in con.execute(
            "SELECT datum FROM koersen WHERE symbool = ? ORDER BY datum", (symbool,)
        )
    ]


def _reeks(con: sqlite3.Connection, pf: str, tot: str) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT datum, totaal_usd, posities_usd FROM waarde
           WHERE portefeuille = ? AND datum <= ? ORDER BY datum""",
        (pf, tot),
    ).fetchall()


def _rendement_tussen(rijen: list[sqlite3.Row], van: str) -> float | None:
    """Rendement van de laatste stand op of voor 'van' tot de laatste stand."""
    voor = [r for r in rijen if r["datum"] <= van]
    if not voor or not rijen:
        return None
    return statistiek.rendement_pct(voor[-1]["totaal_usd"], rijen[-1]["totaal_usd"])


def kerncijfers(
    con: sqlite3.Connection, cfg: dict, pf: str, tot: str, kal: list[str]
) -> Kerncijfers | None:
    info = con.execute("SELECT * FROM portefeuilles WHERE naam = ?", (pf,)).fetchone()
    rijen = _reeks(con, pf, tot)
    if info is None or not rijen:
        return None

    start = float(info["start_kapitaal"])
    datums, waarden = statistiek.op_kalender(
        [r["datum"] for r in rijen], [r["totaal_usd"] for r in rijen], kal
    )
    week_terug = (date.fromisoformat(tot) - timedelta(days=7)).isoformat()

    resultaten = [
        r["resultaat_usd"]
        for r in con.execute(
            """SELECT resultaat_usd FROM trades
               WHERE portefeuille = ? AND actie = 'VERKOOP' AND resultaat_usd IS NOT NULL
                 AND datum <= ?""",
            (pf, tot),
        )
    ]
    trades = con.execute(
        "SELECT COUNT(*) n FROM trades WHERE portefeuille = ? AND datum <= ?", (pf, tot)
    ).fetchone()["n"]
    belegd = [r["posities_usd"] / r["totaal_usd"] for r in rijen if r["totaal_usd"] > 0]

    rap = cfg["rapport"]
    return Kerncijfers(
        portefeuille=pf,
        gestart_op=info["gestart_op"] or rijen[0]["datum"],
        waarde_usd=rijen[-1]["totaal_usd"],
        rendement_pct=statistiek.rendement_pct(start, rijen[-1]["totaal_usd"]),
        rendement_week_pct=_rendement_tussen(rijen, week_terug),
        trades=trades,
        afgerond=len(resultaten),
        win_pct=statistiek.win_pct(resultaten),
        max_daling_pct=statistiek.max_daling_pct(waarden) if waarden else 0.0,
        sharpe=statistiek.sharpe(waarden, datums) if waarden else None,
        belegd_pct=100.0 * sum(belegd) / len(belegd) if belegd else None,
        oordeel=statistiek.beoordeel(
            resultaten,
            min_trades=int(rap["min_trades_voor_uitspraak"]),
            betrouwbaarheid=float(rap["betrouwbaarheid"]),
            onderscheidend_vermogen=float(rap["onderscheidend_vermogen"]),
        ),
        is_meetlat=pf in MEETLATTEN,
    )


def vergelijk(
    con: sqlite3.Connection, a: str, b: str, tot: str, kal: list[str]
) -> Vergelijking | None:
    """Vergelijk portefeuille a met b over de dagen dat ze allebei bestonden."""
    ra = {r["datum"]: r["totaal_usd"] for r in _reeks(con, a, tot)}
    rb = {r["datum"]: r["totaal_usd"] for r in _reeks(con, b, tot)}
    gedeeld = [d for d in kal if d in ra and d in rb]
    if len(gedeeld) < 2:
        return None
    wa = [ra[d] for d in gedeeld]
    wb = [rb[d] for d in gedeeld]
    verschil = statistiek.rendement_pct(wa[0], wa[-1]) - statistiek.rendement_pct(wb[0], wb[-1])
    p = statistiek.vergelijk_reeksen(
        list(statistiek.dagrendementen(wa)), list(statistiek.dagrendementen(wb))
    )
    return Vergelijking(tegen=b, verschil_pp=verschil, p_waarde=p)


EVEN_BELEGD = " (even belegd)"


def even_belegd_reeksen(
    con: sqlite3.Connection, pf: str, meetlat: str, tot: str, kal: list[str]
) -> tuple[list[str], list[float], list[float]] | None:
    """Dagrendementen van pf en van 'meetlat, even belegd als pf'.

    De meetlat even belegd: elke dag hetzelfde deel in de meetlat als pf de dag
    ervoor belegd had, de rest in kas (rendement 0). Zo meet het verschil hoe goed
    pf kiest, en niet hoeveel geld hij in kas houdt (review november 2026).
    """
    eigen = {r["datum"]: r for r in _reeks(con, pf, tot)}
    tegen = {r["datum"]: r["totaal_usd"] for r in _reeks(con, meetlat, tot)}
    dagen = [d for d in kal if d in eigen and d in tegen]
    if len(dagen) < 2:
        return None
    r_pf, r_eb = [], []
    for vorige, d in zip(dagen, dagen[1:]):
        w_vorig = eigen[vorige]["totaal_usd"]
        if not w_vorig or not tegen[vorige]:
            continue
        belegd = max(0.0, min(1.0, eigen[vorige]["posities_usd"] / w_vorig))
        r_pf.append(eigen[d]["totaal_usd"] / w_vorig - 1.0)
        r_eb.append(belegd * (tegen[d] / tegen[vorige] - 1.0))
    return dagen, r_pf, r_eb


def vergelijk_even_belegd(
    con: sqlite3.Connection, pf: str, meetlat: str, tot: str, kal: list[str]
) -> Vergelijking | None:
    reeksen = even_belegd_reeksen(con, pf, meetlat, tot, kal)
    if reeksen is None or not reeksen[1]:
        return None
    _, r_pf, r_eb = reeksen
    groei = lambda r: 100.0 * (float(np.prod([1.0 + x for x in r])) - 1.0)  # noqa: E731
    p = statistiek.vergelijk_reeksen(r_pf, r_eb)
    return Vergelijking(tegen=meetlat + EVEN_BELEGD, verschil_pp=groei(r_pf) - groei(r_eb), p_waarde=p)


def alle_kerncijfers(con: sqlite3.Connection, cfg: dict, tot: str) -> list[Kerncijfers]:
    kal = kalender(con, cfg)
    namen = [
        r["naam"]
        for r in con.execute(
            "SELECT naam FROM portefeuilles WHERE actief = 1 ORDER BY variant, naam"
        )
    ]
    uit: list[Kerncijfers] = []
    for naam in namen:
        k = kerncijfers(con, cfg, naam, tot, kal)
        if k is None:
            continue
        if not k.is_meetlat:
            for meetlat in MEETLATTEN:
                if meetlat in namen:
                    v = vergelijk(con, naam, meetlat, tot, kal)
                    if v:
                        k.vergelijkingen.append(v)
            # Ook tegen de gelijk verdeelde meetlat, even belegd als deze portefeuille:
            # dat meet het kiezen, los van hoeveel geld er in kas staat.
            for meetlat in ("gelijk_alle", "gelijk_ai"):
                if meetlat in namen:
                    v = vergelijk_even_belegd(con, naam, meetlat, tot, kal)
                    if v:
                        k.vergelijkingen.append(v)
        uit.append(k)
    return uit


# ------------------------------------------------------------------- tekst


from bot.tekst import getal  # noqa: E402  (ook gebruikt door de tests)


def _pct(x: float | None, teken: bool = True) -> str:
    return "-" if x is None else getal(x, 1, teken) + "%"


def _p(p: float | None) -> str:
    if p is None:
        return "te weinig dagen om te toetsen"
    # Tussenstanden zijn altijd verkennend: wie elke week kijkt en bij het eerste
    # lage getal conclusies trekt, maakt de toets waardeloos. Alleen de hoofdvraag op
    # de einddatum telt (README).
    return f"p {getal(p, 2)}, verkennend"


def blok(k: Kerncijfers) -> list[str]:
    """De regels over een portefeuille."""
    r = [k.portefeuille]
    r.append(
        f"  rendement {_pct(k.rendement_pct)} (deze week {_pct(k.rendement_week_pct)}), "
        f"waarde ${getal(k.waarde_usd, 2)}"
    )
    if k.is_meetlat:
        r.append(f"  grootste daling {_pct(k.max_daling_pct, teken=False)}")
        return r
    winst = f", {_pct(k.win_pct, teken=False)} met winst" if k.win_pct is not None else ""
    r.append(f"  trades {k.trades}, waarvan {k.afgerond} afgerond{winst}")
    sharpe = getal(k.sharpe, 2) if k.sharpe is not None else "- (nog te weinig dagen)"
    r.append(f"  grootste daling {_pct(k.max_daling_pct, teken=False)}, Sharpe {sharpe}")
    if k.belegd_pct is not None:
        r.append(f"  gemiddeld belegd: {_pct(k.belegd_pct, teken=False)} van het geld")
    for v in k.vergelijkingen:
        naam = {"benchmark_spy": "SPY", "benchmark_btc": "BTC", "gelijk_alle": "gelijk verdeeld (alle)",
                "gelijk_ai": "gelijk verdeeld (AI-lijst)",
                "gelijk_alle" + EVEN_BELEGD: "gelijk verdeeld (alle), even belegd",
                "gelijk_ai" + EVEN_BELEGD: "gelijk verdeeld (AI-lijst), even belegd"}.get(v.tegen, v.tegen)
        r.append(f"  tegen {naam}: {getal(v.verschil_pp, 1, teken=True)} procentpunt ({_p(v.p_waarde)})")
    if k.oordeel:
        r.append(f"  {k.oordeel.uitleg}")
    return r


def cijfers_als_tekst(cijfers: list[Kerncijfers], tot: str, start: str | None) -> str:
    regels = [f"Weekrapport paper trading, tot en met {tot}"]
    if start:
        dagen = (date.fromisoformat(tot) - date.fromisoformat(start)).days
        regels.append(f"De meting loopt sinds {start} ({dagen} dagen).")
    regels.append("")
    strategieen = [k for k in cijfers if not k.is_meetlat]
    meetlatten = [k for k in cijfers if k.is_meetlat]
    for k in strategieen:
        regels.extend(blok(k))
        regels.append("")
    if meetlatten:
        regels.append("Meetlatten (kopen en vasthouden, het hele bedrag in een keer):")
        for k in meetlatten:
            regels.extend(blok(k))
        regels.append("")
    regels.append(
        "Let op bij de vergelijking: de meetlatten zetten alles in een keer in de markt, "
        "de strategieën 10% per positie (hooguit 10 posities). 'Gemiddeld belegd' laat zien "
        "hoeveel geld er werkelijk aan het werk was. Alles in dollars, inclusief dividend."
    )
    return "\n".join(regels)
