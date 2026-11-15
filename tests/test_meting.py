"""Tests voor de spelregels van de meting (bot/meting.py).

* De hoofdvraag: vier vaste meetmomenten met per moment een strengere grens
  (O'Brien-Fleming). Samen ongeveer 5% kans op een onterecht "ja".
* De verkennende portefeuilles: een winnaar telt alleen bij p <= 0,05 gedeeld door
  het aantal portefeuilles, in een afgerond kwartaal, EN nog een keer in een later kwartaal.
* Kas levert 0% op.
"""

from __future__ import annotations

import copy
import json
from datetime import date

import numpy as np
import pytest

from bot import config as conf
from bot import database, meting, rapport, statistiek
from bot.ai import kostenprognose
from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN, werkdagen

ECHT = conf.laad()
DAGEN = werkdagen(270, start="2026-11-23")  # ruim een jaar aan werkdagen vanaf de start
MOMENTEN = ["2027-02-23", "2027-05-23", "2027-08-23", "2027-11-23"]


def _vul(con, reeksen: dict[str, list[float]], belegd: dict[str, float] | None = None, dagen=DAGEN) -> None:
    """Waarden per dag voor elke portefeuille, uit dagrendementen (het eerste telt niet: dat is de startdag)."""
    for naam, rendementen in reeksen.items():
        nieuwe_portefeuille(naam, naam, INSTELLINGEN, gestart_op=dagen[0]).bewaar(con)
        deel = (belegd or {}).get(naam, 1.0)
        w = 1000.0
        for i, d in enumerate(dagen):
            if i:
                w *= 1.0 + rendementen[i]
            con.execute(
                "INSERT INTO waarde (portefeuille, datum, kas_usd, posities_usd, totaal_usd) VALUES (?,?,?,?,?)",
                (naam, d, w * (1 - deel), w * deel, w))
    con.commit()


MARKT = [0.0005] * len(DAGEN)


def _plus(verschil: list[float]) -> list[float]:
    return [m + v for m, v in zip(MARKT, verschil)]


def _vast(per_dag: float, tot_en_met: str | None = None, vanaf: str | None = None) -> list[float]:
    """Een vast voordeel per dag met een klein wisselend deel (anders is de spreiding precies nul)."""
    return [
        (per_dag + (0.0001 if i % 2 else -0.0001))
        if (tot_en_met is None or d <= tot_en_met) and (vanaf is None or d > vanaf) else 0.0
        for i, d in enumerate(DAGEN)
    ]


def _stand(con, laatste: str, pf="ai_rangorde", meetlat="gelijk_ai"):
    return meting.hoofdvraag_stand(con, ECHT, pf, meetlat, laatste, DAGEN)


# --------------------------------------------------------- de instellingen


def test_de_meting_duurt_twaalf_maanden_met_vier_momenten():
    m = ECHT["meting"]
    assert (str(m["start"]), str(m["einde"])) == ("2026-11-23", "2027-11-23")
    momenten = meting.momenten(ECHT)
    assert [x["datum"] for x in momenten] == MOMENTEN
    assert momenten[-1]["datum"] == str(m["einde"]), "het laatste meetmoment is de einddatum"
    grenzen = [x["grens_p"] for x in momenten]
    assert grenzen == sorted(grenzen) and grenzen[0] < 0.001 and 0.04 < grenzen[-1] < 0.05
    assert meting.periodes(ECHT) == [
        ("2026-11-23", "2027-02-23"), ("2027-02-23", "2027-05-23"),
        ("2027-05-23", "2027-08-23"), ("2027-08-23", "2027-11-23")]


def test_de_grenzen_zijn_obrien_fleming():
    """z-grens per moment = C x wortel(4 / k), met C = 1,733 voor vier momenten en eenzijdig 5%."""
    from statistics import NormalDist

    verwacht = [1 - NormalDist().cdf(1.733 * (4 / k) ** 0.5) for k in (1, 2, 3, 4)]
    for moment, p in zip(meting.momenten(ECHT), verwacht):
        assert moment["grens_p"] == pytest.approx(p, rel=0.03)


def test_zonder_meetmomenten_geldt_alleen_de_einddatum():
    cfg = {"meting": {"start": "2026-11-23", "einde": "2027-02-23"}}
    assert meting.momenten(cfg) == [{"datum": "2027-02-23", "grens_p": 0.05}]
    assert meting.momenten({}) == [] and meting.periodes({}) == []


