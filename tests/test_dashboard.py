"""Het dashboard: de export is compleet, eerlijk, en bevat nooit geheimen."""

from __future__ import annotations

import json
import math
import re
from datetime import date

import pytest

from bot import dashboard, database, telegram
from bot.markt import Markt
from bot.motor import Motor
from bot.strategieen.basis import Order, Strategie
from bot.strategieen.benchmarks import GelijkVerdeeld, KoopEnHoud
from tests.conftest import werkdagen

SYMBOLEN = ["SPY", "AAPL", "BTC/USDT"]


class KoopAAPL(Strategie):
    naam = "momentum"

    def __init__(self, dag):
        super().__init__({})
        self.dag_koop = dag

    def besluit(self, datum, markt, symbolen, posities):
        if datum == self.dag_koop and not posities:
            return [Order(symbool="AAPL", actie="KOOP", reden="test: sterk rendement")]
        return []


class Niets(Strategie):
    naam = "ai_analist"

    def besluit(self, datum, markt, symbolen, posities):
        return []


@pytest.fixture
def cfg(cfg_min):
    c = dict(cfg_min)
    c["watchlist"] = {"aandelen": ["AAPL"], "etfs": ["SPY"], "crypto": ["BTC/USDT"]}
    c["meting"] = {"start": "2026-11-23", "einde": "2027-02-23",
                   "ai_portefeuille": "ai_analist", "proef_ai_portefeuille": "proef_ai_mix"}
    return c


@pytest.fixture
def gevuld(con, cfg, vaste_wisselkoers):
    dagen = werkdagen(260, start="2025-11-03")
    rijen = []
    for i, d in enumerate(dagen):
        for s, basis in (("SPY", 500.0), ("AAPL", 200.0), ("BTC/USDT", 90000.0)):
            k = basis * (1 + 0.001 * i) * (1 + 0.01 * math.sin(i / 3))
            rijen.append({"symbool": s, "datum": d, "open": k, "hoog": k * 1.01, "laag": k * 0.99,
                          "slot": k, "volume": 1000.0 + i, "valuta": "USD", "bron": "test"})
    database.bewaar_koersen(con, rijen)
    con.commit()
    markt = Markt.uit_database(con)
    motor = Motor(con, cfg, markt, vaste_wisselkoers)
    begin = dagen[-20]
    motor.voeg_toe(KoopEnHoud("SPY"), ["SPY"], gestart_op=begin)
    motor.voeg_toe(GelijkVerdeeld("gelijk_alle", SYMBOLEN), SYMBOLEN, gestart_op=begin)
    motor.voeg_toe(KoopAAPL(dagen[-15]), SYMBOLEN, gestart_op=begin)
    motor.voeg_toe(Niets({}), SYMBOLEN, gestart_op=begin)
    motor.draai_periode(begin, dagen[-1])
    database.zet_instelling(con, "start_datum", begin)
    laatste = dagen[-1]
    for s, actie, kans, ref, vraag in (("AAPL", "KOOP", 0.62, "SPY", "relatief"),
                                       ("SPY", "NIETS", 0.48, "kas", "absoluut"),
                                       ("BTC/USDT", "NIETS", None, "kas", "absoluut")):
        con.execute(
            """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kans, verwacht_pct, referentie, vraag,
                                         onderbouwing, stappen, koppen, kosten_usd, overgeslagen, nieuws_tot, gemaakt_op,
                                         horizon_dagen)
               VALUES ('ai_analist', ?, ?, ?, ?, 1.0, ?, ?, 'omdat', ?, ?, 0.02, ?, 'x', 'x', 14)""",
            (s, laatste, actie, kans, ref, vraag,
             json.dumps({"nieuws": {"samenvatting": "rustig"}, "risk": {"kans": kans}}),
             json.dumps([{"gepubliceerd": f"{laatste}T12:00:00+00:00", "titel": "<script>alert(1)</script>", "bron": "x", "url": "https://example.com"}]),
             None if kans is not None else "risk: antwoord afgekapt"),
        )
    # Een oude, al gescoorde kans, voor de kalibratie.
    con.execute(
        """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kans, referentie, vraag, nieuws_tot, gemaakt_op,
                                     horizon_dagen, uitkomst_pct, geraakt, gescoord_op)
           VALUES ('ai_analist', 'AAPL', ?, 'NIETS', 0.61, 'SPY', 'relatief', 'x', 'x', 14, 2.0, 1, ?)""",
        (dagen[-30], laatste),
    )
    con.commit()
    return {"dagen": dagen, "laatste": laatste}


