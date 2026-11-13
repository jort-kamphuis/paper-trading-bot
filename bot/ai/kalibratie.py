"""Klopten de kansen van de AI? Scoren na de horizon, en de Brier-score per soort vraag.

Elke beursdag geeft de AI voor elk symbool een kans. Na de horizon (14
kalenderdagen) weten we wat er gebeurde. Dan vult dit bestand in:
* uitkomst_pct: het werkelijke rendement over de horizon, voor kosten. Bij de
  relatieve vraag is dat het rendement van het symbool min dat van de referentie
  (SPY of BTC); bij de absolute vraag (SPY en BTC zelf, tegen kas) het rendement
  zelf;
* geraakt: 1 als dat meer was dan de kosten van kopen en verkopen samen, anders 0.

Gerekend zoals de bot echt zou handelen: vanaf de openingskoers van de eerste
handelsdag na de beslisdag (dan wordt een order uitgevoerd) tot de laatste
slotkoers op of voor de beslisdag plus de horizon, met het dividend van die
periode erbij. Vanaf het slot rekenen zou nieuws van na het slot (dat de AI om
00:30 UTC al ziet) laten meetellen als voorspelling, terwijl de koers bij de
opening al gesprongen is (review november 2026).

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


def _rendement(markt: Markt, symbool: str, beslisdag: str, tot: str) -> float | None:
    """Van de opening na de beslisdag tot het slot op of voor 'tot', met dividend."""
    eerste = markt.volgende_bar_na(symbool, beslisdag)
    if eerste is None or not eerste.open or eerste.datum > tot:
        return None
    eind = markt.slot_op_of_voor(symbool, tot)
    if eind is None:
        return None
    df = markt.tot_en_met(symbool, tot)
    dividend = 0.0
    if "dividend" in df.columns:
        # Wie op de opening van de ex-datum koopt, krijgt dat dividend niet.
        deel = df.loc[(df.index > eerste.datum) & (df.index <= tot), "dividend"]
        dividend = float(deel.fillna(0.0).sum())
    return 100.0 * ((eind + dividend) / eerste.open - 1.0)


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
    # Wie altijd de basiskans zegt (hoe vaak het uitkwam), scoort dit. Een AI die
    # iets weet, zit daar onder; onder 0,25 zitten zegt alleen dat hij de basiskans kent.
    brier_basiskans: float = BRIER_GEEN_IDEE
    beslisdagen: int = 0
    p_beter_dan_basiskans: float | None = None


def venster(cfg: dict) -> tuple[str | None, str | None]:
    """Welke beslisdagen meetellen: in de echte meting alleen die van start tot en met einde."""
    if cfg.get("modus") != "echt":
        return None, None
    m = cfg.get("meting") or {}
    return (str(m["start"]) if m.get("start") else None, str(m["einde"]) if m.get("einde") else None)


def toets_tegen_basiskans(per_dag: dict[str, list[tuple[float, int]]], blok: int = 10) -> float | None:
    """Is de Brier-score beter dan 'altijd de basiskans zeggen'? Eenzijdige p-waarde.

    Per beslisdag het gemiddelde verschil (Brier van de basiskans min Brier van de
    AI); daarop dezelfde toets op blokgemiddelden als bij de hoofdvraag. Per dag en
    in blokken, omdat kansen van dezelfde dag en van opeenvolgende dagen over
    overlappende periodes gaan en dus niet los van elkaar staan. Verkennend.
    """
    from bot import statistiek

    alle = [x for lijst in per_dag.values() for x in lijst]
    if not alle:
        return None
    basis = sum(g for _, g in alle) / len(alle)
    reeks = [
        sum((basis - g) ** 2 - (k - g) ** 2 for k, g in per_dag[d]) / len(per_dag[d])
        for d in sorted(per_dag)
    ]
    return statistiek.blokgemiddelden_p_waarde(reeks, blok=blok)


def scores(con: sqlite3.Connection, van: str | None = None, tot: str | None = None) -> list[Score]:
    rijen = con.execute(
        """SELECT portefeuille, COALESCE(vraag, CASE WHEN referentie = ? THEN ? ELSE ? END) AS vraag,
                  kans, geraakt, datum
           FROM ai_besluiten WHERE gescoord_op IS NOT NULL AND kans IS NOT NULL
             AND datum >= COALESCE(?, datum) AND datum <= COALESCE(?, datum)
           ORDER BY portefeuille""",
        (KAS, ABSOLUUT, RELATIEF, van, tot),
    ).fetchall()
    groepen: dict[tuple[str, str], list[tuple[float, int]]] = {}
    per_dag: dict[tuple[str, str], dict[str, list[tuple[float, int]]]] = {}
    for r in rijen:
        sleutel = (r["portefeuille"], r["vraag"])
        paar = (float(r["kans"]), int(r["geraakt"]))
        groepen.setdefault(sleutel, []).append(paar)
        per_dag.setdefault(sleutel, {}).setdefault(r["datum"], []).append(paar)
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
            brier_basiskans=(lambda f: f * (1.0 - f))(sum(g for _, g in paren) / n),
            beslisdagen=len(per_dag[(pf, vraag)]),
            # Alleen voor de relatieve vraag. De absolute (SPY en BTC) heeft te weinig
            # onafhankelijke waarnemingen voor een toets: die wordt alleen getoond.
            p_beter_dan_basiskans=toets_tegen_basiskans(per_dag[(pf, vraag)]) if vraag == RELATIEF else None,
        ))
    return uit


def als_tekst(con: sqlite3.Connection, van: str | None = None, tot: str | None = None) -> str:
    regels = ["Klopten de kansen van de AI? (verkennend)"]
    lijst = scores(con, van, tot)
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
        f"  Brier-score: lager is beter; altijd 0,5 zeggen geeft {getal(BRIER_GEEN_IDEE, 3)}, "
        f"altijd de basiskans zeggen staat erachter."
    )
    huidig = None
    for s in lijst:
        if s.portefeuille != huidig:
            regels.append(f"  {s.portefeuille}")
            huidig = s.portefeuille
        regels.append(
            f"    {NAAM.get(s.vraag, s.vraag)}: {s.n} kansen, Brier {getal(s.brier, 3)} "
            f"(basiskans {getal(s.brier_basiskans, 3)}), "
            f"gemiddelde kans {getal(s.gemiddelde_kans, 2)}, kwam uit in {getal(s.kwam_uit_pct, 0)}%"
        )
        if s.vraag == RELATIEF:
            p = s.p_beter_dan_basiskans
            regels.append(
                f"      beter dan de basiskans? {s.beslisdagen} beslisdagen, "
                + ("nog te weinig voor de toets" if p is None else f"p {getal(p, 2)} (verkennend)")
            )
        else:
            regels.append("      alleen ter informatie: te weinig onafhankelijke waarnemingen voor een conclusie")
        for naam, n, kans, uit in s.vakken:
            regels.append(f"      kans {naam}: {n}x, gemiddeld {getal(kans, 2)}, kwam uit in {getal(uit, 0)}%")
    return "\n".join(regels)
