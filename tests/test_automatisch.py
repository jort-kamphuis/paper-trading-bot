"""Tests voor het automatisch draaien: Telegram, de slanke database en het slot
tegen per ongeluk lokaal draaien. Zonder internet en zonder echte sleutels.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest
import requests

from bot import database, overzicht, telegram
from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN

PROJECT = Path(__file__).resolve().parent.parent


def _laad_script(naam: str):
    pad = PROJECT / "scripts" / f"{naam}.py"
    spec = importlib.util.spec_from_file_location(f"script_{naam}", pad)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ Telegram


def test_korte_tekst_blijft_een_bericht():
    assert telegram.deel_op("hallo") == ["hallo"]


def test_lange_tekst_wordt_netjes_opgeknipt():
    regels = [f"regel {i:04d} " + "x" * 50 for i in range(200)]
    tekst = "\n".join(regels)
    delen = telegram.deel_op(tekst, max_lengte=1000)
    assert all(len(d) <= 1000 for d in delen)
    assert "\n".join(delen) == tekst, "er mag geen tekst verloren gaan"


def test_een_enkele_te_lange_regel_wordt_ook_geknipt():
    delen = telegram.deel_op("x" * 2500, max_lengte=1000)
    assert [len(d) for d in delen] == [1000, 1000, 500]


def test_sleutel_komt_nooit_in_een_foutmelding(monkeypatch):
    """De sleutel zit in het adres; een verbindingsfout noemt dat adres letterlijk."""
    geheim = "123456:GEHEIM-ABCdef"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", geheim)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    def kapot(url, **kwargs):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(telegram.requests, "post", kapot)
    with pytest.raises(telegram.TelegramFout) as fout:
        telegram.stuur("test")
    assert geheim not in str(fout.value)
    assert "<sleutel>" in str(fout.value)


def test_telegram_weigering_wordt_een_nette_fout(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    class Antwoord:
        status_code = 400

        def json(self):
            return {"ok": False, "description": "Bad Request: chat not found"}

    monkeypatch.setattr(telegram.requests, "post", lambda url, **k: Antwoord())
    with pytest.raises(telegram.TelegramFout, match="chat not found"):
        telegram.stuur("test")


def test_stuur_zonder_instellingen_geeft_een_duidelijke_fout(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(telegram.conf, "laad_env", lambda: None)
    with pytest.raises(telegram.TelegramFout, match="ontbreekt"):
        telegram.stuur("test")


def test_lang_bericht_gaat_in_genummerde_delen(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    verstuurd = []

    class Antwoord:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {}}

    def post(url, json=None, **k):
        verstuurd.append(json)
        return Antwoord()

    monkeypatch.setattr(telegram.requests, "post", post)
    aantal = telegram.stuur("\n".join("x" * 100 for _ in range(100)), stil=True)
    assert aantal == len(verstuurd) == 3
    assert verstuurd[0]["text"].startswith("(1/3)")
    assert all(v["disable_notification"] is True for v in verstuurd)
    assert all(v["chat_id"] == "42" for v in verstuurd)


def test_dagbericht_voor_telegram(con):
    pf = nieuwe_portefeuille("momentum", "momentum", INSTELLINGEN, gestart_op="2026-11-12")
    pf.koop("AAPL", "2026-11-13", "2026-11-12", 200.0, "USD", 1.10, reden="plek 1 van 48")
    pf.bewaar(con)
    pf.bewaar_waarde(con, "2026-11-13", {"AAPL": 182.0})
    con.commit()

    tekst = overzicht.telegram_dagbericht(con, "2026-11-13", extra=["AI-kosten vandaag $0,10"])
    assert "momentum" in tekst
    assert "koop AAPL" in tekst
    assert "plek 1 van 48" in tekst
    assert "AI-kosten vandaag" in tekst
    assert "Nep-geld" in tekst, "elk bericht zegt dat het om nep-geld gaat"


def test_dagbericht_noemt_de_beursdag_voluit(con):
    """Het bericht komt een dag later binnen; dan moet duidelijk zijn over welke dag het gaat."""
    nieuwe_portefeuille("momentum", "momentum", INSTELLINGEN, gestart_op="2026-11-12").bewaar(con)
    con.commit()
    tekst = overzicht.telegram_dagbericht(con, "2026-11-12")
    assert tekst.startswith("Paper trading: stand na de beursdag van donderdag 12 november 2026")


def test_dagbericht_alles_in_nederlandse_notatie(con):
    pf = nieuwe_portefeuille("momentum", "momentum", INSTELLINGEN, gestart_op="2026-11-12")
    pf.koop("AAPL", "2026-11-13", "2026-11-12", 200.0, "USD", 1.10, zekerheid=0.75)
    pf.bewaar(con)
    pf.bewaar_waarde(con, "2026-11-13", {"AAPL": 182.0})
    pf.verkoop("AAPL", "2026-11-13", "2026-11-12", 150.0, "USD", 1.10)
    pf.bewaar(con)
    con.execute("INSERT INTO wisselkoersen (datum, eurusd) VALUES ('2026-11-12', 1.10)")
    con.commit()
    tekst = overzicht.telegram_dagbericht(con, "2026-11-13")
    import re

    assert not re.search(r"\d\.\d+%", tekst), "procenten met een punt"
    assert "kans 0,75" in tekst
    assert "euro" not in tekst.split("Trades")[1], "een resultaat in dollars heet geen euro"
    assert re.search(r"verkoop AAPL, -\$\d+,\d{2}", tekst)
    assert "€" in tekst and re.search(r"€\d{1,3}(\.\d{3})*,\d{2}", tekst)


def test_dagbericht_zegt_wat_de_ai_deed(con):
    nieuwe_portefeuille("proef_ai_opus", "x", INSTELLINGEN, gestart_op="2026-11-12").bewaar(con)
    for s, actie in (("AAA", "NIETS"), ("BBB", "NIETS"), ("CCC", "KOOP")):
        con.execute(
            """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, nieuws_tot, gemaakt_op)
               VALUES ('proef_ai_opus', ?, '2026-11-12', ?, 'x', 'x')""",
            (s, actie),
        )
    con.execute(
        """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, overgeslagen, nieuws_tot, gemaakt_op)
           VALUES ('proef_ai_opus', 'DDD', '2026-11-12', 'NIETS', 'geen koers op deze dag', 'x', 'x')"""
    )
    con.commit()
    tekst = overzicht.telegram_dagbericht(con, "2026-11-12")
    assert "AI: 3 beoordelingen, 1 keer kopen, 0 keer verkopen." in tekst


def test_nederlandse_notatie():
    from bot import tekst

    assert tekst.getal(1234.5, 2) == "1.234,50"
    assert tekst.dollar(2.771) == "$2,77"
    assert tekst.pct(-0.5) == "-0,5%"
    assert tekst.datum_lang("2026-11-20") == "vrijdag 20 november 2026"


# ------------------------------------------------------------ slanke database


def test_slank_maken_houdt_de_meting_en_de_kalender(con):
    slank = _laad_script("slank_maken")
    database.bewaar_koersen(
        con,
        [
            {"symbool": s, "datum": "2026-11-12", "open": 1.0, "hoog": 1.0, "laag": 1.0,
             "slot": 1.0, "volume": 1.0, "valuta": "USD", "bron": "test"}
            for s in ("SPY", "AAPL", "BTC/USDT")
        ],
    )
    pf = nieuwe_portefeuille("momentum", "momentum", INSTELLINGEN, gestart_op="2026-11-12")
    pf.koop("AAPL", "2026-11-13", "2026-11-12", 200.0, "USD", 1.10)
    pf.bewaar(con)
    database.zet_instelling(con, "start_datum", "2026-11-12")
    con.commit()

    weg = slank.maak_slank(con, "SPY")

    assert weg == 2
    over = [r["symbool"] for r in con.execute("SELECT symbool FROM koersen")]
    assert over == ["SPY"], "het symbool van de meetkalender moet blijven"
    assert con.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] == 1
    assert con.execute("SELECT COUNT(*) n FROM posities").fetchone()["n"] == 1
    assert database.lees_instelling(con, "start_datum") == "2026-11-12"


# ------------------------------------------------- het slot tegen lokaal draaien


def _args(db: str, **extra):
    standaard = dict(
        geen_data=True, jaren=2.0, tot=None, db=db, start=None,
        geen_ai=True, telegram=False, lokaal=False, weekrapport="nee", ai_op_kopie=False,
    )
    standaard.update(extra)
    return argparse.Namespace(**standaard)


def test_lokaal_draaien_wordt_geweigerd_als_de_meting_op_github_loopt(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    dagelijks = _laad_script("dagelijks")
    db = tmp_path / "trading.db"
    con = database.verbind(db)
    database.zet_instelling(con, dagelijks.SLEUTEL_GITHUB, "ja")
    con.close()

    code, tekst = dagelijks.ronde(_args(str(db)))
    assert code == 2
    assert tekst is None


def test_lokaal_mag_wel_met_het_juiste_vlaggetje(tmp_path, monkeypatch):
    """Met --lokaal komt de ronde voorbij het slot (en stopt hier omdat er geen koersen zijn)."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    dagelijks = _laad_script("dagelijks")
    db = tmp_path / "trading.db"
    con = database.verbind(db)
    database.zet_instelling(con, dagelijks.SLEUTEL_GITHUB, "ja")
    con.close()

    code, _ = dagelijks.ronde(_args(str(db), lokaal=True))
    assert code == 1  # voorbij het slot, gestopt op "geen koersen"


