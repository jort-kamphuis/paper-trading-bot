"""Technische kengetallen.

Alle kengetallen hier kijken alleen achteruit. De waarde op dag T gebruikt
uitsluitend koersen tot en met dag T. Daarom mag je ze over de hele reeks in
een keer uitrekenen: de waarde op dag T verandert niet als je later data
toevoegt. tests/test_geen_vooruitkijken.py controleert dat.
"""

from __future__ import annotations

import pandas as pd


def voortschrijdend_gemiddelde(slot: pd.Series, periode: int) -> pd.Series:
    """Het gemiddelde van de laatste <periode> slotkoersen."""
    return slot.rolling(window=periode, min_periods=periode).mean()


def rsi(slot: pd.Series, periode: int = 14) -> pd.Series:
    """RSI volgens de methode van Wilder.

    De RSI loopt van 0 tot 100. Laag (onder 30) betekent dat een koers de
    laatste tijd vooral is gedaald, hoog (boven 70) vooral gestegen.
    """
    verschil = slot.diff()
    winst = verschil.clip(lower=0.0)
    verlies = (-verschil).clip(lower=0.0)

    # Wilder gebruikt een exponentieel gemiddelde met alpha = 1/periode.
    gem_winst = winst.ewm(alpha=1.0 / periode, adjust=False, min_periods=periode).mean()
    gem_verlies = verlies.ewm(alpha=1.0 / periode, adjust=False, min_periods=periode).mean()

    rs = gem_winst / gem_verlies
    uitkomst = 100.0 - (100.0 / (1.0 + rs))
    # Als er geen enkele daling was, is de RSI per definitie 100.
    uitkomst = uitkomst.where(gem_verlies != 0, 100.0)
    # Als er geen enkele stijging was, is de RSI 0.
    uitkomst = uitkomst.where(~((gem_winst == 0) & (gem_verlies != 0)), 0.0)
    return uitkomst


def rendement_over(slot: pd.Series, dagen: int) -> pd.Series:
    """Procentuele verandering over <dagen> beursdagen."""
    return 100.0 * (slot / slot.shift(dagen) - 1.0)
