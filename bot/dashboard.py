"""De export voor het dashboard: de resultaten als JSON-bestanden naast een statische site.

Er gaan alleen RESULTATEN uit: koersen, standen, trades, AI-beoordelingen,
rapporten en kosten. Nooit sleutels, nooit de tabel 'instellingen' (daar staat
bijvoorbeeld de stand van Telegram), en alleen de kolommen die hieronder met
naam worden genoemd. tests/test_dashboard.py controleert dat.

Twee weergaven:
* 'echt': trading.db, de echte meting. Tijdens de proefweek staat daar alleen
  wanneer de meting begint.
* 'proef': proefweek.db, met het label "telt niet mee".

De koersen (candlesticks) komen uit de database die die nacht alle koersen
heeft; de trades en de kansen per weergave uit de eigen database.

Dit verandert niets aan een portefeuille.
"""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from bot import config as conf
from bot import indicatoren, rapport, statistiek
from bot.ai import kalibratie
from bot.ai.budget import Budget
from bot.ai.kenmerken import ABSOLUUT, KAS, RELATIEF, vraagsoort
from bot.ai.kostenprognose import GEEN_KOERS
from bot.markt import Markt

SITE_BRON = conf.PROJECT / "site"
VAKBREEDTE = 0.05
MIN_PER_VAK = 10
MEETLAT_P = 0.05

HOOFDVRAAG = (
    "Kiest {ai} beter dan de gelijk verdeelde meetlat van zijn eigen symbolen ({meetlat}), "
    "als die meetlat elke dag even veel belegd is als {ai}?"
)


# ------------------------------------------------------------------ hulpjes


def _getal(x: Any, d: int = 4) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, d)


def _schrijf(pad: Path, data: Any) -> None:
    pad.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False: een NaN in de export is een fout, geen stille 'null'.
    pad.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")


def bestandsnaam(symbool: str) -> str:
    return symbool.replace("/", "-") + ".json"


def _instelling(con: sqlite3.Connection, sleutel: str) -> str | None:
    # Alleen deze ene sleutel wordt gelezen; de rest van 'instellingen' blijft binnen.
    rij = con.execute("SELECT waarde FROM instellingen WHERE sleutel = ?", (sleutel,)).fetchone()
    return rij["waarde"] if rij else None


def ai_namen(con: sqlite3.Connection, cfg: dict) -> set[str]:
    namen = {str(p["naam"]) for p in (cfg.get("ai") or {}).get("portefeuilles") or []}
    namen |= {r["portefeuille"] for r in con.execute("SELECT DISTINCT portefeuille FROM ai_besluiten")}
    namen |= {r["naam"] for r in con.execute("SELECT naam FROM portefeuilles") if r["naam"].startswith(("ai_", "proef_ai_"))}
    return namen


def soort_van(naam: str, ai: set[str]) -> str:
    if naam in rapport.MEETLATTEN:
        return "meetlat"
    if naam in ai:
        return "ai"
    return "regel"


def eigen_meetlat(naam: str, ai: set[str], bestaand: set[str]) -> str | None:
    """De meetlat waar een portefeuille tegen wordt afgezet op zijn scorekaart.

    De AI en de regelstrategieën tegen gelijk_alle. Volgt de AI een kleinere lijst
    (dan bestaat gelijk_ai), dan de AI en de *_ai-varianten tegen gelijk_ai.
    De meetlatten zelf krijgen geen vergelijking.
    """
    if naam in rapport.MEETLATTEN:
        return None
    if "gelijk_ai" in bestaand and (naam in ai or naam.endswith("_ai")):
        return "gelijk_ai"
    return "gelijk_alle" if "gelijk_alle" in bestaand else None


def hoofdvraag_label(laatste_data: date, einde: date, verschil_pp: float | None, p: float | None) -> str:
    """Het eerlijke label bij de hoofdvraag.

    Tot de koers van de einddatum in de data zit altijd 'nog niet te zeggen', ook
    als de p-waarde onderweg laag is: wie elke dag kijkt en bij het eerste mooie
    getal stopt, maakt de toets waardeloos. Daarna 'ja' of 'nee', gerekend tot en
    met de einddatum en niet verder.
    """
    if laatste_data < einde:
        return "nog niet te zeggen"
    if verschil_pp is not None and p is not None and verschil_pp > 0 and p <= MEETLAT_P:
        return "ja"
    return "nee"


