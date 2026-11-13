"""Kas als referentie voor SPY en BTC, de soort vraag bij elke kans, en het scoren achteraf."""

from __future__ import annotations

import json

import pytest

from bot import config as conf
from bot.ai import kalibratie
from bot.ai.budget import Budget
from bot.ai.client import NepClient
from bot.ai.kenmerken import ABSOLUUT, KAS, RELATIEF, referentie, tabel
from bot.ai.ronde import denk_na
from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN, alle_dagen, maak_markt, werkdagen

ECHT = conf.laad()


@pytest.mark.parametrize("symbool, ref", [
    ("SPY", KAS), ("BTC/USDT", KAS), ("AAPL", "SPY"), ("QQQ", "SPY"), ("ETH/USDT", "BTC/USDT"),
])
def test_referentie(symbool, ref):
    assert referentie(symbool) == ref


def test_spy_heeft_een_absolute_vraag_zonder_vergelijking_met_zichzelf():
    datums = werkdagen(200)
    markt = maak_markt({"SPY": [400.0 + i for i in range(200)], "AAPL": [100.0 + i for i in range(200)]}, datums)
    spy = tabel(markt, "SPY", datums[-1])
    aapl = tabel(markt, "AAPL", datums[-1])
    assert spy["referentie"] == KAS and spy["vraag"] == ABSOLUUT
    assert not any("_min_" in k for k in spy), "SPY tegenover SPY zegt niets"
    assert aapl["vraag"] == RELATIEF and aapl["rendement_28d_min_SPY_pct"] is not None


# ------------------------------------------------------ in de ronde van de AI


@pytest.fixture
def cfg_ai(cfg_min):
    cfg = dict(cfg_min)
    cfg["ai"] = json.loads(json.dumps(ECHT["ai"]))
    cfg["prijzen"] = dict(ECHT["prijzen"])
    return cfg


ANTWOORDEN = {
    "nieuws": {"samenvatting": "rustig", "belangrijkste_risico": "geen", "gebeurtenis_binnenkort": False},
    "bull": {"argument": "a", "sterkste_punt": "b", "overtuiging": 0.5},
    "bear": {"argument": "c", "sterkste_punt": "d", "overtuiging": 0.5},
    "risk": {"kans": 0.5, "verwacht_extra_rendement_pct": 0.0, "onderbouwing": "x", "belangrijkste_risico": "y"},
}


def test_de_vraag_staat_in_het_bericht_en_de_soort_in_de_database(con, cfg_ai):
    datums = werkdagen(130)
    markt = maak_markt({"SPY": [400.0 + i for i in range(130)], "AAPL": [100.0 + i for i in range(130)]}, datums)
    nieuwe_portefeuille("ai_analist", "ai_analist", INSTELLINGEN, gestart_op=datums[0]).bewaar(con)
    con.commit()
    client = NepClient(Budget(con, cfg_ai, vandaag=datums[-1]), ANTWOORDEN)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datums[-1], ["SPY", "AAPL"], stil=True)

    risk = {("SPY" if "Symbool: SPY" in c["bericht"] else "AAPL"): c["bericht"]
            for c in client.aanroepen if c["rol"] == "risk"}
    assert "de referentie is kas" in risk["SPY"]
    assert "beter dan SPY" in risk["AAPL"]
    for c in client.aanroepen:
        if c["rol"] in ("bull", "bear", "risk") and "Symbool: SPY" in c["bericht"]:
            assert "kas" in c["bericht"], c["rol"]

    rijen = {r["symbool"]: (r["referentie"], r["vraag"])
             for r in con.execute("SELECT symbool, referentie, vraag FROM ai_besluiten")}
    assert rijen == {"SPY": (KAS, ABSOLUUT), "AAPL": ("SPY", RELATIEF)}


def test_beslisregel_gebruikt_dezelfde_drempels_voor_beide_vragen():
    from bot.ai import beslisregel
    from bot.ai.analist import Beoordeling

    regels = ECHT["ai"]["beslisregel"]
    b = {"SPY": Beoordeling("SPY", "2026-11-13", KAS, kans=0.6, verwacht_pct=1.0)}
    u = beslisregel.kies(b, {}, set(), set(), "2026-11-13", regels, 10)
    assert [o.symbool for o in u.orders] == ["SPY"]
    assert "tegenover kas" in u.orders[0].reden


# ------------------------------------------------------------ scoren


def _markt_twee_weken():
    dagen = alle_dagen(30, start="2026-11-01")
    # AAA +10% over de horizon, SPY +4%, BTC -5%.
    aaa = [100.0] * 5 + [110.0] * 25
    spy = [100.0] * 5 + [104.0] * 25
    btc = [100.0] * 5 + [95.0] * 25
    return maak_markt({"AAA": aaa, "SPY": spy, "BTC/USDT": btc}, dagen)


def test_uitkomst_relatief_en_absoluut():
    markt = _markt_twee_weken()
    assert kalibratie.uitkomst(markt, "AAA", "SPY", "2026-11-01", 14) == pytest.approx(6.0)
    assert kalibratie.uitkomst(markt, "SPY", KAS, "2026-11-01", 14) == pytest.approx(4.0)
    assert kalibratie.uitkomst(markt, "BTC/USDT", KAS, "2026-11-01", 14) == pytest.approx(-5.0)