def _bouw(tmp_path, con, cfg, beschikbaar=True, vandaag=date(2026, 11, 20)):
    uit = tmp_path / "site"
    meta = dashboard.exporteer(uit, con, con, None, beschikbaar, vandaag=vandaag, cfg=cfg)
    return uit, meta


def _json(pad):
    return json.loads(pad.read_text(encoding="utf-8"))


# ------------------------------------------------------------- de export


def test_de_export_is_compleet(tmp_path, con, cfg, gevuld):
    uit, meta = _bouw(tmp_path, con, cfg)
    for bestand in ("index.html", "app.js", "style.css", "data/meta.json", "data/symbolen.json",
                    "data/koersen/AAPL.json", "data/koersen/BTC-USDT.json",
                    "data/echt/overzicht.json", "data/echt/kansen.json", "data/echt/vergaderzaal.json",
                    "data/echt/weekrapport.json", "data/echt/kosten.json", "data/echt/symbolen/AAPL.json",
                    f"data/echt/vergaderzaal/{gevuld['laatste']}.json"):
        assert (uit / bestand).exists(), bestand
    assert meta["standaard"] == "echt" and meta["weergaven"]["proef"]["beschikbaar"] is False


def test_overzicht_scorekaarten_en_ai_vandaag(tmp_path, con, cfg, gevuld):
    uit, _ = _bouw(tmp_path, con, cfg)
    ov = _json(uit / "data/echt/overzicht.json")
    per = {p["naam"]: p for p in ov["portefeuilles"]}
    assert set(per) == {"benchmark_spy", "gelijk_alle", "momentum", "ai_analist"}
    assert per["benchmark_spy"]["soort"] == "meetlat" and per["benchmark_spy"]["meetlat"] is None
    assert per["momentum"]["soort"] == "regel" and per["momentum"]["meetlat"] == "gelijk_alle"
    assert per["ai_analist"]["soort"] == "ai"
    assert per["momentum"]["trades"] == 1 and per["momentum"]["vs_meetlat_pp"] is not None
    assert ov["ai_vandaag"] == [{"portefeuille": "ai_analist", "datum": gevuld["laatste"],
                                 "beoordeeld": 3, "kopen": 1, "verkopen": 0, "gemist": 19}]
    h = ov["hoofdvraag"]
    assert h["ai"] == "ai_analist" and h["meetlat"] == "gelijk_alle"
    assert h["label"] == "nog niet te zeggen" and h["dagen_tot_einde"] == 95


def test_label_bij_de_hoofdvraag():
    einde = date(2027, 2, 23)
    voor, na = date(2027, 2, 22), date(2027, 2, 23)
    assert dashboard.hoofdvraag_label(voor, einde, 5.0, 0.001) == "nog niet te zeggen", "niet vooruitlopen op de einddatum"
    assert dashboard.hoofdvraag_label(na, einde, 5.0, 0.01) == "ja"
    assert dashboard.hoofdvraag_label(na, einde, 5.0, 0.2) == "nee"
    assert dashboard.hoofdvraag_label(na, einde, -1.0, 0.01) == "nee"
    assert dashboard.hoofdvraag_label(na, einde, None, None) == "nee"