def test_samen_ongeveer_vijf_procent_vals_alarm():
    """Zonder echt verschil: hoe vaak zegt de echte toets op een van de vier momenten toch 'ja'?"""
    aantallen = []
    for moment in meting.momenten(ECHT):
        cfg = copy.deepcopy(ECHT)
        cfg["meting"]["einde"] = moment["datum"]
        aantallen.append(kostenprognose.beursdagen_in_meting(cfg))
    assert aantallen == [62, 124, 187, 252]
    grenzen = [m["grens_p"] for m in meting.momenten(ECHT)]

    rng = np.random.default_rng(7)
    n, raak, raak_zonder = 3000, 0, 0
    for _ in range(n):
        x = rng.normal(0.0, 0.008, aantallen[-1])
        p = [statistiek.blokgemiddelden_p_waarde(list(x[:k - 1])) for k in aantallen]
        positief = [x[:k - 1].mean() > 0 for k in aantallen]
        raak += any(pos and pw <= g for pos, pw, g in zip(positief, p, grenzen))
        raak_zonder += any(pos and pw <= 0.05 for pos, pw in zip(positief, p))
    assert 0.035 < raak / n < 0.075, "met de strengere grenzen: rond de 5%"
    assert raak_zonder / n > 0.09, "vier keer kijken met 0,05 geeft veel meer vals alarm; daarom de strengere grenzen"


# ------------------------------------------------------------- de hoofdvraag


def test_voor_het_eerste_moment_is_er_nog_niets_te_zeggen(con):
    _vul(con, {"gelijk_ai": MARKT, "ai_rangorde": _plus(_vast(0.002))})
    s = _stand(con, "2027-02-22")
    assert s["label"] == "nog niet te zeggen" and s["volgende"] == "2027-02-23"
    assert [m["bereikt"] for m in s["momenten"]] == [False] * 4
    assert all(m["p_waarde"] is None and m["gehaald"] is None for m in s["momenten"])
    # Terwijl de tussenstand al heel overtuigend is: er wordt niet vooruitgelopen.
    assert rapport.vergelijk_even_belegd(con, "ai_rangorde", "gelijk_ai", "2027-02-22", DAGEN).p_waarde < 0.0001


def test_overtuigend_voordeel_beslist_op_het_eerste_moment_en_blijft_staan(con):
    """Daarna zakt de AI in, maar het antwoord verandert niet meer: elk moment rekent tot zijn eigen datum."""
    _vul(con, {"gelijk_ai": MARKT, "ai_rangorde": _plus([a + b for a, b in zip(
        _vast(0.002, tot_en_met="2027-02-23"), _vast(-0.004, vanaf="2027-02-23"))])})
    s = _stand(con, "2027-02-23")
    assert s["label"] == "ja" and s["volgende"] is None
    assert s["momenten"][0]["gehaald"] and s["momenten"][0]["beslissend"]
    assert s["momenten"][0]["p_waarde"] <= 0.00026

    later = _stand(con, DAGEN[-1])
    assert later["label"] == "ja"
    assert [m["beslissend"] for m in later["momenten"]] == [True, False, False, False]
    assert later["momenten"][0]["verschil_pp"] == pytest.approx(s["momenten"][0]["verschil_pp"])
    assert later["momenten"][3]["verschil_pp"] < 0 and not later["momenten"][3]["gehaald"]


def test_onder_de_gewone_grens_maar_niet_onder_de_strenge_is_nog_geen_ja(con):
    """Op het eerste moment p onder 0,05 maar boven 0,00026: nog niets. Op het tweede moment wel."""
    rng = np.random.default_rng(9)
    _vul(con, {"gelijk_ai": MARKT, "ai_rangorde": _plus(list(rng.normal(0.0012, 0.004, len(DAGEN))))})
    eerste = _stand(con, "2027-03-01")
    m1 = eerste["momenten"][0]
    assert 0.00026 < m1["p_waarde"] <= 0.05 and m1["verschil_pp"] > 0, "de aanname van deze test"
    assert m1["bereikt"] and not m1["gehaald"]
    assert eerste["label"] == "nog niet te zeggen" and eerste["volgende"] == "2027-05-23"

    tweede = _stand(con, "2027-05-24")  # 23 mei is een zondag: bereikt zodra de koers van maandag er is
    m2 = tweede["momenten"][1]
    assert m2["p_waarde"] <= 0.0071 and m2["gehaald"] and m2["beslissend"]
    assert tweede["label"] == "ja"
    assert not _stand(con, "2027-05-21")["momenten"][1]["bereikt"]