# ------------------------------------------------------------ per weergave


def _overzicht(con: sqlite3.Connection, cfg: dict, weergave: str, vandaag: date) -> dict:
    tot_rij = con.execute("SELECT MAX(datum) d FROM waarde").fetchone()
    tot = tot_rij["d"] if tot_rij else None
    start = _instelling(con, "start_datum")
    uit: dict[str, Any] = {"datum": tot, "start": start, "portefeuilles": [], "hoofdvraag": None, "ai_vandaag": []}
    if not tot:
        return uit

    kal = rapport.kalender(con, cfg)
    ai = ai_namen(con, cfg)
    namen = [r["naam"] for r in con.execute("SELECT naam FROM portefeuilles WHERE actief = 1 ORDER BY variant, naam")]
    bestaand = set(namen)

    for naam in namen:
        k = rapport.kerncijfers(con, cfg, naam, tot, kal)
        if k is None:
            continue
        rijen = con.execute(
            "SELECT datum, totaal_usd FROM waarde WHERE portefeuille = ? ORDER BY datum", (naam,)
        ).fetchall()
        datums, waarden = statistiek.op_kalender([r["datum"] for r in rijen], [r["totaal_usd"] for r in rijen], kal)
        meetlat = eigen_meetlat(naam, ai, bestaand)
        v = rapport.vergelijk(con, naam, meetlat, tot, kal) if meetlat else None
        uit["portefeuilles"].append({
            "naam": naam,
            "soort": soort_van(naam, ai),
            "reeks": [[d, _getal(w, 2)] for d, w in zip(datums, waarden)],
            "waarde": _getal(k.waarde_usd, 2),
            "rendement_pct": _getal(k.rendement_pct, 2),
            "rendement_week_pct": _getal(k.rendement_week_pct, 2),
            "meetlat": meetlat,
            "vs_meetlat_pp": _getal(v.verschil_pp, 2) if v else None,
            "vs_meetlat_p": _getal(v.p_waarde, 3) if v else None,
            "belegd_pct": _getal(k.belegd_pct, 1),
            "max_daling_pct": _getal(k.max_daling_pct, 2),
            "trades": k.trades,
            "afgerond": k.afgerond,
            "win_pct": _getal(k.win_pct, 1),
            "sharpe": _getal(k.sharpe, 2),
        })

    # ---- de hoofdvraag ----
    m = cfg.get("meting") or {}
    ai_pf = m.get("proef_ai_portefeuille" if weergave == "proef" else "ai_portefeuille", "ai_analist")
    meetlat = eigen_meetlat(ai_pf, ai, bestaand) or "gelijk_alle"
    einde = date.fromisoformat(str(m.get("einde", "2027-02-23")))
    # Nooit verder rekenen dan de einddatum: na 23 februari mag de uitslag niet meer
    # verschuiven (anders kies je achteraf een mooie dag).
    tot_vraag = min(tot, einde.isoformat())
    beide = ai_pf in bestaand and meetlat in bestaand
    v = rapport.vergelijk_even_belegd(con, ai_pf, meetlat, tot_vraag, kal) if beide else None
    ruw = rapport.vergelijk(con, ai_pf, meetlat, tot_vraag, kal) if beide else None
    k_ai = rapport.kerncijfers(con, cfg, ai_pf, tot_vraag, kal) if ai_pf in bestaand else None
    k_m = rapport.kerncijfers(con, cfg, meetlat, tot_vraag, kal) if meetlat in bestaand else None
    uit["hoofdvraag"] = {
        "vraag": HOOFDVRAAG.format(ai=ai_pf, meetlat=meetlat),
        "ai": ai_pf,
        "meetlat": meetlat,
        "tot": tot_vraag,
        "ai_rendement_pct": _getal(k_ai.rendement_pct, 2) if k_ai else None,
        "meetlat_rendement_pct": _getal(k_m.rendement_pct, 2) if k_m else None,
        "ai_belegd_pct": _getal(k_ai.belegd_pct, 1) if k_ai else None,
        "verschil_pp": _getal(v.verschil_pp, 2) if v else None,
        "p_waarde": _getal(v.p_waarde, 3) if v else None,
        "ruw_verschil_pp": _getal(ruw.verschil_pp, 2) if ruw else None,
        "ruw_p_waarde": _getal(ruw.p_waarde, 3) if ruw else None,
        "start": str(m.get("start", "")),
        "einde": einde.isoformat(),
        "dagen_tot_einde": max(0, (einde - vandaag).days),
        "label": "telt niet mee" if weergave == "proef" else hoofdvraag_label(
            date.fromisoformat(tot), einde, v.verschil_pp if v else None, v.p_waarde if v else None),
    }

    # ---- AI vandaag ----
    laatste = con.execute("SELECT MAX(datum) d FROM ai_besluiten").fetchone()["d"]
    if laatste:
        for r in con.execute(
            """SELECT portefeuille,
                      SUM(CASE WHEN COALESCE(overgeslagen,'') != ? THEN 1 ELSE 0 END) beoordeeld,
                      SUM(CASE WHEN actie='KOOP' THEN 1 ELSE 0 END) kopen,
                      SUM(CASE WHEN actie='VERKOOP' THEN 1 ELSE 0 END) verkopen
               FROM ai_besluiten WHERE datum = ? GROUP BY portefeuille ORDER BY portefeuille""",
            (GEEN_KOERS, laatste),
        ):
            uit["ai_vandaag"].append({"portefeuille": r["portefeuille"], "datum": laatste,
                                      "beoordeeld": r["beoordeeld"], "kopen": r["kopen"], "verkopen": r["verkopen"],
                                      "gemist": _gemiste_ai_dagen(con, r["portefeuille"], start, tot, kal)})
    return uit


