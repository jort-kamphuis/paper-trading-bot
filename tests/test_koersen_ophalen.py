"""Controleert het ophalen van cryptokoersen, met nep-beurzen en zonder internet.

Hier zat een echte fout: de code stopte met bladeren zodra een beurs een
"kleine" pagina gaf. Beurzen als OKX geven 300 dagen per keer, en dan kreeg je
alleen de oudste 300 dagen, zonder de meest recente.
"""

from __future__ import annotations

import time

from bot.data import koersen

DAG = 24 * 60 * 60 * 1000


class NepBeurs:
    """Geeft vanaf 'since' hooguit 'pagina' dagbars, tot vandaag."""

    def __init__(self, pagina: int, negeert_since: bool = False, max_terug: int | None = None):
        self.pagina = pagina
        self.negeert_since = negeert_since
        self.max_terug = max_terug
        self.verzoeken = 0
        self.nu = (int(time.time() * 1000) // DAG) * DAG

    def fetch_ohlcv(self, paar, timeframe="1d", since=None, limit=1000):
        self.verzoeken += 1
        if self.negeert_since:
            # Zoals Kraken: altijd de laatste 'pagina' dagen, wat je ook vraagt.
            begin = self.nu - (self.pagina - 1) * DAG
        else:
            begin = max(since, self.nu - (self.max_terug or 10_000) * DAG)
        bars = []
        t = begin
        while t <= self.nu and len(bars) < min(self.pagina, limit):
            bars.append([t, 1.0, 1.0, 1.0, 1.0, 1.0])
            t += DAG
        return bars


def test_bladert_door_tot_vandaag_bij_kleine_paginas():
    beurs = NepBeurs(pagina=300)
    sinds = beurs.nu - 729 * DAG  # twee jaar terug
    bars = koersen._haal_alle_bars(beurs, "BTC/USDT", sinds)

    assert bars[0][0] == sinds
    assert bars[-1][0] == beurs.nu, "de meest recente dagen ontbreken"
    assert len(bars) == 730
    assert beurs.verzoeken >= 3


def test_beurs_die_altijd_de_laatste_dagen_geeft():
    """Kraken negeert 'since' en geeft de laatste 720 dagen. Dan niet eindeloos herhalen."""
    beurs = NepBeurs(pagina=720, negeert_since=True)
    sinds = beurs.nu - 729 * DAG
    bars = koersen._haal_alle_bars(beurs, "BTC/USDT", sinds)

    assert bars[-1][0] == beurs.nu
    assert len(bars) == 720
    assert beurs.verzoeken == 1


def test_geen_dubbele_dagen():
    beurs = NepBeurs(pagina=100)
    sinds = beurs.nu - 350 * DAG
    bars = koersen._haal_alle_bars(beurs, "BTC/USDT", sinds)
    tijden = [b[0] for b in bars]
    assert len(tijden) == len(set(tijden))
    assert tijden == sorted(tijden)


def _rij(symbool):
    return {"symbool": symbool, "datum": "2026-11-12", "open": 1.0, "hoog": 1.0, "laag": 1.0,
            "slot": 1.0, "volume": 1.0, "valuta": "USD", "bron": "yfinance"}


def test_ontbrekend_aandeel_wordt_nog_eens_los_opgehaald(monkeypatch):
    """Op GitHub viel ORCL een nacht weg. Een herkansing moet dat opvangen."""
    monkeypatch.setattr(koersen, "haal_aandelen", lambda s, start, eind=None: [_rij("AAPL")])
    pogingen = []

    def los(symbool, start, eind=None):
        pogingen.append(symbool)
        return [] if len(pogingen) < 2 else [_rij(symbool)]  # tweede keer raak

    monkeypatch.setattr(koersen, "haal_een_aandeel", los)
    gewacht = []
    rijen, redenen = koersen.haal_aandelen_met_herkansing(
        ["AAPL", "ORCL"], "2026-01-01", slaap=gewacht.append
    )
    assert {r["symbool"] for r in rijen} == {"AAPL", "ORCL"}
    assert redenen == {}
    assert pogingen == ["ORCL", "ORCL"]
    assert gewacht == list(koersen.HERKANSING_PAUZES[:2]), "tussen pogingen moet gewacht worden"


def test_blijft_een_aandeel_weg_dan_staat_de_reden_erbij(monkeypatch):
    monkeypatch.setattr(koersen, "haal_aandelen", lambda s, start, eind=None: [_rij("AAPL")])

    def kapot(symbool, start, eind=None):
        raise RuntimeError("Too Many Requests")

    monkeypatch.setattr(koersen, "haal_een_aandeel", kapot)
    rijen, redenen = koersen.haal_aandelen_met_herkansing(
        ["AAPL", "ORCL"], "2026-01-01", slaap=lambda s: None
    )
    assert "ORCL" in redenen
    assert "Too Many Requests" in redenen["ORCL"]


def test_stopt_altijd_ook_als_een_beurs_raar_doet():
    """Een beurs die steeds hetzelfde teruggeeft, mag de bot niet laten hangen."""

    class Hangend:
        verzoeken = 0

        def fetch_ohlcv(self, *a, **k):
            self.verzoeken += 1
            return [[1_000, 1, 1, 1, 1, 1]]

    beurs = Hangend()
    koersen._haal_alle_bars(beurs, "BTC/USDT", 0)
    assert beurs.verzoeken <= koersen.MAX_PAGINAS
