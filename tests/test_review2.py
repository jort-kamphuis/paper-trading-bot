"""Tests voor de tweede review (november 2026): wat er vóór de start van de meting is veranderd."""

from __future__ import annotations

import json
from datetime import date

import pytest

from bot import config as conf
from bot import database, rapport, volledigheid
from bot.ai import beslisregel, kalibratie
from bot.ai.analist import Beoordeling
from bot.ai.budget import Budget, bereken_kosten
from bot.ai.client import NepClient, TegoedOp, is_tegoed_fout
from bot.ai.ronde import denk_na
from bot.markt import Markt
from bot.motor import Motor
from bot.portefeuille import nieuwe_portefeuille
from bot.strategieen.basis import Order, Strategie
from bot.strategieen.benchmarks import GelijkVerdeeld
from tests.conftest import INSTELLINGEN, alle_dagen, maak_df, maak_markt, werkdagen

ECHT = conf.laad()


# --------------------------------------------------------- de beurskalender


def _cfg_kalender():
    return {"watchlist": {"aandelen": ["AAPL"], "etfs": ["SPY"], "crypto": ["BTC/USDT"]},
            "rapport": {"kalender_symbool": "SPY"}, "beurskalender": ECHT["beurskalender"]}


def test_weekdag_zonder_spy_koers_blokkeert_de_handel():
    # di 24 en wo 25 nov 2026; SPY mist woensdag, crypto heeft hem wel.
    markt = Markt({"SPY": maak_df([1.0], ["2026-11-24"]), "AAPL": maak_df([1.0], ["2026-11-24"]),
                   "BTC/USDT": maak_df([1.0, 1.0], ["2026-11-24", "2026-11-25"])})
    problemen = volledigheid.controleer(markt, _cfg_kalender(), ["2026-11-24", "2026-11-25"])
    assert any("SPY: geen koers op 2026-11-25" in p for p in problemen)


def test_feestdag_en_weekend_zijn_geen_probleem():
    # Thanksgiving 26 nov 2026 en zaterdag 28 nov: de beurs is dicht.
    markt = Markt({"SPY": maak_df([1.0], ["2026-11-25"]), "AAPL": maak_df([1.0], ["2026-11-25"]),
                   "BTC/USDT": maak_df([1.0] * 4, ["2026-11-25", "2026-11-26", "2026-11-27", "2026-11-28"])})
    problemen = volledigheid.controleer(markt, _cfg_kalender(), ["2026-11-26", "2026-11-28"])
    assert problemen == []


def test_jaar_zonder_kalender_wordt_niet_gecontroleerd():
    assert volledigheid.beurs_open_volgens_kalender(_cfg_kalender(), "2030-01-02") is False
    assert volledigheid.beurs_open_volgens_kalender(_cfg_kalender(), "2026-11-25") is True
    assert volledigheid.beurs_open_volgens_kalender(_cfg_kalender(), "2026-11-26") is False


# ------------------------------------------------ een aandeel dat verdwijnt


class KoopEenmaal(Strategie):
    naam = "koper"

    def __init__(self, dag):
        super().__init__({})
        self.koopdag = dag

    def besluit(self, datum, markt, symbolen, posities):
        return [Order(symbool="AAA", actie="KOOP", reden="test")] if datum == self.koopdag and not posities else []


def test_verdwenen_aandeel_blokkeert_niet_en_wordt_verkocht(con, cfg_min, vaste_wisselkoers):
    dagen = werkdagen(12)
    aaa = maak_df([100.0] * 6, dagen[:6])  # na dag 6 geen koers meer
    markt = Markt({"SPY": maak_df([400.0] * 12, dagen), "AAA": aaa})
    cfg = dict(cfg_min)
    cfg["watchlist"] = {"aandelen": ["AAA"], "etfs": ["SPY"], "crypto": []}
    motor = Motor(con, cfg, markt, vaste_wisselkoers)
    motor.voeg_toe(KoopEenmaal(dagen[0]), ["AAA"], gestart_op=dagen[0])
    motor.draai_periode(dagen[0], dagen[-1])
    pf = motor.deelnemers[0].portefeuille
    assert "AAA" not in pf.posities
    verkoop = con.execute("SELECT * FROM trades WHERE actie='VERKOOP'").fetchone()
    assert verkoop["datum"] == dagen[8], "op de derde beursdag zonder koers"
    assert "geen koers meer sinds" in verkoop["reden"]
    # Twee dagen zonder koers blokkeert nog wel, drie niet meer.
    assert volledigheid.controleer(markt, cfg, [dagen[7]]) != []
    assert volledigheid.controleer(markt, cfg, [dagen[8]]) == []