def test_op_github_wordt_onthouden(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    dagelijks = _laad_script("dagelijks")
    db = tmp_path / "trading.db"
    dagelijks.ronde(_args(str(db)))
    con = database.verbind(db)
    assert database.lees_instelling(con, dagelijks.SLEUTEL_GITHUB) == "ja"
    con.close()


def test_ai_kost_alleen_geld_op_de_echte_database(tmp_path):
    """Dit ging mis: een proef op een kopie deed echte API-aanroepen."""
    dagelijks = _laad_script("dagelijks")
    from bot import config as conf

    assert dagelijks._ai_mag(_args(None))
    assert dagelijks._ai_mag(_args(str(conf.DB_PAD)))
    assert not dagelijks._ai_mag(_args(str(tmp_path / "kopie.db")))
    assert dagelijks._ai_mag(_args(str(tmp_path / "kopie.db"), ai_op_kopie=True))


def test_het_ophalen_gebruikt_dezelfde_database(monkeypatch):
    """Dit ging mis: het ophaalscript kreeg --db niet mee en schreef naar de echte database."""
    dagelijks = _laad_script("dagelijks")
    gevraagd = []

    class Klaar:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(opdracht, **kwargs):
        gevraagd.append(opdracht)
        return Klaar()

    monkeypatch.setattr(dagelijks.subprocess, "run", run)
    assert dagelijks._haal_data_op(2.0, [], db="ergens/proef.db")
    assert gevraagd[0][-2:] == ["--db", "ergens/proef.db"]


# ------------------------------------------------------------- de workflow


def test_workflow_bevat_de_veiligheidsstappen():
    tekst = (PROJECT / ".github" / "workflows" / "dagelijks.yml").read_text(encoding="utf-8")
    assert "pytest" in tekst, "de tests moeten voor de ronde draaien"
    assert tekst.index("pytest") < tekst.index("scripts/dagelijks.py"), "tests eerst"
    assert "slank_maken.py" in tekst
    assert "secrets.ANTHROPIC_API_KEY" in tekst
    # Invoer nooit rechtstreeks in een opdrachtregel (script injection).
    for regel in tekst.splitlines():
        if regel.strip().startswith("run:"):
            assert "${{ inputs." not in regel, f"invoer direct in een opdracht: {regel.strip()}"
    assert "config.yaml" in tekst, "na een akkoord moet config.yaml mee in de commit"
    # Nergens een sleutel in de tekst zelf. 'id-token: write' is geen sleutel maar de
    # toestemming die GitHub Pages nodig heeft om het dashboard te publiceren.
    assert tekst.count("id-token: write") <= 1
    zonder = tekst.replace("id-token: write", "")
    for verboden in ("sk-ant-", "api_key:", "token:"):
        assert verboden not in zonder


def test_databases_in_gitignore_kloppen():
    tekst = (PROJECT / ".gitignore").read_text(encoding="utf-8")
    regels = [r.strip() for r in tekst.splitlines() if r.strip() and not r.startswith("#")]
    assert ".env" in regels
    assert "data/backtest.db" in regels
    assert "data/*.db" not in regels, "trading.db moet in git kunnen, anders gaan de resultaten verloren"
