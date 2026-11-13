"""Tests voor de AI-analist, zonder sleutel, zonder internet en zonder kosten.

Alles draait met een nep-client die vaste antwoorden geeft. Zo is te testen dat
de keten klopt: de kenmerken uit de code, de kans per symbool, de beslisregel,
het budget, het nieuws zonder vooruitkijken en niet twee keer betalen.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from bot import config as conf
from bot.ai import beslisregel
from bot.ai.analist import Beoordeling
from bot.ai.budget import Budget, BudgetOp, bereken_kosten
from bot.ai.client import NepClient
from bot.ai.kenmerken import tabel
from bot.ai.ronde import ai_portefeuilles, denk_na
from bot.data import nieuws as mod_nieuws
from bot.data.koersen import eerste_onaffe_dag_aandelen, eerste_onaffe_dag_crypto
from bot.portefeuille import Positie, nieuwe_portefeuille
from tests.conftest import INSTELLINGEN, maak_markt, werkdagen

ECHT = conf.laad()
REGELS = ECHT["ai"]["beslisregel"]


@pytest.fixture
def cfg_ai(cfg_min):
    cfg = dict(cfg_min)
    cfg["ai"] = json.loads(json.dumps(ECHT["ai"]))
    cfg["prijzen"] = dict(ECHT["prijzen"])
    cfg["ai_watchlist"] = ["AAA"]
    cfg["watchlist"] = {"aandelen": ["AAA", "SPY"], "etfs": [], "crypto": []}
    return cfg


@pytest.fixture
def markt_en_datum():
    datums = werkdagen(130)
    markt = maak_markt(
        {"AAA": [100.0 + i * 0.1 for i in range(130)], "SPY": [400.0 + i * 0.2 for i in range(130)]},
        datums,
    )
    return markt, datums[-1]


def _maak_pf(con, naam="ai_analist", kas=None, positie=None, gekocht_op="2025-01-02"):
    pf = nieuwe_portefeuille(naam, naam, INSTELLINGEN, gestart_op="2025-01-01")
    if kas is not None:
        pf.kas_usd = kas
    if positie:
        pf.koop(positie, gekocht_op, gekocht_op, 100.0, "USD", 1.10)
    pf.bewaar(con)
    con.commit()
    return pf


def _antwoorden(kans=0.62, verwacht=1.5):
    return {
        "nieuws": {"samenvatting": "rustig", "belangrijkste_risico": "geen", "gebeurtenis_binnenkort": False},
        "bull": {"argument": "a", "sterkste_punt": "b"},
        "bear": {"argument": "c", "sterkste_punt": "d"},
        "risk": {"kans": kans, "verwacht_extra_rendement_pct": verwacht,
                 "onderbouwing": "sterker dan SPY", "belangrijkste_risico": "kan omdraaien"},
        "enkel": {"kans": kans, "verwacht_extra_rendement_pct": verwacht,
                  "onderbouwing": "alleen", "belangrijkste_risico": "x"},
    }


# --------------------------------------------------------------------- kosten


def test_kosten_worden_goed_berekend():
    prijzen = {"m": {"invoer": 2.0, "uitvoer": 10.0}}
    k = bereken_kosten(prijzen, "m", 1_000_000, 100_000)
    assert k.usd == pytest.approx(2.0 + 1.0)


def test_onbekend_model_rekent_met_de_duurste_prijs():
    prijzen = {"goedkoop": {"invoer": 0.1, "uitvoer": 0.5}, "duur": {"invoer": 4.0, "uitvoer": 20.0}}
    k = bereken_kosten(prijzen, "bestaat-niet", 1_000_000, 0)
    assert k.usd == pytest.approx(4.0), "liever te voorzichtig dan een budget dat overloopt"


def test_budget_stopt_bij_het_maximum(con, cfg_ai):
    cfg_ai["ai"]["budget"]["max_usd_per_maand"] = 0.01
    budget = Budget(con, cfg_ai, vandaag="2026-11-13")
    budget.controleer()
    budget.boek("risk", bereken_kosten(cfg_ai["prijzen"], "claude-sonnet-5-5", 10_000, 1_000))
    assert budget.is_op()
    with pytest.raises(BudgetOp):
        budget.controleer()


def test_budget_telt_per_maand(con, cfg_ai):
    oud = Budget(con, cfg_ai, vandaag="2026-10-31")
    oud.boek("risk", bereken_kosten(cfg_ai["prijzen"], "claude-sonnet-5-5", 1_000_000, 0))
    assert Budget(con, cfg_ai, vandaag="2026-11-01").uitgegeven() == 0.0


def test_plafonds_laten_ruimte_voor_het_denkwerk():
    ai = ECHT["ai"]
    for rol, instelling in ai["denkwerk"].items():
        if instelling["denken"] == "aan":
            assert ai["max_tokens"][rol] >= 2000, f"{rol}: plafond te krap voor denkwerk"


def test_de_vastgelegde_drempels():
    """Vastgelegd voor de start van de meting; een wijziging moet opvallen."""
    assert REGELS["koop_kans"] == 0.55
    assert REGELS["koop_verwacht_pct"] == 0.5
    assert REGELS["verkoop_kans_onder"] == 0.50
    assert REGELS["houdtijd_kalenderdagen"] == 14
    assert REGELS["horizon_kalenderdagen"] == 14


# ---------------------------------------------------------------- kenmerken


def test_kenmerken_komen_uit_de_code_en_kijken_niet_vooruit():
    datums = werkdagen(300)
    reeks = [100.0 + (i % 17) - 0.02 * i for i in range(300)]
    spy = [400.0 + 0.1 * i for i in range(300)]
    vol = maak_markt({"AAA": reeks, "SPY": spy}, datums)
    kort = maak_markt({"AAA": reeks[:201], "SPY": spy[:201]}, datums[:201])
    a = tabel(vol, "AAA", datums[200])
    b = tabel(kort, "AAA", datums[200])
    assert a == b, "latere koersen veranderen de tabel van een eerdere dag"
    assert a["referentie"] == "SPY"
    assert a["rendement_28d_min_SPY_pct"] is not None


def test_crypto_wordt_vergeleken_met_bitcoin():
    datums = werkdagen(40)
    markt = maak_markt({"ETH/USDT": [10.0 + i for i in range(40)], "BTC/USDT": [100.0] * 40}, datums)
    assert tabel(markt, "ETH/USDT", datums[-1])["referentie"] == "BTC/USDT"


# ---------------------------------------------------------- de keten (debat)


def test_debat_vraagt_vier_rollen_en_slaat_de_kans_op(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), _antwoorden())
    uitkomst = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)

    assert [c["rol"] for c in client.aanroepen] == ["nieuws", "bull", "bear", "risk"]
    rij = con.execute("SELECT * FROM ai_besluiten").fetchone()
    assert rij["kans"] == pytest.approx(0.62)
    assert rij["verwacht_pct"] == pytest.approx(1.5)
    assert rij["referentie"] == "SPY" and rij["horizon_dagen"] == 14
    assert "kenmerken" in json.loads(rij["stappen"])
    assert rij["actie"] == "KOOP"
    assert [o.symbool for o in uitkomst.orders] == ["AAA"]
    order = con.execute("SELECT * FROM wachtrij").fetchone()
    assert order["actie"] == "KOOP" and order["besluit_datum"] == datum
    assert con.execute("SELECT COUNT(*) n FROM ai_kosten").fetchone()["n"] == 4


def test_de_kenmerken_staan_in_het_bericht(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), _antwoorden())
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    risk = next(c for c in client.aanroepen if c["rol"] == "risk")
    assert "rsi14" in risk["bericht"] or "RSI" in risk["bericht"]


def test_enkel_is_een_aanroep(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con, naam="ai_enkel")
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), _antwoorden())
    denk_na(con, cfg_ai, client, markt, "ai_enkel", datum, ["AAA"], soort="enkel", stil=True)
    assert [c["rol"] for c in client.aanroepen] == ["enkel"]
    assert con.execute("SELECT kans FROM ai_besluiten").fetchone()["kans"] == pytest.approx(0.62)


@pytest.mark.parametrize("kans,verwacht", [(0.54, 2.0), (0.70, 0.4)])
def test_onder_een_van_de_drempels_geen_koop_maar_wel_een_kans(con, cfg_ai, markt_en_datum, kans, verwacht):
    markt, datum = markt_en_datum
    _maak_pf(con)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), _antwoorden(kans, verwacht))
    uitkomst = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    assert uitkomst.orders == []
    rij = con.execute("SELECT * FROM ai_besluiten").fetchone()
    assert rij["actie"] == "NIETS" and rij["kans"] == pytest.approx(kans), "de kans telt ook zonder koop"


def test_kans_buiten_nul_tot_een_is_onbruikbaar(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), _antwoorden(kans=1.7))
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    rij = con.execute("SELECT * FROM ai_besluiten").fetchone()
    assert rij["actie"] == "NIETS" and rij["kans"] is None
    assert "buiten 0 tot 1" in rij["overgeslagen"]


def test_afgekapt_antwoord_wordt_zichtbaar_gemeld(con, cfg_ai, markt_en_datum):
    from bot.ai.client import Antwoord

    markt, datum = markt_en_datum
    _maak_pf(con)

    class KaptRiskAf(NepClient):
        def vraag(self, rol, model, systeem, bericht, max_tokens, schema=None,
                  denken="aan", inspanning="low"):
            antwoord = super().vraag(rol, model, systeem, bericht, max_tokens, schema, denken, inspanning)
            if rol == "risk":
                return Antwoord(rol=rol, model=model, tekst='{"kans": 0.', usd=antwoord.usd, afgekapt=True)
            return antwoord

    client = KaptRiskAf(Budget(con, cfg_ai, vandaag=datum), _antwoorden())
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    rij = con.execute("SELECT * FROM ai_besluiten").fetchone()
    assert rij["actie"] == "NIETS"
    assert "afgekapt" in rij["overgeslagen"]
    assert con.execute("SELECT COUNT(*) n FROM wachtrij").fetchone()["n"] == 0


def test_twee_keer_draaien_kost_niet_twee_keer_geld(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    budget = Budget(con, cfg_ai, vandaag=datum)
    denk_na(con, cfg_ai, NepClient(budget, _antwoorden()), markt, "ai_analist", datum, ["AAA"], stil=True)
    kosten = budget.uitgegeven()
    tweede = NepClient(budget, _antwoorden())
    uitkomst = denk_na(con, cfg_ai, tweede, markt, "ai_analist", datum, ["AAA"], stil=True)
    assert tweede.aanroepen == []
    assert uitkomst.al_gedaan == 1
    assert budget.uitgegeven() == pytest.approx(kosten)
    assert con.execute("SELECT COUNT(*) n FROM wachtrij").fetchone()["n"] == 1


def test_geen_beursdag_geen_ai(con, cfg_ai):
    from bot.markt import Markt
    from tests.conftest import alle_dagen, maak_df

    beurs = werkdagen(130)
    alles = alle_dagen(190)
    markt = Markt({"SPY": maak_df([400.0] * 130, beurs), "BTC/USDT": maak_df([100.0] * 190, alles)})
    zaterdag = next(d for d in alles if d > beurs[100] and d not in beurs)
    _maak_pf(con)
    client = NepClient(Budget(con, cfg_ai, vandaag=zaterdag), _antwoorden())
    uitkomst = denk_na(con, cfg_ai, client, markt, "ai_analist", zaterdag, ["BTC/USDT"], stil=True)
    assert client.aanroepen == [] and uitkomst.overgeslagen


def test_budget_op_halverwege_dan_geen_aankopen(con, cfg_ai):
    """Niet alles beoordeeld: dan niet kopen, anders wint wie vooraan in de lijst staat."""
    datums = werkdagen(130)
    markt = maak_markt({s: [100.0 + i * 0.1 for i in range(130)] for s in ("AAA", "BBB", "CCC", "SPY")}, datums)
    datum = datums[-1]
    _maak_pf(con)
    cfg_ai["ai"]["budget"]["max_usd_per_maand"] = 0.02
    budget = Budget(con, cfg_ai, vandaag=datum)
    client = NepClient(budget, _antwoorden())
    uitkomst = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA", "BBB", "CCC"], stil=True)
    assert uitkomst.gestopt is not None and "budget" in uitkomst.gestopt.lower()
    assert con.execute("SELECT COUNT(*) n FROM ai_kosten").fetchone()["n"] == len(client.aanroepen)
    assert uitkomst.orders == [], "onvolledig beoordeeld: geen aankopen"
    assert con.execute("SELECT COUNT(*) n FROM wachtrij").fetchone()["n"] == 0


def test_de_beste_verwachting_gaat_voor_niet_de_eerste_in_de_lijst(con, cfg_ai):
    datums = werkdagen(130)
    markt = maak_markt({s: [100.0 + i * 0.1 for i in range(130)] for s in ("AAA", "BBB", "SPY")}, datums)
    datum = datums[-1]
    _maak_pf(con, kas=None)

    class PerSymbool(NepClient):
        def vraag(self, rol, model, systeem, bericht, max_tokens, schema=None, denken="aan", inspanning="low"):
            if rol == "risk":
                self.antwoorden["risk"] = dict(self.antwoorden["risk"])
                self.antwoorden["risk"]["verwacht_extra_rendement_pct"] = 3.0 if "BBB" in bericht else 1.0
            return super().vraag(rol, model, systeem, bericht, max_tokens, schema, denken, inspanning)

    cfg_ai["algemeen"] = {**cfg_ai["algemeen"], "max_posities": 1}
    pf = nieuwe_portefeuille("ai_analist", "ai_analist", {**INSTELLINGEN, "max_posities": 1})
    con.execute("DELETE FROM portefeuilles")
    pf.bewaar(con)
    con.commit()
    client = PerSymbool(Budget(con, cfg_ai, vandaag=datum), _antwoorden())
    uitkomst = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA", "BBB"], stil=True)
    assert [o.symbool for o in uitkomst.orders] == ["BBB"]


def test_controleportefeuille_gebruikt_overal_het_goedkope_model(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    controle = [p for p in ai_portefeuilles(cfg_ai) if p["model"]]
    assert controle, "de controleportefeuille staat aan in config.yaml"
    p = controle[0]
    _maak_pf(con, naam=p["naam"])
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), _antwoorden())
    denk_na(con, cfg_ai, client, markt, p["naam"], datum, ["AAA"], model_override=p["model"], stil=True)
    assert {c["model"] for c in client.aanroepen} == {p["model"]}


def test_hoofdportefeuille_gebruikt_de_modellen_per_rol(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), _antwoorden())
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    per_rol = {c["rol"]: c["model"] for c in client.aanroepen}
    assert per_rol == {r: cfg_ai["ai"]["rollen"][r] for r in ("nieuws", "bull", "bear", "risk")}


def test_drie_ai_portefeuilles_in_de_echte_meting(cfg_ai):
    namen = {p["naam"]: p for p in ai_portefeuilles(cfg_ai)}
    assert set(namen) == {"ai_analist", "ai_controle_haiku", "ai_enkel"}
    assert namen["ai_enkel"]["soort"] == "enkel"


def test_portefeuille_met_einddatum_stopt_daarna(cfg_ai):
    from datetime import date

    cfg_ai["ai"]["portefeuilles"].append({"naam": "proef_ai_opus", "soort": "debat", "tot_en_met": "2026-11-19"})
    voor = {p["naam"] for p in ai_portefeuilles(cfg_ai, date(2026, 11, 19))}
    na = {p["naam"] for p in ai_portefeuilles(cfg_ai, date(2026, 11, 20))}
    assert "proef_ai_opus" in voor and "proef_ai_opus" not in na


# ------------------------------------------------------------ de beslisregel


def _b(symbool, kans, verwacht=1.0):
    return Beoordeling(symbool=symbool, datum="2026-11-13", referentie="SPY", kans=kans, verwacht_pct=verwacht)


def _pos(symbool, gekocht_op, bevestigd_op=None):
    return Positie(symbool, 1.0, 100.0, gekocht_op, koopprijs=100.0, bevestigd_op=bevestigd_op)


def test_regel_verkoopt_onder_de_helft():
    u = beslisregel.kies({"AAA": _b("AAA", 0.45)}, {"AAA": _pos("AAA", "2026-11-10")},
                         set(), set(), "2026-11-13", REGELS, 10)
    assert [(o.symbool, o.actie) for o in u.orders] == [("AAA", "VERKOOP")]


def test_regel_houdt_vast_tussen_de_drempels():
    u = beslisregel.kies({"AAA": _b("AAA", 0.52, 0.2)}, {"AAA": _pos("AAA", "2026-11-10")},
                         set(), set(), "2026-11-13", REGELS, 10)
    assert u.orders == []


def test_regel_houdtijd_om_zonder_bevestiging_verkopen():
    u = beslisregel.kies({"AAA": _b("AAA", 0.52, 0.2)}, {"AAA": _pos("AAA", "2026-10-30")},
                         set(), set(), "2026-11-13", REGELS, 10)
    assert [o.actie for o in u.orders] == ["VERKOOP"]


def test_regel_houdtijd_om_maar_opnieuw_sterk_is_herbevestigen():
    u = beslisregel.kies({"AAA": _b("AAA", 0.60, 1.0)}, {"AAA": _pos("AAA", "2026-10-30")},
                         set(), set(), "2026-11-13", REGELS, 10)
    assert u.orders == [] and u.herbevestigd == ["AAA"]


def test_regel_houdtijd_telt_vanaf_de_herbevestiging():
    u = beslisregel.kies({"AAA": _b("AAA", 0.52, 0.2)},
                         {"AAA": _pos("AAA", "2026-10-01", bevestigd_op="2026-11-05")},
                         set(), set(), "2026-11-13", REGELS, 10)
    assert u.orders == []


def test_regel_zonder_beoordeling_na_de_houdtijd_toch_verkopen():
    u = beslisregel.kies({}, {"AAA": _pos("AAA", "2026-10-30")}, set(), set(), "2026-11-13", REGELS, 10)
    assert [o.actie for o in u.orders] == ["VERKOOP"]


def test_regel_wachtende_kopen_tellen_als_bezet():
    b = {s: _b(s, 0.7, 2.0) for s in ("A", "B", "C")}
    u = beslisregel.kies(b, {}, {"X", "Y"}, set(), "2026-11-13", REGELS, 3)
    assert len(u.orders) == 1


def test_regel_niet_kopen_als_het_beoordelen_niet_compleet_was():
    u = beslisregel.kies({"AAA": _b("AAA", 0.8, 3.0)}, {}, set(), set(), "2026-11-13", REGELS, 10,
                         kopen_toegestaan=False)
    assert u.orders == []


# ------------------------------------------------------- nieuws zonder vooruitkijken


def _kop(titel: str, gepubliceerd: str) -> dict:
    return {
        "symbool": "AAA", "datum": gepubliceerd[:10], "gepubliceerd": gepubliceerd,
        "titel": titel, "samenvatting": None, "bron": "test", "url": None,
        "opgehaald_op": "2026-11-13T23:00:00+00:00",
    }


def test_nieuws_van_na_het_beslismoment_blijft_buiten_beeld(con):
    mod_nieuws.bewaar(con, [
        _kop("oud nieuws", "2026-11-12T15:00:00+00:00"),
        _kop("op tijd", "2026-11-13T20:00:00+00:00"),
        _kop("UIT DE TOEKOMST", "2026-11-14T09:00:00+00:00"),
    ])
    titels = [k["titel"] for k in mod_nieuws.koppen_voor(con, "AAA", voor_moment="2026-11-13T23:00:00+00:00")]
    assert "op tijd" in titels and "oud nieuws" in titels
    assert "UIT DE TOEKOMST" not in titels


def test_te_oud_nieuws_blijft_buiten_beeld(con):
    mod_nieuws.bewaar(con, [_kop("vorige week", "2026-11-05T12:00:00+00:00")])
    assert mod_nieuws.koppen_voor(con, "AAA", voor_moment="2026-11-13T23:00:00+00:00", max_dagen_oud=3) == []


def test_de_ai_krijgt_geen_toekomstig_nieuws_te_zien(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    moment = f"{datum}T23:00:00+00:00"
    mod_nieuws.bewaar(con, [
        _kop("gewoon nieuws", f"{datum}T12:00:00+00:00"),
        _kop("GEHEIM UIT DE TOEKOMST", "2099-01-01T00:00:00+00:00"),
    ])
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), _antwoorden())
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], nieuws_tot=moment, stil=True)
    for aanroep in client.aanroepen:
        assert "GEHEIM UIT DE TOEKOMST" not in aanroep["bericht"], aanroep["rol"]
    assert "gewoon nieuws" in next(c for c in client.aanroepen if c["rol"] == "nieuws")["bericht"]
    assert "gewoon nieuws" in next(c for c in client.aanroepen if c["rol"] == "risk")["bericht"], (
        "de dure rollen zien de koppen zelf, niet alleen een samenvatting"
    )
    gezien = json.loads(con.execute("SELECT koppen FROM ai_besluiten").fetchone()["koppen"])
    assert [k["titel"] for k in gezien] == ["gewoon nieuws"]


def test_crypto_ziet_geen_nieuws_van_na_middernacht():
    from bot.ai.ronde import nieuws_grens

    nu = datetime(2026, 11, 14, 0, 47, tzinfo=timezone.utc)
    assert nieuws_grens("BTC/USDT", "2026-11-13", nu=nu) == "2026-11-14T00:00:00+00:00"


def test_aandelen_mogen_nieuws_tot_het_draaimoment_zien():
    from bot.ai.ronde import nieuws_grens

    nu = datetime(2026, 11, 14, 0, 47, tzinfo=timezone.utc)
    assert nieuws_grens("AAPL", "2026-11-13", nu=nu) == nu.isoformat()


def test_aandelen_nooit_nieuws_van_na_de_opening():
    from bot.ai.ronde import nieuws_grens

    laat = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)
    assert nieuws_grens("AAPL", "2026-11-13", nu=laat) == "2026-11-14T13:30:00+00:00"


def test_crypto_nieuws_heet_bij_yahoo_anders():
    assert mod_nieuws.yahoo_symbool("BTC/USDT") == "BTC-USD"
    assert mod_nieuws.yahoo_symbool("AAPL") == "AAPL"


def test_de_backtest_krijgt_nooit_een_ai_portefeuille(con, cfg_ai, vaste_wisselkoers):
    from bot.opzet import bouw_motor

    datums = werkdagen(10)
    markt = maak_markt({"AAA": [100.0] * 10, "SPY": [100.0] * 10}, datums)
    motor = bouw_motor(con, cfg_ai, markt, vaste_wisselkoers, begin=datums[0])
    namen = [d.portefeuille.naam for d in motor.deelnemers]
    assert not any(n.startswith("ai_") for n in namen), f"AI in de backtest: {namen}"


# ------------------------------------------------------------- de prognose


def _besluit_rij(con, pf, symbool, datum, kosten, overgeslagen=None):
    con.execute(
        """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kosten_usd,
                                     overgeslagen, stappen, nieuws_tot, gemaakt_op)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (pf, symbool, datum, "NIETS", kosten, overgeslagen, "{}", datum, datum),
    )
    con.execute(
        """INSERT INTO ai_kosten (datum, maand, rol, model, tokens_in, tokens_uit, kosten_usd)
           VALUES (?,?,?,?,?,?,?)""",
        (datum, datum[:7], "risk", "m", 0, 0, kosten),
    )


