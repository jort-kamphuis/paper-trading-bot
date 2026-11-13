"""Klopten de kansen van de AI? Scoren na de horizon, en de Brier-score per soort vraag.

Elke beursdag geeft de AI voor elk symbool een kans. Na de horizon (14
kalenderdagen) weten we wat er gebeurde. Dan vult dit bestand in:
* uitkomst_pct: het werkelijke rendement over de horizon, voor kosten. Bij de
  relatieve vraag is dat het rendement van het symbool min dat van de referentie
  (SPY of BTC); bij de absolute vraag (SPY en BTC zelf, tegen kas) het rendement
  zelf;
* geraakt: 1 als dat meer was dan de kosten van kopen en verkopen samen, anders 0.

Gerekend op slotkoersen: van de beslisdag tot de laatste slotkoers op of voor
de beslisdag plus de horizon.

De Brier-score is het gemiddelde van (kans - uitkomst)^2. Lager is beter. Wie
altijd 0,5 zegt, scoort precies 0,25: een AI die zijn geld waard is, zit daar
onder. De twee soorten vraag worden apart gerapporteerd, want het zijn
verschillende vragen: tegen de markt winnen of gewoon stijgen.

Dit verandert niets aan een portefeuille. Het is meten, voor het rapport.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from bot.ai.kenmerken import ABSOLUUT, KAS, RELATIEF, vraagsoort
from bot.markt import Markt
from bot.tekst import getal

# Wie altijd 0,5 zegt, scoort dit.
BRIER_GEEN_IDEE = 0.25
VAKKEN = ((0.0, 0.45, "onder 0,45"), (0.45, 0.55, "0,45 tot 0,55"), (0.55, 1.01, "0,55 of meer"))

NAAM = {
    RELATIEF: "relatief (tegen SPY of BTC)",
    ABSOLUUT: "absoluut (SPY en BTC zelf, tegen kas)",
}


def kosten_heen_en_terug_pct(cfg: dict) -> float:
    a = cfg["algemeen"]
    return 2.0 * (float(a["kosten_pct"]) + float(a["slippage_pct"]))


def _rendement(markt: Markt, symbool: str, van: str, tot: str) -> float | None:
    begin = markt.slot_op_of_voor(symbool, van)
    eind = markt.slot_op_of_voor(symbool, tot)
    if not begin or eind is None:
        return None
    return 100.0 * (eind / begin - 1.0)


def uitkomst(markt: Markt, symbool: str, referentie: str, datum: str, horizon: int) -> float | None:
    """Het werkelijke (extra) rendement over de horizon, of None als het nog niet bekend is."""
    eind = (date.fromisoformat(datum) + timedelta(days=horizon)).isoformat()
    if not markt.datums or markt.datums[-1] < eind:
        return None  # de horizon is nog niet voorbij
    eigen = _rendement(markt, symbool, datum, eind)
    if eigen is None:
        return None
    if referentie == KAS:
        return eigen
    tegen = _rendement(markt, referentie, datum, eind)
    return None if tegen is None else eigen - tegen


def scoor(con: sqlite3.Connection, markt: Markt, cfg: dict) -> int:
    """Vul de uitkomst in van elke kans waarvan de horizon voorbij is. Geeft het aantal."""
    kosten = kosten_heen_en_terug_pct(cfg)
    standaard = int(cfg["ai"]["beslisregel"]["horizon_kalenderdagen"])
    rijen = con.execute(
        """SELECT id, symbool, datum, referentie, horizon_dagen, vraag FROM ai_besluiten
           WHERE kans IS NOT NULL AND gescoord_op IS NULL"""
    ).fetchall()
    aantal = 0
    for r in rijen:
        ref = r["referentie"] or ""
        if not ref:
            continue
        u = uitkomst(markt, r["symbool"], ref, r["datum"], int(r["horizon_dagen"] or standaard))
        if u is None:
            continue
        con.execute(
            "UPDATE ai_besluiten SET uitkomst_pct=?, geraakt=?, gescoord_op=?, vraag=? WHERE id=?",
            (u, 1 if u > kosten else 0, markt.datums[-1], r["vraag"] or vraagsoort(ref), r["id"]),
        )
        aantal += 1
    con.commit()
    return aantal


@dataclass
class Score:
    portefeuille: str
    vraag: str
    n: int
    brier: float
    gemiddelde_kans: float
    kwam_uit_pct: float
    vakken: list[tuple[str, int, float, float]]  # naam, aantal, gemiddelde kans, kwam uit (%)


def scores(con: sqlite3.Connection) -> list[Score]:
    rijen = con.execute(
        """SELECT portefeuille, COALESCE(vraag, CASE WHEN referentie = ? THEN ? ELSE ? END) AS vraag,
                  kans, geraakt
           FROM ai_besluiten WHERE gescoord_op IS NOT NULL AND kans IS NOT NULL
           ORDER BY portefeuille""",
        (KAS, ABSOLUUT, RELATIEF),
    ).fetchall()
    groepen: dict[tuple[str, str], list[tuple[float, int]]] = {}
    for r in rijen:
        groepen.setdefault((r["portefeuille"], r["vraag"]), []).append((float(r["kans"]), int(r["geraakt"])))
    uit: list[Score] = []
    for (pf, vraag), paren in sorted(groepen.items(), key=lambda x: (x[0][0], x[0][1] != RELATIEF)):
        n = len(paren)
        vakken = []
        for laag, hoog, naam in VAKKEN:
            deel = [(k, g) for k, g in paren if laag <= k < hoog]
            if deel:
                vakken.append((naam, len(deel), sum(k for k, _ in deel) / len(deel),
                               100.0 * sum(g for _, g in deel) / len(deel)))
        uit.append(Score(
            portefeuille=pf, vraag=vraag, n=n,
            brier=sum((k - g) ** 2 for k, g in paren) / n,
            gemiddelde_kans=sum(k for k, _ in paren) / n,
            kwam_uit_pct=100.0 * sum(g for _, g in paren) / n,
            vakken=vakken,
        ))
    return uit


def als_tekst(con: sqlite3.Connection) -> str:
    regels = ["Klopten de kansen van de AI? (verkennend)"]
    lijst = scores(con)
    if not lijst:
        open_ = con.execute(
            "SELECT COUNT(*) n FROM ai_besluiten WHERE kans IS NOT NULL AND gescoord_op IS NULL"
        ).fetchone()["n"]
        regels.append(
            f"  Nog niets te scoren: een kans wordt pas na 14 dagen beoordeeld "
            f"({open_} kansen wachten daarop)."
        )
        return "\n".join(regels)
    regels.append(
        f"  Brier-score: lager is beter; altijd 0,5 zeggen geeft {getal(BRIER_GEEN_IDEE, 3)}."
    )
    huidig = None
    for s in lijst:
        if s.portefeuille != huidig:
            regels.append(f"  {s.portefeuille}")
            huidig = s.portefeuille
        regels.append(
            f"    {NAAM.get(s.vraag, s.vraag)}: {s.n} kansen, Brier {getal(s.brier, 3)}, "
            f"gemiddelde kans {getal(s.gemiddelde_kans, 2)}, kwam uit in {getal(s.kwam_uit_pct, 0)}%"
        )
        for naam, n, kans, uit in s.vakken:
            regels.append(f"      kans {naam}: {n}x, gemiddeld {getal(kans, 2)}, kwam uit in {getal(uit, 0)}%")
    return "\n".join(regels)
