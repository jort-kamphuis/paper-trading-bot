"""Tests voor het vangnet van de nachtronde (17 november 2026).

Aanleiding: om 01:42 UTC had de koersbron de beursdag van maandag nog niet, en de
bot handelde die nacht terecht niet. Sindsdien:

* de hoofdronde begint om 04:00 UTC in plaats van 00:30 UTC;
* een herkansing om 08:00 UTC doet alleen iets als de hoofdronde niet compleet was,
  en stuurt alleen dan een bericht;
* de AI ziet nieuws tot een vast tijdstip (04:00 UTC), ook in de herkansing;
* elke ronde legt vast of de koersen van de laatste beursdag er al waren.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from bot import config as conf
from bot import database, koerslog
from bot.ai import ronde as ronde_mod
from bot.ai.budget import Budget
from bot.ai.client import ClaudeClient, NepClient
from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN, maak_markt, werkdagen

ECHT = conf.laad()
PROJECT = Path(__file__).resolve().parent.parent
WORKFLOW = (PROJECT / ".github" / "workflows" / "dagelijks.yml").read_text(encoding="utf-8")
UTC = timezone.utc


def _laad_dagelijks(naam: str = "script_dagelijks_vangnet"):
    spec = importlib.util.spec_from_file_location(naam, PROJECT / "scripts" / "dagelijks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------ de tijden


def test_de_hoofdronde_om_vier_uur_en_de_herkansing_om_acht_uur_utc():
    w = yaml.safe_load(WORKFLOW)
    tijden = [s["cron"] for s in w[True]["schedule"]]   # YAML leest de sleutel 'on' als True
    assert tijden == ["0 4 * * *", "0 8 * * *"]
    # De herkansing is precies de tweede geplande tijd; een ronde die je zelf start is geen herkansing.
    stap = [s for s in w["jobs"]["ronde"]["steps"] if s.get("id") == "ronde"][0]
    assert stap["env"]["HERKANSING"] == "${{ github.event.schedule == '0 8 * * *' && 'ja' || 'nee' }}"
    assert stap["env"]["GEPLAND"] == "${{ github.event_name == 'schedule' && 'ja' || 'nee' }}"
    assert '--herkansing "$HERKANSING" --gepland "$GEPLAND"' in stap["run"]


def test_beide_tijden_liggen_na_de_cryptodag_en_ruim_voor_de_opening_van_de_beurs():
    """Zomer en winter: de cryptodag sluit om 00:00 UTC, de beurs opent om 9:30 in New York."""
    from zoneinfo import ZoneInfo

    from bot.data import koersen

    for dag in (date(2026, 11, 18), date(2027, 1, 15), date(2027, 3, 15), date(2027, 7, 15), date(2027, 11, 1)):
        opening = datetime(dag.year, dag.month, dag.day, 9, 30, tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
        for uur in (4, 8):
            start = datetime(dag.year, dag.month, dag.day, uur, 0, tzinfo=UTC)
            assert start + timedelta(hours=3) < opening, f"{dag} {uur}:00 UTC: minstens drie uur voor de opening"
            # De cryptodag van gisteren is af, die van vandaag nog niet.
            assert koersen.eerste_onaffe_dag_crypto(start) == dag.isoformat()
            # De beursdag van gisteren is af (de beurs sloot om 16:00 in New York), die van vandaag niet.
            assert koersen.eerste_onaffe_dag_aandelen(start) == dag.isoformat()


def test_de_nieuwsgrens_is_de_starttijd_van_de_hoofdronde():
    assert ECHT["ai"]["nieuwsgrens_utc"] == "04:00"
    assert "0 4 * * *" in WORKFLOW


def test_het_dashboard_wordt_niet_gebouwd_na_een_herkansing_die_niets_deed():
    w = yaml.safe_load(WORKFLOW)
    stappen = {s.get("id"): s for s in w["jobs"]["ronde"]["steps"] if s.get("id")}
    assert "steps.ronde.outputs.overgeslagen != 'ja'" in stappen["dashboard"]["if"]
    assert 'if [ -f .ronde_overgeslagen ]; then echo "overgeslagen=ja" >> "$GITHUB_OUTPUT"; fi' in stappen["ronde"]["run"]
    assert w["jobs"]["ronde"]["outputs"]["dashboard"] == "${{ steps.dashboard.outcome }}"
    assert "needs.ronde.outputs.dashboard == 'success'" in w["jobs"]["dashboard"]["if"]
    assert ".ronde_overgeslagen" in (PROJECT / ".gitignore").read_text(encoding="utf-8")


# ---------------------------------------------------- de vaste nieuwsgrens


def test_de_ai_ziet_nooit_later_nieuws_dan_vier_uur_utc():
    f = ronde_mod.vaste_nieuwsgrens
    assert f(ECHT, datetime(2026, 11, 18, 4, 0, 5, tzinfo=UTC)) == "2026-11-18T04:00:00+00:00"
    assert f(ECHT, datetime(2026, 11, 18, 5, 12, tzinfo=UTC)) == "2026-11-18T04:00:00+00:00", "GitHub startte een uur later"
    assert f(ECHT, datetime(2026, 11, 18, 8, 40, tzinfo=UTC)) == "2026-11-18T04:00:00+00:00", "de herkansing"
    assert f(ECHT, datetime(2026, 11, 18, 15, 0, tzinfo=UTC)) == "2026-11-18T04:00:00+00:00", "met de hand, na de opening"
    # Voor vier uur (met de hand): nieuws uit de toekomst bestaat niet.
    assert f(ECHT, datetime(2026, 11, 18, 1, 42, tzinfo=UTC)) == "2026-11-18T01:42:00+00:00"
    # Het tijdstip komt uit de instellingen.
    assert f({"ai": {"nieuwsgrens_utc": "06:30"}}, datetime(2026, 11, 18, 9, 0, tzinfo=UTC)) == "2026-11-18T06:30:00+00:00"
    assert f({}, datetime(2026, 11, 18, 9, 0, tzinfo=UTC)) == "2026-11-18T04:00:00+00:00"


def test_de_grens_per_symbool_blijft_gelden_onder_de_vaste_grens():
    """Crypto: nieuws tot middernacht UTC (dan gaat de order af). Aandelen: tot de vaste grens van 04:00."""
    plafond = datetime.fromisoformat(ronde_mod.vaste_nieuwsgrens(ECHT, datetime(2026, 11, 18, 8, 40, tzinfo=UTC)))
    assert ronde_mod.nieuws_grens("BTC/USDT", "2026-11-17", nu=plafond) == "2026-11-18T00:00:00+00:00"
    assert ronde_mod.nieuws_grens("AAPL", "2026-11-17", nu=plafond) == "2026-11-18T04:00:00+00:00"


GOED = {
    "nieuws": {"samenvatting": "rustig", "belangrijkste_risico": "geen", "gebeurtenis_binnenkort": False},
    "bull": {"argument": "a", "sterkste_punt": "b", "overtuiging": 0.5},
    "bear": {"argument": "c", "sterkste_punt": "d", "overtuiging": 0.5},
    "risk": {"onderbouwing": "x", "belangrijkste_risico": "y", "kans": 0.47, "verwacht_extra_rendement_pct": 0.2},
    "enkel": {"onderbouwing": "x", "belangrijkste_risico": "y", "kans": 0.47, "verwacht_extra_rendement_pct": 0.2},
}


def test_de_nachtronde_geeft_de_vaste_grens_door_aan_elke_ai_portefeuille(con, cfg_min, monkeypatch):
    dagelijks = _laad_dagelijks("script_dagelijks_vangnet_ai")
    cfg = copy.deepcopy(cfg_min)
    cfg["modus"] = "echt"
    cfg["ai_watchlist"] = ["AAA", "BBB"]
    dagen = [d for d in werkdagen(800, start="2025-01-01") if d <= "2027-01-15"][-60:]
    markt = maak_markt({s: [100.0 + i * 0.1 for i in range(len(dagen))] for s in ("AAA", "BBB", "SPY")}, dagen)
    for p in ronde_mod.ai_portefeuilles(cfg):
        nieuwe_portefeuille(p["naam"], p["naam"], INSTELLINGEN, gestart_op=dagen[0]).bewaar(con)
    con.commit()

    def dicht(self):
        raise AssertionError("de echte API mag in een test nooit worden aangeroepen")

    monkeypatch.setattr(ClaudeClient, "_verbinding", dicht)
    monkeypatch.setattr(conf, "sleutel", lambda naam: "nep-sleutel-voor-de-test")
    monkeypatch.setattr(ronde_mod, "maak_client", lambda con, cfg, vandaag=None: NepClient(Budget(con, cfg, vandaag=vandaag), GOED))
    # Alsof de herkansing om 08:40 UTC draait, de ochtend na de beslisdag.
    monkeypatch.setattr(ronde_mod, "vaste_nieuwsgrens",
                        lambda cfg, nu=None: "2027-01-16T04:00:00+00:00")
    dagelijks._ai_ronde(con, cfg, markt, "2027-01-15", nieuw_nieuws=False, ingehaald=1)

    grenzen = {r["nieuws_tot"] for r in con.execute("SELECT nieuws_tot FROM ai_besluiten")}
    assert grenzen == {"2027-01-16T04:00:00+00:00"}, "elke beoordeling van elke portefeuille, dezelfde grens"
    assert con.execute("SELECT COUNT(*) n FROM ai_besluiten").fetchone()["n"] == 2 * len(ronde_mod.ai_portefeuilles(cfg))


# -------------------------------------------------------------- het koerslog


def test_welke_beursdag_af_had_moeten_zijn():
    f = koerslog.verwachte_beursdag
    assert f(ECHT, datetime(2026, 11, 17, 1, 42, tzinfo=UTC)) == "2026-11-16", "de nacht van het incident"
    assert f(ECHT, datetime(2026, 11, 17, 4, 5, tzinfo=UTC)) == "2026-11-16"
    assert f(ECHT, datetime(2026, 11, 17, 8, 30, tzinfo=UTC)) == "2026-11-16", "de herkansing verwacht dezelfde dag"
    assert f(ECHT, datetime(2026, 11, 16, 4, 0, tzinfo=UTC)) == "2026-11-13", "maandagochtend: vrijdag"
    assert f(ECHT, datetime(2026, 11, 15, 4, 0, tzinfo=UTC)) == "2026-11-13", "zondag: nog steeds vrijdag"
    # Thanksgiving (donderdag 26 november 2026) is de beurs dicht: vrijdagochtend geldt woensdag.
    assert f(ECHT, datetime(2026, 11, 27, 4, 0, tzinfo=UTC)) == "2026-11-25"
    # Na 17:00 in New York staat de dag van vandaag vast.
    assert f(ECHT, datetime(2026, 11, 17, 22, 30, tzinfo=UTC)) == "2026-11-17"
    assert f(ECHT, datetime(2026, 11, 17, 21, 30, tzinfo=UTC)) == "2026-11-16", "16:30 in New York: nog niet"
    # Een jaar zonder beurskalender: niets te zeggen.
    assert f({"beurskalender": {"feestdagen": {}}}, datetime(2030, 5, 7, 4, 0, tzinfo=UTC)) is None


def _koers(con, symbool, datum):
    database.bewaar_koersen(con, [{"symbool": symbool, "datum": datum, "open": 1.0, "hoog": 1.0, "laag": 1.0,
                                   "slot": 1.0, "volume": 1.0, "valuta": "USD", "bron": "test"}])


def test_het_koerslog_telt_voor_hoeveel_symbolen_de_dag_er_was(con):
    symbolen = conf.aandelen_symbolen(ECHT)
    assert len(symbolen) == 38
    for s in symbolen:
        _koers(con, s, "2026-11-13")
    for s in symbolen[:5]:
        _koers(con, s, "2026-11-16")
    _koers(con, "BTC/USDT", "2026-11-16")   # crypto telt niet mee

    leeg = koerslog.leg_vast(con, ECHT, "hoofd", datetime(2026, 11, 17, 1, 42, tzinfo=UTC))
    assert leeg == {"gevraagd_op": "2026-11-17T01:42:00Z", "ronde": "hoofd", "beursdag": "2026-11-16",
                    "aanwezig": 5, "verwacht": 38}
    assert koerslog.als_regel(leeg) == "2026-11-17 om 01:42 UTC (hoofd): beursdag 2026-11-16, 5 van 38 koersen, ONVOLLEDIG"

    for s in symbolen[5:]:
        _koers(con, s, "2026-11-16")
    vol = koerslog.leg_vast(con, ECHT, "herkansing", datetime(2026, 11, 17, 8, 20, tzinfo=UTC))
    assert vol["aanwezig"] == 38 and koerslog.als_regel(vol).endswith("38 van 38 koersen, compleet")

    tekst = koerslog.samenvatting(con)
    assert tekst[0] == ("Koersen van Yahoo bij het ophalen (2 rondes gelogd): in 1 van de 1 hoofdrondes was de "
                        "laatste beursdag er nog niet (helemaal).")
    assert tekst[1].strip().startswith("2026-11-17 om 01:42 UTC (hoofd)") and "(herkansing)" in tekst[2]
    assert koerslog.samenvatting(database.verbind(":memory:")) == []


def test_helemaal_geen_koers_heet_ontbreekt(con):
    r = koerslog.leg_vast(con, ECHT, "hoofd", datetime(2026, 11, 17, 4, 2, tzinfo=UTC))
    assert r["aanwezig"] == 0 and koerslog.als_regel(r).endswith("0 van 38 koersen, ONTBREEKT")


# ------------------------------------------------------------ de herkansing


def _args(db: str, **extra):
    standaard = dict(
        geen_data=True, jaren=2.0, tot=None, db=db, start=None, geen_ai=True, telegram=False, lokaal=True,
        weekrapport="nee", ai_op_kopie=False, omschakelen="auto", herkansing="nee", gepland="nee",
    )
    standaard.update(extra)
    return argparse.Namespace(**standaard)


def _vandaag_utc() -> date:
    return datetime.now(UTC).date()


BEURSDAGEN = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]


def _vul_koersen(db: Path, zonder_spy_op: str | None = None) -> None:
    """Een week koersen voor alle symbolen van de echte lijst, en een wisselkoers."""
    con = database.verbind(db)
    rijen = []
    for s in conf.alle_symbolen(ECHT):
        for i, d in enumerate(BEURSDAGEN):
            if s == "SPY" and d == zonder_spy_op:
                continue
            rijen.append({"symbool": s, "datum": d, "open": 100.0 + i, "hoog": 101.0 + i, "laag": 99.0 + i,
                          "slot": 100.0 + i, "volume": 1000.0, "valuta": "USD", "bron": "test"})
    database.bewaar_koersen(con, rijen)
    database.bewaar_wisselkoersen(con, [{"datum": d, "eurusd": 1.10} for d in BEURSDAGEN])
    con.commit()
    con.close()


@pytest.fixture
def dagelijks(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return _laad_dagelijks()


def test_een_complete_ronde_wordt_onthouden_en_de_herkansing_doet_dan_niets(tmp_path, dagelijks):
    db = tmp_path / "kopie.db"
    _vul_koersen(db)

    args = _args(str(db), gepland="ja")
    code, tekst = dagelijks.ronde(args)
    assert code == 0 and tekst and "NIET gehandeld" not in tekst and not args.overgeslagen
    con = database.verbind(db)
    assert database.lees_instelling(con, dagelijks.sleutel_ronde(_vandaag_utc())) == "klaar"
    standen = con.execute("SELECT COUNT(*) n FROM waarde").fetchone()["n"]
    con.close()

    her = _args(str(db), herkansing="ja", gepland="ja")
    code, tekst = dagelijks.ronde(her)
    assert (code, tekst) == (0, None) and her.overgeslagen
    con = database.verbind(db)
    assert con.execute("SELECT COUNT(*) n FROM waarde").fetchone()["n"] == standen, "de herkansing heeft niets veranderd"
    con.close()


def test_na_een_ronde_die_niet_kon_handelen_doet_de_herkansing_alles_alsnog(tmp_path, dagelijks):
    db = tmp_path / "kopie.db"
    _vul_koersen(db)
    # Eerst een gewone ronde tot en met donderdag, zodat de meting loopt.
    dagelijks.ronde(_args(str(db), tot="2026-11-12"))
    con = database.verbind(db)
    con.execute("DELETE FROM koersen WHERE symbool = 'SPY' AND datum = '2026-11-13'")
    con.execute("DELETE FROM instellingen WHERE sleutel = ?", (dagelijks.sleutel_ronde(_vandaag_utc()),))
    con.commit()
    con.close()

    # De hoofdronde: SPY heeft vrijdag nog niet. Niet handelen, en dat onthouden.
    code, tekst = dagelijks.ronde(_args(str(db), gepland="ja"))
    assert code == 0 and "Vannacht NIET gehandeld" in tekst and "SPY: geen koers op 2026-11-13" in tekst
    assert "De herkansing van rond 08:00 UTC probeert het vannacht nog een keer." in tekst
    con = database.verbind(db)
    assert database.lees_instelling(con, dagelijks.sleutel_ronde(_vandaag_utc())).startswith("niet gehandeld")
    assert con.execute("SELECT MAX(datum) d FROM waarde").fetchone()["d"] == "2026-11-12"
    con.close()

    # De herkansing, nog steeds zonder de koers: weer niet handelen, met een eigen tekst.
    her = _args(str(db), herkansing="ja", gepland="ja")
    code, tekst = dagelijks.ronde(her)
    assert code == 0 and not her.overgeslagen
    assert "Dit is de herkansing" in tekst and "Ook de herkansing kon niet handelen." in tekst

    # De koers komt alsnog: de herkansing doet de hele ronde en meldt dat hij de herkansing is.
    con = database.verbind(db)
    database.bewaar_koersen(con, [{"symbool": "SPY", "datum": "2026-11-13", "open": 104.0, "hoog": 105.0,
                                   "laag": 103.0, "slot": 104.0, "volume": 1000.0, "valuta": "USD", "bron": "test"}])
    con.commit()
    con.close()
    her = _args(str(db), herkansing="ja", gepland="ja")
    code, tekst = dagelijks.ronde(her)
    assert code == 0 and not her.overgeslagen and "NIET gehandeld" not in tekst
    assert "Dit is de herkansing: de eerste ronde van vannacht heeft niet gehandeld of is mislukt." in tekst
    con = database.verbind(db)
    assert con.execute("SELECT MAX(datum) d FROM waarde").fetchone()["d"] == "2026-11-13"
    assert database.lees_instelling(con, dagelijks.sleutel_ronde(_vandaag_utc())) == "klaar"
    con.close()


def test_zonder_eerste_ronde_doet_de_herkansing_alles(tmp_path, dagelijks):
    """De hoofdronde is helemaal niet gestart of is gecrasht: er staat niets genoteerd."""
    db = tmp_path / "kopie.db"
    _vul_koersen(db)
    her = _args(str(db), herkansing="ja", gepland="ja")
    code, tekst = dagelijks.ronde(her)
    assert code == 0 and not her.overgeslagen and "Dit is de herkansing" in tekst
    con = database.verbind(db)
    assert con.execute("SELECT COUNT(*) n FROM waarde").fetchone()["n"] > 0
    con.close()


def test_een_ronde_van_gisteren_telt_niet_voor_vandaag(tmp_path, dagelijks):
    db = tmp_path / "kopie.db"
    _vul_koersen(db)
    con = database.verbind(db)
    database.zet_instelling(con, dagelijks.sleutel_ronde(_vandaag_utc() - timedelta(days=1)), "klaar")
    con.commit()
    con.close()
    her = _args(str(db), herkansing="ja")
    dagelijks.ronde(her)
    assert not her.overgeslagen


def test_een_herkansing_die_niets_deed_stuurt_geen_bericht(tmp_path, dagelijks, monkeypatch):
    from bot import telegram

    db = tmp_path / "kopie.db"
    _vul_koersen(db)
    dagelijks.ronde(_args(str(db)))   # de eerste ronde, compleet

    verstuurd: list[str] = []
    monkeypatch.setattr(telegram, "is_ingesteld", lambda: True)
    monkeypatch.setattr(telegram, "stuur", lambda tekst, stil=False: verstuurd.append(tekst) or 1)
    monkeypatch.setattr(dagelijks.conf, "PROJECT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["dagelijks.py", "--db", str(db), "--geen-data", "--geen-ai", "--lokaal",
                                      "--weekrapport", "nee", "--telegram", "--herkansing", "ja"])
    assert dagelijks.main() == 0
    assert verstuurd == [], "de eerste ronde heeft zijn bericht al gestuurd"

    # Had de eerste ronde niet gehandeld, dan stuurt de herkansing wel een bericht.
    con = database.verbind(db)
    con.execute("DELETE FROM instellingen WHERE sleutel = ?", (dagelijks.sleutel_ronde(_vandaag_utc()),))
    con.commit()
    con.close()
    assert dagelijks.main() == 0
    assert len(verstuurd) == 1 and "Dit is de herkansing" in verstuurd[0]


def test_het_briefje_voor_de_workflow_alleen_op_de_echte_database(tmp_path, dagelijks, monkeypatch):
    """Op de echte database (zonder --db) laat een herkansing die niets deed een briefje achter; een ronde die
    wel iets deed ruimt een oud briefje op."""
    def nep_ronde(overgeslagen):
        def ronde(args):
            args.overgeslagen = overgeslagen
            args.berichten = []
            return 0, (None if overgeslagen else "dagbericht")
        return ronde

    monkeypatch.setattr(dagelijks.conf, "PROJECT", tmp_path)
    briefje = tmp_path / dagelijks.OVERGESLAGEN_BESTAND
    monkeypatch.setattr(sys, "argv", ["dagelijks.py", "--herkansing", "ja"])
    monkeypatch.setattr(dagelijks, "ronde", nep_ronde(True))
    assert dagelijks.main() == 0 and briefje.exists()
    monkeypatch.setattr(dagelijks, "ronde", nep_ronde(False))
    assert dagelijks.main() == 0 and not briefje.exists()