def test_prognose_per_symbool_dag_maal_beursdagen(con, cfg_ai):
    from bot.ai.kostenprognose import BEURSDAGEN_PER_MAAND, maak_prognose

    cfg_ai["ai_watchlist"] = ["AAA", "BTC/USDT"]
    for dag in ("2026-11-11", "2026-11-12", "2026-11-13"):
        _besluit_rij(con, "ai_analist", "AAA", dag, 0.010)
        _besluit_rij(con, "ai_analist", "BTC/USDT", dag, 0.020)
    con.commit()
    p = maak_prognose(con, cfg_ai)
    assert p.rondes == 3
    assert p.maand_usd == pytest.approx(0.015 * 2 * BEURSDAGEN_PER_MAAND), "ook crypto alleen op beursdagen"


def test_dagen_zonder_koers_tellen_niet_mee_in_het_gemiddelde(con, cfg_ai):
    from bot.ai.kostenprognose import BEURSDAGEN_PER_MAAND, GEEN_KOERS, maak_prognose

    _besluit_rij(con, "ai_analist", "AAA", "2026-11-13", 0.010)
    _besluit_rij(con, "ai_analist", "AAA", "2026-11-14", 0.0, overgeslagen=GEEN_KOERS)
    con.commit()
    assert maak_prognose(con, cfg_ai).maand_usd == pytest.approx(0.010 * BEURSDAGEN_PER_MAAND)


