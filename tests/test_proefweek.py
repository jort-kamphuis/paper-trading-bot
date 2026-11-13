"""Tests voor de proefweek en het omschakelen naar de echte meting."""

from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from bot import challenger, config as conf, database, modus, omschakelen, weekrapport
from bot.ai.ronde import ai_portefeuilles
from bot.challenger import Voorstel
from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN

PROJECT = Path(__file__).resolve().parent.parent
OVERLAY = modus.lees_proefweek()

TIJDENS = datetime(2026, 11, 15, 0, 30, tzinfo=timezone.utc)
LAATSTE = datetime(2026, 11, 23, 0, 30, tzinfo=timezone.utc)
NA = datetime(2026, 11, 24, 0, 30, tzinfo=timezone.utc)


def _proef():
    return modus.laad(nu=TIJDENS, overlay=OVERLAY, omgeschakeld=False)


def _echt():
    return modus.laad(nu=NA, overlay=OVERLAY, omgeschakeld=False)


# ----------------------------------------------------------------- de modus


def test_het_proefweekbestand_klopt():
    assert OVERLAY is not None
    assert str(OVERLAY["proefweek"]["tot_en_met"]) == "2026-11-23"
    assert OVERLAY["proefweek"]["database"] == "data/proefweek.db"
    assert "claude-opus-5-5" in conf.laad()["prijzen"], "zonder prijs kan de bot Opus niet afrekenen"


def test_proefweek_tot_en_met_23_november():
    assert modus.is_proefweek(TIJDENS, OVERLAY)
    assert modus.is_proefweek(LAATSTE, OVERLAY), "de ronde van 23 november (over vrijdag) hoort er nog bij"
    assert not modus.is_proefweek(NA, OVERLAY), "de ronde van 24 november verwerkt maandag 23 november"


def test_herinnering_over_de_credits():
    h = OVERLAY["proefweek"]["herinnering"]
    assert h["datum"] == "2026-11-18"
    assert "40 dollar" in h["tekst"] and "automatisch opwaarderen uit" in h["tekst"]


def test_proefweek_instellingen():
    cfg = _proef()
    assert cfg["modus"] == "proefweek"
    assert Path(cfg["database"]).name == "proefweek.db"
    assert cfg["label"].startswith("[PROEFWEEK")
    assert len(cfg["ai_watchlist"]) == len(conf.alle_symbolen(conf.laad())) == 46
    assert cfg["ai"]["budget"]["max_usd_per_maand"] == 90
    assert cfg["challenger"]["weken"] == 0
    assert cfg["challenger"]["droogtest"] is True


def test_vier_ai_portefeuilles_in_de_proefweek():
    pf = ai_portefeuilles(_proef(), date(2026, 11, 15))
    assert [(p["naam"], p["soort"], p["model"]) for p in pf] == [
        ("proef_ai_opus", "debat", "claude-opus-5-5"),
        ("proef_ai_mix", "debat", None),
        ("proef_ai_haiku", "debat", "claude-haiku-5-5"),
        ("proef_ai_enkel", "enkel", None),
    ]


def test_opus_alleen_binnen_het_gratis_tegoed():
    """Het gratis tegoed verloopt 20 november 00:00 UTC; de ronde van 00:30 die dag draait zonder Opus."""
    namen = [p["naam"] for p in ai_portefeuilles(_proef(), date(2026, 11, 19))]
    assert "proef_ai_opus" in namen
    namen = [p["naam"] for p in ai_portefeuilles(_proef(), date(2026, 11, 20))]
    assert "proef_ai_opus" not in namen and "proef_ai_mix" in namen


def test_elke_proefportefeuille_heeft_een_tegenhanger_in_de_echte_meting():
    echt = {(p["soort"], p["model"]) for p in ai_portefeuilles(conf.laad())}
    for p in ai_portefeuilles(_proef(), date(2026, 11, 21)):
        assert (p["soort"], p["model"]) in echt, p["naam"]


def test_mix_gebruikt_precies_de_rollen_van_de_echte_meting():
    assert _proef()["ai"]["rollen"] == conf.laad()["ai"]["rollen"]


def test_opus_krijgt_ruimte_voor_zijn_denkwerk():
    """Opus kan zijn denkwerk niet uitzetten; met een krap plafond wordt alles afgekapt."""
    for rol, plafond in _proef()["ai"]["max_tokens"].items():
        assert plafond >= 4000, f"{rol}: plafond te krap voor Opus"


def test_na_de_proefweek_geldt_alleen_config_yaml():
    cfg = _echt()
    assert cfg["modus"] == "echt"
    assert Path(cfg["database"]) == conf.DB_PAD
    assert cfg["label"] == ""
    normaal = conf.laad()
    assert cfg["ai_watchlist"] == normaal["ai_watchlist"]
    assert cfg["ai"] == normaal["ai"]
    assert cfg["challenger"] == normaal["challenger"]