# ------------------------------------------------- gelijk_alle echt gelijk


def test_gelijk_alle_is_echt_gelijk_verdeeld(con, cfg_min, vaste_wisselkoers):
    dagen = werkdagen(4)
    symbolen = [f"S{i}" for i in range(46)]
    markt = maak_markt({s: [10.0 + i] * 4 for i, s in enumerate(symbolen)}, dagen)
    motor = Motor(con, cfg_min, markt, vaste_wisselkoers)
    motor.voeg_toe(GelijkVerdeeld("gelijk_alle", symbolen), symbolen, gestart_op=dagen[0])
    motor.draai_periode(dagen[0], dagen[-1])
    bedragen = [r["bedrag_usd"] for r in con.execute("SELECT bedrag_usd FROM trades")]
    assert len(bedragen) == 46
    assert max(bedragen) - min(bedragen) < 0.01, "de laatste symbolen krijgen niet minder"


# ---------------------------------------------------------- de AI-ronde


def test_rangorde_op_kans_niet_op_verwachting():
    regels = ECHT["ai"]["beslisregel"]
    b = {"SPY": Beoordeling("SPY", "2026-11-23", "kas", kans=0.56, verwacht_pct=5.0),
         "AAPL": Beoordeling("AAPL", "2026-11-23", "SPY", kans=0.70, verwacht_pct=0.8)}
    u = beslisregel.kies(b, {}, set(), set(), "2026-11-23", regels, 1)
    assert [o.symbool for o in u.orders] == ["AAPL"], "SPY gaat niet voor door de marktstijging in zijn verwachting"


@pytest.fixture
def cfg_ai(cfg_min):
    cfg = dict(cfg_min)
    cfg["ai"] = json.loads(json.dumps(ECHT["ai"]))
    cfg["prijzen"] = dict(ECHT["prijzen"])
    return cfg


GOED = {
    "nieuws": {"samenvatting": "rustig", "belangrijkste_risico": "geen", "gebeurtenis_binnenkort": False},
    "bull": {"argument": "a", "sterkste_punt": "b", "overtuiging": 0.5},
    "bear": {"argument": "c", "sterkste_punt": "d", "overtuiging": 0.5},
    "risk": {"kans": 0.7, "verwacht_extra_rendement_pct": 2.0, "onderbouwing": "x", "belangrijkste_risico": "y"},
}


def _markt_ai():
    datums = werkdagen(130)
    return maak_markt({s: [100.0 + i * 0.1 for i in range(130)] for s in ("AAA", "BBB", "SPY")}, datums), datums[-1]


def _pf(con):
    nieuwe_portefeuille("ai_analist", "ai_analist", INSTELLINGEN, gestart_op="2025-01-01").bewaar(con)
    con.commit()


def test_een_onbruikbare_beoordeling_betekent_geen_aankopen(con, cfg_ai):
    from bot.ai.client import Antwoord

    markt, datum = _markt_ai()
    _pf(con)

    class RiskKaptBijBBB(NepClient):
        def vraag(self, rol, model, systeem, bericht, max_tokens, schema=None, denken="aan", inspanning="low"):
            a = super().vraag(rol, model, systeem, bericht, max_tokens, schema, denken, inspanning)
            if rol == "risk" and "Symbool: BBB" in bericht:
                return Antwoord(rol=rol, model=model, tekst="{", usd=a.usd, afgekapt=True)
            return a

    uit = denk_na(con, cfg_ai, RiskKaptBijBBB(Budget(con, cfg_ai, vandaag=datum), GOED), markt,
                  "ai_analist", datum, ["AAA", "BBB"], stil=True)
    assert uit.onbruikbaar == ["BBB"] and not uit.compleet
    assert uit.orders == [], "AAA zag er goed uit, maar de nacht was niet compleet"