def test_past_niet_geeft_voorstellen_en_verandert_niets(con, cfg_ai):
    from bot.ai.kostenprognose import als_tekst, maak_prognose

    cfg_ai["ai_watchlist"] = ["AAA", "BBB", "CCC"]
    for dag in ("2026-11-11", "2026-11-12", "2026-11-13"):
        for s in ("AAA", "BBB", "CCC"):
            _besluit_rij(con, "ai_analist", s, dag, 0.60)
            _besluit_rij(con, "ai_controle_haiku", s, dag, 0.01)
            _besluit_rij(con, "ai_enkel", s, dag, 0.05)
    con.commit()
    voor = json.dumps(cfg_ai, sort_keys=True)
    p = maak_prognose(con, cfg_ai)
    assert p.past is False
    tekst = " ".join(p.voorstellen)
    assert "Controleportefeuille" in tekst and "ai_enkel uit" in tekst and "Minder symbolen" in tekst
    assert "NIET" in als_tekst(p)
    assert json.dumps(cfg_ai, sort_keys=True) == voor, "de prognose mag niets aanpassen"


def test_prognose_zonder_metingen(con, cfg_ai):
    from bot.ai.kostenprognose import maak_prognose

    p = maak_prognose(con, cfg_ai)
    assert p.maand_usd is None and p.past is None


