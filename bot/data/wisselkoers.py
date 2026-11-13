"""De dagelijkse EUR/USD-koers, om dollarkoersen in euro's uit te drukken.

eurusd = hoeveel dollar een euro kost. Prijs in euro's = prijs in dollars / eurusd.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import yfinance as yf


def haal_eurusd(symbool: str, start: str, eind: str | None = None) -> list[dict]:
    """Haal de dagelijkse EUR/USD-koers op."""
    ruw = yf.Ticker(symbool).history(
        start=start, end=eind, interval="1d", auto_adjust=False
    )
    if ruw is None or len(ruw) == 0:
        return []
    from datetime import datetime, timezone

    # Vandaag is nog niet af: de koers van dit moment is geen dagkoers.
    vandaag = datetime.now(timezone.utc).date().isoformat()
    rijen = []
    for stempel, rij in ruw.iterrows():
        slot = rij.get("Close")
        if pd.isna(slot) or float(slot) <= 0:
            continue
        datum = stempel.date().isoformat()
        if datum >= vandaag:
            continue
        rijen.append({"datum": datum, "eurusd": float(slot)})
    return rijen


class Wisselkoers:
    """Zoekt de koers op van een dag, of de laatste koers daarvoor.

    De valutamarkt is in het weekend dicht, terwijl crypto dan wel handelt.
    Daarom pakken we voor zo'n dag de laatst bekende koers. Dat is een keuze,
    geen meetwaarde: zie README.
    """

    def __init__(self, koersen: dict[str, float]):
        self._koersen = dict(koersen)
        self._datums = sorted(self._koersen)

    @classmethod
    def uit_database(cls, con: sqlite3.Connection) -> "Wisselkoers":
        from bot.database import wisselkoersen_dict

        return cls(wisselkoersen_dict(con))

    def op(self, datum: str) -> float:
        """De koers van die dag, of de laatste koers ervoor."""
        if not self._datums:
            raise ValueError(
                "geen wisselkoersen in de database; draai eerst het ophalen van data"
            )
        if datum in self._koersen:
            return self._koersen[datum]
        import bisect

        i = bisect.bisect_right(self._datums, datum)
        if i == 0:
            # Gevraagde dag ligt voor de eerste koers die we hebben.
            return self._koersen[self._datums[0]]
        return self._koersen[self._datums[i - 1]]

    def naar_eur(self, bedrag_usd: float, datum: str) -> float:
        return bedrag_usd / self.op(datum)

    def __len__(self) -> int:
        return len(self._koersen)