def test_geen_voordeel_na_vier_momenten_is_nee(con):
    rng = np.random.default_rng(11)
    _vul(con, {"gelijk_ai": MARKT, "ai_rangorde": _plus(list(rng.normal(0.0, 0.004, len(DAGEN))))})
    tussen = _stand(con, "2027-09-01")
    assert tussen["label"] == "nog niet te zeggen" and tussen["volgende"] == "2027-11-23"
    eind = _stand(con, "2027-11-23")
    assert [m["bereikt"] for m in eind["momenten"]] == [True] * 4
    assert not any(m["gehaald"] for m in eind["momenten"])
    assert eind["label"] == "nee" and eind["volgende"] is None


def test_overtuigend_slechter_is_geen_ja(con):
    """De toets is eenzijdig: een heel lage p-waarde telt alleen bij een hoger rendement."""
    _vul(con, {"gelijk_ai": MARKT, "ai_rangorde": _plus(_vast(-0.002))})
    eind = _stand(con, "2027-11-23")
    assert eind["label"] == "nee" and all(m["verschil_pp"] < 0 for m in eind["momenten"])


def test_zonder_gegevens_geen_uitslag(con):
    s = _stand(con, "")
    assert s["label"] == "nog niet te zeggen" and s["volgende"] == "2027-02-23"


def test_kas_levert_nul_procent_op(con):
    """Een portefeuille die half in kas zit en verder de meetlat volgt: geen verschil met 'even belegd'."""
    half = [0.5 * r for r in MARKT]
    _vul(con, {"gelijk_ai": MARKT, "ai_rangorde": half}, belegd={"ai_rangorde": 0.5})
    _, r_pf, r_eb = rapport.even_belegd_reeksen(con, "ai_rangorde", "gelijk_ai", DAGEN[60], DAGEN)
    assert r_eb == pytest.approx([0.5 * 0.0005] * 60), "de helft in de meetlat, de andere helft tegen 0%"
    assert rapport.vergelijk_even_belegd(con, "ai_rangorde", "gelijk_ai", DAGEN[60], DAGEN).verschil_pp == pytest.approx(0, abs=1e-9)


# ------------------------------------------------ de verkennende portefeuilles


def _verkennend(con, tot: str):
    return meting.verkennend(con, ECHT, tot, DAGEN, "ai_rangorde", {"ai_rangorde", "ai_analist"})


@pytest.fixture
def veld(con):
    """Drie verkennende portefeuilles naast de hoofdvraag en de meetlatten."""
    _vul(con, {
        "gelijk_alle": MARKT,
        "gelijk_ai": [r * 2 for r in MARKT],
        "ai_rangorde": _plus(_vast(0.002)),                           # de hoofdvraag: telt hier niet mee
        "momentum": _plus(_vast(0.002, tot_en_met="2027-05-23")),     # twee kwartalen overtuigend beter
        "trend": _plus(_vast(0.002, tot_en_met="2027-02-23")),        # alleen het eerste kwartaal
        "ai_analist": [r * 2 for r in MARKT],                         # precies zijn meetlat (gelijk_ai)
        "gestopt": _plus(_vast(0.002)),
    })
    con.execute("UPDATE portefeuilles SET actief = 0 WHERE naam = 'gestopt'")
    con.commit()
    return con


def test_de_grens_is_005_gedeeld_door_het_aantal(veld):
    v = _verkennend(veld, "2027-01-15")
    assert v["aantal"] == 3 and v["grens_p"] == pytest.approx(0.05 / 3)
    assert set(v["portefeuilles"]) == {"momentum", "trend", "ai_analist"}, "geen meetlatten, niet de hoofdvraag, niet gestopt"
    assert v["portefeuilles"]["momentum"]["meetlat"] == "gelijk_alle"
    assert v["portefeuilles"]["ai_analist"]["meetlat"] == "gelijk_ai"


def test_een_lopend_kwartaal_telt_nog_niet(veld):
    v = _verkennend(veld, "2027-02-22")
    p = v["portefeuilles"]["momentum"]["periodes"]
    assert len(p) == 1 and p[0]["gehaald"] and not p[0]["af"]
    assert v["portefeuilles"]["momentum"]["status"] == ""


