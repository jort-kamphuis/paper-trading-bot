"""Tests voor de AI-analist, zonder sleutel, zonder internet en zonder kosten.

Alles draait met een nep-client die vaste antwoorden geeft. Zo is te testen dat
de keten klopt: het budget, de harde controles, het nieuws zonder vooruitkijken
en het niet twee keer betalen voor dezelfde dag.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from bot import config as conf
from bot.ai.budget import Budget, BudgetOp, bereken_kosten
from bot.ai.client import NepClient
from bot.ai.ronde import ai_portefeuilles, denk_na
from bot.data import nieuws as mod_nieuws
from bot.data.koersen import eerste_onaffe_dag_aandelen, eerste_onaffe_dag_crypto
from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN, maak_markt, werkdagen

ECHT = conf.laad()


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
    markt = maak_markt({"AAA": [100.0 + i * 0.1 for i in range(130)]}, datums)
    return markt, datums[-1]


def _maak_pf(con, naam="ai_analist", kas=None, positie=None):
    pf = nieuwe_portefeuille(naam, naam, INSTELLINGEN, gestart_op="2025-01-01")
    if kas is not None:
        pf.kas_eur = kas
    if positie:
        pf.koop(positie, "2025-01-02", "2025-01-01", 100.0, "USD", 1.10)
    pf.bewaar(con)
    con.commit()
    return pf


KOOP_ZEKER = {
    "technisch": {"samenvatting": "stijgt", "signaal": "positief", "zekerheid": 0.8},
    "nieuws": {"samenvatting": "goed nieuws", "signaal": "positief", "zekerheid": 0.7},
    "bull": {"argument": "a", "sterkste_punt": "b", "overtuiging": 0.8},
    "bear": {"argument": "c", "sterkste_punt": "d", "overtuiging": 0.3},
    "risk": {
        "actie": "KOOP", "onderbouwing": "duidelijk sterk",
        "zekerheid": 0.75, "belangrijkste_risico": "kan omdraaien",
    },
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
    budget.controleer()  # nog niets uitgegeven: mag
    budget.boek("risk", bereken_kosten(cfg_ai["prijzen"], "claude-sonnet-5-5", 10_000, 1_000))
    assert budget.is_op()
    with pytest.raises(BudgetOp):
        budget.controleer()


def test_budget_telt_per_maand(con, cfg_ai):
    oud = Budget(con, cfg_ai, vandaag="2026-10-31")
    oud.boek("risk", bereken_kosten(cfg_ai["prijzen"], "claude-sonnet-5-5", 1_000_000, 0))
    nieuw = Budget(con, cfg_ai, vandaag="2026-11-01")
    assert nieuw.uitgegeven() == 0.0, "de kosten van vorige maand tellen niet mee"


# --------------------------------------------------------------- de beslissing


def test_volledige_keten_zet_een_order_klaar(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), KOOP_ZEKER)

    uitkomst = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)

    assert [c["rol"] for c in client.aanroepen] == ["technisch", "nieuws", "bull", "bear", "risk"]
    assert len(uitkomst.orders) == 1
    order = con.execute("SELECT * FROM wachtrij").fetchone()
    assert order["actie"] == "KOOP" and order["symbool"] == "AAA"
    assert order["besluit_datum"] == datum
    assert order["zekerheid"] == pytest.approx(0.75)

    besluit = con.execute("SELECT * FROM ai_besluiten").fetchone()
    assert besluit["actie"] == "KOOP"
    stappen = json.loads(besluit["stappen"])
    assert set(stappen) == {"technisch", "nieuws", "bull", "bear", "risk"}
    # Alle vijf de aanroepen zijn geboekt.
    assert con.execute("SELECT COUNT(*) n FROM ai_kosten").fetchone()["n"] == 5


def test_lage_zekerheid_betekent_niets_doen(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    antwoorden = json.loads(json.dumps(KOOP_ZEKER))
    antwoorden["risk"]["zekerheid"] = 0.4
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), antwoorden)

    uitkomst = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    assert uitkomst.orders == []
    assert con.execute("SELECT COUNT(*) n FROM wachtrij").fetchone()["n"] == 0
    besluit = con.execute("SELECT * FROM ai_besluiten").fetchone()
    assert besluit["actie"] == "NIETS"
    assert "onder de grens" in besluit["onderbouwing"]


def test_kopen_wat_je_al_hebt_wordt_tegengehouden(con, cfg_ai, markt_en_datum):
    """De bot controleert zelf; een instructie aan het model is geen garantie."""
    markt, datum = markt_en_datum
    _maak_pf(con, positie="AAA")
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), KOOP_ZEKER)

    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    besluit = con.execute("SELECT * FROM ai_besluiten").fetchone()
    assert besluit["actie"] == "NIETS"
    assert "hebben we al" in besluit["onderbouwing"]


def test_verkopen_wat_je_niet_hebt_wordt_tegengehouden(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    antwoorden = json.loads(json.dumps(KOOP_ZEKER))
    antwoorden["risk"]["actie"] = "VERKOOP"
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), antwoorden)

    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    assert con.execute("SELECT actie FROM ai_besluiten").fetchone()["actie"] == "NIETS"


def test_zonder_kas_geen_aankoop(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con, kas=20.0)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), KOOP_ZEKER)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    besluit = con.execute("SELECT * FROM ai_besluiten").fetchone()
    assert besluit["actie"] == "NIETS"
    assert "kas" in besluit["onderbouwing"].lower()


def test_afgekapt_antwoord_wordt_zichtbaar_gemeld(con, cfg_ai, markt_en_datum):
    """Een afgekapt antwoord mag niet stil verdwijnen; je hebt er wel voor betaald."""
    from bot.ai.client import Antwoord

    markt, datum = markt_en_datum
    _maak_pf(con)

    class KaptRiskAf(NepClient):
        def vraag(self, rol, model, systeem, bericht, max_tokens, schema=None,
                  denken="aan", inspanning="low"):
            antwoord = super().vraag(rol, model, systeem, bericht, max_tokens, schema,
                                     denken, inspanning)
            if rol == "risk":
                return Antwoord(rol=rol, model=model, tekst='{"actie": "KO',
                                usd=antwoord.usd, afgekapt=True)
            return antwoord

    client = KaptRiskAf(Budget(con, cfg_ai, vandaag=datum), KOOP_ZEKER)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    besluit = con.execute("SELECT * FROM ai_besluiten").fetchone()
    assert besluit["actie"] == "NIETS"
    assert "afgekapt" in besluit["overgeslagen"]
    assert con.execute("SELECT COUNT(*) n FROM wachtrij").fetchone()["n"] == 0


def test_plafonds_laten_ruimte_voor_het_denkwerk():
    """Rollen met denkwerk hebben een ruim plafond nodig, anders wordt het antwoord afgekapt."""
    ai = ECHT["ai"]
    for rol, instelling in ai["denkwerk"].items():
        if instelling["denken"] == "aan":
            assert ai["max_tokens"][rol] >= 2000, f"{rol}: plafond te krap voor denkwerk"


def test_onzin_antwoord_wordt_niets_doen(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    antwoorden = json.loads(json.dumps(KOOP_ZEKER))
    antwoorden["risk"]["actie"] = "ALLES_KOPEN"
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), antwoorden)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    assert con.execute("SELECT actie FROM ai_besluiten").fetchone()["actie"] == "NIETS"


# --------------------------------------------------------------- de kosten laag


def test_debat_wordt_overgeslagen_als_er_niets_te_zien_is(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    neutraal = {
        "technisch": {"samenvatting": "niets", "signaal": "neutraal", "zekerheid": 0.5},
        "nieuws": {"samenvatting": "niets", "signaal": "neutraal", "zekerheid": 0.5},
    }
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), neutraal)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)

    assert [c["rol"] for c in client.aanroepen] == ["technisch", "nieuws"]
    besluit = con.execute("SELECT * FROM ai_besluiten").fetchone()
    assert besluit["actie"] == "NIETS"
    assert "overgeslagen" in besluit["overgeslagen"]


def test_met_positie_wordt_altijd_gedebatteerd(con, cfg_ai, markt_en_datum):
    """Een positie die we hebben, moet altijd beoordeeld worden, ook als het rustig is."""
    markt, datum = markt_en_datum
    _maak_pf(con, positie="AAA")
    neutraal = {
        "technisch": {"samenvatting": "niets", "signaal": "neutraal", "zekerheid": 0.5},
        "nieuws": {"samenvatting": "niets", "signaal": "neutraal", "zekerheid": 0.5},
        "risk": {"actie": "NIETS", "onderbouwing": "houden", "zekerheid": 0.5,
                 "belangrijkste_risico": "x"},
    }
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), neutraal)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    assert [c["rol"] for c in client.aanroepen] == ["technisch", "nieuws", "bull", "bear", "risk"]


def test_twee_keer_draaien_kost_niet_twee_keer_geld(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    budget = Budget(con, cfg_ai, vandaag=datum)

    eerste = NepClient(budget, KOOP_ZEKER)
    denk_na(con, cfg_ai, eerste, markt, "ai_analist", datum, ["AAA"], stil=True)
    kosten_na_een = budget.uitgegeven()

    tweede = NepClient(budget, KOOP_ZEKER)
    uitkomst = denk_na(con, cfg_ai, tweede, markt, "ai_analist", datum, ["AAA"], stil=True)

    assert tweede.aanroepen == [], "de API is opnieuw aangeroepen voor een dag die al beoordeeld was"
    assert uitkomst.al_gedaan == 1
    assert budget.uitgegeven() == pytest.approx(kosten_na_een)
    assert con.execute("SELECT COUNT(*) n FROM wachtrij").fetchone()["n"] == 1


def test_budget_op_halverwege_bewaart_wat_er_al_is_uitgegeven(con, cfg_ai):
    datums = werkdagen(130)
    markt = maak_markt(
        {s: [100.0 + i * 0.1 for i in range(130)] for s in ("AAA", "BBB", "CCC")}, datums
    )
    datum = datums[-1]
    _maak_pf(con)
    # Ruimte voor ongeveer een symbool.
    cfg_ai["ai"]["budget"]["max_usd_per_maand"] = 0.004
    budget = Budget(con, cfg_ai, vandaag=datum)
    client = NepClient(budget, KOOP_ZEKER)

    uitkomst = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA", "BBB", "CCC"], stil=True)

    assert uitkomst.gestopt is not None and "budget" in uitkomst.gestopt.lower()
    geboekt = con.execute("SELECT COUNT(*) n FROM ai_kosten").fetchone()["n"]
    assert geboekt == len(client.aanroepen), "uitgegeven geld moet geboekt blijven"
    assert con.execute("SELECT COUNT(*) n FROM ai_besluiten").fetchone()["n"] < 3


def test_controleportefeuille_gebruikt_overal_het_goedkope_model(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    namen = ai_portefeuilles(cfg_ai)
    controle = [n for n in namen if n[1] is not None]
    assert controle, "de controleportefeuille staat aan in config.yaml"
    naam, model = controle[0]

    _maak_pf(con, naam=naam)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), KOOP_ZEKER)
    denk_na(con, cfg_ai, client, markt, naam, datum, ["AAA"], model_override=model, stil=True)
    assert {c["model"] for c in client.aanroepen} == {model}


def test_hoofdportefeuille_gebruikt_de_modellen_per_rol(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), KOOP_ZEKER)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    per_rol = {c["rol"]: c["model"] for c in client.aanroepen}
    assert per_rol == cfg_ai["ai"]["rollen"]


# ------------------------------------------------------- nieuws zonder vooruitkijken


def _kop(titel: str, gepubliceerd: str) -> dict:
    return {
        "symbool": "AAA", "datum": gepubliceerd[:10], "gepubliceerd": gepubliceerd,
        "titel": titel, "samenvatting": None, "bron": "test", "url": None,
        "opgehaald_op": "2026-11-13T23:00:00+00:00",
    }


def test_nieuws_van_na_het_beslismoment_blijft_buiten_beeld(con):
    mod_nieuws.bewaar(
        con,
        [
            _kop("oud nieuws", "2026-11-12T15:00:00+00:00"),
            _kop("op tijd", "2026-11-13T20:00:00+00:00"),
            _kop("UIT DE TOEKOMST", "2026-11-14T09:00:00+00:00"),
        ],
    )
    koppen = mod_nieuws.koppen_voor(con, "AAA", voor_moment="2026-11-13T23:00:00+00:00")
    titels = [k["titel"] for k in koppen]
    assert "op tijd" in titels
    assert "oud nieuws" in titels
    assert "UIT DE TOEKOMST" not in titels


def test_te_oud_nieuws_blijft_buiten_beeld(con):
    mod_nieuws.bewaar(con, [_kop("vorige week", "2026-11-05T12:00:00+00:00")])
    koppen = mod_nieuws.koppen_voor(
        con, "AAA", voor_moment="2026-11-13T23:00:00+00:00", max_dagen_oud=3
    )
    assert koppen == []


def test_de_nieuwsanalist_krijgt_geen_toekomstig_nieuws_te_zien(con, cfg_ai, markt_en_datum):
    markt, datum = markt_en_datum
    _maak_pf(con)
    moment = f"{datum}T23:00:00+00:00"
    mod_nieuws.bewaar(
        con,
        [
            {**_kop("gewoon nieuws", f"{datum}T12:00:00+00:00")},
            {**_kop("GEHEIM UIT DE TOEKOMST", "2099-01-01T00:00:00+00:00")},
        ],
    )
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), KOOP_ZEKER)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], nieuws_tot=moment, stil=True)

    nieuwsvraag = next(c for c in client.aanroepen if c["rol"] == "nieuws")
    assert "gewoon nieuws" in nieuwsvraag["bericht"]
    assert "GEHEIM UIT DE TOEKOMST" not in nieuwsvraag["bericht"]

    gezien = json.loads(con.execute("SELECT koppen FROM ai_besluiten").fetchone()["koppen"])
    assert [k["titel"] for k in gezien] == ["gewoon nieuws"], "de opgeslagen koppen moeten kloppen"


def test_crypto_ziet_geen_nieuws_van_na_middernacht():
    """De order gaat af op de opening van 00:00 UTC; later nieuws kon die koers niet raken."""
    from bot.ai.ronde import nieuws_grens

    # De bot draait om 00:47 UTC (GitHub start vaak wat later dan gepland).
    nu = datetime(2026, 11, 14, 0, 47, tzinfo=timezone.utc)
    assert nieuws_grens("BTC/USDT", "2026-11-13", nu=nu) == "2026-11-14T00:00:00+00:00"


def test_aandelen_mogen_nieuws_tot_het_draaimoment_zien():
    from bot.ai.ronde import nieuws_grens

    nu = datetime(2026, 11, 14, 0, 47, tzinfo=timezone.utc)
    # De beurs opent pas om 13:30 UTC of later, dus alles tot nu is op tijd.
    assert nieuws_grens("AAPL", "2026-11-13", nu=nu) == nu.isoformat()


def test_aandelen_nooit_nieuws_van_na_de_opening():
    """Zou de bot pas middags draaien, dan telt nieuws van na de opening niet mee."""
    from bot.ai.ronde import nieuws_grens

    laat = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)
    assert nieuws_grens("AAPL", "2026-11-13", nu=laat) == "2026-11-14T13:30:00+00:00"


def test_de_grens_wordt_per_besluit_opgeslagen(con, cfg_ai):
    datums = werkdagen(130)
    markt = maak_markt({"BTC/USDT": [100.0 + i for i in range(130)]}, datums)
    datum = datums[-1]
    _maak_pf(con)
    mod_nieuws.bewaar(
        con,
        [
            {**_kop("voor middernacht", f"{datum}T22:00:00+00:00"), "symbool": "BTC/USDT"},
            {**_kop("na middernacht", f"{datum}T23:59:59+00:00"), "symbool": "BTC/USDT"},
        ],
    )
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), KOOP_ZEKER)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["BTC/USDT"], stil=True)
    rij = con.execute("SELECT nieuws_tot FROM ai_besluiten").fetchone()
    from datetime import date as _d, timedelta as _td

    volgende = (_d.fromisoformat(datum) + _td(days=1)).isoformat()
    assert rij["nieuws_tot"] == f"{volgende}T00:00:00+00:00"


def test_de_backtest_krijgt_nooit_een_ai_portefeuille(con, cfg_ai, vaste_wisselkoers):
    from bot.opzet import bouw_motor

    datums = werkdagen(10)
    markt = maak_markt({"AAA": [100.0] * 10, "SPY": [100.0] * 10}, datums)
    motor = bouw_motor(con, cfg_ai, markt, vaste_wisselkoers, begin=datums[0])
    namen = [d.portefeuille.naam for d in motor.deelnemers]
    assert not any(n.startswith("ai_") for n in namen), f"AI in de backtest: {namen}"


# ------------------------------------------------------------- de prognose


def _besluit_rij(con, pf, symbool, datum, kosten, overgeslagen=None, stappen=None):
    con.execute(
        """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kosten_usd,
                                     overgeslagen, stappen, nieuws_tot, gemaakt_op)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (pf, symbool, datum, "NIETS", kosten, overgeslagen,
         json.dumps(stappen or {}), datum, datum),
    )
    con.execute(
        """INSERT INTO ai_kosten (datum, maand, rol, model, tokens_in, tokens_uit, kosten_usd)
           VALUES (?,?,?,?,?,?,?)""",
        (datum, datum[:7], "risk", "m", 0, 0, kosten),
    )


