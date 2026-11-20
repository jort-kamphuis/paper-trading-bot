"""Tests voor het gratis API-tegoed (bot/tegoed.py) en het budget dat ervan afhangt.

Beslissing van 18 november 2026: ai_opus en ai_opus_rangorde zitten NIET in de echte
meting (in de proefweek gaf Opus bijna dezelfde kansen als de mix) en het AI-budget
is $35 per maand, met en zonder gratis tegoed. De regeling zelf blijft bestaan en
wordt hier getest met de opzet van 17 november ('RUIM': ai_opus erbij, $75 zolang er
gratis tegoed is en anders $35):
* is het laatste bekende tegoed verlopen en is er geen nieuw ingevoerd, dan valt het
  budget terug, doet een portefeuille met 'alleen_met_gratis_tegoed' niet meer mee,
  loopt ai_analist door, en gaat er een melding naar Telegram;
* het weekrapport laat zien welk tegoed er bekend is en wanneer het verloopt.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot import config as conf
from bot import database, tegoed
from bot.ai import budget as budget_mod
from bot.ai import ronde as ronde_mod
from bot.ai.budget import Budget
from bot.ai.client import ClaudeClient, NepClient
from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN, maak_markt, werkdagen

ECHT = conf.laad()
PROJECT = Path(__file__).resolve().parent.parent


def _met_opus(cfg: dict) -> dict:
    """De opzet van 17 november: ai_opus en zijn rangorde erbij, $75 zolang er gratis tegoed is."""
    cfg = copy.deepcopy(cfg)
    cfg["ai"]["portefeuilles"].append(
        {"naam": "ai_opus", "soort": "debat", "model": "claude-opus-5-5", "alleen_met_gratis_tegoed": True})
    cfg["ai"]["afgeleid"].append({"naam": "ai_opus_rangorde", "soort": "rangorde", "bron": "ai_opus"})
    cfg["ai"]["budget"]["max_usd_per_maand"] = 75.0
    cfg["ai"]["budget"]["zonder_gratis_tegoed_usd"] = 35.0
    return cfg


RUIM = _met_opus(ECHT)
VANDAAG = datetime.now(timezone.utc).date()


def _laad_dagelijks(naam: str):
    spec = importlib.util.spec_from_file_location(naam, PROJECT / "scripts" / "dagelijks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _met_tegoed(cfg: dict, *regels: tuple[float, date, date]) -> dict:
    cfg = copy.deepcopy(cfg)
    cfg["tegoed"] = {"waarschuw_dagen_vooraf": 5, "gratis": [
        {"bedrag_usd": b, "toegekend": van.isoformat(), "verloopt": tot.isoformat()} for b, van, tot in regels]}
    return cfg


def _kosten(con, datum: str, usd: float) -> None:
    con.execute(
        "INSERT INTO ai_kosten (datum, maand, rol, model, tokens_in, tokens_uit, kosten_usd) VALUES (?,?,?,?,?,?,?)",
        (datum, datum[:7], "risk", "m", 0, 0, usd))
    con.commit()


# ------------------------------------------------------------ de instellingen


def test_de_instellingen_zoals_vastgelegd():
    budget = ECHT["ai"]["budget"]
    assert budget["max_usd_per_maand"] == 35 and budget["zonder_gratis_tegoed_usd"] == 35
    assert tegoed.afhankelijk(ECHT) == [] and tegoed.afhankelijk(RUIM) == ["ai_opus"]
    alle = tegoed.lijst(ECHT)
    assert [(t.bedrag_usd, t.toegekend.isoformat(), t.verloopt.isoformat()) for t in alle] == [
        (100.0, "2026-11-13", "2026-11-20"), (100.0, "2026-11-17", "2026-12-20")]
    assert tegoed.laatste_verloop(ECHT) == date(2026, 12, 20) and tegoed.terugval_usd(ECHT) == 35.0
    assert ECHT["tegoed"]["waarschuw_dagen_vooraf"] == 5


def test_de_hoofdvraag_is_niet_veranderd():
    m = ECHT["meting"]
    assert m["ai_portefeuille"] == "ai_rangorde" and m["ai_bron"] == "ai_analist"
    assert len(ECHT["ai_watchlist"]) == 30
    rangorde = [p for p in ECHT["ai"]["afgeleid"] if p["naam"] == "ai_rangorde"][0]
    assert rangorde["bron"] == "ai_analist"
    # ai_analist zelf hangt niet van het gratis tegoed af en draait op de gewone rollen.
    analist = [p for p in ECHT["ai"]["portefeuilles"] if p["naam"] == "ai_analist"][0]
    assert "model" not in analist and "alleen_met_gratis_tegoed" not in analist


def test_opus_gebruikt_dezelfde_prompt_en_dezelfde_symbolen(con, cfg_min):
    """ai_opus is het gewone debat met een ander model: dezelfde instructies, dezelfde 30 symbolen."""
    from bot.ai.analist import AIAnalist

    cfg_min = _met_opus(cfg_min)
    opus = [p for p in ronde_mod.ai_portefeuilles(cfg_min) if p["naam"] == "ai_opus"][0]
    client = NepClient(Budget(con, cfg_min))
    gewoon = AIAnalist(con, cfg_min, client, soort="debat")
    met_opus = AIAnalist(con, cfg_min, client, model_override=opus["model"], soort=opus["soort"])
    assert {gewoon._model(r) for r in ("bull", "bear", "risk")} == {"claude-sonnet-5-5"}
    assert {met_opus._model(r) for r in ("nieuws", "bull", "bear", "risk")} == {"claude-opus-5-5"}
    assert "claude-opus-5-5" in cfg_min["prijzen"], "zonder prijs kan de bot Opus niet afrekenen"


# ---------------------------------------------------------------- geldigheid


def test_geldig_tot_de_dag_voor_de_vervaldatum():
    assert tegoed.is_geldig(ECHT, date(2026, 11, 17)) and tegoed.is_geldig(ECHT, date(2026, 11, 20))
    assert tegoed.is_geldig(ECHT, date(2026, 12, 19)), "de laatste geldige dag"
    assert not tegoed.is_geldig(ECHT, date(2026, 12, 20)), "het verloopt om 00:00 UTC op die dag"
    assert not tegoed.is_geldig(ECHT, date(2027, 3, 1))
    assert not tegoed.is_geldig(ECHT, date(2026, 11, 12)), "nog niet toegekend"


def test_zonder_tegoed_in_de_instellingen_speelt_het_niet(cfg_min):
    """Staat er geen 'tegoed' (zoals in de tests), dan is het budget gewoon het maandbudget."""
    assert "tegoed" not in cfg_min and not tegoed.ingesteld(cfg_min)
    assert tegoed.is_geldig(cfg_min, date(2030, 1, 1))
    assert tegoed.melding(None, cfg_min, date(2030, 1, 1)) is None and tegoed.als_tekst(None, cfg_min, VANDAAG) == ""


def test_een_lege_lijst_betekent_geen_gratis_tegoed(cfg_min):
    leeg = _met_tegoed(cfg_min)
    assert tegoed.ingesteld(leeg) and not tegoed.is_geldig(leeg, date(2026, 12, 1))


def test_nieuw_tegoed_invoeren_maakt_het_weer_geldig(cfg_min):
    cfg = _met_tegoed(cfg_min, (100, date(2026, 11, 17), date(2026, 12, 20)))
    assert not tegoed.is_geldig(cfg, date(2026, 12, 22))
    cfg = _met_tegoed(cfg_min, (100, date(2026, 11, 17), date(2026, 12, 20)), (100, date(2026, 12, 17), date(2027, 1, 20)))
    assert tegoed.is_geldig(cfg, date(2026, 12, 22)) and tegoed.laatste_verloop(cfg) == date(2027, 1, 20)


# ------------------------------------------------------------------ het budget


def test_in_de_echte_meting_is_het_budget_35_met_en_zonder_gratis_tegoed(con):
    """Besluit van 18 november 2026. Ook wat in de gratis dagen van de maand is uitgegeven, telt gewoon mee."""
    for dag in ("2026-11-24", "2026-12-19", "2026-12-20", "2027-01-15"):
        b = Budget(con, ECHT, vandaag=dag)
        assert b.max_usd == 35.0 and b.resterend() == 35.0, dag
    for dag in range(1, 20):
        _kosten(con, f"2026-12-{dag:02d}", 1.00)            # $19 terwijl het tegoed geldig was
    assert Budget(con, ECHT, vandaag="2026-12-19").resterend() == pytest.approx(16.0)
    na = Budget(con, ECHT, vandaag="2026-12-21")
    assert na.max_usd == 35.0 and na.resterend() == pytest.approx(16.0), "het plafond blijft $35 voor de hele maand"


def test_het_budget_valt_terug_naar_35_zodra_het_tegoed_verlopen_is(con):
    assert tegoed.budget_plafond(con, RUIM, date(2026, 12, 19)) == 75.0
    assert Budget(con, RUIM, vandaag="2026-12-19").max_usd == 75.0
    assert tegoed.budget_plafond(con, RUIM, date(2027, 1, 15)) == 35.0
    januari = Budget(con, RUIM, vandaag="2027-01-15")
    assert januari.max_usd == 35.0 and januari.resterend() == 35.0


def test_wat_in_de_gratis_dagen_van_de_maand_is_uitgegeven_telt_niet_mee(con):
    """December: tot en met de 19e gratis. Daarna blijft er $35 voor de rest van de maand, ook al kostte de
    eerste helft meer. Zo raakt het budget niet in een klap 'op' en loopt ai_analist door."""
    for dag in range(1, 20):
        _kosten(con, f"2026-12-{dag:02d}", 2.00)            # $38 terwijl het tegoed geldig was
    op_de_20e = Budget(con, RUIM, vandaag="2026-12-20")
    assert op_de_20e.uitgegeven() == pytest.approx(38.0)
    assert op_de_20e.max_usd == pytest.approx(35.0 + 38.0) and op_de_20e.resterend() == pytest.approx(35.0)
    assert not op_de_20e.is_op()
    # Wat daarna wordt uitgegeven, gaat wel van de $35 af.
    _kosten(con, "2026-12-21", 5.00)
    _kosten(con, "2026-12-22", 5.00)
    assert Budget(con, RUIM, vandaag="2026-12-23").resterend() == pytest.approx(25.0)
    _kosten(con, "2026-12-28", 25.00)
    assert Budget(con, RUIM, vandaag="2026-12-29").is_op(), "meer dan $35 zelf betaald: op"
    # In januari begint de teller opnieuw, met $35.
    assert Budget(con, RUIM, vandaag="2027-01-04").resterend() == pytest.approx(35.0)


def test_het_plafond_wordt_nooit_hoger_dan_het_maandbudget(con):
    """Kostte het gratis deel van de maand al meer dan $40, dan blijft het plafond het maandbudget van $75."""
    for dag in range(1, 20):
        _kosten(con, f"2026-12-{dag:02d}", 2.50)            # $47,50 terwijl het tegoed geldig was
    op_de_20e = Budget(con, RUIM, vandaag="2026-12-20")
    assert op_de_20e.max_usd == 75.0 and op_de_20e.resterend() == pytest.approx(27.50)


def test_met_nieuw_tegoed_is_het_budget_weer_75(con, cfg_min):
    cfg_min = _met_opus(cfg_min)
    cfg = _met_tegoed(cfg_min, (100, date(2026, 11, 17), date(2026, 12, 20)), (100, date(2026, 12, 18), date(2027, 1, 20)))
    assert Budget(con, cfg, vandaag="2026-12-21").max_usd == 75.0
    assert Budget(con, cfg, vandaag="2027-01-20").max_usd == 35.0


def test_het_lage_bedrag_is_nooit_hoger_dan_het_gewone(con, cfg_min):
    cfg = _met_tegoed(cfg_min)
    cfg["ai"]["budget"]["max_usd_per_maand"] = 20.0
    assert tegoed.budget_plafond(con, cfg, date(2026, 12, 1)) == 20.0


def test_alleen_opus_hangt_van_het_gratis_tegoed_af():
    for p in ronde_mod.ai_portefeuilles(RUIM):
        geldig = tegoed.mag_draaien(RUIM, p, date(2026, 12, 19))
        verlopen = tegoed.mag_draaien(RUIM, p, date(2026, 12, 20))
        assert geldig and verlopen == (p["naam"] != "ai_opus"), p["naam"]


def test_in_de_echte_meting_hangt_geen_portefeuille_van_het_gratis_tegoed_af():
    namen = [p["naam"] for p in ronde_mod.ai_portefeuilles(ECHT)]
    assert namen == ["ai_analist", "ai_controle_haiku", "ai_enkel"]
    for p in ronde_mod.ai_portefeuilles(ECHT):
        assert tegoed.mag_draaien(ECHT, p, date(2026, 12, 19)) and tegoed.mag_draaien(ECHT, p, date(2027, 6, 1))


# ------------------------------------------------------------- de meldingen


def test_melding_vijf_dagen_voor_het_verlopen_en_bij_het_terugvallen(con):
    assert tegoed.melding(con, RUIM, date(2026, 12, 14)) is None, "zes dagen ervoor: nog niets"
    vooraf = tegoed.melding(con, RUIM, date(2026, 12, 15))
    assert vooraf.startswith("Herinnering: het gratis API-tegoed verloopt op zondag 20 december 2026 om 00:00 UTC.")
    assert "credits kopen is pas nodig voor die datum" in vooraf and "van $75,00 naar $35,00 per maand" in vooraf
    assert "(ai_opus stopt, ai_analist loopt door)." in vooraf
    assert tegoed.melding(con, RUIM, date(2026, 12, 16)) is None, "een keer is genoeg"

    terug = tegoed.melding(con, RUIM, date(2026, 12, 20))
    assert terug.startswith("LET OP: het gratis API-tegoed is volgens config.yaml verlopen op zondag 20 december 2026")
    assert "teruggevallen van $75,00 naar $35,00 per maand" in terug
    assert "ai_opus doet niet meer mee, ai_analist loopt door" in terug
    assert tegoed.melding(con, RUIM, date(2026, 12, 21)) is None and tegoed.melding(con, RUIM, date(2027, 2, 1)) is None


def test_de_meldingen_in_de_echte_meting_zonder_opus(con):
    """Budget $35 met en zonder tegoed: de ronde verandert niet, maar je betaalt vanaf dan zelf. Dat wordt gemeld."""
    assert tegoed.melding(con, ECHT, date(2026, 12, 14)) is None
    vooraf = tegoed.melding(con, ECHT, date(2026, 12, 15))
    assert vooraf.startswith("Herinnering: het gratis API-tegoed verloopt op zondag 20 december 2026 om 00:00 UTC.")
    assert vooraf.endswith("Zo nee: credits kopen is pas nodig voor die datum. Het AI-budget blijft dan $35,00 per "
                           "maand en aan de meting verandert niets.")
    assert "opus" not in vooraf and "valt" not in vooraf

    terug = tegoed.melding(con, ECHT, date(2026, 12, 20))
    assert terug.startswith("LET OP: het gratis API-tegoed is volgens config.yaml verlopen op zondag 20 december 2026")
    assert "Het AI-budget blijft $35,00 per maand en aan de meting verandert niets." in terug
    assert "dan betaal je vanaf nu zelf: zorg dat er credits zijn, met automatisch opwaarderen uit." in terug
    assert "opus" not in terug and "teruggevallen" not in terug
    assert tegoed.melding(con, ECHT, date(2026, 12, 21)) is None


def test_ook_als_de_ronde_de_eerste_dag_miste_komt_de_melding_nog(con):
    assert tegoed.melding(con, ECHT, date(2026, 12, 18)).startswith("Herinnering")
    assert tegoed.melding(con, ECHT, date(2026, 12, 24)).startswith("LET OP")


def test_geen_melding_over_het_eerste_tegoed_als_er_al_een_tweede_is(con):
    """Het tegoed van 13 november verloopt op de 20e, maar dat van 17 november loopt door: niets aan de hand."""
    for dag in (date(2026, 11, 17), date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 25)):
        assert tegoed.melding(con, ECHT, dag) is None, dag


# --------------------------------------------------------- het weekrapport


def test_de_tekst_voor_het_weekrapport_met_geldig_tegoed(con):
    _kosten(con, "2026-11-19", 3.00)     # telt nog bij het eerste tegoed
    _kosten(con, "2026-11-24", 2.75)
    _kosten(con, "2026-11-27", 2.25)
    tekst = tegoed.als_tekst(con, ECHT, date(2026, 11, 29))
    assert tekst.startswith("Gratis API-tegoed (uit config.yaml; de bot kan het saldo niet zelf opvragen):")
    assert "$100,00, toegekend op dinsdag 17 november 2026, verloopt op zondag 20 december 2026 om 00:00 UTC (nog 21 dagen)" in tekst
    # Het tweede tegoed telt vanaf het moment dat het eerste verliep.
    assert "Sinds vrijdag 20 november 2026 heeft de bot $5,00 geboekt in deze database" in tekst
    assert "naar schatting is er nog ongeveer $95,00 over" in tekst
    assert "13 november" not in tekst, "het verlopen tegoed staat er niet meer bij"
    assert "Kijk rond de vervaldatum in de console of er nieuw tegoed is bijgekomen." in tekst
    assert tekst.endswith("Zo nee: dan betaal je vanaf die dag zelf. Het AI-budget blijft dan $35,00 per maand en "
                          "aan de meting verandert niets.")
    assert "opus" not in tekst
    # Met de ruime opzet staat erbij wat er dan wegvalt.
    ruim = tegoed.als_tekst(con, RUIM, date(2026, 11, 29))
    assert ruim.endswith("Het AI-budget valt dan terug van $75,00 naar $35,00 per maand (ai_opus stopt, ai_analist "
                         "loopt door).")


def test_de_tekst_voor_het_weekrapport_zonder_geldig_tegoed(con):
    tekst = tegoed.als_tekst(con, ECHT, date(2027, 1, 10))
    assert "geen geldig gratis tegoed bekend: het laatste verliep op zondag 20 december 2026" in tekst
    assert "Het AI-budget is $35,00 voor deze maand ($35,00 per maand zonder gratis tegoed). Je betaalt de AI-kosten nu zelf." in tekst
    assert "opus" not in tekst
    ruim = tegoed.als_tekst(con, RUIM, date(2027, 1, 10))
    assert "$35,00 voor deze maand" in ruim and "en ai_opus doet niet mee. Je betaalt de AI-kosten nu zelf." in ruim


def test_het_weekrapport_laat_het_tegoed_zien(con, cfg_min):
    from bot import weekrapport

    dagen = werkdagen(12, start="2026-11-23")
    for naam in ("momentum", "gelijk_alle"):
        nieuwe_portefeuille(naam, naam, INSTELLINGEN, gestart_op=dagen[0]).bewaar(con)
        for i, d in enumerate(dagen):
            con.execute("INSERT INTO waarde (portefeuille, datum, kas_usd, posities_usd, totaal_usd) VALUES (?,?,?,?,?)",
                        (naam, d, 0.0, 1000.0 + i, 1000.0 + i))
    for d in dagen:
        database.bewaar_koersen(con, [{"symbool": "SPY", "datum": d, "open": 1.0, "hoog": 1.0, "laag": 1.0,
                                       "slot": 1.0, "volume": 1.0, "valuta": "USD", "bron": "test"}])
    con.commit()
    cfg = _met_tegoed(cfg_min, (100, VANDAAG - timedelta(days=3), VANDAAG + timedelta(days=30)))
    cfg["modus"] = "echt"
    week = weekrapport.maak(con, cfg, dagen[-1], client=None)
    assert "Gratis API-tegoed (uit config.yaml; de bot kan het saldo niet zelf opvragen):" in week.tekst
    assert "(nog 30 dagen)" in week.tekst and "naar schatting is er nog ongeveer $100,00 over" in week.tekst


# ------------------------------------------------- de nachtronde (_ai_ronde)


GOED = {
    "nieuws": {"samenvatting": "rustig", "belangrijkste_risico": "geen", "gebeurtenis_binnenkort": False},
    "bull": {"argument": "a", "sterkste_punt": "b", "overtuiging": 0.5},
    "bear": {"argument": "c", "sterkste_punt": "d", "overtuiging": 0.5},
    "risk": {"onderbouwing": "x", "belangrijkste_risico": "y", "kans": 0.47, "verwacht_extra_rendement_pct": 0.2},
    "enkel": {"onderbouwing": "x", "belangrijkste_risico": "y", "kans": 0.47, "verwacht_extra_rendement_pct": 0.2},
}
VIER = ("ai_analist", "ai_controle_haiku", "ai_enkel", "ai_opus")
AFGELEID = ("ai_rangorde", "ai_rangorde_omgekeerd", "ai_consensus", "ai_opus_rangorde")


def _nacht(con, cfg, monkeypatch, laatste: str = "2027-01-15"):
    dagen = [d for d in werkdagen(800, start="2025-01-01") if d <= laatste][-60:]
    assert dagen[-1] == laatste
    markt = maak_markt({s: [100.0 + i * 0.1 for i in range(len(dagen))] for s in ("AAA", "BBB", "SPY")}, dagen)
    for naam in VIER + AFGELEID:
        nieuwe_portefeuille(naam, naam, INSTELLINGEN, gestart_op=dagen[0]).bewaar(con)
    con.commit()

    def dicht(self):
        raise AssertionError("de echte API mag in een test nooit worden aangeroepen")

    monkeypatch.setattr(ClaudeClient, "_verbinding", dicht)
    monkeypatch.setattr(conf, "sleutel", lambda naam: "nep-sleutel-voor-de-test")
    clients: list[NepClient] = []

    def maak(con, cfg, vandaag=None):
        clients.append(NepClient(Budget(con, cfg, vandaag=vandaag), GOED))
        return clients[-1]

    monkeypatch.setattr(ronde_mod, "maak_client", maak)
    return markt, dagen[-1], clients


def _cfg_nacht(cfg_min, met_opus: bool = True) -> dict:
    """De instellingen voor een testnacht. Standaard de ruime opzet (met ai_opus), om de regeling te testen."""
    cfg = _met_opus(cfg_min) if met_opus else copy.deepcopy(cfg_min)
    cfg["modus"] = "echt"
    cfg["ai_watchlist"] = ["AAA", "BBB"]
    return cfg


def _beoordeeld(con, datum: str) -> dict[str, int]:
    return {r["portefeuille"]: r["n"] for r in con.execute(
        "SELECT portefeuille, COUNT(*) n FROM ai_besluiten WHERE datum = ? GROUP BY portefeuille", (datum,))}


def _wachtrij(con, naam: str) -> list[tuple[str, str]]:
    return [(r["symbool"], r["actie"]) for r in con.execute(
        "SELECT symbool, actie FROM wachtrij WHERE portefeuille = ? ORDER BY symbool", (naam,))]


@pytest.mark.parametrize("dagen_tot_verlopen", [20, -2])
def test_de_echte_meting_draait_met_en_zonder_gratis_tegoed_hetzelfde(con, cfg_min, monkeypatch, dagen_tot_verlopen):
    """Zonder ai_opus: drie AI-portefeuilles, geen aanroep op Opus, budget $35, en niets wordt overgeslagen."""
    dagelijks = _laad_dagelijks(f"script_dagelijks_tegoed_0_{dagen_tot_verlopen + 2}")
    cfg = _met_tegoed(_cfg_nacht(cfg_min, met_opus=False),
                      (100, VANDAAG - timedelta(days=40), VANDAAG + timedelta(days=dagen_tot_verlopen)))
    markt, datum, clients = _nacht(con, cfg, monkeypatch)

    regels = dagelijks._ai_ronde(con, cfg, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert _beoordeeld(con, datum) == {"ai_analist": 2, "ai_controle_haiku": 2, "ai_enkel": 2}
    assert not any("overgeslagen" in r for r in regels)
    assert not [a for a in clients[0].aanroepen if a["model"] == "claude-opus-5-5"]
    assert clients[0].budget.max_usd == pytest.approx(35.0)
    assert _wachtrij(con, "ai_rangorde") == [("AAA", "KOOP"), ("BBB", "KOOP")]
    assert _wachtrij(con, "ai_opus_rangorde") == [], "bestaat niet in de echte meting en doet dus niets"


def test_met_gratis_tegoed_draaien_alle_vier_en_kiest_de_rangorde_van_opus(con, cfg_min, monkeypatch):
    dagelijks = _laad_dagelijks("script_dagelijks_tegoed_1")
    cfg = _met_tegoed(_cfg_nacht(cfg_min), (100, VANDAAG - timedelta(days=5), VANDAAG + timedelta(days=20)))
    markt, datum, clients = _nacht(con, cfg, monkeypatch)

    regels = dagelijks._ai_ronde(con, cfg, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert _beoordeeld(con, datum) == {naam: 2 for naam in VIER}
    assert not any("overgeslagen" in r for r in regels)
    opus = [a for a in clients[0].aanroepen if a["model"] == "claude-opus-5-5"]
    assert len(opus) == 8 and {a["rol"] for a in opus} == {"nieuws", "bull", "bear", "risk"}, "alle rollen op Opus"
    # De rangorde van Opus leest de kansen van ai_opus, zonder eigen aanroepen.
    assert _wachtrij(con, "ai_opus_rangorde") == [("AAA", "KOOP"), ("BBB", "KOOP")]
    assert _wachtrij(con, "ai_rangorde") == [("AAA", "KOOP"), ("BBB", "KOOP")]
    assert set(_beoordeeld(con, datum)) == set(VIER), "de afgeleiden beoordelen zelf niets"


def test_zonder_gratis_tegoed_valt_opus_weg_en_loopt_de_rest_door(con, cfg_min, monkeypatch):
    dagelijks = _laad_dagelijks("script_dagelijks_tegoed_2")
    cfg = _met_tegoed(_cfg_nacht(cfg_min), (100, VANDAAG - timedelta(days=40), VANDAAG - timedelta(days=2)))
    markt, datum, clients = _nacht(con, cfg, monkeypatch)

    regels = dagelijks._ai_ronde(con, cfg, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert _beoordeeld(con, datum) == {"ai_analist": 2, "ai_controle_haiku": 2, "ai_enkel": 2}
    assert "ai_opus overgeslagen: er is volgens config.yaml geen geldig gratis tegoed." in regels
    assert not [a for a in clients[0].aanroepen if a["model"] == "claude-opus-5-5"], "geen enkele aanroep op Opus"
    assert con.execute("SELECT COUNT(*) n FROM ai_kosten WHERE model = 'claude-opus-5-5'").fetchone()["n"] == 0
    # De hoofdvraag merkt er niets van; de rangorde van Opus doet niets zonder kansen.
    assert _wachtrij(con, "ai_rangorde") == [("AAA", "KOOP"), ("BBB", "KOOP")]
    assert _wachtrij(con, "ai_opus_rangorde") == []
    # Het budget van deze nacht is het lage bedrag.
    assert clients[0].budget.max_usd == pytest.approx(35.0)


def test_bij_een_krap_budget_valt_opus_als_eerste_af(con, cfg_min, monkeypatch):
    """Met gratis tegoed en $75: is er te weinig over om ai_analist de maand te laten afmaken EN Opus te betalen,
    dan wordt Opus overgeslagen. De goedkope bijportefeuilles passen nog en draaien gewoon; ai_analist altijd."""
    dagelijks = _laad_dagelijks("script_dagelijks_tegoed_3")
    cfg = _cfg_nacht(cfg_min)                      # ruime opzet zonder 'tegoed': het budget is $75, ai_opus mag draaien
    assert cfg["ai"]["budget"]["max_usd_per_maand"] == 75
    markt, datum, clients = _nacht(con, cfg, monkeypatch, laatste="2026-12-02")
    # Wat een nacht van elk tot nu toe kostte. Voor ai_analist precies wat de nep-client in deze test rekent
    # (twee symbolen, vier rollen), zodat zijn gemiddelde door de nacht van vandaag niet verschuift.
    for d in ("2026-11-30", "2026-12-01"):
        for naam, usd in (("ai_analist", 0.0244), ("ai_controle_haiku", 0.10), ("ai_enkel", 0.40), ("ai_opus", 3.00)):
            con.execute(
                """INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kosten_usd, nieuws_tot, gemaakt_op)
                   VALUES (?, 'AAA', ?, 'NIETS', ?, 'x', 'x')""", (naam, d, usd))

    class Vandaag(date):
        @classmethod
        def today(cls):
            return cls.fromisoformat("2026-12-03")

    monkeypatch.setattr(budget_mod, "date", Vandaag)
    reserve = Budget(con, cfg).reserve_voor("ai_analist", datum, cfg)
    assert 0.3 < reserve < 1.0
    # Zoveel is er al uit: er blijft de reserve voor ai_analist plus $2 over.
    _kosten(con, "2026-12-01", 75.0 - reserve - 2.0)

    regels = dagelijks._ai_ronde(con, cfg, markt, datum, nieuw_nieuws=False, ingehaald=1)

    assert _beoordeeld(con, datum) == {"ai_analist": 2, "ai_controle_haiku": 2, "ai_enkel": 2}
    assert any(r.startswith("ai_opus overgeslagen: het budget") and "ai_analist de maand te laten afmaken" in r
               for r in regels)
    assert not any(r.startswith(("ai_controle_haiku overgeslagen", "ai_enkel overgeslagen")) for r in regels)
    assert not [a for a in clients[0].aanroepen if a["model"] == "claude-opus-5-5"]


# ---------------------------------------------- de hele ronde: de melding gaat uit


def test_de_ronde_stuurt_de_melding_over_het_verlopen_een_keer(tmp_path, monkeypatch):
    from bot import modus

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    dagelijks = _laad_dagelijks("script_dagelijks_tegoed_4")
    cfg = _met_tegoed(modus.laad(nu=datetime(2026, 11, 25, 4, 0, tzinfo=timezone.utc), overlay=modus.lees_proefweek(),
                                 omgeschakeld=True),
                      (100, VANDAAG - timedelta(days=40), VANDAAG - timedelta(days=1)))
    assert cfg["modus"] == "echt"
    monkeypatch.setattr(modus, "laad", lambda *a, **k: copy.deepcopy(cfg))

    db = tmp_path / "kopie.db"
    con = database.verbind(db)
    rijen = [{"symbool": s, "datum": d, "open": 100.0 + i, "hoog": 101.0 + i, "laag": 99.0 + i, "slot": 100.0 + i,
              "volume": 1000.0, "valuta": "USD", "bron": "test"}
             for s in conf.alle_symbolen(ECHT)
             for i, d in enumerate(["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"])]
    database.bewaar_koersen(con, rijen)
    database.bewaar_wisselkoersen(con, [{"datum": "2026-11-13", "eurusd": 1.10}])
    con.commit()
    con.close()

    def args():
        return argparse.Namespace(geen_data=True, jaren=2.0, tot=None, db=str(db), start=None, geen_ai=True,
                                  telegram=False, lokaal=True, weekrapport="nee", ai_op_kopie=False,
                                  omschakelen="auto", herkansing="nee", gepland="nee")

    eerste = args()
    code, _ = dagelijks.ronde(eerste)
    assert code == 0
    over_tegoed = [b for b in eerste.berichten if b.startswith("LET OP: het gratis API-tegoed is volgens config.yaml verlopen")]
    assert len(over_tegoed) == 1
    assert "Het AI-budget blijft $35,00 per maand en aan de meting verandert niets." in over_tegoed[0]
    assert "dan betaal je vanaf nu zelf" in over_tegoed[0] and "opus" not in over_tegoed[0]

    # De portefeuilles van de echte meting na deze eerste ronde: geen Opus, en naast de hoofdvraag
    # (ai_rangorde) en de meetlatten 11 verkennende. De winnaarsgrens is dus 0,05 gedeeld door 11.
    from bot import meting, rapport

    con = database.verbind(db)
    namen = [r["naam"] for r in con.execute("SELECT naam FROM portefeuilles WHERE actief = 1 ORDER BY naam")]
    assert not any("opus" in n for n in namen), namen
    verkennend = [n for n in namen if n not in rapport.MEETLATTEN and n != cfg["meting"]["ai_portefeuille"]]
    assert len(verkennend) == 11, verkennend
    kal = [r["datum"] for r in con.execute("SELECT DISTINCT datum FROM koersen WHERE symbool = 'SPY' ORDER BY datum")]
    v = meting.verkennend(con, cfg, kal[-1], kal, cfg["meting"]["ai_portefeuille"], set())
    assert v["aantal"] == 11 and v["grens_p"] == pytest.approx(0.05 / 11)
    con.close()

    tweede = args()
    dagelijks.ronde(tweede)
    assert not [b for b in tweede.berichten if "gratis API-tegoed" in b], "de melding gaat een keer uit"
