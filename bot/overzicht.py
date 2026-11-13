"""Teksten over de stand van zaken.

Deze functies geven gewone tekst terug, zodat ze straks ook voor de
Telegram-melding gebruikt kunnen worden (stap 4).
"""

from __future__ import annotations

import sqlite3

from bot import statistiek


def _euro(bedrag: float) -> str:
    return f"{bedrag:,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")


def portefeuille_standen(con: sqlite3.Connection, datum: str | None = None) -> list[dict]:
    """De laatste bekende stand van elke portefeuille."""
    if datum is None:
        rij = con.execute("SELECT MAX(datum) AS d FROM waarde").fetchone()
        datum = rij["d"] if rij else None
    if datum is None:
        return []

    rijen = con.execute(
        """SELECT w.portefeuille, w.datum, w.kas_eur, w.posities_eur, w.totaal_eur,
                  p.start_kapitaal, p.strategie, p.gestart_op
           FROM waarde w
           JOIN portefeuilles p ON p.naam = w.portefeuille
           WHERE w.datum = (SELECT MAX(datum) FROM waarde w2
                            WHERE w2.portefeuille = w.portefeuille AND w2.datum <= ?)
           ORDER BY w.totaal_eur DESC""",
        (datum,),
    ).fetchall()

    uit = []
    for r in rijen:
        uit.append(
            {
                "portefeuille": r["portefeuille"],
                "datum": r["datum"],
                "kas_eur": r["kas_eur"],
                "posities_eur": r["posities_eur"],
                "totaal_eur": r["totaal_eur"],
                "start_kapitaal": r["start_kapitaal"],
                "gestart_op": r["gestart_op"],
                "rendement_pct": statistiek.rendement_pct(
                    r["start_kapitaal"], r["totaal_eur"]
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
    regels = [f"Paper trading, {datum}", ""]

    standen = portefeuille_standen(con, datum)
    if not standen:
        regels.append("Nog geen standen vastgelegd.")
    for s in standen:
        teken = "+" if s["rendement_pct"] >= 0 else ""
        regels.append(f"{s['portefeuille']}: €{_euro(s['totaal_eur'])} ({teken}{s['rendement_pct']:.1f}%)")

    trades = trades_op(con, datum)
    regels.append("")
    if trades:
        regels.append(f"Trades ({len(trades)}):")
        for t in trades:
            resultaat = ""
            if t["actie"] == "VERKOOP" and t["resultaat_eur"] is not None:
                teken = "+" if t["resultaat_eur"] >= 0 else ""
                resultaat = f", {teken}€{t['resultaat_eur']:.2f}"
            zeker = f", zekerheid {t['zekerheid']:.2f}" if t["zekerheid"] is not None else ""
            regels.append(f"• {t['portefeuille']}: {t['actie'].lower()} {t['symbool']}{resultaat}{zeker}")
            if t["reden"]:
                regels.append(f"  {_kort(t['reden'])}")
    else:
        regels.append("Geen trades.")

    wachtend = openstaande_orders(con)
    if wachtend:
        regels.append(f"\nKlaar voor de volgende handelsdag: {len(wachtend)} order(s).")

    if extra:
        regels.append("")
        regels.extend(extra)

    regels.append("\nNep-geld, geen beleggingsadvies.")
    return "\n".join(regels)


def dagbericht(con: sqlite3.Connection, datum: str) -> str:
    """Een korte samenvatting van een dag, in gewone taal."""
    regels = [f"Paper trading-bot, stand van {datum}", ""]

    standen = portefeuille_standen(con, datum)
    if not standen:
        return "\n".join(regels + ["Nog geen standen vastgelegd."])

    for s in standen:
        teken = "+" if s["rendement_pct"] >= 0 else ""
        regels.append(
            f"{s['portefeuille']:16s} EUR {_euro(s['totaal_eur']):>9s}  "
            f"({teken}{s['rendement_pct']:.1f}%)"
        )

    trades = trades_op(con, datum)
    regels.append("")
    if trades:
        regels.append(f"Trades vandaag ({len(trades)}):")
        for t in trades:
            resultaat = ""
            if t["actie"] == "VERKOOP" and t["resultaat_eur"] is not None:
                teken = "+" if t["resultaat_eur"] >= 0 else ""
                resultaat = f"  resultaat {teken}{t['resultaat_eur']:.2f} euro"
            regels.append(
                f"  {t['portefeuille']:16s} {t['actie']:8s} {t['symbool']:10s} "
                f"EUR {t['bedrag_eur']:7.2f}{resultaat}"
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