def test_correctie_telt_mee_in_het_budget_maar_niet_in_de_prognose(con, cfg_ai):
    from bot import database
    from bot.ai.kostenprognose import maak_prognose

    _besluit_rij(con, "ai_analist", "AAA", "2026-11-13", 0.010)
    con.commit()
    zonder = maak_prognose(con, cfg_ai).maand_usd
    database.boek_correctie(con, "2026-11-13", 0.2339, "proef op een kopie riep de API aan")
    assert maak_prognose(con, cfg_ai).maand_usd == pytest.approx(zonder)
    assert Budget(con, cfg_ai, vandaag="2026-11-13").uitgegeven() == pytest.approx(0.010 + 0.2339)


def test_herstart_wist_de_meting_maar_niet_de_kosten(con, cfg_ai):
    from bot import database

    _besluit_rij(con, "ai_analist", "AAA", "2026-11-13", 0.05)
    con.commit()
    database.leeg_handelsdata(con)
    assert con.execute("SELECT COUNT(*) n FROM ai_besluiten").fetchone()["n"] == 0
    assert con.execute("SELECT COUNT(*) n FROM ai_kosten").fetchone()["n"] == 1, "uitgegeven is uitgegeven"


# ------------------------------------------------------------ onaffe koersdagen


def test_aandelendag_is_pas_na_sluiting_af():
    tijdens = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)
    assert eerste_onaffe_dag_aandelen(tijdens) == "2026-11-13"
    na = datetime(2026, 11, 13, 23, 0, tzinfo=timezone.utc)
    assert eerste_onaffe_dag_aandelen(na) == "2026-11-14"


def test_cryptodag_is_pas_om_middernacht_utc_af():
    middag = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    assert eerste_onaffe_dag_crypto(middag) == "2026-11-13"
