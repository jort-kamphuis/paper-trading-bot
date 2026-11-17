"""Tests voor de takken die de eindreview (14 november 2026) als ongedekt noemde.

Ze leggen vast wat de code nu doet en veranderen geen gedrag:

* de nachtloop zelf (scripts/dagelijks.py, _ai_ronde): een bijportefeuille wordt
  overgeslagen als het budget krap is, en na de einddatum beoordeelt de AI niets meer;
* de herkansing voor de andere rollen dan 'risk', en een netwerkfout bij de tweede poging;
* de vergelijking 'zelfde soort, even belegd' met dagen zonder crypto_usd.

Alles draait met de NepClient en een database in het geheugen. De echte API is
in deze tests dichtgezet: wordt hij toch aangeroepen, dan faalt de test.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from bot import config as conf
from bot import rapport
from bot.ai import budget as budget_mod
from bot.ai import ronde as ronde_mod
from bot.ai.budget import Budget
from bot.ai.client import Antwoord, ClaudeClient, NepClient
from bot.ai.ronde import denk_na
from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN, maak_markt, werkdagen

ECHT = conf.laad()
PROJECT = Path(__file__).resolve().parent.parent
BIJ = ("ai_controle_haiku", "ai_enkel")
ALLE = ("ai_analist",) + BIJ

# Kans 0,7 en verwacht 2%: voldoet aan de koopregel, dus kopen en herbevestigen.
GOED = {
    "nieuws": {"samenvatting": "rustig", "belangrijkste_risico": "geen", "gebeurtenis_binnenkort": False},
    "bull": {"argument": "a", "sterkste_punt": "b", "overtuiging": 0.5},
    "bear": {"argument": "c", "sterkste_punt": "d", "overtuiging": 0.5},
    "risk": {"onderbouwing": "x", "belangrijkste_risico": "y", "kans": 0.7, "verwacht_extra_rendement_pct": 2.0},
    "enkel": {"onderbouwing": "x", "belangrijkste_risico": "y", "kans": 0.7, "verwacht_extra_rendement_pct": 2.0},
}


# ------------------------------------------------------------ hulpjes


@pytest.fixture(scope="module")
def dagelijks():
    spec = importlib.util.spec_from_file_location("s_dagelijks_eindreview", PROJECT / "scripts" / "dagelijks.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def cfg_nacht(cfg_min):
    """De echte instellingen, in de modus van de echte meting, met twee symbolen voor de AI."""
    cfg = copy.deepcopy(cfg_min)
    cfg["modus"] = "echt"
    cfg["ai_watchlist"] = ["AAA", "BBB"]
    # Deze tests gaan over een krap budget: reken met $35 per maand, los van wat er nu in config.yaml staat.
    cfg["ai"]["budget"]["max_usd_per_maand"] = 35.0
    return cfg


def _markt_tot(laatste: str):
    """Koersen voor AAA, BBB en SPY op 140 werkdagen, tot en met de beslisdag."""
    dagen = [d for d in werkdagen(800, start="2025-01-01") if d <= laatste][-140:]
    assert dagen[-1] == laatste, "de beslisdag moet een werkdag zijn"
    koersen = {s: [100.0 + i * 0.1 for i in range(len(dagen))] for s in ("AAA", "BBB", "SPY")}
    return maak_markt(koersen, dagen), dagen


def _zet_vandaag(monkeypatch, iso: str) -> None:
    """De ronde draait de dag na de beslisdag; het budget telt per maand van 'vandaag'."""

    class Vandaag(date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(iso)

    monkeypatch.setattr(budget_mod, "date", Vandaag)


def _nachtloop_met_nepclient(monkeypatch, antwoorden=GOED) -> list[NepClient]:
    """Laat _ai_ronde draaien zonder sleutel, zonder internet en zonder kosten."""

    def dicht(self):
        raise AssertionError("de echte API mag in een test nooit worden aangeroepen")

    monkeypatch.setattr(ClaudeClient, "_verbinding", dicht)
    monkeypatch.setattr(conf, "sleutel", lambda naam: "nep-sleutel-voor-de-test")
    clients: list[NepClient] = []

    def maak(con, cfg, vandaag=None):
        clients.append(NepClient(Budget(con, cfg, vandaag=vandaag), antwoorden))
        return clients[-1]

    monkeypatch.setattr(ronde_mod, "maak_client", maak)
    return clients


def _portefeuilles(con, dagen, met_posities: str) -> None:
    """Drie AI-portefeuilles. 'met_posities' heeft AAA (houdtijd om) en BBB (pas gekocht)."""
    for naam in ALLE:
        pf = nieuwe_portefeuille(naam, naam, INSTELLINGEN, gestart_op=dagen[0])
        if naam == met_posities:
            pf.koop("AAA", dagen[-20], dagen[-21], 100.0, "USD", 1.10)  # 20 werkdagen = 28 kalenderdagen
            pf.koop("BBB", dagen[-3], dagen[-4], 100.0, "USD", 1.10)
            assert set(pf.posities) == {"AAA", "BBB"}
        pf.bewaar(con)
    con.commit()


def _eerdere_nacht(con, pf: str, datum: str, usd: float) -> None:
    con.execute(
        """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kosten_usd, nieuws_tot, gemaakt_op)
           VALUES (?, 'AAA', ?, 'NIETS', ?, 'x', 'x')""", (pf, datum, usd))


def _al_uitgegeven(con, maand: str, usd: float) -> None:
    con.execute(
        """INSERT INTO ai_kosten (datum, maand, rol, model, tokens_in, tokens_uit, kosten_usd)
           VALUES (?, ?, 'eerder', 'm', 0, 0, ?)""", (maand + "-01", maand, usd))


def _beoordeeld(con, datum: str) -> dict[str, int]:
    return {
        r["portefeuille"]: r["n"] for r in con.execute(
            "SELECT portefeuille, COUNT(*) n FROM ai_besluiten WHERE datum = ? GROUP BY portefeuille", (datum,))
    }


def _wachtrij(con, pf: str) -> list[tuple[str, str]]:
    return [(r["symbool"], r["actie"]) for r in con.execute(
        "SELECT symbool, actie FROM wachtrij WHERE portefeuille = ? ORDER BY symbool", (pf,))]


# ---------------------------------- de nachtloop: reserve voor de hoofdvraag


def _decembernacht(con, monkeypatch, uitgegeven: float):
    """De ronde van 3 december over beslisdag 2 december, met twee eerdere nachten in de boeken."""
    markt, dagen = _markt_tot("2026-12-02")
    _portefeuilles(con, dagen, met_posities="ai_enkel")
    for d in ("2026-11-30", "2026-12-01"):
        _eerdere_nacht(con, "ai_analist", d, 1.00)
        _eerdere_nacht(con, "ai_controle_haiku", d, 0.10)
        _eerdere_nacht(con, "ai_enkel", d, 0.40)
    if uitgegeven:
        _al_uitgegeven(con, "2026-12", uitgegeven)
    con.commit()
    _zet_vandaag(monkeypatch, "2026-12-03")
    return markt, dagen[-1]


def test_krap_budget_slaat_de_bijportefeuilles_over_en_de_hoofdvraag_niet(con, cfg_nacht, dagelijks, monkeypatch):
    """Er is 5 dollar over en de hoofd-AI heeft voor de rest van december meer nodig: alleen hij draait."""
    markt, datum = _decembernacht(con, monkeypatch, uitgegeven=30.0)
    clients = _nachtloop_met_nepclient(monkeypatch)

    regels = dagelijks._ai_ronde(con, cfg_nacht, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert _beoordeeld(con, datum) == {"ai_analist": 2}, "alleen de portefeuille van de hoofdvraag is beoordeeld"
    for naam in BIJ:
        assert any(r.startswith(f"{naam} overgeslagen") and "ai_analist de maand te laten afmaken" in r
                   for r in regels), naam
    rollen_gevraagd = {a["rol"] for a in clients[0].aanroepen}
    assert rollen_gevraagd == {"nieuws", "bull", "bear", "risk"}, "geen aanroep voor ai_enkel"
    assert len(clients[0].aanroepen) == 8, "vier rollen voor twee symbolen, alleen voor ai_analist"
    # De hoofd-AI koopt gewoon (kans 0,7); de bijportefeuilles kopen niets.
    assert _wachtrij(con, "ai_analist") == [("AAA", "KOOP"), ("BBB", "KOOP")]
    assert _wachtrij(con, "ai_controle_haiku") == []


def test_een_overgeslagen_bijportefeuille_verkoopt_alleen_na_de_houdtijd(con, cfg_nacht, dagelijks, monkeypatch):
    """Zonder beoordeling: AAA (houdtijd om) gaat eruit, BBB (pas gekocht) blijft, en er wordt niets gekocht."""
    markt, datum = _decembernacht(con, monkeypatch, uitgegeven=30.0)
    _nachtloop_met_nepclient(monkeypatch)

    dagelijks._ai_ronde(con, cfg_nacht, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert _wachtrij(con, "ai_enkel") == [("AAA", "VERKOOP")]
    reden = con.execute("SELECT reden FROM wachtrij WHERE portefeuille = 'ai_enkel'").fetchone()["reden"]
    assert "houdtijd van 14 dagen om, en geen nieuwe beoordeling" in reden


def test_met_ruim_budget_draaien_de_bijportefeuilles_gewoon(con, cfg_nacht, dagelijks, monkeypatch):
    """Dezelfde nacht zonder eerdere uitgaven: iedereen beoordeelt, en AAA wordt herbevestigd in plaats van verkocht."""
    markt, datum = _decembernacht(con, monkeypatch, uitgegeven=0.0)
    _nachtloop_met_nepclient(monkeypatch)

    regels = dagelijks._ai_ronde(con, cfg_nacht, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert _beoordeeld(con, datum) == {naam: 2 for naam in ALLE}
    assert not any("overgeslagen" in r for r in regels)
    assert _wachtrij(con, "ai_enkel") == [], "AAA voldoet aan de koopregel: herbevestigd, niet verkocht"
    bevestigd = con.execute(
        "SELECT bevestigd_op FROM posities WHERE portefeuille = 'ai_enkel' AND symbool = 'AAA'").fetchone()
    assert bevestigd["bevestigd_op"] == datum


def test_de_reserve_geldt_alleen_in_de_echte_meting(con, cfg_nacht, dagelijks, monkeypatch):
    """In de proefweek wordt geen bijportefeuille overgeslagen, ook niet met hetzelfde krappe budget."""
    markt, datum = _decembernacht(con, monkeypatch, uitgegeven=30.0)
    _nachtloop_met_nepclient(monkeypatch)
    cfg_nacht["modus"] = "proefweek"

    regels = dagelijks._ai_ronde(con, cfg_nacht, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert _beoordeeld(con, datum) == {naam: 2 for naam in ALLE}
    assert not any("overgeslagen" in r for r in regels)


# ------------------------------------------- de nachtloop: na de einddatum


def _eindnacht(con, monkeypatch, vandaag: str, beslisdag: str):
    markt, dagen = _markt_tot(beslisdag)
    _portefeuilles(con, dagen, met_posities="ai_analist")
    _zet_vandaag(monkeypatch, vandaag)
    return markt, dagen[-1]


def test_op_de_einddatum_beoordeelt_de_ai_nog(con, cfg_nacht, dagelijks, monkeypatch):
    assert str(cfg_nacht["meting"]["einde"]) == "2027-11-23"
    markt, datum = _eindnacht(con, monkeypatch, vandaag="2027-11-24", beslisdag="2027-11-23")
    clients = _nachtloop_met_nepclient(monkeypatch)

    regels = dagelijks._ai_ronde(con, cfg_nacht, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert _beoordeeld(con, datum) == {naam: 2 for naam in ALLE}
    assert clients[0].aanroepen, "de laatste beslisdag van de meting telt nog mee"
    assert not any("AI gestopt" in r for r in regels)


def test_na_de_einddatum_beoordeelt_de_ai_niets_en_lopen_de_posities_af(con, cfg_nacht, dagelijks, monkeypatch):
    """Een dag later: geen aanroep, geen kosten, geen aankopen. Wat de houdtijd uit heeft, wordt verkocht."""
    markt, datum = _eindnacht(con, monkeypatch, vandaag="2027-11-25", beslisdag="2027-11-24")
    clients = _nachtloop_met_nepclient(monkeypatch)

    regels = dagelijks._ai_ronde(con, cfg_nacht, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert "AI gestopt: de meting liep tot en met 2027-11-23." in regels
    assert clients[0].aanroepen == []
    assert con.execute("SELECT COUNT(*) n FROM ai_kosten").fetchone()["n"] == 0
    assert _beoordeeld(con, datum) == {}
    assert _wachtrij(con, "ai_analist") == [("AAA", "VERKOOP")], "AAA: houdtijd om; BBB blijft tot zijn houdtijd om is"
    for naam in BIJ:
        assert _wachtrij(con, naam) == []


def test_in_de_proefweek_stopt_de_ai_niet_op_de_einddatum(con, cfg_nacht, dagelijks, monkeypatch):
    markt, datum = _eindnacht(con, monkeypatch, vandaag="2027-11-25", beslisdag="2027-11-24")
    _nachtloop_met_nepclient(monkeypatch)
    cfg_nacht["modus"] = "proefweek"

    regels = dagelijks._ai_ronde(con, cfg_nacht, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert _beoordeeld(con, datum) == {naam: 2 for naam in ALLE}
    assert not any("AI gestopt" in r for r in regels)


# --------------------------------------------------------- de herkansing


class Hapert(NepClient):
    """Laat een rol haperen.

    'mislukt': zoveel pogingen van die rol geven een onbruikbaar antwoord (wel betaald).
    'fout_bij': die poging loopt stuk op het netwerk (niet betaald: er kwam geen antwoord).
    """

    def __init__(self, budget, antwoorden, rol, mislukt=1, manier="afgekapt", fout_bij=None):
        super().__init__(budget, antwoorden)
        self.rol, self.mislukt, self.manier, self.fout_bij = rol, mislukt, manier, fout_bij
        self.keren = 0

    def vraag(self, rol, model, systeem, bericht, max_tokens, schema=None, denken="aan", inspanning="low"):
        if rol == self.rol:
            self.keren += 1
            if self.keren == self.fout_bij:
                raise ConnectionError("netwerk weg")
        a = super().vraag(rol, model, systeem, bericht, max_tokens, schema, denken, inspanning)
        if rol == self.rol and self.keren <= self.mislukt:
            return Antwoord(
                rol=rol, model=model, tekst="" if self.manier == "leeg" else "{", usd=a.usd,
                afgekapt=self.manier == "afgekapt", geweigerd=self.manier == "geweigerd",
            )
        return a


@pytest.fixture
def cfg_ai(cfg_min):
    return copy.deepcopy(cfg_min)


def _een_symbool(con, pf: str):
    dagen = werkdagen(130)
    markt = maak_markt({s: [100.0 + i * 0.1 for i in range(130)] for s in ("AAA", "SPY")}, dagen)
    nieuwe_portefeuille(pf, pf, INSTELLINGEN, gestart_op="2025-01-01").bewaar(con)
    con.commit()
    return markt, dagen[-1]


def _som(con, tabel: str) -> float:
    return float(con.execute(f"SELECT COALESCE(SUM(kosten_usd), 0) s FROM {tabel}").fetchone()["s"])


@pytest.mark.parametrize("manier", ["afgekapt", "geweigerd", "leeg"])
@pytest.mark.parametrize("pf, soort, rol, per_symbool", [
    ("ai_analist", "debat", "nieuws", 4), ("ai_analist", "debat", "bull", 4),
    ("ai_analist", "debat", "bear", 4), ("ai_enkel", "enkel", "enkel", 1),
])
def test_elke_rol_krijgt_een_herkansing(con, cfg_ai, pf, soort, rol, per_symbool, manier):
    """Niet alleen 'risk': ook nieuws, bull, bear en enkel worden een keer opnieuw gevraagd."""
    markt, datum = _een_symbool(con, pf)
    client = Hapert(Budget(con, cfg_ai, vandaag=datum), GOED, rol, manier=manier)

    uit = denk_na(con, cfg_ai, client, markt, pf, datum, ["AAA"], soort=soort, stil=True)

    assert client.keren == 2 and len(client.aanroepen) == per_symbool + 1
    assert uit.compleet and uit.onbruikbaar == []
    rij = con.execute("SELECT kans, kosten_usd, overgeslagen FROM ai_besluiten").fetchone()
    assert rij["kans"] == pytest.approx(0.7) and rij["overgeslagen"] is None
    assert rij["kosten_usd"] == pytest.approx(_som(con, "ai_kosten")), "ook de mislukte poging is betaald en geteld"
    assert uit.kosten_usd == pytest.approx(_som(con, "ai_kosten"))


def test_twee_keer_mislukt_is_geen_kans_en_geen_derde_poging(con, cfg_ai):
    """Bull mislukt twee keer: geen kans, de voorspeller wordt niet meer gevraagd, en die nacht wordt niets gekocht."""
    markt, datum = _een_symbool(con, "ai_analist")
    client = Hapert(Budget(con, cfg_ai, vandaag=datum), GOED, "bull", mislukt=2)

    uit = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)

    assert client.keren == 2, "een herkansing, geen derde poging"
    assert "risk" not in {a["rol"] for a in client.aanroepen}
    assert uit.onbruikbaar == ["AAA"] and not uit.compleet and uit.orders == []
    rij = con.execute("SELECT kans, kosten_usd, overgeslagen FROM ai_besluiten").fetchone()
    assert rij["kans"] is None and "afgekapt" in rij["overgeslagen"]
    assert rij["kosten_usd"] == pytest.approx(_som(con, "ai_kosten")), "beide mislukte pogingen zijn geteld"


def test_zonder_herkansingen_blijft_het_bij_een_poging(con, cfg_ai):
    markt, datum = _een_symbool(con, "ai_analist")
    cfg_ai["ai"]["herkansingen"] = 0
    client = Hapert(Budget(con, cfg_ai, vandaag=datum), GOED, "risk")

    uit = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)

    assert client.keren == 1 and uit.onbruikbaar == ["AAA"]


@pytest.mark.parametrize("pf, soort, rol, betaald", [
    ("ai_analist", "debat", "risk", 4),   # nieuws, bull, bear en de eerste poging van risk
    ("ai_analist", "debat", "bull", 2),   # nieuws en de eerste poging van bull
    ("ai_enkel", "enkel", "enkel", 1),
])
def test_netwerkfout_bij_de_tweede_poging(con, cfg_ai, pf, soort, rol, betaald):
    """De herkansing loopt stuk op het netwerk. Zo werkt het nu (eindreview, ernst laag):

    * het is een fout, dus de nacht is niet compleet en er wordt niets gekocht;
    * de betaalde aanroepen staan in ai_kosten, dus het maandbudget klopt;
    * er komt geen rij in ai_besluiten, dus die kosten ontbreken in de nachtkosten;
    * bij een volgende run wordt het symbool opnieuw gevraagd (en opnieuw betaald).
    """
    markt, datum = _een_symbool(con, pf)
    budget = Budget(con, cfg_ai, vandaag=datum)
    client = Hapert(budget, GOED, rol, fout_bij=2)

    uit = denk_na(con, cfg_ai, client, markt, pf, datum, ["AAA"], soort=soort, stil=True)

    assert len(uit.fouten) == 1 and "ConnectionError" in uit.fouten[0]
    assert not uit.compleet and uit.orders == [] and uit.beoordelingen == {}
    assert con.execute("SELECT COUNT(*) n FROM ai_besluiten").fetchone()["n"] == 0
    assert con.execute("SELECT COUNT(*) n FROM ai_kosten").fetchone()["n"] == betaald
    eerste_run = _som(con, "ai_kosten")
    assert eerste_run > 0 and budget.uitgegeven() == pytest.approx(eerste_run)
    assert uit.kosten_usd == 0.0

    # De volgende run (zonder storing) vraagt het symbool opnieuw en slaat het dan wel op.
    opnieuw = NepClient(Budget(con, cfg_ai, vandaag=datum), GOED)
    uit2 = denk_na(con, cfg_ai, opnieuw, markt, pf, datum, ["AAA"], soort=soort, stil=True)
    assert uit2.al_gedaan == 0 and uit2.compleet
    rij = con.execute("SELECT kans, kosten_usd FROM ai_besluiten").fetchone()
    assert rij["kans"] == pytest.approx(0.7)
    assert _som(con, "ai_kosten") == pytest.approx(eerste_run + rij["kosten_usd"]), \
        "het budget telt beide runs; de beoordeling alleen de tweede"


# ----------------------------- zelfde soort, even belegd: lege crypto_usd


def _waarde(con, pf, d, totaal, posities, crypto):
    con.execute("""INSERT INTO waarde (portefeuille, datum, kas_usd, posities_usd, totaal_usd, crypto_usd)
                   VALUES (?,?,?,?,?,?)""", (pf, d, totaal - posities, posities, totaal, crypto))


def _reeksen(con, dagen, leeg: set[int], sprong: set[int]):
    """ai_analist zit helemaal in crypto en volgt gelijk_crypto precies, behalve op de dagen in 'sprong'.

    Op de dagen in 'leeg' is crypto_usd van ai_analist niet ingevuld (een rij van voor de kolom bestond).
    """
    aandelen = crypto = ai = 1000.0
    for i, d in enumerate(dagen):
        if i:
            aandelen *= 1.001
            crypto *= 1.02
            ai *= 1.02 * (1.5 if i in sprong else 1.0)
        _waarde(con, "gelijk_aandelen", d, aandelen, aandelen, 0.0)
        _waarde(con, "gelijk_crypto", d, crypto, crypto, crypto)
        _waarde(con, "ai_analist", d, ai, ai, None if i in leeg else ai)
    con.commit()


def test_dagen_zonder_crypto_usd_tellen_niet_mee(con):
    """Het rendement van een dag telt alleen als de verdeling van de dag ERVOOR bekend is.

    Dag 1 en 2 volgen op een dag zonder crypto_usd en vallen dus weg, sprong en al.
    De rest volgt de cryptomeetlat precies: het verschil is nul.
    """
    dagen = werkdagen(12)
    _reeksen(con, dagen, leeg={0, 1}, sprong={1, 2})
    v = rapport.vergelijk_zelfde_soort(con, "ai_analist", dagen[-1], dagen)
    assert v is not None and v.tegen == rapport.ZELFDE_SOORT
    assert v.verschil_pp == pytest.approx(0.0, abs=1e-9)


def test_een_sprong_op_een_dag_met_bekende_verdeling_telt_wel(con):
    """De tegenproef: dezelfde sprong op dagen waarvan de vorige dag wel is ingevuld, is wel te zien."""
    dagen = werkdagen(12)
    _reeksen(con, dagen, leeg={0, 1}, sprong={5})
    v = rapport.vergelijk_zelfde_soort(con, "ai_analist", dagen[-1], dagen)
    assert v.verschil_pp > 40.0


def test_een_lege_dag_midden_in_de_reeks_kost_alleen_de_dag_erna(con):
    dagen = werkdagen(12)
    _reeksen(con, dagen, leeg={5}, sprong={6})  # de sprong valt op de dag na de lege rij
    v = rapport.vergelijk_zelfde_soort(con, "ai_analist", dagen[-1], dagen)
    assert v.verschil_pp == pytest.approx(0.0, abs=1e-9)


def test_zonder_enige_crypto_usd_is_er_geen_vergelijking(con):
    dagen = werkdagen(6)
    _reeksen(con, dagen, leeg=set(range(6)), sprong=set())
    assert rapport.vergelijk_zelfde_soort(con, "ai_analist", dagen[-1], dagen) is None


def test_zonder_meetlat_van_een_soort_is_er_geen_vergelijking(con):
    dagen = werkdagen(6)
    _reeksen(con, dagen, leeg=set(), sprong=set())
    con.execute("DELETE FROM waarde WHERE portefeuille = 'gelijk_crypto'")
    con.commit()
    assert rapport.vergelijk_zelfde_soort(con, "ai_analist", dagen[-1], dagen) is None