def test_mislukte_bull_geen_scheve_afweging(con, cfg_ai):
    from bot.ai.client import Antwoord

    markt, datum = _markt_ai()
    _pf(con)

    class BullWeigert(NepClient):
        def vraag(self, rol, model, systeem, bericht, max_tokens, schema=None, denken="aan", inspanning="low"):
            a = super().vraag(rol, model, systeem, bericht, max_tokens, schema, denken, inspanning)
            return Antwoord(rol=rol, model=model, tekst="", usd=a.usd, geweigerd=True) if rol == "bull" else a

    client = BullWeigert(Budget(con, cfg_ai, vandaag=datum), GOED)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    assert "risk" not in [c["rol"] for c in client.aanroepen], "geen risk manager die maar een kant hoort"
    rij = con.execute("SELECT kans, overgeslagen FROM ai_besluiten").fetchone()
    assert rij["kans"] is None and "bull weigerde" in rij["overgeslagen"]


def test_de_kansvraag_gaat_niet_over_de_eigen_portefeuille(con, cfg_ai):
    markt, datum = _markt_ai()
    _pf(con)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), GOED)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    for c in client.aanroepen:
        assert "Positie:" not in c["bericht"] and "Portefeuille:" not in c["bericht"], c["rol"]


def test_tegoed_op_stopt_netjes_en_wordt_gemeld(con, cfg_ai):
    markt, datum = _markt_ai()
    _pf(con)

    class GeenTegoed(NepClient):
        def vraag(self, *a, **k):
            raise TegoedOp("Your credit balance is too low to access the Anthropic API.")

    uit = denk_na(con, cfg_ai, GeenTegoed(Budget(con, cfg_ai, vandaag=datum), GOED), markt,
                  "ai_analist", datum, ["AAA", "BBB"], stil=True)
    assert uit.tegoed_op and "tegoed" in uit.gestopt and uit.orders == []


def test_tegoedfout_herkennen():
    assert is_tegoed_fout(Exception("Error code: 400 - Your credit balance is too low to access the API"))
    assert not is_tegoed_fout(Exception("Error code: 529 - overloaded"))


def test_het_teruggemelde_model_wordt_bewaard(con, cfg_ai):
    b = Budget(con, cfg_ai, vandaag="2026-11-23")
    b.boek("risk", bereken_kosten(cfg_ai["prijzen"], "claude-sonnet-5-5", 100, 10),
           model_antwoord="claude-sonnet-5-5", verzoek_id="req_123")
    rij = con.execute("SELECT model, model_antwoord, verzoek_id FROM ai_kosten").fetchone()
    assert tuple(rij) == ("claude-sonnet-5-5", "claude-sonnet-5-5", "req_123")


# ------------------------------------------------- de kansen scoren


def test_scoren_vanaf_de_opening_met_dividend():
    dagen = alle_dagen(20, start="2026-11-01")
    slot = [100.0] * 20
    open_ = [100.0] * 20
    open_[1] = 110.0  # de koers springt bij de opening na de beslisdag (nieuws van na het slot)
    slot = [100.0] + [110.0] * 19
    dividend = [0.0] * 20
    dividend[5] = 2.0
    markt = Markt({"AAA": maak_df(slot, dagen, open_=open_, dividend=dividend)})
    u = kalibratie.uitkomst(markt, "AAA", "kas", "2026-11-01", 14)
    assert u == pytest.approx(100.0 * ((110.0 + 2.0) / 110.0 - 1.0)), "de sprong bij de opening telt niet als voorspelling"


