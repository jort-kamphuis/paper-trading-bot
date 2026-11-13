"""Wanneer komen de volgende kwartaalcijfers?

Dat staat vooraf vast en is dus geen vooruitkijken. We bewaren per symbool wat
er op welke dag bekend was, zodat je later kunt nagaan wat de AI wist.
Alleen voor aandelen; ETF's en crypto hebben geen kwartaalcijfers.
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
from datetime import date

import yfinance as yf


def haal_datum(symbool: str, vandaag: date) -> str | None:
    """De eerstvolgende datum van kwartaalcijfers, of None als die niet bekend is."""
    try:
        # yfinance schrijft bij een fout zelf een melding weg; die vangen we op.
        with contextlib.redirect_stderr(io.StringIO()):
            kalender = yf.Ticker(symbool).calendar
    except Exception:
        return None
    if not isinstance(kalender, dict):
        return None
    datums = kalender.get("Earnings Date") or []
    toekomst = sorted(d for d in datums if isinstance(d, date) and d >= vandaag)
    return toekomst[0].isoformat() if toekomst else None


def vernieuw(con: sqlite3.Connection, aandelen: list[str], vandaag: date | None = None) -> int:
    vandaag = vandaag or date.today()
    aantal = 0
    for symbool in aandelen:
        datum = haal_datum(symbool, vandaag)
        if datum is None:
            continue
        con.execute(
            """INSERT INTO kwartaalcijfers (symbool, opgehaald_op, datum) VALUES (?,?,?)
               ON CONFLICT(symbool, opgehaald_op) DO UPDATE SET datum=excluded.datum""",
            (symbool, vandaag.isoformat(), datum),
        )
        aantal += 1
    con.commit()
    return aantal


def bekend_op(con: sqlite3.Connection, symbool: str, datum: str) -> str | None:
    """De datum van de volgende kwartaalcijfers zoals die op 'datum' bekend was."""
    rij = con.execute(
        """SELECT datum FROM kwartaalcijfers
           WHERE symbool = ? AND opgehaald_op <= ? AND datum >= ?
           ORDER BY opgehaald_op DESC LIMIT 1""",
        (symbool, datum, datum),
    ).fetchone()
    return rij["datum"] if rij else None
