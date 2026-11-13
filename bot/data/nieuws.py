"""Nieuwskoppen ophalen en bewaren.

Hier zit de strengste regel van het hele project. De AI-analist mag alleen
koppen zien die op het moment van beslissen al gepubliceerd waren. Anders
"weet" hij hoe het afliep en meet je niets meer.

Daarom:
* Elke kop wordt opgeslagen met zijn echte publicatietijd (pubDate) en met het
  moment waarop wij hem ophaalden. Beide staan in de database, zodat je
  achteraf kunt narekenen dat er niets uit de toekomst is gebruikt.
* Bij het opvragen filteren we hard op publicatietijd.

Let op: yfinance geeft alleen de tien nieuwste berichten per symbool. Er is
dus geen nieuwsgeschiedenis. Daarom kan de AI-analist niet worden getest op het
verleden; die begint vandaag en loopt vooruit mee.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import yfinance as yf


def _tijd(waarde) -> datetime | None:
    """Maak van een tijdstempel een datum met tijdzone, of None."""
    if not waarde:
        return None
    if isinstance(waarde, datetime):
        return waarde if waarde.tzinfo else waarde.replace(tzinfo=timezone.utc)
    tekst = str(waarde).strip().replace("Z", "+00:00")
    try:
        stempel = datetime.fromisoformat(tekst)
    except ValueError:
        return None
    return stempel if stempel.tzinfo else stempel.replace(tzinfo=timezone.utc)


def yahoo_symbool(symbool: str) -> str:
    """Hoe Yahoo een symbool kent. Crypto heet daar BTC-USD, niet BTC/USDT.

    Dit ging mis: met BTC/USDT kreeg de AI nooit cryptonieuws (0 koppen in de
    proefweek). Het nieuws wordt wel onder ons eigen symbool opgeslagen.
    """
    if "/" in symbool:
        basis = symbool.split("/")[0]
        return f"{basis}-USD"
    return symbool


def haal_koppen(symbool: str, max_aantal: int = 10) -> list[dict]:
    """Haal de nieuwste koppen op voor een symbool.

    Berichten zonder publicatietijd worden weggelaten. Zonder die tijd kunnen
    we niet aantonen dat ze op tijd bekend waren, en dan gebruiken we ze niet.
    """
    nu = datetime.now(timezone.utc)
    try:
        ruw = yf.Ticker(yahoo_symbool(symbool)).news or []
    except Exception as fout:
        print(f"  nieuws {symbool}: niet gelukt ({type(fout).__name__}: {str(fout)[:80]})")
        return []

    uit: list[dict] = []
    for bericht in ruw[:max_aantal]:
        inhoud = bericht.get("content") or {}
        gepubliceerd = _tijd(inhoud.get("pubDate")) or _tijd(inhoud.get("displayTime"))
        titel = (inhoud.get("title") or "").strip()
        if not titel or gepubliceerd is None:
            continue
        if gepubliceerd > nu:
            # Een publicatietijd in de toekomst kan niet kloppen. Overslaan.
            continue
        aanbieder = (inhoud.get("provider") or {}).get("displayName")
        link = (inhoud.get("canonicalUrl") or {}).get("url")
        uit.append(
            {
                "symbool": symbool,
                "datum": gepubliceerd.date().isoformat(),
                "gepubliceerd": gepubliceerd.isoformat(),
                "titel": titel,
                "samenvatting": (inhoud.get("summary") or "").strip() or None,
                "bron": aanbieder,
                "url": link,
                "opgehaald_op": nu.isoformat(),
            }
        )
    return uit


def bewaar(con: sqlite3.Connection, koppen: list[dict]) -> int:
    if not koppen:
        return 0
    con.executemany(
        """INSERT INTO nieuws
           (symbool, datum, gepubliceerd, titel, samenvatting, bron, url, opgehaald_op)
           VALUES (:symbool, :datum, :gepubliceerd, :titel, :samenvatting, :bron, :url,
                   :opgehaald_op)
           ON CONFLICT(symbool, datum, titel) DO NOTHING""",
        koppen,
    )
    con.commit()
    return len(koppen)


def koppen_voor(
    con: sqlite3.Connection,
    symbool: str,
    voor_moment: str,
    max_dagen_oud: int = 3,
    max_aantal: int = 8,
) -> list[sqlite3.Row]:
    """De koppen die op 'voor_moment' al gepubliceerd waren.

    'voor_moment' is een tijdstempel met tijdzone. Alles wat daarna is
    gepubliceerd, blijft buiten beeld. Dat is de regel die vooruitkijken
    onmogelijk maakt.
    """
    moment = _tijd(voor_moment)
    if moment is None:
        return []
    ondergrens = (moment - timedelta(days=max_dagen_oud)).isoformat()
    return con.execute(
        """SELECT * FROM nieuws
           WHERE symbool = ?
             AND gepubliceerd <= ?
             AND gepubliceerd >= ?
           ORDER BY gepubliceerd DESC
           LIMIT ?""",
        (symbool, moment.isoformat(), ondergrens, max_aantal),
    ).fetchall()


def vernieuw(con: sqlite3.Connection, symbolen: list[str], max_aantal: int = 10) -> int:
    """Haal voor elk symbool het nieuwste nieuws op en bewaar het."""
    totaal = 0
    for symbool in symbolen:
        totaal += bewaar(con, haal_koppen(symbool, max_aantal=max_aantal))
    return totaal