def _gemiste_ai_dagen(con: sqlite3.Connection, pf: str, start: str | None, tot: str, kal: list[str]) -> int:
    """Beursdagen sinds de start waarop deze AI-portefeuille niets beoordeelde.

    Na een gemiste nacht haalt de bot de regelstrategieën in, maar de AI alleen de
    nieuwste dag (het nieuws van toen is weg). Dat wordt hier geteld.
    """
    if not start:
        return 0
    gedaan = {r["datum"] for r in con.execute("SELECT DISTINCT datum FROM ai_besluiten WHERE portefeuille = ?", (pf,))}
    eerste = min(gedaan) if gedaan else None
    if eerste is None:
        return 0
    return sum(1 for d in kal if max(start, eerste) <= d <= tot and d not in gedaan)


def _vraag_van(rij: sqlite3.Row) -> str:
    return rij["vraag"] or vraagsoort(rij["referentie"] or "")


def _kansen(con: sqlite3.Connection, cfg: dict) -> dict:
    horizon = int(cfg["ai"]["beslisregel"]["horizon_kalenderdagen"])
    rijen = con.execute(
        "SELECT portefeuille, datum, kans, referentie, vraag, geraakt, gescoord_op FROM ai_besluiten WHERE kans IS NOT NULL"
    ).fetchall()
    groepen: dict[tuple[str, str], dict] = {}
    for r in rijen:
        g = groepen.setdefault((r["portefeuille"], _vraag_van(r)), {"gescoord": [], "open": []})
        if r["gescoord_op"]:
            g["gescoord"].append((float(r["kans"]), int(r["geraakt"])))
        else:
            g["open"].append(r["datum"])

    uit = []
    for (pf, vraag), g in sorted(groepen.items(), key=lambda x: (x[0][0], x[0][1] != RELATIEF)):
        paren = g["gescoord"]
        vakken = []
        aantal_vakken = int(round(1 / VAKBREEDTE))
        for i in range(aantal_vakken):
            laag = i * VAKBREEDTE
            hoog = laag + VAKBREEDTE
            deel = [(k, x) for k, x in paren if laag <= k < hoog or (i == aantal_vakken - 1 and k == 1.0)]
            if deel:
                vakken.append({
                    "van": round(laag, 2), "tot": round(hoog, 2), "n": len(deel),
                    "gem_kans": _getal(sum(k for k, _ in deel) / len(deel), 3),
                    "kwam_uit_pct": _getal(100.0 * sum(x for _, x in deel) / len(deel), 1),
                    "weinig": len(deel) < MIN_PER_VAK,
                })
        eerste = None
        if g["open"]:
            # Gescoord in de ronde na de horizon (dan is de slotkoers van die dag bekend).
            eerste = (date.fromisoformat(min(g["open"])) + timedelta(days=horizon + 1)).isoformat()
        uit.append({
            "portefeuille": pf, "vraag": vraag, "n": len(paren), "open": len(g["open"]),
            "brier": _getal(sum((k - x) ** 2 for k, x in paren) / len(paren), 4) if paren else None,
            "gem_kans": _getal(sum(k for k, _ in paren) / len(paren), 3) if paren else None,
            "kwam_uit_pct": _getal(100.0 * sum(x for _, x in paren) / len(paren), 1) if paren else None,
            # Wie altijd de basiskans zegt (hoe vaak het uitkwam), scoort f * (1 - f).
            "brier_basiskans": _getal((lambda f: f * (1 - f))(sum(x for _, x in paren) / len(paren)), 4) if paren else None,
            "vakken": vakken, "eerste_score": eerste,
        })
    return {"brier_geen_idee": kalibratie.BRIER_GEEN_IDEE, "horizon_dagen": horizon,
            "kosten_pct": kalibratie.kosten_heen_en_terug_pct(cfg), "groepen": uit}


