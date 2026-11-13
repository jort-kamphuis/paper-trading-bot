"""De koershistorie, klaar voor gebruik door de strategieën.

Belangrijk: een strategie krijgt via dit object nooit een koers van na de
beslisdag te zien. Alle kengetallen kijken alleen achteruit.

Alle koersen zijn in dollars. Er wordt hier niets omgerekend.

Periodes in kalenderdagen: aandelen handelen ongeveer 252 dagen per jaar,
crypto 365. "60 dagen in de data" is voor een aandeel dus bijna drie maanden
en voor crypto twee. Voor alles wat aandelen en crypto vergelijkt (de
ranglijst van momentum, de tabel voor de AI) rekenen we daarom in
kalenderdagen, zodat beide over dezelfde periode worden gemeten.
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

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
    dividend: float = 0.0
    splitsing: float = 0.0


def _min_dagen(datum: str, dagen: int) -> str:
    return (date.fromisoformat(datum) - timedelta(days=dagen)).isoformat()


class Markt:
    def __init__(self, per_symbool: dict[str, pd.DataFrame]):
        self._data = per_symbool
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
        vraag = (
            "SELECT symbool, datum, open, hoog, laag, slot, volume, valuta, "
            "COALESCE(dividend, 0) AS dividend, COALESCE(splitsing, 0) AS splitsing FROM koersen"
        )
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

    def _bouw_slotmatrix(self) -> None:
        """Een tabel met alle slotkoersen, waarin gaten worden opgevuld.

        Op een dag dat de beurs dicht is, is een aandeel nog steeds iets waard:
        de laatste koers. Dat vullen we hier in (alleen vooruit, dus nooit met
        een koers van later). Zonder dit zou de waarde in het weekend wegvallen.
        """
        if not self._data:
            self._slot_ingevuld = pd.DataFrame()
            return
        kolommen = {s: df["slot"] for s, df in self._data.items()}
        self._slot_ingevuld = pd.DataFrame(kolommen).sort_index().ffill()

    def handelsdagen_van(self, symbool: str) -> list[str]:
        """De dagen waarop dit symbool echt gehandeld heeft."""
        df = self._data.get(symbool)
        return [] if df is None else [str(d) for d in df.index]

    def is_handelsdag(self, symbool: str, datum: str) -> bool:
        df = self._data.get(symbool)
        return df is not None and datum in df.index

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
            dividend=float(rij["dividend"]) if "dividend" in rij and pd.notna(rij["dividend"]) else 0.0,
            splitsing=float(rij["splitsing"]) if "splitsing" in rij and pd.notna(rij["splitsing"]) else 0.0,
        )

    @staticmethod
    def _zorg_voor(df: pd.DataFrame, naam: str) -> bool:
        """Reken een kengetal uit als het er nog niet is, zoals 'ma50' of 'rsi2'.

        Deze tellen in dagen van de data (handelsdagen). Ze kijken alleen
        achteruit. Geeft False bij een naam die we niet kennen.
        """
        if naam in df.columns:
            return True
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

    # ---------- in kalenderdagen ----------

    def _reeks_tot(self, symbool: str, datum: str) -> pd.DataFrame | None:
        df = self._data.get(symbool)
        if df is None:
            return None
        return df.loc[df.index <= datum]

    def slot_op_of_voor(self, symbool: str, datum: str) -> float | None:
        """De laatste slotkoers op of voor die dag."""
        deel = self._reeks_tot(symbool, datum)
        if deel is None or len(deel) == 0:
            return None
        return float(deel["slot"].iloc[-1])

    def kalender_rendement(self, symbool: str, datum: str, dagen: int) -> float | None:
        """Rendement in procent over de laatste <dagen> kalenderdagen.

        Van de laatste slotkoers op of voor (datum - dagen) tot de laatste
        slotkoers op of voor datum. None als de geschiedenis niet ver genoeg terug gaat.
        """
        deel = self._reeks_tot(symbool, datum)
        if deel is None or len(deel) < 2:
            return None
        begin_datum = _min_dagen(datum, dagen)
        voor = deel.loc[deel.index <= begin_datum]
        if len(voor) == 0:
            return None
        begin = float(voor["slot"].iloc[-1])
        eind = float(deel["slot"].iloc[-1])
        if begin <= 0:
            return None
        return 100.0 * (eind / begin - 1.0)

    def kalender_schommeling(self, symbool: str, datum: str, dagen: int) -> float | None:
        """De gemiddelde schommeling per dag in procent (standaardafwijking), over <dagen> kalenderdagen."""
        deel = self._reeks_tot(symbool, datum)
        if deel is None:
            return None
        venster = deel.loc[deel.index > _min_dagen(datum, dagen)]["slot"]
        if len(venster) < 5:
            return None
        r = venster.pct_change().dropna()
        if len(r) < 4:
            return None
        return float(r.std(ddof=1) * 100.0)

    def kalender_hoogste(self, symbool: str, datum: str, dagen: int) -> float | None:
        deel = self._reeks_tot(symbool, datum)
        if deel is None:
            return None
        venster = deel.loc[deel.index > _min_dagen(datum, dagen)]["slot"]
        return float(venster.max()) if len(venster) else None

    def gecorrigeerd_rendement(self, symbool: str, datum: str, dagen: int) -> float | None:
        """Rendement gedeeld door de schommeling over dezelfde periode.

        Zo wint niet vanzelf het symbool dat het wildst beweegt (vaak crypto):
        een stijging van 20% met veel schommeling telt minder dan 20% bij weinig.
        """
        rend = self.kalender_rendement(symbool, datum, dagen)
        schom = self.kalender_schommeling(symbool, datum, dagen)
        deel = self._reeks_tot(symbool, datum)
        if rend is None or not schom or deel is None:
            return None
        n = int((deel.index > _min_dagen(datum, dagen)).sum())
        if n < 5:
            return None
        return rend / (schom * math.sqrt(n))

    def volume_verhouding(self, symbool: str, datum: str, dagen: int = 50) -> float | None:
        """Het volume van vandaag gedeeld door het gemiddelde van de <dagen> handelsdagen ervoor."""
        df = self._data.get(symbool)
        if df is None or datum not in df.index or "volume" not in df.columns:
            return None
        plek = df.index.get_loc(datum)
        if not isinstance(plek, int) or plek < dagen:
            return None
        eerder = df["volume"].iloc[plek - dagen:plek]
        gemiddeld = float(eerder.mean())
        nu = df["volume"].iloc[plek]
        if not gemiddeld or pd.isna(nu):
            return None
        return float(nu) / gemiddeld

    def beursdagen_tussen(self, symbool: str, van: str, tot: str) -> int:
        """Hoeveel handelsdagen van dit symbool er tussen twee datums liggen."""
        df = self._data.get(symbool)
        if df is None:
            return 0
        return int(((df.index > van) & (df.index <= tot)).sum())

    def tot_en_met(self, symbool: str, datum: str) -> pd.DataFrame:
        """De geschiedenis tot en met die dag. Nooit verder."""
        df = self._data.get(symbool)
        if df is None:
            return pd.DataFrame()
        return df.loc[df.index <= datum]

    def volgende_bar_na(self, symbool: str, datum: str) -> Bar | None:
        df = self._data.get(symbool)
        if df is None:
            return None
        later = df.index[df.index > datum]
        if len(later) == 0:
            return None
        return self.bar(symbool, str(later[0]))

    def slotkoersen_usd(self, datum: str) -> dict[str, float]:
        """Alle slotkoersen van die dag, in dollars.

        Handelde een symbool die dag niet, dan gebruiken we zijn laatst bekende
        koers. Zo houdt een positie in het weekend gewoon zijn waarde.
        """
        if len(self._slot_ingevuld) == 0:
            return {}
        if datum not in self._slot_ingevuld.index:
            eerder = self._slot_ingevuld.index[self._slot_ingevuld.index <= datum]
            if len(eerder) == 0:
                return {}
            datum = str(eerder[-1])
        rij = self._slot_ingevuld.loc[datum]
        return {str(s): float(w) for s, w in rij.items() if not pd.isna(w)}