def test_een_keer_is_kandidaat_twee_keer_is_winnaar(veld):
    na_een = _verkennend(veld, "2027-02-23")["portefeuilles"]
    assert na_een["momentum"]["status"] == "kandidaat" and na_een["trend"]["status"] == "kandidaat"
    assert na_een["ai_analist"]["status"] == ""

    na_twee = _verkennend(veld, "2027-05-24")["portefeuilles"]
    assert na_twee["momentum"]["status"] == "winnaar", "herhaald in een nieuw kwartaal"
    assert na_twee["trend"]["status"] == "kandidaat", "een keer gehaald is geen winnaar"
    # Het tweede kwartaal is apart gemeten: het voordeel uit het eerste telt daar niet mee.
    assert [p["gehaald"] for p in na_twee["trend"]["periodes"][:2]] == [True, False]
    assert na_twee["trend"]["periodes"][1]["verschil_pp"] == pytest.approx(0.0, abs=1e-6)
    assert [(p["van"], p["tot"]) for p in na_twee["momentum"]["periodes"]] == meting.periodes(ECHT)[:3]


def test_voor_de_start_zijn_er_geen_periodes(veld):
    v = _verkennend(veld, "2026-11-20")
    assert all(p["periodes"] == [] and p["status"] == "" for p in v["portefeuilles"].values())


def test_met_veel_portefeuilles_is_005_niet_genoeg(con):
    """p onder 0,05 maar boven 0,05 / N: geen kandidaat. Dat is het hele punt van de regel."""
    rng = np.random.default_rng(9)
    reeksen = {"gelijk_alle": MARKT, "matig": _plus(list(rng.normal(0.0012, 0.004, len(DAGEN))))}
    reeksen.update({f"vulling{i}": MARKT for i in range(9)})
    _vul(con, reeksen)
    v = meting.verkennend(con, ECHT, "2027-02-23", DAGEN, "ai_rangorde", set())
    p = v["portefeuilles"]["matig"]["periodes"][0]
    assert v["aantal"] == 10 and v["grens_p"] == pytest.approx(0.005)
    assert 0.005 < p["p_waarde"] <= 0.05 and not p["gehaald"]
    assert v["portefeuilles"]["matig"]["status"] == ""


def test_de_tekst_voor_het_weekrapport(veld):
    tekst = meting.verkennend_tekst(_verkennend(veld, "2027-05-24"))
    assert "3 portefeuilles naast de hoofdvraag" in tekst and "0,05 gedeeld door 3" in tekst
    assert "Winnaars: momentum." in tekst and "Kandidaten (een keer gehaald): trend." in tekst
    assert meting.verkennend_tekst({"aantal": 0, "grens_p": None, "portefeuilles": {}}) == ""


# ------------------------------------------------------------ het dashboard


def test_het_dashboard_toont_de_meetmomenten_en_de_spelregels(tmp_path, veld, cfg_min):
    from bot import dashboard

    for d in DAGEN:
        database.bewaar_koersen(veld, [{"symbool": "SPY", "datum": d, "open": 1.0, "hoog": 1.0, "laag": 1.0,
                                        "slot": 1.0, "volume": 1.0, "valuta": "USD", "bron": "test"}])
    veld.commit()
    cfg = copy.deepcopy(cfg_min)
    cfg["watchlist"] = {"aandelen": [], "etfs": ["SPY"], "crypto": []}
    uit = tmp_path / "site"
    dashboard.exporteer(uit, veld, veld, None, True, vandaag=date(2027, 12, 20), cfg=cfg)
    ov = json.loads((uit / "data/echt/overzicht.json").read_text(encoding="utf-8"))

    h = ov["hoofdvraag"]
    assert h["ai"] == "ai_rangorde" and h["bron"] == "ai_analist" and h["meetlat"] == "gelijk_ai"
    assert "rangorde" in h["vraag"]
    assert h["label"] == "ja" and h["volgend_moment"] is None and h["tot"] == "2027-11-23"
    assert [m["datum"] for m in h["momenten"]] == MOMENTEN
    assert [m["beslissend"] for m in h["momenten"]] == [True, False, False, False]
    assert h["alle_verschil_pp"] is not None, "tegen gelijk_alle als extra cijfer"

    assert ov["spelregels"] == {"aantal": 3, "grens_p": pytest.approx(0.05 / 3, abs=1e-5)}
    per = {p["naam"]: p for p in ov["portefeuilles"]}
    assert per["ai_rangorde"]["hoofdvraag"] is True and "status" not in per["ai_rangorde"]
    assert per["momentum"]["status"] == "winnaar" and per["momentum"]["hoofdvraag"] is False
    assert per["trend"]["status"] == "kandidaat" and per["ai_analist"]["status"] == ""
    assert "status" not in per["gelijk_alle"]