def _vergaderzaal(con: sqlite3.Connection, map_: Path) -> dict:
    dagen = [r["datum"] for r in con.execute("SELECT DISTINCT datum FROM ai_besluiten ORDER BY datum DESC")]
    for d in dagen:
        items = []
        for r in con.execute(
            """SELECT portefeuille, symbool, actie, kans, verwacht_pct, referentie, vraag, onderbouwing,
                      overgeslagen, stappen, koppen, kosten_usd, uitkomst_pct, geraakt
               FROM ai_besluiten WHERE datum = ? ORDER BY symbool, portefeuille""",
            (d,),
        ):
            try:
                stappen = json.loads(r["stappen"] or "{}")
            except json.JSONDecodeError:
                stappen = {}
            try:
                koppen = json.loads(r["koppen"] or "[]")
            except json.JSONDecodeError:
                koppen = []
            items.append({
                "portefeuille": r["portefeuille"], "symbool": r["symbool"], "actie": r["actie"],
                "kans": _getal(r["kans"], 3), "verwacht_pct": _getal(r["verwacht_pct"], 2),
                "referentie": r["referentie"], "vraag": _vraag_van(r),
                "onderbouwing": r["onderbouwing"] or "", "probleem": r["overgeslagen"],
                "stappen": {k: v for k, v in stappen.items() if k in ("kenmerken", "nieuws", "bull", "bear", "risk", "enkel")},
                "koppen": [{"gepubliceerd": k.get("gepubliceerd"), "titel": k.get("titel"),
                            "bron": k.get("bron"), "url": k.get("url")} for k in koppen if isinstance(k, dict)],
                "kosten_usd": _getal(r["kosten_usd"], 4),
                "uitkomst_pct": _getal(r["uitkomst_pct"], 2), "geraakt": r["geraakt"],
            })
        _schrijf(map_ / "vergaderzaal" / f"{d}.json", items)
    symbolen = [r["symbool"] for r in con.execute("SELECT DISTINCT symbool FROM ai_besluiten ORDER BY symbool")]
    portefeuilles = [r["portefeuille"] for r in con.execute("SELECT DISTINCT portefeuille FROM ai_besluiten ORDER BY portefeuille")]
    return {"dagen": dagen, "symbolen": symbolen, "portefeuilles": portefeuilles}


def _weekrapport(con: sqlite3.Connection) -> dict:
    rapporten = []
    for r in con.execute("SELECT datum, tekst, conclusies FROM rapporten ORDER BY datum DESC LIMIT 26"):
        try:
            conclusies = json.loads(r["conclusies"] or "[]")
        except json.JSONDecodeError:
            conclusies = []
        rapporten.append({"datum": r["datum"], "tekst": r["tekst"], "conclusies": conclusies})
    challengers = [
        {k: r[k] for k in ("id", "strategie", "parameter", "oude_waarde", "nieuwe_waarde", "status",
                           "gestart_op", "beoordeeld_op", "uitslag", "portefeuille", "portefeuille_a", "onderbouwing")}
        for r in con.execute("SELECT * FROM challengers ORDER BY id DESC")
    ]
    wijzigingen = [
        {k: r[k] for k in ("datum", "strategie", "parameter", "oude_waarde", "nieuwe_waarde", "challenger_id", "akkoord")}
        for r in con.execute("SELECT * FROM wijzigingen ORDER BY datum DESC")
    ]
    return {"rapporten": rapporten, "challengers": challengers, "wijzigingen": wijzigingen}