def test_eigen_meetlat():
    ai = {"ai_analist", "ai_enkel"}
    alleen_alle = {"gelijk_alle", "momentum", "ai_analist"}
    met_ai_lijst = alleen_alle | {"gelijk_ai", "momentum_ai"}
    assert dashboard.eigen_meetlat("momentum", ai, alleen_alle) == "gelijk_alle"
    assert dashboard.eigen_meetlat("ai_analist", ai, alleen_alle) == "gelijk_alle"
    assert dashboard.eigen_meetlat("ai_analist", ai, met_ai_lijst) == "gelijk_ai"
    assert dashboard.eigen_meetlat("momentum_ai", ai, met_ai_lijst) == "gelijk_ai"
    assert dashboard.eigen_meetlat("momentum", ai, met_ai_lijst) == "gelijk_alle"
    assert dashboard.eigen_meetlat("gelijk_alle", ai, met_ai_lijst) is None


def test_kansen_per_soort_vraag_met_vakken_van_005(tmp_path, con, cfg, gevuld):
    cfg = {**cfg, "meting": {**cfg["meting"], "start": "2025-01-01"}}  # de testdata valt dan in de meting
    uit, _ = _bouw(tmp_path, con, cfg)
    ka = _json(uit / "data/echt/kansen.json")
    g = {x["vraag"]: x for x in ka["groepen"] if x["portefeuille"] == "ai_analist"}
    assert set(g) == {"relatief", "absoluut"}
    rel = g["relatief"]
    assert rel["n"] == 1 and rel["brier"] == pytest.approx((0.61 - 1) ** 2, abs=1e-4)
    assert rel["vakken"] == [{"van": 0.6, "tot": 0.65, "n": 1, "gem_kans": 0.61, "kwam_uit_pct": 100.0, "weinig": True}]
    assert rel["open"] == 1
    assert g["absoluut"]["n"] == 0 and g["absoluut"]["eerste_score"] is not None
    assert ka["brier_geen_idee"] == 0.25


def test_lijnen_zijn_dezelfde_als_die_van_de_bot(tmp_path, con, cfg, gevuld):
    uit, _ = _bouw(tmp_path, con, cfg)
    k = _json(uit / "data/koersen/AAPL.json")
    kol = {n: i for i, n in enumerate(k["kolommen"])}
    laatste = k["rijen"][-1]
    markt = Markt.uit_database(con)
    d = laatste[kol["datum"]]
    assert laatste[kol["ma50"]] == pytest.approx(markt.kengetal("AAPL", d, "ma50"), rel=1e-6)
    assert laatste[kol["ma200"]] == pytest.approx(markt.kengetal("AAPL", d, "ma200"), rel=1e-6)
    assert laatste[kol["ma5"]] == pytest.approx(markt.kengetal("AAPL", d, "ma5"), rel=1e-6)
    assert laatste[kol["rsi2"]] == pytest.approx(markt.kengetal("AAPL", d, "rsi2"), abs=0.01)


def test_trades_met_reden_per_symbool(tmp_path, con, cfg, gevuld):
    uit, _ = _bouw(tmp_path, con, cfg)
    s = _json(uit / "data/echt/symbolen/AAPL.json")
    eigen = [t for t in s["trades"] if t["portefeuille"] == "momentum"]
    assert eigen and eigen[0]["actie"] == "KOOP" and eigen[0]["reden"] == "test: sterk rendement"
    assert s["kansen"]["ai_analist"][-1] == [gevuld["laatste"], 0.62]


def test_vergaderzaal_ook_bij_niets_doen(tmp_path, con, cfg, gevuld):
    uit, _ = _bouw(tmp_path, con, cfg)
    items = _json(uit / f"data/echt/vergaderzaal/{gevuld['laatste']}.json")
    acties = {i["symbool"]: (i["actie"], i["vraag"]) for i in items}
    assert acties == {"AAPL": ("KOOP", "relatief"), "SPY": ("NIETS", "absoluut"), "BTC/USDT": ("NIETS", "absoluut")}
    btc = next(i for i in items if i["symbool"] == "BTC/USDT")
    assert btc["probleem"] == "risk: antwoord afgekapt"
    assert items[0]["koppen"][0]["gepubliceerd"].endswith("+00:00")