def test_het_dashboard_voor_het_eerste_meetmoment(tmp_path, veld, cfg_min):
    from bot import dashboard

    for d in DAGEN[:40]:
        database.bewaar_koersen(veld, [{"symbool": "SPY", "datum": d, "open": 1.0, "hoog": 1.0, "laag": 1.0,
                                        "slot": 1.0, "volume": 1.0, "valuta": "USD", "bron": "test"}])
    veld.execute("DELETE FROM waarde WHERE datum > ?", (DAGEN[39],))
    veld.commit()
    cfg = copy.deepcopy(cfg_min)
    cfg["watchlist"] = {"aandelen": [], "etfs": ["SPY"], "crypto": []}
    uit = tmp_path / "site"
    vandaag = date.fromisoformat(DAGEN[39])
    dashboard.exporteer(uit, veld, veld, None, True, vandaag=vandaag, cfg=cfg)
    h = json.loads((uit / "data/echt/overzicht.json").read_text(encoding="utf-8"))["hoofdvraag"]
    assert h["label"] == "nog niet te zeggen" and h["volgend_moment"] == "2027-02-23"
    assert h["dagen_tot_moment"] == (date(2027, 2, 23) - vandaag).days
    assert h["dagen_tot_einde"] == (date(2027, 11, 23) - vandaag).days
    assert all(not m["bereikt"] for m in h["momenten"])


# ------------------------------------------------- credits per kwartaal


def _echt_met_kosten(con, per_symbool_dag=0.02):
    cfg = copy.deepcopy(ECHT)
    cfg["modus"] = "echt"
    cfg["ai_watchlist"] = ["AAA", "BBB"]
    for s in cfg["ai_watchlist"]:
        con.execute(
            """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kosten_usd, nieuws_tot, gemaakt_op)
               VALUES ('ai_analist', ?, '2027-02-01', 'NIETS', ?, 'x', 'x')""", (s, per_symbool_dag))
    con.commit()
    return cfg


def test_herinnering_twee_weken_voor_elk_meetmoment_behalve_het_laatste(con):
    cfg = _echt_met_kosten(con)
    assert meting.credits_herinnering(con, cfg, date(2027, 2, 8)) is None, "15 dagen ervoor: nog niet"
    tekst = meting.credits_herinnering(con, cfg, date(2027, 2, 9))
    assert "dinsdag 23 februari 2027" in tekst and "automatisch opwaarderen uit" in tekst
    assert "voor een kwartaal" in tekst and "met 20% marge" in tekst
    # Een keer per meetmoment, ook als de ronde de dagen erna weer draait.
    assert meting.credits_herinnering(con, cfg, date(2027, 2, 10)) is None
    assert meting.credits_herinnering(con, cfg, date(2027, 2, 23)) is None
    # De volgende kwartalen krijgen hun eigen herinnering; het laatste meetmoment niet (daarna stopt de AI).
    assert "23 mei 2027" in meting.credits_herinnering(con, cfg, date(2027, 5, 12))
    assert "23 augustus 2027" in meting.credits_herinnering(con, cfg, date(2027, 8, 20))
    assert meting.credits_herinnering(con, cfg, date(2027, 11, 15)) is None


def test_herinnering_komt_ook_als_de_eerste_dag_gemist_is(con):
    cfg = _echt_met_kosten(con)
    assert meting.credits_herinnering(con, cfg, date(2027, 2, 15)) is not None


def test_geen_kwartaalherinnering_in_de_proefweek(con):
    cfg = _echt_met_kosten(con)
    cfg["modus"] = "proefweek"
    assert meting.credits_herinnering(con, cfg, date(2027, 2, 9)) is None


def test_herinnering_zonder_gemeten_kosten_verzint_geen_bedrag(con):
    cfg = copy.deepcopy(ECHT)
    cfg["modus"] = "echt"
    tekst = meting.credits_herinnering(con, cfg, date(2027, 2, 9))
    assert "nog geen gemeten kosten" in tekst and "$" not in tekst