def _kosten(con: sqlite3.Connection, cfg: dict, vandaag: date) -> dict:
    budget = Budget(con, cfg, vandaag=vandaag.isoformat())
    per_dag = [
        {"datum": r["datum"], "usd": _getal(r["usd"], 4)}
        for r in con.execute(
            "SELECT datum, SUM(kosten_usd) usd FROM ai_kosten WHERE rol != 'correctie' GROUP BY datum ORDER BY datum"
        )
    ]
    per_rol = [
        {"maand": r["maand"], "rol": r["rol"], "model": r["model"], "aanroepen": r["n"],
         "tokens_in": r["ti"], "tokens_uit": r["tu"], "usd": _getal(r["usd"], 4)}
        for r in con.execute(
            """SELECT maand, rol, model, COUNT(*) n, SUM(tokens_in) ti, SUM(tokens_uit) tu, SUM(kosten_usd) usd
               FROM ai_kosten GROUP BY maand, rol, model ORDER BY maand DESC, usd DESC"""
        )
    ]
    afwijkend = con.execute(
        "SELECT COUNT(*) n FROM ai_kosten WHERE model_antwoord IS NOT NULL AND model_antwoord != model"
    ).fetchone()["n"]
    return {
        "maand": budget.maand, "budget_usd": _getal(budget.max_usd, 2),
        "ander_model": afwijkend,
        "uitgegeven_usd": _getal(budget.uitgegeven(), 4),
        "per_dag": per_dag, "per_rol": per_rol,
    }


def _symbool_extra(con: sqlite3.Connection, symbool: str, vanaf: str) -> dict:
    """Trades en AI-kansen van een weergave voor een symbool."""
    trades = [
        {"datum": r["datum"], "portefeuille": r["portefeuille"], "actie": r["actie"],
         "prijs": _getal(r["prijs_usd"], 4), "bedrag": _getal(r["bedrag_usd"], 2),
         "resultaat": _getal(r["resultaat_usd"], 2), "resultaat_pct": _getal(r["resultaat_pct"], 2),
         "besluit_datum": r["besluit_datum"], "reden": r["reden"] or ""}
        for r in con.execute(
            """SELECT datum, portefeuille, actie, prijs_usd, bedrag_usd, resultaat_usd, resultaat_pct,
                      besluit_datum, reden FROM trades WHERE symbool = ? AND datum >= ? ORDER BY datum, id""",
            (symbool, vanaf),
        )
    ]
    kansen: dict[str, list] = {}
    for r in con.execute(
        "SELECT portefeuille, datum, kans FROM ai_besluiten WHERE symbool = ? AND kans IS NOT NULL ORDER BY datum",
        (symbool,),
    ):
        kansen.setdefault(r["portefeuille"], []).append([r["datum"], _getal(r["kans"], 3)])
    return {"trades": trades, "kansen": kansen}


# ------------------------------------------------------------------- koersen


def _koersen(markt: Markt, symbool: str, dagen: int, eind: str) -> dict | None:
    df = markt.tot_en_met(symbool, eind)
    if df is None or df.empty:
        return None
    slot = df["slot"].astype(float)
    ma = {n: indicatoren.voortschrijdend_gemiddelde(slot, n) for n in (5, 20, 50, 200)}
    rsi2 = indicatoren.rsi(slot, 2)
    std20 = slot.rolling(20, min_periods=20).std(ddof=0)
    vanaf = (date.fromisoformat(eind) - timedelta(days=dagen)).isoformat()
    deel = df.index >= vanaf
    rijen = []
    for i, (d, r) in enumerate(df.iterrows()):
        if not deel[i]:
            continue
        rijen.append([
            d, _getal(r["open"]), _getal(r["hoog"]), _getal(r["laag"]), _getal(r["slot"]), _getal(r["volume"], 0),
            _getal(ma[5].iloc[i]), _getal(ma[50].iloc[i]), _getal(ma[200].iloc[i]),
            _getal(ma[20].iloc[i] + 2 * std20.iloc[i]) if not pd.isna(std20.iloc[i]) else None,
            _getal(ma[20].iloc[i]),
            _getal(ma[20].iloc[i] - 2 * std20.iloc[i]) if not pd.isna(std20.iloc[i]) else None,
            _getal(rsi2.iloc[i], 2),
        ])
    return {
        "symbool": symbool,
        "kolommen": ["datum", "open", "hoog", "laag", "slot", "volume", "ma5", "ma50", "ma200",
                     "bb_boven", "bb_midden", "bb_onder", "rsi2"],
        "rijen": rijen,
        "vanaf": vanaf,
    }


