"""Rekenen aan de resultaten, en eerlijk zijn over wat we nog niet weten.

Twee dingen zitten hier door elkaar en dat is met opzet:
1. de gewone cijfers (rendement, winstpercentage, grootste daling, Sharpe);
2. de vraag of een verschil al iets betekent of nog geluk kan zijn.

Voor punt 2 gebruiken we een bootstrap: we trekken duizenden keren een nieuwe
steekproef uit de trades die we hebben, en kijken hoe vaak het gemiddelde dan
nog positief is. Dat is betrouwbaarder dan een gewone t-toets, omdat
handelsresultaten zelden netjes normaal verdeeld zijn.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist

import numpy as np

DAGEN_PER_JAAR = 365.25


# ---------------------------------------------------------------- basiscijfers


def rendement_pct(begin: float, eind: float) -> float:
    """Hoeveel procent is de portefeuille gestegen of gedaald."""
    if begin <= 0:
        return 0.0
    return 100.0 * (eind / begin - 1.0)


def max_daling_pct(waarden: list[float]) -> float:
    """De grootste daling vanaf een hoogste punt (de 'max drawdown').

    Geeft een positief getal: 12.5 betekent dat de portefeuille op het slechtste
    moment 12,5% onder zijn hoogste punt stond.
    """
    if not waarden:
        return 0.0
    top = waarden[0]
    diepste = 0.0
    for waarde in waarden:
        top = max(top, waarde)
        if top > 0:
            daling = 100.0 * (1.0 - waarde / top)
            diepste = max(diepste, daling)
    return diepste


def dagrendementen(waarden: list[float]) -> np.ndarray:
    """Van een reeks portefeuillewaardes naar de verandering per meetmoment."""
    arr = np.asarray(waarden, dtype=float)
    if arr.size < 2:
        return np.array([], dtype=float)
    vorige = arr[:-1]
    met_waarde = vorige > 0
    return (arr[1:][met_waarde] / vorige[met_waarde]) - 1.0


def metingen_per_jaar(datums: list[str]) -> float:
    """Hoeveel meetmomenten er per jaar zijn.

    Nodig om de Sharpe ratio op jaarbasis te zetten. Een portefeuille met
    crypto meet (bijna) elke dag, een met alleen aandelen ongeveer 252 keer
    per jaar. We rekenen met wat er echt in de data staat.
    """
    if len(datums) < 2:
        return 252.0
    eerste = date.fromisoformat(datums[0])
    laatste = date.fromisoformat(datums[-1])
    kalenderdagen = (laatste - eerste).days
    if kalenderdagen <= 0:
        return 252.0
    return len(datums) * DAGEN_PER_JAAR / kalenderdagen


def sharpe(waarden: list[float], datums: list[str], rente_pct: float = 0.0) -> float | None:
    """Rendement gedeeld door de schommeling, op jaarbasis.

    Hoger is beter: je krijgt meer rendement per eenheid risico. We rekenen
    met een risicovrije rente van 0%, wat de gebruikelijke vereenvoudiging is.
    Geeft None als er te weinig of geen schommeling is.
    """
    r = dagrendementen(waarden)
    if r.size < 20:
        return None
    standaardafwijking = float(np.std(r, ddof=1))
    if standaardafwijking == 0:
        return None
    per_jaar = metingen_per_jaar(datums)
    gemiddelde = float(np.mean(r)) - (rente_pct / 100.0) / per_jaar
    return (gemiddelde / standaardafwijking) * math.sqrt(per_jaar)


def op_kalender(
    datums: list[str], waarden: list[float], kalender: list[str]
) -> tuple[list[str], list[float]]:
    """Houd alleen de meetmomenten over die op de meetkalender staan.

    Waarom: de aandelenbeurs is in het weekend dicht, crypto niet. Als je
    gewoon alle dagen meetelt, heeft een portefeuille met aandelen in het
    weekend 'rust' en een met crypto niet. Dan vergelijk je appels met peren
    bij de schommeling en de Sharpe ratio. Daarom rekenen we alle
    risicocijfers op dezelfde kalender: de dagen dat de Amerikaanse beurs open
    was. Crypto-bewegingen in het weekend gaan niet verloren, die zitten dan
    in de stap van vrijdag naar maandag.
    """
    toegestaan = set(kalender)
    paren = [(d, w) for d, w in zip(datums, waarden) if d in toegestaan]
    return [d for d, _ in paren], [w for _, w in paren]


def win_pct(resultaten: list[float]) -> float | None:
    """Welk deel van de afgeronde trades winst opleverde."""
    if not resultaten:
        return None
    winnaars = sum(1 for r in resultaten if r > 0)
    return 100.0 * winnaars / len(resultaten)


# ------------------------------------------------- is het al iets, of geluk?


@dataclass
class Zekerheid:
    """Het antwoord op: betekent dit al iets?"""

    aantal: int
    gemiddelde: float
    p_waarde: float | None
    betekenisvol: bool
    trades_nodig: int | None
    uitleg: str


def _z(kans: float) -> float:
    """Het z-getal dat bij een kans hoort (standaard normale verdeling)."""
    return NormalDist().inv_cdf(kans)


def bootstrap_p_waarde(
    waarnemingen: list[float], trekkingen: int = 10_000, zaad: int = 12345
) -> float | None:
    """De kans dat we dit gemiddelde zien terwijl er in werkelijkheid niets is.

    We trekken 10.000 keer een nieuwe steekproef (met teruglegging) uit de
    resultaten die we hebben. De p-waarde is het deel van die steekproeven
    waarin het gemiddelde niet positief is. Een p-waarde van 0,05 of lager
    noemen we 'betekenisvol'; dat is de gangbare afspraak, geen natuurwet.
    """
    arr = np.asarray(waarnemingen, dtype=float)
    if arr.size < 5:
        return None
    generator = np.random.default_rng(zaad)
    steekproeven = generator.choice(arr, size=(trekkingen, arr.size), replace=True)
    gemiddelden = steekproeven.mean(axis=1)
    deel_niet_positief = float(np.mean(gemiddelden <= 0.0))
    # Nooit exact 0 teruggeven: met 10.000 trekkingen weet je niet beter
    # dan "kleiner dan 1 op 10.000".
    return max(deel_niet_positief, 1.0 / trekkingen)


def trades_nodig_voor_uitspraak(
    waarnemingen: list[float],
    betrouwbaarheid: float = 0.95,
    onderscheidend_vermogen: float = 0.80,
) -> int | None:
    """Hoeveel trades er in totaal nodig zijn voor een harde uitspraak.

    De formule is de standaard steekproefberekening:
        n = (z_betrouwbaarheid + z_vermogen)^2 * spreiding^2 / effect^2
    Met 'effect' het gemiddelde resultaat per trade dat we nu meten, en
    'spreiding' hoe sterk de resultaten onderling verschillen.

    Let op: dit is een schatting op basis van de trades die we nu hebben. Met
    weinig trades is de schatting zelf nog onzeker. Geeft None als het
    gemiddelde niet positief is, want dan is er nog niets om te bevestigen.
    """
    arr = np.asarray(waarnemingen, dtype=float)
    if arr.size < 5:
        return None
    effect = float(np.mean(arr))
    if effect <= 0:
        return None
    spreiding = float(np.std(arr, ddof=1))
    if spreiding == 0:
        return arr.size
    z_een = _z(1.0 - (1.0 - betrouwbaarheid) / 2.0)
    z_twee = _z(onderscheidend_vermogen)
    nodig = ((z_een + z_twee) ** 2) * (spreiding**2) / (effect**2)
    return int(math.ceil(nodig))


def beoordeel(
    resultaten_per_trade: list[float],
    min_trades: int = 30,
    betrouwbaarheid: float = 0.95,
    onderscheidend_vermogen: float = 0.80,
) -> Zekerheid:
    """Zeg in gewone taal of het resultaat al iets betekent.

    'resultaten_per_trade' is het resultaat in dollars per afgeronde trade.
    """
    aantal = len(resultaten_per_trade)
    gemiddelde = float(np.mean(resultaten_per_trade)) if aantal else 0.0

    if aantal == 0:
        return Zekerheid(0, 0.0, None, False, None, "Nog geen afgeronde trades.")

    p = bootstrap_p_waarde(resultaten_per_trade)
    nodig = trades_nodig_voor_uitspraak(
        resultaten_per_trade, betrouwbaarheid, onderscheidend_vermogen
    )
    grens = 1.0 - betrouwbaarheid

    if aantal < min_trades:
        tekort = min_trades - aantal
        delen = [
            f"Nog maar {aantal} afgeronde trades, en we willen er minstens {min_trades}.",
            f"Dit kan dus nog gewoon geluk zijn ({tekort} te gaan).",
        ]
        if nodig:
            delen.append(
                f"Met de spreiding die we nu zien, zijn er naar schatting "
                f"ongeveer {nodig} trades nodig voor een harde uitspraak."
            )
        elif gemiddelde <= 0:
            delen.append("Het gemiddelde resultaat per trade is nu niet positief.")
        return Zekerheid(aantal, gemiddelde, p, False, nodig, " ".join(delen))

    if p is not None and p <= grens and gemiddelde > 0:
        return Zekerheid(
            aantal, gemiddelde, p, True, nodig,
            f"Met {aantal} trades en een p-waarde van {p:.3f} is dit resultaat "
            f"waarschijnlijk geen geluk. Dat is geen garantie voor de toekomst.",
        )

    delen = [f"{aantal} trades, maar het resultaat is nog niet te onderscheiden van geluk"]
    if p is not None:
        delen.append(f" (p-waarde {p:.3f}, we wilden {grens:.2f} of lager)")
    delen.append(".")
    if nodig and nodig > aantal:
        delen.append(
            f" Naar schatting zijn er ongeveer {nodig} trades nodig, dus nog "
            f"{nodig - aantal} erbij."
        )
    elif gemiddelde <= 0:
        delen.append(" Het gemiddelde resultaat per trade is niet positief.")
    return Zekerheid(aantal, gemiddelde, p, False, nodig, "".join(delen))


def blok_bootstrap_p_waarde(
    waarnemingen: list[float], blok: int = 10, trekkingen: int = 10_000, zaad: int = 12345
) -> float | None:
    """Zoals bootstrap_p_waarde, maar met blokken van opeenvolgende dagen.

    Rendementen van opeenvolgende dagen hangen samen (een goede week bestaat uit
    goede dagen achter elkaar). Trek je losse dagen, dan lijkt een verschil
    zekerder dan het is. Daarom trekken we blokken van 'blok' dagen achter
    elkaar (een 'moving block bootstrap'). De p-waarde is het deel van de
    trekkingen waarin het gemiddelde niet positief is.
    """
    arr = np.asarray(waarnemingen, dtype=float)
    n = arr.size
    if n < max(20, 2 * blok):
        return None
    generator = np.random.default_rng(zaad)
    aantal_blokken = int(np.ceil(n / blok))
    starts = generator.integers(0, n - blok + 1, size=(trekkingen, aantal_blokken))
    plekken = (starts[:, :, None] + np.arange(blok)[None, None, :]).reshape(trekkingen, -1)[:, :n]
    gemiddelden = arr[plekken].mean(axis=1)
    return max(float(np.mean(gemiddelden <= 0.0)), 1.0 / trekkingen)


def vergelijk_reeksen(
    rendementen_strategie: list[float],
    rendementen_benchmark: list[float],
    trekkingen: int = 10_000,
    zaad: int = 12345,
    blok: int = 10,
) -> float | None:
    """De kans dat het verschil met de meetlat toeval is.

    We kijken per meetmoment (beursdag) naar het verschil in rendement en toetsen
    met een blok-bootstrap (blokken van 10 beursdagen) of dat verschil gemiddeld
    boven nul ligt.
    """
    a = np.asarray(rendementen_strategie, dtype=float)
    b = np.asarray(rendementen_benchmark, dtype=float)
    n = min(a.size, b.size)
    if n < 20:
        return None
    verschil = a[:n] - b[:n]
    return blok_bootstrap_p_waarde(list(verschil), blok=blok, trekkingen=trekkingen, zaad=zaad)
