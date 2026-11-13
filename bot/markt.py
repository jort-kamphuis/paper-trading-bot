"""De koershistorie, klaar voor gebruik door de strategieën.

Belangrijk: een strategie krijgt via dit object nooit een koers van na de
beslisdag te zien. Daar zijn de methodes op gebouwd.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd

from bot import indicatoren


@dataclass
class Bar:
    """Een dagkoers van een symbool."""

    symbool: str
    datum: str
    open: float
    slot: float
    valuta: str


class Markt:
    def __init__(self, per_symbool: dict[str, pd.DataFrame]):
        self._data = per_symbool
        self._bereken_kengetallen()
        alle_datums: set[str] = set()
        for df in self._data.values():
            alle_datums.update(df.index)
        self.datums: list[str] = sorted(alle_datums)
        self._bouw_slotmatrix()

    # ---------- inlezen ----------

    @classmethod
    def uit_database(
        cls, con: sqlite3.Connection, symbolen: list[str] | None = None
    ) -> "Markt":
        vraag = "SELECT symbool, datum, open, hoog, laag, slot, volume, valuta FROM koersen"
        params: tuple = ()
        if symbolen:
            plekken = ",".join("?" * len(symbolen))
            vraag += f" WHERE symbool IN ({plekken})"
            params = tuple(symbolen)
        vraag += " ORDER BY symbool, datum"

        rijen = con.execute(vraag, params).fetchall()
        per_symbool: dict[str, pd.DataFrame] = {}
        if not rijen:
            return cls(per_symbool)

        df = pd.DataFrame([dict(r) for r in rijen])
        for symbool, deel in df.groupby("symbool", sort=True):
            deel = deel.drop(columns=["symbool"]).set_index("datum").sort_index()
            per_symbool[str(symbool)] = deel
        return cls(per_symbool)

    def _bereken_kengetallen(self) -> None:
        """Reken de kengetallen een keer uit over de hele reeks (zie indicatoren.py)."""
        for df in self._data.values():
            slot = df["slot"]
            df["ma20"] = indicatoren.voortschrijdend_gemiddelde(slot, 20)
            df["ma100"] = indicatoren.voortschrijdend_gemiddelde(slot, 100)
            df["rsi14"] = indicatoren.rsi(slot, 14)
            df["rend60"] = indicatoren.rendement_over(slot, 60)

    def _bouw_slotmatrix(self) -> None:
        """Een tabel met alle slotkoersen, waarin gaten worden opgevuld.

        Op een dag dat de beurs dicht is, is een aandeel nog steeds iets waard:
        namelijk de laatste koers. Dat vullen we hier in (alleen vooruit, dus
        nooit met een koers van later). Zonder dit zou de waarde van een
        portefeuille in het weekend wegvallen.
        """
        self._valuta_van: dict[str, str] = {}
        if not self._data:
            self._slot_ingevuld = pd.DataFrame()
            return
        kolommen = {s: df["slot"] for s, df in self._data.items()}
        matrix = pd.DataFrame(kolommen).sort_index().ffill()
        self._slot_ingevuld = matrix
        for symbool, df in self._data.items():
            self._valuta_van[symbool] = str(df["valuta"].iloc[0]) if len(df) else "USD"

    def handelsdagen_van(self, symbool: str) -> list[str]:
        """De dagen waarop dit symbool echt gehandeld heeft."""
        df = self._data.get(symbool)
        return [] if df is None else [str(d) for d in df.index]

    # ---------- opvragen ----------

    @property
    def symbolen(self) -> list[str]:
        return sorted(self._data)

    def heeft(self, symbool: str) -> bool:
        return symbool in self._data

    def bar(self, symbool: str, datum: str) -> Bar | None:
        """De koers van die dag, of None als er die dag niet gehandeld is."""
        df = self._data.get(symbool)
        if df is None or datum not in df.index:
            return None
        rij = df.loc[datum]
        return Bar(
            symbool=symbool,
            datum=datum,
            open=float(rij["open"]),
            slot=float(rij["slot"]),
            valuta=str(rij["valuta"]),
        )

    @staticmethod
    def _zorg_voor(df: pd.DataFrame, naam: str) -> bool:
        """Reken een kengetal uit als het er nog niet is, zoals 'ma30' of 'rend90'.

        Zo kan een challenger met andere instellingen (bijvoorbeeld 90 dagen
        terugkijken in plaats van 60) gewoon draaien. Ook deze kengetallen
        kijken alleen achteruit, net als de vaste (zie indicatoren.py).
        Geeft False bij een naam die we niet kennen.
        """
        if naam in df.columns:
            return True
        import re

        gevonden = re.fullmatch(r"(ma|rsi|rend)(\d+)", naam)
        if not gevonden:
            return False
        soort, periode = gevonden.group(1), int(gevonden.group(2))
        if periode < 1:
            return False
        slot = df["slot"]
        if soort == "ma":
            df[naam] = indicatoren.voortschrijdend_gemiddelde(slot, periode)
        elif soort == "rsi":
            df[naam] = indicatoren.rsi(slot, periode)
        else:
            df[naam] = indicatoren.rendement_over(slot, periode)
        return True

    def kengetal(self, symbool: str, datum: str, naam: str) -> float | None:
        """Een kengetal op die dag, bijvoorbeeld 'rsi14'. None als het er niet is."""
        df = self._data.get(symbool)
        if df is None or datum not in df.index or not self._zorg_voor(df, naam):
            return None
        waarde = df.at[datum, naam]
        if pd.isna(waarde):
            return None
        return float(waarde)

    def kengetal_vorige_dag(self, symbool: str, datum: str, naam: str) -> float | None:
        """Hetzelfde kengetal, maar van de vorige beursdag van dit symbool."""
        df = self._data.get(symbool)
        if df is None or datum not in df.index or not self._zorg_voor(df, naam):
            return None
        plek = df.index.get_loc(datum)
        if not isinstance(plek, int) or plek < 1:
            return None
        waarde = df.iloc[plek - 1][naam]
        if pd.isna(waarde):
            return None
        return float(waarde)

    def tot_en_met(self, symbool: str, datum: str) -> pd.DataFrame:
        """De geschiedenis tot en met die dag. Nooit verder."""
        df = self._data.get(symbool)
        if df is None:
            return pd.DataFrame()
        return df.loc[df.index <= datum]

    def volgende_bar_na(self, symbool: str, datum: str) -> Bar | None:
        """De eerste dag na <datum> waarop dit symbool handelde. Hier voeren we orders uit."""
        df = self._data.get(symbool)
        if df is None:
            return None
        later = df.index[df.index > datum]
        if len(later) == 0:
            return None
        return self.bar(symbool, str(later[0]))

    def beursdagen_tussen(self, symbool: str, van: str, tot: str) -> int:
        """Hoeveel handelsdagen van dit symbool er tussen twee datums liggen."""
        df = self._data.get(symbool)
        if df is None:
            return 0
        return int(((df.index > van) & (df.index <= tot)).sum())

    def slotkoersen_eur(self, datum: str, wisselkoers) -> dict[str, float]:
        """Alle slotkoersen van die dag in euro's.

        Handelde een symbool die dag niet, dan gebruiken we zijn laatst bekende
        koers. Zo houdt een positie in het weekend gewoon zijn waarde.
        """
        if len(self._slot_ingevuld) == 0 or datum not in self._slot_ingevuld.index:
            return {}
        koers = wisselkoers.op(datum)
        rij = self._slot_ingevuld.loc[datum]
        uitkomst: dict[str, float] = {}
        for symbool, waarde in rij.items():
            if pd.isna(waarde):
                continue  # dit symbool bestond toen nog niet in onze data
            slot = float(waarde)
            uitkomst[str(symbool)] = (
                slot if self._valuta_van.get(str(symbool)) == "EUR" else slot / koers
            )
        return uitkomst
