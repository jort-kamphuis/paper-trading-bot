"""Getallen en datums op zijn Nederlands, op een plek voor alle berichten."""

from __future__ import annotations

from datetime import date

DAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
MAANDEN = [
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
]


def getal(x: float, decimalen: int = 1, teken: bool = False) -> str:
    """Komma voor decimalen, punt voor duizendtallen: 1.234,50."""
    tekst = f"{x:+,.{decimalen}f}" if teken else f"{x:,.{decimalen}f}"
    return tekst.replace(",", "~").replace(".", ",").replace("~", ".")


def euro(x: float) -> str:
    return f"€{getal(x, 2)}"


def dollar(x: float, decimalen: int = 2) -> str:
    return f"${getal(x, decimalen)}"


def pct(x: float | None, teken: bool = True, decimalen: int = 1) -> str:
    return "-" if x is None else getal(x, decimalen, teken) + "%"


def datum_lang(datum: str) -> str:
    """'2026-11-12' wordt 'donderdag 12 november 2026'."""
    d = date.fromisoformat(datum)
    return f"{DAGEN[d.weekday()]} {d.day} {MAANDEN[d.month - 1]} {d.year}"
