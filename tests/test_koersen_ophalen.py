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