def test_echte_weergave_tijdens_de_proefweek_is_leeg(tmp_path, con, cfg, gevuld):
    uit, meta = _bouw(tmp_path, con, cfg, beschikbaar=False)
    assert meta["weergaven"]["echt"] == {"beschikbaar": False, "melding": "De echte meting begint op 2026-11-23."}
    assert not (uit / "data/echt").exists()


# ----------------------------------------------------------- geen geheimen


def test_nooit_geheimen_in_de_export(tmp_path, con, cfg, gevuld, monkeypatch):
    nep_token = "123456789:NEPTOKENNEPTOKENNEPTOKENNEPTOKEN12"
    nep_sleutel = "sk-ant-nepsleutel-0000000000000000"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", nep_token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "424242")
    monkeypatch.setenv("ANTHROPIC_API_KEY", nep_sleutel)
    database.zet_instelling(con, "telegram_offset", "987654321")
    database.zet_instelling(con, "iets_geheims", "ZEERGEHEIMEWAARDE")
    con.commit()
    uit, _ = _bouw(tmp_path, con, cfg)
    alles = "\n".join(p.read_text(encoding="utf-8") for p in uit.rglob("*") if p.is_file())
    for verboden in (nep_token, nep_sleutel, "424242", "987654321", "ZEERGEHEIMEWAARDE",
                     "TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "TELEGRAM_CHAT_ID"):
        assert verboden not in alles, verboden
    assert not re.search(r"\d{8,10}:[A-Za-z0-9_-]{30,}", alles), "iets dat op een Telegram-sleutel lijkt"
    assert not re.search(r"sk-ant-[A-Za-z0-9_-]{10,}", alles), "iets dat op een Anthropic-sleutel lijkt"


def test_geen_nan_in_de_export():
    assert dashboard._getal(float("nan")) is None
    assert dashboard._getal(float("inf")) is None
    with pytest.raises(ValueError):
        json.dumps({"x": float("nan")}, allow_nan=False)


# --------------------------------------------------------------- de site


def test_site_vaste_versie_licentie_en_nepgeld():
    html = (dashboard.SITE_BRON / "index.html").read_text(encoding="utf-8")
    js = (dashboard.SITE_BRON / "app.js").read_text(encoding="utf-8")
    assert "lightweight-charts@5.2.1/" in html and 'integrity="sha256-' in html
    assert "TradingView Lightweight Charts" in html and "https://www.tradingview.com/" in html
    assert "attributionLogo: true" in js
    assert "Nep-geld" in html
    assert "innerHTML" not in js, "tekst uit de data altijd via textContent"


def test_geen_gedachtestreepjes_in_de_site():
    for naam in ("index.html", "app.js", "style.css"):
        tekst = (dashboard.SITE_BRON / naam).read_text(encoding="utf-8")
        assert "—" not in tekst and "–" not in tekst, naam


# -------------------------------------------------------------- Telegram


def test_elk_telegrambericht_krijgt_de_dashboardlink(monkeypatch):
    verstuurd = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(telegram, "_roep_aan", lambda token, methode, gegevens=None, timeout=20: verstuurd.append(gegevens) or {"ok": True})
    telegram.stuur("Paper trading: stand")
    url = telegram.dashboard_url()
    assert url and url.startswith("https://")
    assert verstuurd[-1]["text"].endswith("Dashboard: " + url)
    assert telegram.met_dashboardlink("al met " + url) == "al met " + url, "niet twee keer"


def test_kansen_buiten_de_meetperiode_tellen_niet_mee(tmp_path, con, cfg, gevuld):
    """In de echte meting tellen alleen kansen van de start tot en met de einddatum."""
    uit, _ = _bouw(tmp_path, con, cfg)  # start 2026-11-23, de testdata is van daarvoor
    assert _json(uit / "data/echt/kansen.json")["groepen"] == []
