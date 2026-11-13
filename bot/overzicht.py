"""Teksten over de stand van zaken.

Deze functies geven gewone tekst terug, zodat ze straks ook voor de
Telegram-melding gebruikt kunnen worden (stap 4).
"""

from __future__ import annotations

import sqlite3

from bot import statistiek


def _euro(bedrag: float) -> str:
    return f"{bedrag:,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")


def _in_euro(con: sqlite3.Connection, usd: float, datum: str) -> float | None:
    """Alleen voor de weergave: dollars omrekenen met de laatst bekende EUR/USD-koers."""
    rij = con.execute(
        "SELECT eurusd FROM wisselkoersen WHERE datum <= ? ORDER BY datum DESC LIMIT 1", (datum,)
    ).fetchone()
    return usd / rij['eurusd'] if rij and rij['eurusd'] else None


def portefeuille_standen(con: sqlite3.Connection, datum: str | None = None) -> list[dict]:
    """De laatste bekende stand van elke portefeuille."""
    if datum is None:
        rij = con.execute("SELECT MAX(datum) AS d FROM waarde").fetchone()
        datum = rij["d"] if rij else None
    if datum is None:
        return []

    rijen = con.execute(
        """SELECT w.portefeuille, w.datum, w.kas_usd, w.posities_usd, w.totaal_usd,
                  p.start_kapitaal, p.strategie, p.gestart_op
           FROM waarde w
           JOIN portefeuilles p ON p.naam = w.portefeuille
           WHERE p.actief = 1
             AND w.datum = (SELECT MAX(datum) FROM waarde w2
                            WHERE w2.portefeuille = w.portefeuille AND w2.datum <= ?)
           ORDER BY w.totaal_usd DESC""",
        (datum,),
    ).fetchall()

    uit = []
    for r in rijen:
        uit.append(
            {
                "portefeuille": r["portefeuille"],
                "datum": r["datum"],
                "kas_usd": r["kas_usd"],
                "posities_usd": r["posities_usd"],
                "totaal_usd": r["totaal_usd"],
                "start_kapitaal": r["start_kapitaal"],
                "gestart_op": r["gestart_op"],
                "rendement_pct": statistiek.rendement_pct(
                    r["start_kapitaal"], r["totaal_usd"]
                ),
            }
        )
    return uit


def trades_op(con: sqlite3.Connection, datum: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM trades WHERE datum = ? ORDER BY portefeuille, actie, symbool",
        (datum,),
    ).fetchall()


def openstaande_orders(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM wachtrij ORDER BY portefeuille, actie, symbool"
    ).fetchall()


def open_posities(con: sqlite3.Connection, portefeuille: str | None = None) -> list[sqlite3.Row]:
    if portefeuille:
        return con.execute(
            "SELECT * FROM posities WHERE portefeuille = ? ORDER BY symbool",
            (portefeuille,),
        ).fetchall()
    return con.execute("SELECT * FROM posities ORDER BY portefeuille, symbool").fetchall()


def ai_samenvatting(con: sqlite3.Connection, datum: str) -> str | None:
    """Een regel over wat de AI-analist over deze dag besloot. None als hij niets deed."""
    rijen = con.execute(
        """SELECT actie, COUNT(*) n FROM ai_besluiten
           WHERE datum = ? AND COALESCE(overgeslagen, '') != 'geen koers op deze dag'
           GROUP BY actie""",
        (datum,),
    ).fetchall()
    if not rijen:
        return None
    telling = {r["actie"]: r["n"] for r in rijen}
    totaal = sum(telling.values())
    kopen, verkopen = telling.get("KOOP", 0), telling.get("VERKOOP", 0)
    if not kopen and not verkopen:
        return f"AI: {totaal} beoordelingen, 0 keer kopen of verkopen."
    return f"AI: {totaal} beoordelingen, {kopen} keer kopen, {verkopen} keer verkopen."


def _kort(tekst: str | None, max_lengte: int = 160) -> str:
    tekst = " ".join((tekst or "").split())
    return tekst if len(tekst) <= max_lengte else tekst[: max_lengte - 1].rstrip() + "…"