def test_na_omschakelen_is_het_geen_proefweek_meer():
    cfg = modus.laad(nu=TIJDENS, overlay=OVERLAY, omgeschakeld=True)
    assert cfg["modus"] == "echt"


def test_de_proefweek_verandert_config_yaml_niet():
    voor = conf.CONFIG_PAD.read_text(encoding="utf-8")
    _proef()
    assert conf.CONFIG_PAD.read_text(encoding="utf-8") == voor


# ------------------------------------------------------------ de akkoordroute


@pytest.fixture
def config_kopie(tmp_path):
    pad = tmp_path / "config.yaml"
    shutil.copy(conf.CONFIG_PAD, pad)
    return pad


def _proef_challenger(con, cfg, a_groei, b_groei):
    v = Voorstel("momentum", "terugblik_kalenderdagen", 120, "test")
    nummer = challenger.start(con, cfg, v, voorgesteld_op="2026-11-14", gestart_op="2026-11-14")
    c = con.execute("SELECT * FROM challengers WHERE id=?", (nummer,)).fetchone()
    for naam, groei in ((c["portefeuille_a"], a_groei), (c["portefeuille"], b_groei)):
        nieuwe_portefeuille(naam, "momentum", INSTELLINGEN, gestart_op="2026-11-14").bewaar(con)
        for i, d in enumerate(["2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17"]):
            con.execute(
                "INSERT INTO waarde (portefeuille, datum, kas_usd, posities_usd, totaal_usd) VALUES (?,?,?,?,?)",
                (naam, d, 1000.0, 0.0, 1000.0 + groei * i),
            )
    con.commit()
    return c


def test_in_de_proefweek_wordt_ook_bij_een_slechtere_b_om_akkoord_gevraagd(con):
    cfg = _proef()
    c = _proef_challenger(con, cfg, a_groei=2.0, b_groei=1.0)
    uitslagen = challenger.beoordeel(con, cfg, "2026-11-17", ["2026-11-16", "2026-11-17"])
    assert len(uitslagen) == 1 and uitslagen[0].beter
    rij = con.execute("SELECT * FROM challengers WHERE id=?", (c["id"],)).fetchone()
    assert rij["status"] == "wacht_op_akkoord"
    assert "Geen betekenisvol verschil" in rij["uitslag"] and "proefweek" in rij["uitslag"]


def test_in_de_echte_meting_stopt_een_slechtere_b_gewoon(con):
    cfg = _echt()
    cfg["challenger"]["weken"] = 0
    c = _proef_challenger(con, cfg, a_groei=2.0, b_groei=1.0)
    challenger.beoordeel(con, cfg, "2026-11-17", ["2026-11-16", "2026-11-17"])
    assert con.execute("SELECT status FROM challengers WHERE id=?", (c["id"],)).fetchone()["status"] == "afgewezen"


def test_akkoord_in_de_proefweek_is_als_test_gemarkeerd(con, config_kopie):
    cfg = _proef()
    c = _proef_challenger(con, cfg, a_groei=1.0, b_groei=2.0)
    con.execute("UPDATE challengers SET status='wacht_op_akkoord', uitslag='beter' WHERE id=?", (c["id"],))
    con.commit()
    voor = config_kopie.read_text(encoding="utf-8")
    antwoord = challenger.verwerk(
        con, config_kopie, "akkoord", c["id"], "2026-11-18 09:00 UTC", "2026-11-18", droog=True
    )
    assert config_kopie.read_text(encoding="utf-8") == voor
    assert antwoord.startswith("Akkoord verwerkt (proefweek)")
    w = con.execute("SELECT * FROM wijzigingen").fetchone()
    assert "(proefweek)" in w["akkoord"]
    assert con.execute("SELECT status FROM challengers").fetchone()["status"] == "aangenomen"


def test_testvoorstel_in_de_proefweek_als_claude_niets_voorstelt(con):
    cfg = _proef()
    database.zet_instelling(con, "start_datum", "2026-11-12")
    week = weekrapport.maak(con, cfg, "2026-11-14", client=None)
    rij = con.execute("SELECT * FROM challengers").fetchone()
    assert rij is not None
    assert "TESTVOORSTEL" in rij["onderbouwing"]
    assert "TESTVOORSTEL" in week.tekst


def test_geen_testvoorstel_in_de_echte_meting(con):
    cfg = _echt()
    database.zet_instelling(con, "start_datum", "2026-11-23")
    weekrapport.maak(con, cfg, "2026-11-29", client=None)
    assert con.execute("SELECT COUNT(*) n FROM challengers").fetchone()["n"] == 0


# ---------------------------------------------------------------- omschakelen