def test_prognose_rekent_aandelen_en_crypto_apart(con, cfg_ai):
    from bot.ai.kostenprognose import BEURSDAGEN_PER_MAAND, CRYPTODAGEN_PER_MAAND, maak_prognose

    cfg_ai["ai_watchlist"] = ["AAA", "BTC/USDT"]
    for dag in ("2026-11-12", "2026-11-13", "2026-11-14"):
        _besluit_rij(con, "ai_analist", "AAA", dag, 0.010)
        _besluit_rij(con, "ai_analist", "BTC/USDT", dag, 0.020)
    con.commit()

    p = maak_prognose(con, cfg_ai)
    verwacht = 0.010 * BEURSDAGEN_PER_MAAND + 0.020 * CRYPTODAGEN_PER_MAAND
    assert p.rondes == 3
    assert p.maand_usd == pytest.approx(verwacht)


def test_dagen_zonder_koers_tellen_niet_mee_in_het_gemiddelde(con, cfg_ai):
    """Een aandeel in het weekend kost niets; dat mag het gemiddelde niet omlaag trekken."""
    from bot.ai.kostenprognose import GEEN_KOERS, maak_prognose

    cfg_ai["ai_watchlist"] = ["AAA"]
    _besluit_rij(con, "ai_analist", "AAA", "2026-11-13", 0.010)
    _besluit_rij(con, "ai_analist", "AAA", "2026-11-14", 0.0, overgeslagen=GEEN_KOERS)
    con.commit()
    p = maak_prognose(con, cfg_ai)
    from bot.ai.kostenprognose import BEURSDAGEN_PER_MAAND

    assert p.maand_usd == pytest.approx(0.010 * BEURSDAGEN_PER_MAAND)