def telegram_dagbericht(
    con: sqlite3.Connection, datum: str, extra: list[str] | None = None
) -> str:
    """Het korte dagbericht voor Telegram.

    Kort genoeg om op een telefoon in een oogopslag te lezen: de stand per
    portefeuille, de trades van die dag met hun reden, en wat er klaarstaat.
    """
    from bot.tekst import datum_lang, dollar, euro, getal, pct

    regels = [f"Paper trading: stand na de beursdag van {datum_lang(datum)}", ""]

    standen = portefeuille_standen(con, datum)
    if not standen:
        regels.append("Nog geen standen vastgelegd.")
    for s in standen:
        in_euro = _in_euro(con, s['totaal_usd'], s['datum'])
        erbij = f", ≈ {euro(in_euro)}" if in_euro is not None else ""
        regels.append(f"{s['portefeuille']}: {dollar(s['totaal_usd'])} ({pct(s['rendement_pct'])}{erbij})")

    trades = trades_op(con, datum)
    regels.append("")
    if trades:
        regels.append(f"Trades ({len(trades)}):")
        for t in trades:
            resultaat = ""
            if t["actie"] == "VERKOOP" and t["resultaat_usd"] is not None:
                teken = "+" if t["resultaat_usd"] >= 0 else "-"
                resultaat = f", {teken}{dollar(abs(t['resultaat_usd']))}"
            # Bij de AI is dit de kans die het model gaf; de regelstrategieen hebben er geen.
            zeker = f", kans {getal(t['zekerheid'], 2)}" if t["zekerheid"] is not None else ""
            regels.append(f"• {t['portefeuille']}: {t['actie'].lower()} {t['symbool']}{resultaat}{zeker}")
            if t["reden"]:
                regels.append(f"  {_kort(t['reden'])}")
    else:
        regels.append("Geen trades.")

    ai = ai_samenvatting(con, datum)
    if ai:
        regels.append("")
        regels.append(ai)

    wachtend = openstaande_orders(con)
    if wachtend:
        regels.append(f"\nKlaar voor de volgende handelsdag: {len(wachtend)} order(s).")

    if extra:
        regels.append("")
        regels.extend(extra)

    regels.append("\nNep-geld, geen beleggingsadvies.")
    return "\n".join(regels)


def dagbericht(con: sqlite3.Connection, datum: str) -> str:
    """Een korte samenvatting van een dag, in gewone taal. Alles in dollars."""
    from bot.tekst import dollar, getal, pct

    regels = [f"Paper trading-bot, stand van {datum}", ""]

    standen = portefeuille_standen(con, datum)
    if not standen:
        return "\n".join(regels + ["Nog geen standen vastgelegd."])

    for s in standen:
        regels.append(f"{s['portefeuille']:18s} {dollar(s['totaal_usd']):>11s}  ({pct(s['rendement_pct'])})")

    trades = trades_op(con, datum)
    regels.append("")
    if trades:
        regels.append(f"Trades vandaag ({len(trades)}):")
        for t in trades:
            resultaat = ""
            if t["actie"] == "VERKOOP" and t["resultaat_usd"] is not None:
                resultaat = f"  resultaat {getal(t['resultaat_usd'], 2, teken=True)} dollar"
            regels.append(
                f"  {t['portefeuille']:16s} {t['actie']:8s} {t['symbool']:10s} "
                f"{dollar(t['bedrag_usd']):>9s}{resultaat}"
            )
            if t["reden"]:
                regels.append(f"      reden: {t['reden']}")
    else:
        regels.append("Geen trades vandaag.")

    wachtend = openstaande_orders(con)
    if wachtend:
        regels.append("")
        regels.append(f"Staat klaar voor de volgende handelsdag ({len(wachtend)}):")
        for w in wachtend:
            regels.append(
                f"  {w['portefeuille']:16s} {w['actie']:8s} {w['symbool']:10s} "
                f"(besloten op {w['besluit_datum']})"
            )

    return "\n".join(regels)
