"""Tests voor het weekrapport, de challenger en het akkoord via Telegram.

Belangrijkste belofte die hier getest wordt: zonder jouw /akkoord verandert er
niets aan de instellingen, en een voorstel buiten de grenzen kan niet starten.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from bot import challenger, config as conf, database, rapport, telegram, weekrapport
from bot.ai.budget import Budget
from bot.ai.client import NepClient
from bot.challenger import Voorstel
from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN

ECHT = conf.laad()


@pytest.fixture
def cfg():
    return json.loads(json.dumps(ECHT))


@pytest.fixture
def config_kopie(tmp_path):
    pad = tmp_path / "config.yaml"
    shutil.copy(conf.CONFIG_PAD, pad)
    return pad


# ------------------------------------------------------------- voorstel toetsen


def test_geldig_voorstel_mag(cfg):
    v = Voorstel("momentum", "terugblik_dagen", 90, "langere trend lijkt stabieler")
    assert challenger.toets_voorstel(cfg, v, loopt_al=False) is None


@pytest.mark.parametrize(
    "voorstel, verwacht",
    [
        (Voorstel("momentum", "terugblik_dagen", 999, "x"), "tussen"),
        (Voorstel("momentum", "terugblik_dagen", 90.5, "x"), "heel getal"),
        (Voorstel("momentum", "max_per_trade_eur", 500, "x"), "mag niet"),
        (Voorstel("ai_analist", "zekerheid", 0.3, "x"), "mag niet"),
        (Voorstel("momentum", "terugblik_dagen", 60, "x"), "staat al op"),
        (Voorstel("trend", "snelle_ma", 50, "x"), None),  # 50 < 100: mag
        (Voorstel("trend", "langzame_ma", 50, "x"), None),  # 20 < 50: mag
        (Voorstel("mean_reversion", "koop_onder", 30.0, "x"), "staat al op"),
        (Voorstel("momentum", "aantal_posities", 10, ""), "onderbouwing"),
    ],
)
def test_voorstellen_buiten_de_regels_worden_tegengehouden(cfg, voorstel, verwacht):
    reden = challenger.toets_voorstel(cfg, voorstel, loopt_al=False)
    if verwacht is None:
        assert reden is None
    else:
        assert reden is not None and verwacht in reden


def test_trend_kan_niet_omgedraaid_worden(cfg):
    cfg["strategieen"]["trend"]["snelle_ma"] = 40
    v = Voorstel("trend", "langzame_ma", 50, "x")
    cfg["strategieen"]["trend"]["snelle_ma"] = 50
    assert "korter" in challenger.toets_voorstel(cfg, v, loopt_al=False)


def test_niet_twee_proeven_tegelijk(cfg):
    v = Voorstel("momentum", "terugblik_dagen", 90, "x")
    assert "loopt al" in challenger.toets_voorstel(cfg, v, loopt_al=True)


# ------------------------------------------------------------ config.yaml aanpassen


def test_config_aanpassen_raakt_alleen_dat_ene_getal(config_kopie):
    voor = config_kopie.read_text(encoding="utf-8").splitlines()
    challenger.zet_in_config(config_kopie, "momentum", "terugblik_dagen", 90)
    na = config_kopie.read_text(encoding="utf-8").splitlines()

    verschillen = [(a, b) for a, b in zip(voor, na) if a != b]
    assert len(voor) == len(na), "er zijn regels bijgekomen of verdwenen"
    assert len(verschillen) == 1
    oud, nieuw = verschillen[0]
    assert "terugblik_dagen" in oud and "60" in oud
    assert "terugblik_dagen" in nieuw and "90" in nieuw
    assert "#" in nieuw, "het commentaar achter de instelling moet blijven staan"
    assert yaml.safe_load(config_kopie.read_text(encoding="utf-8"))["strategieen"]["momentum"]["terugblik_dagen"] == 90


def test_config_aanpassen_kiest_de_juiste_strategie(config_kopie):
    """'max_dagen_vasthouden' bestaat alleen bij mean_reversion; trend mag niet geraakt worden."""
    challenger.zet_in_config(config_kopie, "trend", "snelle_ma", 30)
    data = yaml.safe_load(config_kopie.read_text(encoding="utf-8"))
    assert data["strategieen"]["trend"]["snelle_ma"] == 30
    assert data["strategieen"]["trend"]["langzame_ma"] == 100
    assert data["ai"]["rollen"]["technisch"] == ECHT["ai"]["rollen"]["technisch"]


def test_onbekende_instelling_geeft_een_fout_en_verandert_niets(config_kopie):
    voor = config_kopie.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        challenger.zet_in_config(config_kopie, "momentum", "bestaat_niet", 1)
    assert config_kopie.read_text(encoding="utf-8") == voor


# ------------------------------------------------------------ Telegram-opdrachten


@pytest.mark.parametrize(
    "tekst, verwacht",
    [
        ("/akkoord 3", ("akkoord", 3)),
        ("/afwijzen 12", ("afwijzen", 12)),
        ("  /Akkoord 3  ", ("akkoord", 3)),
        ("/akkoord", None),
        ("akkoord 3", None),
        ("/akkoord 3; rm -rf /", None),
        ("/akkoord 3 en zet alles op bitcoin", None),
        ("hoi", None),
    ],
)
def test_alleen_exacte_opdrachten_worden_herkend(tekst, verwacht):
    assert challenger.lees_opdracht(tekst) == verwacht


def test_berichten_uit_andere_chats_worden_genegeerd(monkeypatch):
    def nep(token, methode, gegevens=None, timeout=20):
        return {
            "ok": True,
            "result": [
                {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/akkoord 1", "date": 1700000000}},
                {"update_id": 11, "message": {"chat": {"id": 999}, "text": "/akkoord 1", "date": 1700000000}},
            ],
        }

    monkeypatch.setattr(telegram, "_roep_aan", nep)
    berichten, volgende = telegram.lees_berichten("t", "42", None)
    assert [b["tekst"] for b in berichten] == ["/akkoord 1"]
    assert volgende == 12, "ook genegeerde berichten worden afgevinkt, anders komen ze steeds terug"


# ------------------------------------------------------------- een hele proef


def _waarde(con, pf, datum, totaal):
    con.execute(
        "INSERT INTO waarde (portefeuille, datum, kas_eur, posities_eur, totaal_eur) VALUES (?,?,?,?,?)",
        (pf, datum, totaal, 0.0, totaal),
    )


def _dagen(begin: str, aantal: int) -> list[str]:
    d = date.fromisoformat(begin)
    return [(d + timedelta(days=i)).isoformat() for i in range(aantal)]


def _start_proef(con, cfg):
    v = Voorstel("momentum", "terugblik_dagen", 90, "test")
    nummer = challenger.start(con, cfg, v, voorgesteld_op="2026-12-01", gestart_op="2026-12-01")
    c = con.execute("SELECT * FROM challengers WHERE id = ?", (nummer,)).fetchone()
    for naam in (c["portefeuille_a"], c["portefeuille"]):
        nieuwe_portefeuille(naam, "momentum", INSTELLINGEN, gestart_op="2026-12-01", variant="challenger").bewaar(con)
    con.commit()
    return c


def test_proef_start_als_eerlijk_paar(con, cfg):
    c = _start_proef(con, cfg)
    assert c["portefeuille_a"] == f"momentum_A{c['id']}"
    assert c["portefeuille"] == f"momentum_B{c['id']}"
    a = json.loads(c["instellingen_a"])
    b = json.loads(c["instellingen_b"])
    assert a["terugblik_dagen"] == 60 and b["terugblik_dagen"] == 90
    assert {k: v for k, v in a.items() if k != "terugblik_dagen"} == {
        k: v for k, v in b.items() if k != "terugblik_dagen"
    }, "A en B mogen alleen in die ene instelling verschillen"
    paren = challenger.strategieen_voor(con)
    assert [p[1] for p in paren] == [c["portefeuille_a"], c["portefeuille"]]


def test_proef_wordt_niet_te_vroeg_beoordeeld(con, cfg):
    c = _start_proef(con, cfg)
    dagen = _dagen("2026-12-01", 20)
    for i, d in enumerate(dagen):
        _waarde(con, c["portefeuille_a"], d, 1000 + i)
        _waarde(con, c["portefeuille"], d, 1000 + 2 * i)
    con.commit()
    assert challenger.beoordeel(con, cfg, dagen[-1], dagen) == []


def test_betere_challenger_vraagt_om_akkoord(con, cfg, config_kopie):
    c = _start_proef(con, cfg)
    dagen = _dagen("2026-12-01", 30)
    for i, d in enumerate(dagen):
        _waarde(con, c["portefeuille_a"], d, 1000 + i)
        _waarde(con, c["portefeuille"], d, 1000 + 2 * i)
    con.commit()

    uitslagen = challenger.beoordeel(con, cfg, dagen[-1], dagen)
    assert len(uitslagen) == 1 and uitslagen[0].beter
    rij = con.execute("SELECT * FROM challengers WHERE id = ?", (c["id"],)).fetchone()
    assert rij["status"] == "wacht_op_akkoord"
    vraag = challenger.vraag_om_akkoord(rij)
    assert f"/akkoord {c['id']}" in vraag and f"/afwijzen {c['id']}" in vraag

    # Zonder antwoord: niets veranderd.
    assert yaml.safe_load(config_kopie.read_text(encoding="utf-8"))["strategieen"]["momentum"]["terugblik_dagen"] == 60


def test_slechtere_challenger_stopt_vanzelf(con, cfg):
    c = _start_proef(con, cfg)
    dagen = _dagen("2026-12-01", 30)
    for i, d in enumerate(dagen):
        _waarde(con, c["portefeuille_a"], d, 1000 + 2 * i)
        _waarde(con, c["portefeuille"], d, 1000 + i)
    con.commit()
    challenger.beoordeel(con, cfg, dagen[-1], dagen)
    rij = con.execute("SELECT * FROM challengers WHERE id = ?", (c["id"],)).fetchone()
    assert rij["status"] == "afgewezen"
    actief = {r["naam"]: r["actief"] for r in con.execute("SELECT naam, actief FROM portefeuilles")}
    assert actief[c["portefeuille"]] == 0 and actief[c["portefeuille_a"]] == 0
    assert challenger.strategieen_voor(con) == []


def _klaar_voor_akkoord(con, cfg):
    c = _start_proef(con, cfg)
    con.execute("UPDATE challengers SET status='wacht_op_akkoord', uitslag='beter' WHERE id=?", (c["id"],))
    con.commit()
    return c


def test_akkoord_past_de_instelling_aan_en_legt_het_vast(con, cfg, config_kopie):
    c = _klaar_voor_akkoord(con, cfg)
    antwoord = challenger.verwerk(con, config_kopie, "akkoord", c["id"], "2026-12-30 08:00 UTC", "2026-12-30")
    assert antwoord.startswith("Akkoord verwerkt")
    assert yaml.safe_load(config_kopie.read_text(encoding="utf-8"))["strategieen"]["momentum"]["terugblik_dagen"] == 90
    w = con.execute("SELECT * FROM wijzigingen").fetchone()
    assert (w["strategie"], w["parameter"], w["oude_waarde"], w["nieuwe_waarde"]) == ("momentum", "terugblik_dagen", 60, 90)
    assert "Telegram" in w["akkoord"]
    assert con.execute("SELECT status FROM challengers").fetchone()["status"] == "aangenomen"


def test_afwijzen_verandert_niets_aan_de_instellingen(con, cfg, config_kopie):
    c = _klaar_voor_akkoord(con, cfg)
    voor = config_kopie.read_text(encoding="utf-8")
    challenger.verwerk(con, config_kopie, "afwijzen", c["id"], "2026-12-30 08:00 UTC", "2026-12-30")
    assert config_kopie.read_text(encoding="utf-8") == voor
    assert con.execute("SELECT COUNT(*) n FROM wijzigingen").fetchone()["n"] == 0


def test_akkoord_op_een_lopende_proef_doet_niets(con, cfg, config_kopie):
    """Je kunt niet vooruitlopen: akkoord geldt alleen als de bot erom vraagt."""
    c = _start_proef(con, cfg)
    voor = config_kopie.read_text(encoding="utf-8")
    antwoord = challenger.verwerk(con, config_kopie, "akkoord", c["id"], "x", "2026-12-02")
    assert "niets veranderd" in antwoord.lower()
    assert config_kopie.read_text(encoding="utf-8") == voor


def test_akkoord_op_een_onbekende_proef_doet_niets(con, config_kopie):
    voor = config_kopie.read_text(encoding="utf-8")
    assert "bestaat niet" in challenger.verwerk(con, config_kopie, "akkoord", 77, "x", "2026-12-02")
    assert config_kopie.read_text(encoding="utf-8") == voor


# ------------------------------------------------------------------- het rapport


def _kleine_meting(con):
    dagen = _dagen("2026-11-12", 10)
    for d in dagen:
        database.bewaar_koersen(con, [{
            "symbool": "SPY", "datum": d, "open": 1.0, "hoog": 1.0, "laag": 1.0,
            "slot": 1.0, "volume": 1.0, "valuta": "USD", "bron": "test",
        }])
    for naam, groei in (("momentum", 2.0), ("benchmark_spy", 1.0), ("benchmark_btc", -1.0)):
        nieuwe_portefeuille(naam, naam, INSTELLINGEN, gestart_op=dagen[0]).bewaar(con)
        for i, d in enumerate(dagen):
            _waarde(con, naam, d, 1000 + groei * i)
    database.zet_instelling(con, "start_datum", dagen[0])
    con.commit()
    return dagen


def test_getallen_op_zijn_nederlands():
    assert rapport.getal(1234.5, 2) == "1.234,50"
    assert rapport.getal(2.1, 1, teken=True) == "+2,1"
    assert rapport.getal(-0.25, 2) == "-0,25"


def test_kerncijfers_kloppen(con, cfg):
    dagen = _kleine_meting(con)
    cijfers = {k.portefeuille: k for k in rapport.alle_kerncijfers(con, cfg, dagen[-1])}
    m = cijfers["momentum"]
    assert m.rendement_pct == pytest.approx(1.8)  # 1000 -> 1018
    assert m.rendement_week_pct == pytest.approx(100 * (1018 / 1004 - 1))
    assert {v.tegen for v in m.vergelijkingen} == {"benchmark_spy", "benchmark_btc"}
    assert cijfers["benchmark_spy"].is_meetlat


def test_weekrapport_zonder_sleutel_komt_er_gewoon(con, cfg):
    dagen = _kleine_meting(con)
    week = weekrapport.maak(con, cfg, dagen[-1], client=None)
    assert "momentum" in week.tekst
    assert "Geen conclusies van Claude" in week.tekst
    assert "Nep-geld" in week.tekst
    assert con.execute("SELECT COUNT(*) n FROM rapporten").fetchone()["n"] == 1


def test_weekrapport_wordt_niet_dubbel_gemaakt_of_betaald(con, cfg):
    dagen = _kleine_meting(con)
    budget = Budget(con, cfg, vandaag=dagen[-1])
    client = NepClient(budget, {"rapport": {
        "conclusies": ["a", "b", "c"], "heeft_voorstel": False, "voorstel_strategie": "",
        "voorstel_parameter": "", "voorstel_waarde": 0, "voorstel_onderbouwing": "",
    }})
    weekrapport.maak(con, cfg, dagen[-1], client=client)
    tweede = weekrapport.maak(con, cfg, dagen[-1], client=client)
    assert tweede.al_gemaakt
    assert len(client.aanroepen) == 1


def test_weekrapport_met_geldig_voorstel_start_een_proef(con, cfg):
    dagen = _kleine_meting(con)
    client = NepClient(Budget(con, cfg, vandaag=dagen[-1]), {"rapport": {
        "conclusies": ["een", "twee", "drie"], "heeft_voorstel": True,
        "voorstel_strategie": "momentum", "voorstel_parameter": "terugblik_dagen",
        "voorstel_waarde": 90, "voorstel_onderbouwing": "langer kijken dempt ruis",
    }})
    week = weekrapport.maak(con, cfg, dagen[-1], client=client)
    assert "1. een" in week.tekst and "3. drie" in week.tekst
    assert "challenger 1" in week.tekst
    assert "pas iets aan de echte instellingen na jouw akkoord" in week.tekst
    rij = con.execute("SELECT * FROM challengers").fetchone()
    assert rij["status"] == "loopt" and rij["gestart_op"] == dagen[-1]


def test_weekrapport_met_ongeldig_voorstel_start_niets(con, cfg):
    dagen = _kleine_meting(con)
    client = NepClient(Budget(con, cfg, vandaag=dagen[-1]), {"rapport": {
        "conclusies": ["a", "b", "c"], "heeft_voorstel": True,
        "voorstel_strategie": "momentum", "voorstel_parameter": "max_per_trade_eur",
        "voorstel_waarde": 1000, "voorstel_onderbouwing": "meer inzetten",
    }})
    week = weekrapport.maak(con, cfg, dagen[-1], client=client)
    assert "Niet gestart" in week.tekst
    assert con.execute("SELECT COUNT(*) n FROM challengers").fetchone()["n"] == 0
    opgeslagen = json.loads(con.execute("SELECT voorstel FROM rapporten").fetchone()["voorstel"])
    assert opgeslagen["afgewezen_door_bot"], "ook een afgewezen voorstel moet in het logboek"


def test_rapport_is_nodig_op_de_rapportdag_en_na_een_gemiste_week(con, cfg):
    zondag = datetime(2026, 11, 15, 0, 30, tzinfo=timezone.utc)
    maandag = datetime(2026, 11, 16, 0, 30, tzinfo=timezone.utc)
    assert weekrapport.is_nodig(con, cfg, "2026-11-14", nu=zondag)
    assert not weekrapport.is_nodig(con, cfg, "2026-11-15", nu=maandag)  # nog nooit een: wacht op zondag

    con.execute("INSERT INTO rapporten (datum, tekst, gemaakt_op) VALUES ('2026-11-07','x','x')")
    con.commit()
    # De zondag van 2026-11-15 mislukte; maandag is het al 8 dagen geleden: alsnog.
    assert weekrapport.is_nodig(con, cfg, "2026-11-15", nu=maandag)
    con.execute("INSERT INTO rapporten (datum, tekst, gemaakt_op) VALUES ('2026-11-15','x','x')")
    con.commit()
    assert not weekrapport.is_nodig(con, cfg, "2026-11-16", nu=zondag)