def test_basiskans_bij_de_scores(con, cfg_min):
    cfg = dict(cfg_min)
    cfg["ai"] = ECHT["ai"]
    dagen = alle_dagen(30, start="2026-11-01")
    markt = maak_markt({"AAA": [100.0] + [110.0] * 29, "SPY": [100.0] * 30}, dagen)
    for i, d in enumerate(("2026-11-01", "2026-11-02")):
        con.execute(
            """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kans, referentie, vraag,
                                         horizon_dagen, nieuws_tot, gemaakt_op)
               VALUES ('ai_analist', 'AAA', ?, 'NIETS', 0.6, 'SPY', 'relatief', 14, 'x', 'x')""", (d,))
    con.commit()
    kalibratie.scoor(con, markt, cfg)
    s = kalibratie.scores(con)[0]
    # 1 nov: vanaf de opening van 2 nov (110) geen winst meer; 2 nov: ook niet. Basiskans 0.
    assert s.kwam_uit_pct == 0.0 and s.brier_basiskans == 0.0


# ---------------------------------------------- de hoofdvraag, even belegd


def _waarde(con, pf, d, totaal, posities):
    con.execute("INSERT INTO waarde (portefeuille, datum, kas_usd, posities_usd, totaal_usd) VALUES (?,?,?,?,?)",
                (pf, d, totaal - posities, posities, totaal))


def test_half_belegd_en_verder_de_markt_volgen_is_geen_verschil(con):
    dagen = werkdagen(40)
    m = a = 1000.0
    for i, d in enumerate(dagen):
        r = 0.01 if i % 3 else -0.005
        if i:
            m *= 1 + r
            a *= 1 + 0.5 * r
        _waarde(con, "gelijk_alle", d, m, m)
        _waarde(con, "ai_analist", d, a, a * 0.5)
    con.commit()
    eb = rapport.vergelijk_even_belegd(con, "ai_analist", "gelijk_alle", dagen[-1], dagen)
    ruw = rapport.vergelijk(con, "ai_analist", "gelijk_alle", dagen[-1], dagen)
    assert eb.verschil_pp == pytest.approx(0.0, abs=1e-9), "even belegd: geen verschil, want geen keuzekunst"
    assert ruw.verschil_pp < -1.0, "ruw: het lijkt slechter, alleen door de kas"


def test_hoofdvraag_rekent_niet_verder_dan_de_einddatum(tmp_path, con, cfg_min):
    from bot import dashboard

    dagen = werkdagen(30, start="2027-02-01")
    m = a = 1000.0
    for i, d in enumerate(dagen):
        database.bewaar_koersen(con, [{"symbool": "SPY", "datum": d, "open": 1.0, "hoog": 1.0, "laag": 1.0,
                                       "slot": 1.0, "volume": 1.0, "valuta": "USD", "bron": "test"}])
        if i:
            m *= 1.001
            a *= 1.002
        _waarde(con, "gelijk_alle", d, m, m)
        _waarde(con, "ai_analist", d, a, a)
    for naam in ("gelijk_alle", "ai_analist"):
        nieuwe_portefeuille(naam, naam, INSTELLINGEN, gestart_op=dagen[0]).bewaar(con)
    con.commit()
    cfg = dict(cfg_min)
    cfg["meting"] = {"start": dagen[0], "einde": "2027-02-23", "ai_portefeuille": "ai_analist"}
    cfg["watchlist"] = {"aandelen": [], "etfs": ["SPY"], "crypto": []}
    uit = tmp_path / "site"
    dashboard.exporteer(uit, con, con, None, True, vandaag=date(2027, 3, 10), cfg=cfg)
    h = json.loads((uit / "data/echt/overzicht.json").read_text(encoding="utf-8"))["hoofdvraag"]
    assert h["tot"] == "2027-02-23", "nooit verder dan de einddatum"
    assert h["label"] in ("ja", "nee")


def test_kwartaalcijfers_met_de_beslisdag_als_datum(con, monkeypatch):
    from bot.data import kwartaalcijfers

    monkeypatch.setattr(kwartaalcijfers, "haal_datum", lambda s, v: "2026-12-01")
    kwartaalcijfers.vernieuw(con, ["AAPL"], vandaag=date(2026, 11, 23))
    assert kwartaalcijfers.bekend_op(con, "AAPL", "2026-11-23") == "2026-12-01", "de AI ziet het op de beslisdag"