# --------------------------------------------------------------------- alles


def _cfg_proef(basis: dict) -> dict:
    from bot import modus

    overlay = modus.lees_proefweek()
    if not overlay:
        return basis
    cfg = modus._voeg_samen(basis, overlay)
    if cfg.get("ai_watchlist") == "alles":
        cfg["ai_watchlist"] = conf.alle_symbolen(cfg)
    return cfg


def exporteer(
    uit: Path,
    koers_con: sqlite3.Connection,
    con_echt: sqlite3.Connection | None,
    con_proef: sqlite3.Connection | None,
    echt_beschikbaar: bool,
    vandaag: date | None = None,
    label: str = "",
    cfg: dict | None = None,
) -> dict:
    """Bouw de hele site in 'uit': de statische bestanden en de gegevens."""
    vandaag = vandaag or datetime.now(timezone.utc).date()
    basis = cfg or conf.laad()
    if uit.exists():
        shutil.rmtree(uit)
    shutil.copytree(SITE_BRON, uit)
    data = uit / "data"

    m = basis.get("meting") or {}
    markt = Markt.uit_database(koers_con)
    eind = markt.datums[-1] if markt.datums else vandaag.isoformat()
    dagen = int((basis.get("dashboard") or {}).get("symbool_dagen", 365))
    symbolen = [s for s in conf.alle_symbolen(basis) if markt.heeft(s)]
    vanaf = (date.fromisoformat(eind) - timedelta(days=dagen)).isoformat()

    lijst = []
    for s in symbolen:
        k = _koersen(markt, s, dagen, eind)
        if k is None:
            continue
        _schrijf(data / "koersen" / bestandsnaam(s), k)
        soort = "crypto" if conf.is_crypto(s) else ("etf" if s in basis["watchlist"].get("etfs", []) else "aandeel")
        lijst.append({"symbool": s, "bestand": bestandsnaam(s), "soort": soort})
    _schrijf(data / "symbolen.json", lijst)

    weergaven: dict[str, dict] = {}
    for naam, con, beschikbaar, cfg_w in (
        ("echt", con_echt, echt_beschikbaar and con_echt is not None, basis),
        ("proef", con_proef, con_proef is not None, _cfg_proef(basis)),
    ):
        info: dict[str, Any] = {"beschikbaar": beschikbaar}
        if naam == "echt" and not beschikbaar:
            info["melding"] = f"De echte meting begint op {m.get('start', '')}."
        if naam == "proef":
            info["label"] = "Proefweek, telt niet mee"
        weergaven[naam] = info
        if not beschikbaar:
            continue
        map_ = data / naam
        _schrijf(map_ / "overzicht.json", _overzicht(con, cfg_w, naam, vandaag))
        _schrijf(map_ / "kansen.json", _kansen(con, cfg_w))
        _schrijf(map_ / "vergaderzaal.json", _vergaderzaal(con, map_))
        _schrijf(map_ / "weekrapport.json", _weekrapport(con))
        _schrijf(map_ / "kosten.json", _kosten(con, cfg_w, vandaag))
        for s in symbolen:
            _schrijf(map_ / "symbolen" / bestandsnaam(s), _symbool_extra(con, s, vanaf))

    meta = {
        "gemaakt_op": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "koersen_tot": eind,
        "label": label,
        "meting": {"start": str(m.get("start", "")), "einde": str(m.get("einde", ""))},
        "beslisregel": {k: basis["ai"]["beslisregel"][k] for k in ("koop_kans", "koop_verwacht_pct", "verkoop_kans_onder")},
        "mean_reversion": {k: basis["strategieen"]["mean_reversion"][k] for k in ("rsi_periode", "koop_onder", "trendfilter_ma", "verkoop_boven_ma")},
        "trend": {k: basis["strategieen"]["trend"][k] for k in ("snelle_ma", "langzame_ma")},
        "weergaven": weergaven,
        "standaard": "echt" if weergaven["echt"]["beschikbaar"] else "proef",
        "vragen": {RELATIEF: "tegen SPY of BTC", ABSOLUUT: "SPY en BTC zelf, tegen kas"},
        "kas": KAS,
    }
    _schrijf(data / "meta.json", meta)
    return meta