def test_past_niet_geeft_voorstellen_en_verandert_niets(con, cfg_ai):
    from bot.ai.kostenprognose import als_tekst, maak_prognose

    cfg_ai["ai_watchlist"] = ["AAA", "BBB", "CCC"]
    sterk = {"technisch": {"signaal": "positief", "zekerheid": 0.9}, "nieuws": {"signaal": "neutraal"}}
    zwak = {"technisch": {"signaal": "positief", "zekerheid": 0.4}, "nieuws": {"signaal": "neutraal"}}
    for dag in ("2026-11-11", "2026-11-12", "2026-11-13"):
        _besluit_rij(con, "ai_analist", "AAA", dag, 0.20, stappen=sterk)
        _besluit_rij(con, "ai_analist", "BBB", dag, 0.20, stappen=zwak)
        _besluit_rij(con, "ai_analist", "CCC", dag, 0.20, stappen=zwak)
        _besluit_rij(con, "ai_controle_haiku", "AAA", dag, 0.01, stappen=zwak)
    con.commit()
    voor = json.dumps(cfg_ai, sort_keys=True)

    p = maak_prognose(con, cfg_ai)
    assert p.past is False
    tekst = " ".join(p.voorstellen)
    assert "Controleportefeuille" in tekst
    assert "Minder symbolen" in tekst
    assert "sterk signaal" in tekst
    assert "NIET" in als_tekst(p)
    assert json.dumps(cfg_ai, sort_keys=True) == voor, "de prognose mag niets aanpassen"


def test_prognose_zonder_metingen(con, cfg_ai):
    from bot.ai.kostenprognose import maak_prognose

    p = maak_prognose(con, cfg_ai)
    assert p.maand_usd is None and p.past is None


# ------------------------------------------------------------ onaffe koersdagen


def test_aandelendag_is_pas_na_sluiting_af():
    # 15:00 in New York (20:00 UTC in november): de beurs is nog open.
    tijdens = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)
    assert eerste_onaffe_dag_aandelen(tijdens) == "2026-11-13"
    # 18:00 in New York (23:00 UTC): de dag is af.
    na = datetime(2026, 11, 13, 23, 0, tzinfo=timezone.utc)
    assert eerste_onaffe_dag_aandelen(na) == "2026-11-14"


def test_cryptodag_is_pas_om_middernacht_utc_af():
    middag = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    assert eerste_onaffe_dag_crypto(middag) == "2026-11-13"