def test_nog_niet_scoren_voor_de_horizon_voorbij_is():
    markt = _markt_twee_weken()
    assert kalibratie.uitkomst(markt, "AAA", "SPY", "2026-11-20", 14) is None


def _besluit(con, pf, symbool, datum, kans, ref, vraag):
    con.execute(
        """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kans, verwacht_pct, referentie,
                                     horizon_dagen, vraag, nieuws_tot, gemaakt_op)
           VALUES (?,?,?,'NIETS',?,0,?,14,?,'x','x')""",
        (pf, symbool, datum, kans, ref, vraag),
    )


def test_scoren_rekent_de_kosten_mee_en_scoort_maar_een_keer(con, cfg_min):
    cfg = dict(cfg_min)
    cfg["ai"] = ECHT["ai"]
    markt = _markt_twee_weken()
    _besluit(con, "ai_analist", "AAA", "2026-11-01", 0.8, "SPY", RELATIEF)
    _besluit(con, "ai_analist", "SPY", "2026-11-01", 0.6, KAS, ABSOLUUT)
    _besluit(con, "ai_analist", "BTC/USDT", "2026-11-01", 0.3, KAS, ABSOLUUT)
    _besluit(con, "ai_analist", "AAA", "2026-11-25", 0.5, "SPY", RELATIEF)  # horizon nog niet om
    con.commit()
    assert kalibratie.scoor(con, markt, cfg) == 3
    uit = {(r["symbool"], r["datum"]): (r["uitkomst_pct"], r["geraakt"])
           for r in con.execute("SELECT * FROM ai_besluiten")}
    assert uit[("AAA", "2026-11-01")] == (pytest.approx(6.0), 1)
    assert uit[("SPY", "2026-11-01")] == (pytest.approx(4.0), 1)
    assert uit[("BTC/USDT", "2026-11-01")] == (pytest.approx(-5.0), 0)
    assert uit[("AAA", "2026-11-25")] == (None, None)
    assert kalibratie.scoor(con, markt, cfg) == 0, "niet twee keer scoren"


def test_kosten_tellen_mee_in_geraakt(con, cfg_min):
    cfg = dict(cfg_min)
    cfg["ai"] = ECHT["ai"]
    assert kalibratie.kosten_heen_en_terug_pct(cfg) == pytest.approx(0.3)
    dagen = alle_dagen(30, start="2026-11-01")
    markt = maak_markt({"SPY": [100.0] * 5 + [100.2] * 25}, dagen)  # +0,2%: minder dan de kosten
    _besluit(con, "ai_analist", "SPY", "2026-11-01", 0.6, KAS, ABSOLUUT)
    con.commit()
    kalibratie.scoor(con, markt, cfg)
    assert con.execute("SELECT geraakt FROM ai_besluiten").fetchone()["geraakt"] == 0


def test_brier_apart_per_soort_vraag(con, cfg_min):
    cfg = dict(cfg_min)
    cfg["ai"] = ECHT["ai"]
    markt = _markt_twee_weken()
    _besluit(con, "ai_analist", "AAA", "2026-11-01", 0.8, "SPY", RELATIEF)   # geraakt: (0,8-1)^2 = 0,04
    _besluit(con, "ai_analist", "SPY", "2026-11-01", 0.6, KAS, ABSOLUUT)     # geraakt: 0,16
    _besluit(con, "ai_analist", "BTC/USDT", "2026-11-01", 0.6, KAS, ABSOLUUT)  # niet: 0,36
    con.commit()
    kalibratie.scoor(con, markt, cfg)
    s = {x.vraag: x for x in kalibratie.scores(con)}
    assert s[RELATIEF].n == 1 and s[RELATIEF].brier == pytest.approx(0.04)
    assert s[ABSOLUUT].n == 2 and s[ABSOLUUT].brier == pytest.approx((0.16 + 0.36) / 2)
    assert s[ABSOLUUT].kwam_uit_pct == pytest.approx(50.0)
    tekst = kalibratie.als_tekst(con)
    assert "relatief (tegen SPY of BTC): 1 kansen, Brier 0,040" in tekst
    assert "absoluut (SPY en BTC zelf, tegen kas): 2 kansen, Brier 0,260" in tekst
    assert "0,250" in tekst, "de maat voor 'geen idee' staat erbij"


def test_zonder_gescoorde_kansen_zegt_het_rapport_dat_eerlijk(con):
    _besluit(con, "ai_analist", "AAA", "2026-11-13", 0.5, "SPY", RELATIEF)
    con.commit()
    assert "Nog niets te scoren" in kalibratie.als_tekst(con)


def test_weekrapport_toont_de_kalibratie(con, cfg_min):
    from bot import database, weekrapport

    cfg = json.loads(json.dumps(ECHT))
    for d in alle_dagen(10, start="2026-11-12"):
        database.bewaar_koersen(con, [{"symbool": "SPY", "datum": d, "open": 1.0, "hoog": 1.0, "laag": 1.0,
                                       "slot": 1.0, "volume": 1.0, "valuta": "USD", "bron": "test"}])
    database.zet_instelling(con, "start_datum", "2026-11-12")
    _besluit(con, "ai_analist", "AAA", "2026-11-13", 0.5, "SPY", RELATIEF)
    con.commit()
    week = weekrapport.maak(con, cfg, "2026-11-21", client=None)
    assert "Klopten de kansen van de AI?" in week.tekst