def test_omschakelen_zet_de_vaste_startdag(tmp_path):
    from bot import omschakelen

    echt = database.verbind(tmp_path / "t.db")
    omschakelen.voer_uit(echt, None, {"proefweek": {"tot_en_met": "2026-11-23"}}, date(2026, 11, 25))
    assert database.lees_instelling(echt, "start_datum") == ECHT["meting"]["start"] == "2026-11-23"
    echt.close()


# ------------------------------------- de credits voor de hele meting


def _proefkosten(con, pf, symbool, datum, usd):
    con.execute(
        """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kosten_usd, nieuws_tot, gemaakt_op)
           VALUES (?, ?, ?, 'NIETS', ?, 'x', 'x')""", (pf, symbool, datum, usd))


def test_beursdagen_in_de_meting():
    from bot.ai import kostenprognose

    # 23 november 2026 tot en met 23 februari 2027, zonder weekenden en NYSE-feestdagen.
    assert kostenprognose.beursdagen_in_meting(ECHT) == 62


def test_bedrag_voor_de_hele_meting_uit_gemeten_kosten(con):
    from bot.ai import kostenprognose

    cfg = json.loads(json.dumps(ECHT))
    cfg["ai_watchlist"] = [f"S{i}" for i in range(15)]  # de gekozen lijst
    for dag in ("2026-11-13", "2026-11-16"):
        for s in ("AAA", "BBB"):
            _proefkosten(con, "proef_ai_mix", s, dag, 0.020)
            _proefkosten(con, "proef_ai_haiku", s, dag, 0.001)
            _proefkosten(con, "proef_ai_enkel", s, dag, 0.006)
            _proefkosten(con, "proef_ai_opus", s, dag, 0.900)  # telt niet mee: geen deel van de echte meting
    con.execute("INSERT INTO ai_kosten (datum, maand, rol, model, tokens_in, tokens_uit, kosten_usd) "
                "VALUES ('2026-11-15', '2026-11', 'rapport', 'm', 0, 0, 0.10)")
    con.commit()
    b = kostenprognose.bedrag_hele_meting(con, cfg)
    assert b.symbolen == 15 and b.beursdagen == 62 and b.rondes == 2
    assert b.per_portefeuille["ai_analist"] == pytest.approx(0.020 * 15 * 62)
    assert b.rapporten_usd == pytest.approx(0.10 * 13)
    assert b.totaal_usd == pytest.approx((0.020 + 0.001 + 0.006) * 15 * 62 + 1.30)
    tekst = kostenprognose.bedrag_als_tekst(b, cfg, marge_pct=20)
    assert "15 symbolen" in tekst and "62 beursdagen" in tekst
    assert "Totaal: $26,41. Met 20% marge: $31,69." in tekst
    # Voor een ander aantal symbolen (het proefweekverslag).
    assert kostenprognose.bedrag_hele_meting(con, cfg, symbolen=46).per_portefeuille["ai_analist"] == pytest.approx(0.020 * 46 * 62)


def test_zonder_metingen_geen_verzonnen_bedrag(con):
    from bot.ai import kostenprognose

    b = kostenprognose.bedrag_hele_meting(con, ECHT)
    assert b.totaal_usd is None
    assert "nog niet te berekenen" in kostenprognose.bedrag_als_tekst(b, ECHT)


def test_waarschuwing_als_het_niet_in_het_maandbudget_past(con):
    from bot.ai import kostenprognose

    for s in ("AAA", "BBB"):
        _proefkosten(con, "proef_ai_mix", s, "2026-11-13", 0.05)
    con.commit()
    tekst = kostenprognose.bedrag_als_tekst(kostenprognose.bedrag_hele_meting(con, ECHT), ECHT)
    assert "LET OP: dat past niet" in tekst
    assert "nog geen gemeten kosten voor ai_controle_haiku, ai_enkel" in tekst