def test_omschakelen_alleen_na_de_proefweek(con):
    assert not omschakelen.is_nodig(con, OVERLAY, date(2026, 11, 23))
    assert omschakelen.is_nodig(con, OVERLAY, date(2026, 11, 24))
    assert not omschakelen.is_nodig(con, None, date(2026, 11, 24)), "zonder proefweek niets omschakelen"


def test_omschakelen_start_de_echte_meting_en_boekt_de_correctie(tmp_path):
    echt = database.verbind(tmp_path / "trading.db")
    proef = database.verbind(tmp_path / "proefweek.db")
    # Een oude meting in de echte database.
    pf = nieuwe_portefeuille("momentum", "momentum", INSTELLINGEN, gestart_op="2026-11-12")
    pf.koop("AAPL", "2026-11-13", "2026-11-12", 200.0, "USD", 1.10)
    pf.bewaar(echt)
    database.zet_instelling(echt, "start_datum", "2026-11-12")
    echt.commit()

    melding = omschakelen.voer_uit(echt, proef, OVERLAY, date(2026, 11, 24))

    assert "echte meting begint nu" in melding
    assert echt.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] == 0
    assert echt.execute("SELECT COUNT(*) n FROM portefeuilles").fetchone()["n"] == 0
    assert database.lees_instelling(echt, "start_datum") is None, "de nieuwe meting kiest zelf haar startdag"
    correctie = echt.execute("SELECT * FROM ai_kosten WHERE rol='correctie'").fetchone()
    assert correctie["kosten_usd"] == pytest.approx(0.2339)
    assert correctie["maand"] == "2026-11"
    assert database.lees_instelling(echt, omschakelen.SLEUTEL) == "2026-11-24"
    assert database.lees_instelling(proef, "afgesloten_op") == "2026-11-24"
    assert "1.000 dollar" in melding

    # Nooit twee keer: geen tweede correctie, en de nieuwe meting blijft staan.
    tweede = omschakelen.voer_uit(echt, proef, OVERLAY, date(2026, 11, 25))
    assert "al gestart" in tweede
    assert echt.execute("SELECT COUNT(*) n FROM ai_kosten WHERE rol='correctie'").fetchone()["n"] == 1
    assert not omschakelen.is_nodig(echt, OVERLAY, date(2026, 11, 25))
    echt.close()
    proef.close()


# ---------------------------------------------------------- overige onderdelen


def _laad_script(naam):
    spec = importlib.util.spec_from_file_location(f"s_{naam}", PROJECT / "scripts" / f"{naam}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_de_ai_mag_op_de_proefweekdatabase_draaien():
    import argparse

    dagelijks = _laad_script("dagelijks")
    args = argparse.Namespace(db=str(PROJECT / "data" / "proefweek.db"), ai_op_kopie=False)
    assert dagelijks._ai_mag(args)


def test_slank_maken_neemt_ook_de_proefweek_mee():
    slank = _laad_script("slank_maken")
    namen = [p.name for p in slank._paden()]
    assert namen == ["trading.db", "proefweek.db"]


def test_proefweekverslag_telt_kansen_drempels_en_kosten(con):
    verslag = _laad_script("proefweek_verslag")
    for symbool, actie, kans, verwacht in [
        ("AAA", "NIETS", 0.52, 0.2),
        ("BBB", "KOOP", 0.62, 1.4),
        ("CCC", "NIETS", 0.58, 0.3),
        ("DDD", "NIETS", None, None),
    ]:
        con.execute(
            """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kans, verwacht_pct,
                                         overgeslagen, stappen, kosten_usd, nieuws_tot, gemaakt_op)
               VALUES ('proef_ai_mix', ?, '2026-11-16', ?, ?, ?, ?, '{}', 0.02, 'x', 'x')""",
            (symbool, actie, kans, verwacht, None if kans is not None else "risk: antwoord afgekapt"),
        )
    con.commit()
    tekst = verslag.verslag(con)
    assert "1 koop, 0 verkoop, 3 niets" in tekst
    assert "haalt beide koopdrempels: 1 van 3 (alleen de kans: 2)" in tekst
    assert "zonder bruikbare kans: 1" in tekst
    assert "$0,0200 per symbool-dag" in tekst
    assert "15 symbolen $6,30" in tekst  # 0,02 x 15 x 21


def test_workflow_bewaart_ook_de_proefweek_en_heeft_tijd_genoeg():
    tekst = (PROJECT / ".github" / "workflows" / "dagelijks.yml").read_text(encoding="utf-8")
    assert "data/proefweek.db" in tekst
    stappen = yaml.safe_load(tekst)["jobs"]["ronde"]
    ronde = next(s for s in stappen["steps"] if s.get("id") == "ronde")
    assert ronde["timeout-minutes"] < stappen["timeout-minutes"], (
        "de ronde moet eerder stoppen dan de job, anders gaat het bewaren van de kosten verloren"
    )
