"""Tests voor de afgeleide AI-portefeuilles (bot/ai/afgeleid.py).

ai_rangorde (de hoofdvraag), ai_rangorde_omgekeerd (de controle) en ai_consensus
handelen op kansen die er al zijn. Ze mogen de API nooit aanroepen.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

from bot import config as conf
from bot.ai import afgeleid
from bot.ai import ronde as ronde_mod
from bot.ai.analist import Beoordeling
from bot.ai.budget import Budget
from bot.ai.client import ClaudeClient, NepClient
from bot.ai.kostenprognose import GEEN_KOERS
from bot.portefeuille import Positie, nieuwe_portefeuille
from tests.conftest import INSTELLINGEN, maak_markt, werkdagen

ECHT = conf.laad()
PROJECT = Path(__file__).resolve().parent.parent
REGELS = ECHT["ai"]["beslisregel"]
BASIS = ECHT["ai"]["basiskansen"]
DAG = "2027-01-15"


def b(symbool: str, kans: float, verwacht: float = 0.0, schommeling: float = 1.0, basis: float | None = None,
      probleem: str | None = None, datum: str = DAG) -> Beoordeling:
    """Een beoordeling zoals de AI hem opslaat. Zonder 'basis' geldt de basiskans uit de instellingen."""
    kenmerken = {"schommeling_per_dag_28d_pct": schommeling}
    if basis is not None:
        kenmerken["basiskans"] = basis
    return Beoordeling(symbool=symbool, datum=datum, referentie="", kans=kans, verwacht_pct=verwacht,
                       stappen={"kenmerken": kenmerken}, probleem=probleem)


def positie(symbool: str, gekocht_op: str, bevestigd_op: str | None = None) -> Positie:
    return Positie(symbool=symbool, aantal=1.0, kostprijs_usd=100.0, gekocht_op=gekocht_op, bevestigd_op=bevestigd_op)


def kies(beoordelingen, posities=None, kopen=(), verkopen=(), **extra):
    return afgeleid.kies_rangorde(
        {x.symbool: x for x in beoordelingen}, posities or {}, set(kopen), set(verkopen),
        DAG, REGELS, 10, BASIS, **extra)


def gekocht(uitkomst) -> list[str]:
    return [o.symbool for o in uitkomst.orders if o.actie == "KOOP"]


def verkocht(uitkomst) -> list[str]:
    return [o.symbool for o in uitkomst.orders if o.actie == "VERKOOP"]


AANDELEN = [f"A{i:02d}" for i in range(20)]


# --------------------------------------------------------------- de rangorde


def test_basiskansen_zoals_vastgelegd():
    assert BASIS == {"aandeel_tegen_spy": 0.46, "crypto_tegen_btc": 0.38, "spy_tegen_kas": 0.53, "btc_tegen_kas": 0.45}


def test_rangorde_is_altijd_vol_ook_zonder_voorsprong():
    """Alle kansen op of onder de basiskans: de gewone AI koopt niets, de rangorde koopt er toch 10."""
    lijst = [b(s, 0.46 - 0.01 * (i % 3)) for i, s in enumerate(AANDELEN[:12])]
    assert all(x.kans < REGELS["koop_kans"] for x in lijst)
    uit = kies(lijst)
    assert len(gekocht(uit)) == 10 and not verkocht(uit)
    # De twee met de laagste kans (en de laatste naam) vallen af.
    assert set(AANDELEN[:12]) - set(gekocht(uit)) == {"A08", "A11"}


def test_rangorde_gaat_op_voorsprong_niet_op_de_kans_zelf():
    """Een aandeel op 0,50 (basis 0,46) staat boven SPY op 0,55 (basis 0,53) en bitcoin op 0,48 (basis 0,45)."""
    rang = afgeleid.rangschik(
        {x.symbool: x for x in [b("SPY", 0.55), b("AAPL", 0.50), b("BTC/USDT", 0.48), b("ETH/USDT", 0.40)]},
        REGELS, BASIS)
    assert [s for s, _ in rang] == ["AAPL", "BTC/USDT", "ETH/USDT", "SPY"]
    assert [v for _, v in rang] == pytest.approx([0.04, 0.03, 0.02, 0.02])


def test_de_basiskans_die_het_model_zag_gaat_voor():
    """Staat de basiskans bij de beoordeling opgeslagen, dan telt die, niet die uit de instellingen van nu."""
    rang = dict(afgeleid.rangschik({"AAPL": b("AAPL", 0.50, basis=0.49), "MSFT": b("MSFT", 0.49)}, REGELS, BASIS))
    assert rang["AAPL"] == pytest.approx(0.01) and rang["MSFT"] == pytest.approx(0.03)


def test_gelijke_voorsprong_dan_verwachting_per_schommeling_dan_naam():
    lijst = [b("CCC", 0.50, verwacht=1.0, schommeling=2.0),   # 0,5 per schommeling
             b("BBB", 0.50, verwacht=1.0, schommeling=1.0),   # 1,0
             b("AAA", 0.50, verwacht=1.0, schommeling=2.0),   # 0,5, en de eerste naam
             b("DDD", 0.51)]
    assert [s for s, _ in afgeleid.rangschik({x.symbool: x for x in lijst}, REGELS, BASIS)] == ["DDD", "BBB", "AAA", "CCC"]
    # Twee keer dezelfde kansen geeft twee keer dezelfde keuze.
    assert gekocht(kies(lijst)) == gekocht(kies(list(reversed(lijst))))


def test_omgekeerd_is_het_spiegelbeeld():
    lijst = [b(s, 0.40 + 0.01 * i) for i, s in enumerate(AANDELEN)]  # A00 laagste, A19 hoogste
    boven, onder = gekocht(kies(lijst)), gekocht(kies(lijst, omgekeerd=True))
    assert set(boven) == set(AANDELEN[10:]) and set(onder) == set(AANDELEN[:10])
    assert not set(boven) & set(onder)
    assert boven[0] == "A19" and onder[0] == "A00"
    assert "omgekeerde rangorde: plek 1 van 20" in kies(lijst, omgekeerd=True).orders[0].reden


def test_onbruikbare_beoordeling_doet_niet_mee():
    lijst = [b("AAA", 0.60), b("BBB", 0.70, probleem="antwoord afgekapt"), b("CCC", 0.50)]
    assert gekocht(kies(lijst)) == ["AAA", "CCC"]


def test_hooguit_twee_crypto():
    munten = [b(f"M{i}/USDT", 0.60) for i in range(5)]           # voorsprong 0,22: bovenaan
    aandelen = [b(s, 0.50) for s in AANDELEN[:12]]
    uit = gekocht(kies(munten + aandelen))
    assert len(uit) == 10 and [s for s in uit if "/" in s] == ["M0/USDT", "M1/USDT"]
    # Met al twee cryptoposities komt er geen derde bij.
    heeft = {"M3/USDT": positie("M3/USDT", "2027-01-10"), "M4/USDT": positie("M4/USDT", "2027-01-10")}
    assert not [s for s in gekocht(kies(munten + aandelen, heeft)) if "/" in s]


# ----------------------------------------------------- wisselen na de houdtijd


def test_binnen_de_houdtijd_wordt_niet_gewisseld():
    lijst = [b(s, 0.40 + 0.01 * i) for i, s in enumerate(AANDELEN)]
    heeft = {s: positie(s, "2027-01-02") for s in AANDELEN[:10]}  # de slechtste tien, 13 dagen geleden gekocht
    uit = kies(lijst, heeft)
    assert uit.orders == [] and uit.herbevestigd == []


def test_na_de_houdtijd_eruit_als_hij_niet_meer_in_de_top_staat():
    lijst = [b(s, 0.40 + 0.01 * i) for i, s in enumerate(AANDELEN)]
    heeft = {s: positie(s, "2027-01-01") for s in ("A00", "A19")}  # precies 14 dagen
    heeft["A18"] = positie("A18", "2027-01-10")
    uit = kies(lijst, heeft)
    assert verkocht(uit) == ["A00"] and "houdtijd van 14 dagen om" in uit.orders[0].reden
    assert uit.herbevestigd == ["A19"], "nog in de top: de houdtijd begint opnieuw"
    # A00 weg, 2 blijven: 8 nieuwe, de beste eerst, en niet wat er al is.
    assert gekocht(uit) == ["A17", "A16", "A15", "A14", "A13", "A12", "A11", "A10"]


def test_houdtijd_telt_vanaf_de_laatste_bevestiging():
    lijst = [b(s, 0.40 + 0.01 * i) for i, s in enumerate(AANDELEN)]
    heeft = {"A00": positie("A00", "2026-12-01", bevestigd_op="2027-01-05")}
    assert verkocht(kies(lijst, heeft)) == []


def test_wachtende_orders_tellen_mee():
    lijst = [b(s, 0.40 + 0.01 * i) for i, s in enumerate(AANDELEN)]
    uit = kies(lijst, kopen={"A19", "A18", "A17"})
    assert len(gekocht(uit)) == 7 and not {"A19", "A18", "A17"} & set(gekocht(uit))
    # Een positie waarvoor al een verkoop klaarstaat, wordt niet nog eens verkocht en maakt een plek vrij.
    heeft = {s: positie(s, "2026-12-01") for s in AANDELEN[:10]}
    uit = kies(lijst, heeft, verkopen={"A00"})
    assert "A00" not in verkocht(uit) and len(verkocht(uit)) == 9 and len(gekocht(uit)) == 10


def test_geen_aankopen_als_kopen_niet_mag():
    lijst = [b(s, 0.50) for s in AANDELEN[:3]]
    heeft = {"ZZZ": positie("ZZZ", "2026-12-01")}
    uit = kies(lijst, heeft, kopen_toegestaan=False)
    assert gekocht(uit) == [] and verkocht(uit) == ["ZZZ"]
    assert "geen bruikbare beoordeling" in uit.orders[0].reden


# ------------------------------------------------------------------ consensus


def consensus(per_bron, posities=None):
    return afgeleid.kies_consensus(
        {bron: {x.symbool: x for x in lijst} for bron, lijst in per_bron.items()},
        posities or {}, set(), set(), DAG, REGELS, 10, BASIS)


def test_consensus_koopt_alleen_als_alle_drie_boven_de_basiskans_zitten():
    uit = consensus({
        "debat": [b("AAA", 0.50), b("BBB", 0.50), b("CCC", 0.50), b("DDD", 0.52), b("EEE", 0.50)],
        "enkel": [b("AAA", 0.48), b("BBB", 0.46), b("CCC", 0.50), b("DDD", 0.60), b("EEE", 0.50, probleem="x")],
        "haiku": [b("AAA", 0.47), b("BBB", 0.60), b("DDD", 0.55)],
    })
    # BBB: een bron precies op de basiskans. CCC: ontbreekt bij Haiku. EEE: onbruikbaar bij een bron.
    assert gekocht(uit) == ["DDD", "AAA"], "de grootste gemiddelde voorsprong eerst"
    assert "alle 3 bronnen boven de basiskans" in uit.orders[0].reden


def test_consensus_is_niet_altijd_belegd():
    assert consensus({"debat": [b("AAA", 0.46)], "enkel": [b("AAA", 0.60)], "haiku": [b("AAA", 0.60)]}).orders == []


def test_consensus_verkoopt_na_de_houdtijd_wat_geen_consensus_meer_heeft():
    bronnen = {"debat": [b("AAA", 0.50), b("BBB", 0.45)], "enkel": [b("AAA", 0.50), b("BBB", 0.50)],
               "haiku": [b("AAA", 0.50), b("BBB", 0.50)]}
    heeft = {"AAA": positie("AAA", "2026-12-20"), "BBB": positie("BBB", "2026-12-20"), "CCC": positie("CCC", "2027-01-10")}
    uit = consensus(bronnen, heeft)
    assert verkocht(uit) == ["BBB"] and uit.herbevestigd == ["AAA"] and gekocht(uit) == []


# --------------------------------------------------- de ronde, met de database


RANG = {"naam": "ai_rangorde", "soort": "rangorde", "bronnen": ["ai_analist"]}
OMGEKEERD = {"naam": "ai_rangorde_omgekeerd", "soort": "rangorde_omgekeerd", "bronnen": ["ai_analist"]}
CONSENSUS = {"naam": "ai_consensus", "soort": "consensus", "bronnen": ["ai_analist", "ai_enkel", "ai_controle_haiku"]}
SYMBOLEN = [f"S{i:02d}" for i in range(14)]


@pytest.fixture
def cfg(cfg_min):
    c = copy.deepcopy(cfg_min)
    c["ai_watchlist"] = list(SYMBOLEN)
    return c


def _markt(laatste: str):
    dagen = [d for d in werkdagen(800, start="2025-01-01") if d <= laatste][-60:]
    assert dagen[-1] == laatste
    return maak_markt({s: [100.0 + i * 0.1 for i in range(len(dagen))] for s in SYMBOLEN + ["SPY"]}, dagen), dagen


def _portefeuilles(con, dagen, namen=("ai_rangorde", "ai_rangorde_omgekeerd", "ai_consensus")):
    for naam in namen:
        nieuwe_portefeuille(naam, naam, INSTELLINGEN, gestart_op=dagen[0]).bewaar(con)
    con.commit()


def _beoordeel(con, bron: str, datum: str, kansen: dict[str, float | None]):
    """Zet beoordelingen in ai_besluiten, zoals de nachtronde dat doet. None = onbruikbaar."""
    for s, kans in kansen.items():
        x = b(s, kans or 0.0, datum=datum, probleem=None if kans is not None else "antwoord afgekapt")
        ronde_mod._bewaar_beoordeling(con, bron, x, nieuws_tot="x", horizon=14)
    con.commit()


def _wachtrij(con, naam):
    return [(r["symbool"], r["actie"]) for r in con.execute(
        "SELECT symbool, actie FROM wachtrij WHERE portefeuille = ? ORDER BY id", (naam,))]


def test_werk_bij_zet_de_orders_in_de_wachtrij(con, cfg):
    markt, dagen = _markt("2027-01-15")
    _portefeuilles(con, dagen)
    _beoordeel(con, "ai_analist", DAG, {s: 0.40 + 0.01 * i for i, s in enumerate(SYMBOLEN)})

    uit = afgeleid.werk_bij(con, cfg, markt, RANG, DAG, SYMBOLEN)
    om = afgeleid.werk_bij(con, cfg, markt, OMGEKEERD, DAG, SYMBOLEN)

    assert uit.compleet and uit.overgeslagen is None
    assert _wachtrij(con, "ai_rangorde") == [(s, "KOOP") for s in reversed(SYMBOLEN[4:])]
    assert _wachtrij(con, "ai_rangorde_omgekeerd") == [(s, "KOOP") for s in SYMBOLEN[:10]]
    assert len(om.orders) == 10
    # Twee keer draaien op dezelfde dag geeft geen dubbele orders.
    afgeleid.werk_bij(con, cfg, markt, RANG, DAG, SYMBOLEN)
    assert len(_wachtrij(con, "ai_rangorde")) == 10
    # Er is niets beoordeeld of betaald namens de afgeleide portefeuilles.
    assert {r["portefeuille"] for r in con.execute("SELECT DISTINCT portefeuille FROM ai_besluiten")} == {"ai_analist"}
    assert con.execute("SELECT COUNT(*) n FROM ai_kosten").fetchone()["n"] == 0


def test_werk_bij_doet_niets_in_een_onvolledige_nacht(con, cfg):
    """Een deel beoordeeld, een deel niet: dan is de rangorde scheef. Geen koop en geen verkoop."""
    markt, dagen = _markt("2027-01-15")
    _portefeuilles(con, dagen)
    pf = nieuwe_portefeuille("ai_rangorde", "ai_rangorde", INSTELLINGEN, gestart_op=dagen[0])
    pf.koop("S00", dagen[-30], dagen[-31], 100.0, "USD", 1.10)
    pf.bewaar(con)
    kansen = {s: 0.40 + 0.01 * i for i, s in enumerate(SYMBOLEN)}

    _beoordeel(con, "ai_analist", DAG, {s: k for s, k in kansen.items() if s != "S13"})  # een ontbreekt
    uit = afgeleid.werk_bij(con, cfg, markt, RANG, DAG, SYMBOLEN)
    assert not uit.compleet and "niet alle symbolen" in uit.overgeslagen and _wachtrij(con, "ai_rangorde") == []

    _beoordeel(con, "ai_analist", DAG, {"S13": None})  # nu wel een rij, maar onbruikbaar
    uit = afgeleid.werk_bij(con, cfg, markt, RANG, DAG, SYMBOLEN)
    assert not uit.compleet and _wachtrij(con, "ai_rangorde") == []


def test_symbool_zonder_koers_telt_niet_als_onvolledig(con, cfg):
    markt, dagen = _markt("2027-01-15")
    _portefeuilles(con, dagen)
    _beoordeel(con, "ai_analist", DAG, {s: 0.50 for s in SYMBOLEN[:-1]})
    ronde_mod._bewaar_beoordeling(con, "ai_analist", b("S13", 0.0, probleem=GEEN_KOERS), nieuws_tot="x", horizon=14)
    con.commit()
    uit = afgeleid.werk_bij(con, cfg, markt, RANG, DAG, SYMBOLEN)
    assert uit.compleet and len(gekocht(uit)) == 10 and "S13" not in gekocht(uit)


def test_zonder_beoordelingen_lopen_de_posities_af(con, cfg):
    """Na de einddatum beoordeelt de AI niets meer: geen aankopen, en verkopen zodra de houdtijd om is."""
    markt, dagen = _markt("2027-01-15")
    pf = nieuwe_portefeuille("ai_rangorde", "ai_rangorde", INSTELLINGEN, gestart_op=dagen[0])
    pf.koop("S00", dagen[-30], dagen[-31], 100.0, "USD", 1.10)
    pf.koop("S01", dagen[-3], dagen[-4], 100.0, "USD", 1.10)
    pf.bewaar(con)
    con.commit()
    # Tijdens de meting (tegoed op, storing): niets doen, anders loopt de hoofdvraag leeg.
    uit = afgeleid.werk_bij(con, cfg, markt, RANG, DAG, SYMBOLEN)
    assert _wachtrij(con, "ai_rangorde") == [] and not uit.compleet and "geen wijzigingen" in uit.overgeslagen
    # Na de einddatum: afbouwen.
    uit = afgeleid.werk_bij(con, cfg, markt, RANG, DAG, SYMBOLEN, afbouwen=True)
    assert _wachtrij(con, "ai_rangorde") == [("S00", "VERKOOP")] and not uit.compleet


def test_niet_op_een_dag_dat_de_beurs_dicht_is(con, cfg):
    markt, dagen = _markt("2027-01-15")
    _portefeuilles(con, dagen)
    _beoordeel(con, "ai_analist", "2027-01-16", {s: 0.50 for s in SYMBOLEN})
    uit = afgeleid.werk_bij(con, cfg, markt, RANG, "2027-01-16", SYMBOLEN)  # zaterdag
    assert "geen beursdag" in uit.overgeslagen and _wachtrij(con, "ai_rangorde") == []


def test_consensus_wacht_tot_alle_bronnen_compleet_zijn(con, cfg):
    markt, dagen = _markt("2027-01-15")
    _portefeuilles(con, dagen)
    _beoordeel(con, "ai_analist", DAG, {s: 0.50 for s in SYMBOLEN})
    _beoordeel(con, "ai_enkel", DAG, {s: 0.50 for s in SYMBOLEN})
    uit = afgeleid.werk_bij(con, cfg, markt, CONSENSUS, DAG, SYMBOLEN)  # Haiku is overgeslagen (budget)
    assert not uit.compleet and _wachtrij(con, "ai_consensus") == []

    _beoordeel(con, "ai_controle_haiku", DAG, {s: (0.50 if s < "S03" else 0.40) for s in SYMBOLEN})
    uit = afgeleid.werk_bij(con, cfg, markt, CONSENSUS, DAG, SYMBOLEN)
    assert uit.compleet and _wachtrij(con, "ai_consensus") == [("S00", "KOOP"), ("S01", "KOOP"), ("S02", "KOOP")]


def test_herbevestiging_wordt_opgeslagen(con, cfg):
    markt, dagen = _markt("2027-01-15")
    pf = nieuwe_portefeuille("ai_rangorde", "ai_rangorde", INSTELLINGEN, gestart_op=dagen[0])
    pf.koop("S13", dagen[-30], dagen[-31], 100.0, "USD", 1.10)
    pf.bewaar(con)
    _beoordeel(con, "ai_analist", DAG, {s: 0.40 + 0.01 * i for i, s in enumerate(SYMBOLEN)})
    uit = afgeleid.werk_bij(con, cfg, markt, RANG, DAG, SYMBOLEN)
    assert uit.herbevestigd == ["S13"]
    rij = con.execute("SELECT bevestigd_op FROM posities WHERE portefeuille='ai_rangorde' AND symbool='S13'").fetchone()
    assert rij["bevestigd_op"] == DAG


# ------------------------------------------------------------ instellingen


def test_afgeleide_portefeuilles_uit_de_instellingen():
    namen = {p["naam"]: p for p in afgeleid.afgeleide_portefeuilles(ECHT)}
    # Geen ai_opus_rangorde: Opus zit sinds 18 november 2026 niet in de echte meting.
    assert set(namen) == {"ai_rangorde", "ai_rangorde_omgekeerd", "ai_consensus"}
    assert namen["ai_rangorde"]["bronnen"] == ["ai_analist"] == namen["ai_rangorde_omgekeerd"]["bronnen"]
    assert namen["ai_consensus"]["bronnen"] == ["ai_analist", "ai_enkel", "ai_controle_haiku"]
    # De bronnen zijn echte AI-portefeuilles, en de hoofdvraag wijst naar de rangorde.
    echte = {p["naam"] for p in ronde_mod.ai_portefeuilles(ECHT)}
    assert all(set(p["bronnen"]) <= echte for p in namen.values())
    assert ECHT["meting"]["ai_portefeuille"] == "ai_rangorde" and ECHT["meting"]["ai_bron"] == "ai_analist"


def test_afgeleide_portefeuilles_in_de_proefweek():
    from datetime import date, datetime, timezone

    from bot import modus

    proef = modus.laad(nu=datetime(2026, 11, 17, 0, 30, tzinfo=timezone.utc), overlay=modus.lees_proefweek(),
                       omgeschakeld=False)
    assert proef["modus"] == "proefweek"
    namen = {p["naam"]: p for p in afgeleid.afgeleide_portefeuilles(proef)}
    assert set(namen) == {"proef_ai_rangorde", "proef_ai_rangorde_omgekeerd", "proef_ai_consensus",
                          "proef_ai_opus_rangorde"}
    assert namen["proef_ai_opus_rangorde"]["bronnen"] == ["proef_ai_opus"]
    echte = {p["naam"] for p in ronde_mod.ai_portefeuilles(proef, vandaag=date(2026, 11, 17))}
    assert all(set(p["bronnen"]) <= echte for p in namen.values())
    assert proef["meting"]["proef_ai_portefeuille"] == "proef_ai_rangorde"
    assert proef["meting"]["proef_ai_bron"] == "proef_ai_mix"


def test_onbekende_soort_wordt_geweigerd():
    cfg = copy.deepcopy(ECHT)
    cfg["ai"]["afgeleid"] = [{"naam": "x", "soort": "gokken", "bron": "ai_analist"}]
    with pytest.raises(ValueError):
        afgeleid.afgeleide_portefeuilles(cfg)


def test_afgeleid_gebruikt_de_api_niet():
    """De module kent de client niet eens: er valt niets aan te roepen."""
    bron = (PROJECT / "bot" / "ai" / "afgeleid.py").read_text(encoding="utf-8")
    assert "bot.ai.client" not in bron and "anthropic" not in bron and "maak_client" not in bron


# ------------------------------------------------- de hele nacht (scripts/dagelijks.py)


GOED = {
    "nieuws": {"samenvatting": "rustig", "belangrijkste_risico": "geen", "gebeurtenis_binnenkort": False},
    "bull": {"argument": "a", "sterkste_punt": "b", "overtuiging": 0.5},
    "bear": {"argument": "c", "sterkste_punt": "d", "overtuiging": 0.5},
    # Kans 0,47: boven de basiskans van een aandeel (0,46), onder de koopgrens (0,55).
    "risk": {"onderbouwing": "x", "belangrijkste_risico": "y", "kans": 0.47, "verwacht_extra_rendement_pct": 0.2},
    "enkel": {"onderbouwing": "x", "belangrijkste_risico": "y", "kans": 0.47, "verwacht_extra_rendement_pct": 0.2},
}


def test_de_nachtronde_voedt_de_afgeleide_portefeuilles_zonder_extra_aanroepen(con, cfg_min, monkeypatch):
    spec = importlib.util.spec_from_file_location("s_dagelijks_afgeleid", PROJECT / "scripts" / "dagelijks.py")
    dagelijks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dagelijks)

    cfg = copy.deepcopy(cfg_min)
    cfg["modus"] = "echt"
    cfg["ai_watchlist"] = ["S00", "S01"]
    markt, dagen = _markt("2027-01-15")
    echte = [p["naam"] for p in ronde_mod.ai_portefeuilles(cfg)]
    _portefeuilles(con, dagen, namen=echte + ["ai_rangorde", "ai_rangorde_omgekeerd", "ai_consensus"])

    def dicht(self):
        raise AssertionError("de echte API mag in een test nooit worden aangeroepen")

    monkeypatch.setattr(ClaudeClient, "_verbinding", dicht)
    monkeypatch.setattr(conf, "sleutel", lambda naam: "nep-sleutel-voor-de-test")
    clients: list[NepClient] = []

    def maak(con, cfg, vandaag=None):
        clients.append(NepClient(Budget(con, cfg, vandaag=vandaag), GOED))
        return clients[-1]

    monkeypatch.setattr(ronde_mod, "maak_client", maak)
    dagelijks._ai_ronde(con, cfg, markt, DAG, nieuw_nieuws=False, ingehaald=1)

    # De gewone AI koopt niets (kans onder 0,55); de rangorde en de consensus wel.
    for naam in echte:
        assert _wachtrij(con, naam) == []
    assert _wachtrij(con, "ai_rangorde") == [("S00", "KOOP"), ("S01", "KOOP")]
    assert _wachtrij(con, "ai_rangorde_omgekeerd") == [("S00", "KOOP"), ("S01", "KOOP")]
    assert _wachtrij(con, "ai_consensus") == [("S00", "KOOP"), ("S01", "KOOP")]
    # Alleen de echte AI-portefeuilles hebben beoordeeld en betaald.
    assert {r["portefeuille"] for r in con.execute("SELECT DISTINCT portefeuille FROM ai_besluiten")} == set(echte)
    aanroepen = len(clients[0].aanroepen)

    # Dezelfde nacht nog een keer: niets nieuws, en geen enkele aanroep erbij.
    dagelijks._ai_ronde(con, cfg, markt, DAG, nieuw_nieuws=False, ingehaald=1)
    assert sum(len(c.aanroepen) for c in clients) == aanroepen
    assert len(_wachtrij(con, "ai_rangorde")) == 2


# ------------------------------------ van kans tot trade, door de gewone motor


def test_de_motor_voert_de_orders_van_de_rangorde_de_volgende_dag_uit(con, cfg, vaste_wisselkoers):
    """De rangorde handelt als elke andere portefeuille: 10% per positie, kosten, een dag later."""
    from bot.motor import Motor
    from bot.strategieen.ai_analist import AIStrategie

    markt, dagen = _markt("2027-02-12")
    d = dagen[-25:]  # 25 werkdagen: genoeg voor een houdtijd van 14 kalenderdagen

    def motor():
        m = Motor(con, cfg, markt, vaste_wisselkoers)
        m.voeg_toe(AIStrategie("ai_rangorde", soort="rangorde"), SYMBOLEN, gestart_op=d[0])
        return m

    def nacht(datum, kansen):
        motor().draai_dag(datum)
        _beoordeel(con, "ai_analist", datum, kansen)
        return afgeleid.werk_bij(con, cfg, markt, RANG, datum, SYMBOLEN)

    oplopend = {s: 0.40 + 0.01 * i for i, s in enumerate(SYMBOLEN)}   # S13 het hoogst
    aflopend = {s: 0.53 - 0.01 * i for i, s in enumerate(SYMBOLEN)}   # S00 het hoogst

    # Nacht 1: tien orders in de wachtrij, nog geen trades.
    assert len(nacht(d[0], oplopend).orders) == 10
    assert con.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] == 0

    # Nacht 2: de motor voert ze uit; de rangorde is vol en doet verder niets.
    uit = nacht(d[1], oplopend)
    trades = con.execute("SELECT * FROM trades WHERE portefeuille='ai_rangorde'").fetchall()
    assert len(trades) == 10 and {t["actie"] for t in trades} == {"KOOP"}
    assert {t["symbool"] for t in trades} == set(SYMBOLEN[4:]) and {t["datum"] for t in trades} == {d[1]}
    assert all(t["besluit_datum"] == d[0] for t in trades)
    assert uit.orders == [] and _wachtrij(con, "ai_rangorde") == []
    pf = con.execute("SELECT kas_usd FROM portefeuilles WHERE naam='ai_rangorde'").fetchone()
    assert pf["kas_usd"] < 15.0, "altijd belegd: tien keer 10%"

    # De rangorde keert om, maar binnen de houdtijd verandert er niets.
    for datum in d[2:9]:
        assert nacht(datum, aflopend).orders == []

    # Na 14 kalenderdagen (10 werkdagen): wat niet meer in de top staat gaat eruit, en er komt evenveel bij.
    uit = nacht(d[11], aflopend)
    assert sorted(verkocht(uit)) == SYMBOLEN[10:] and gekocht(uit) == SYMBOLEN[:4]
    assert sorted(uit.herbevestigd) == SYMBOLEN[4:10]
    motor().draai_dag(d[12])
    nu = {r["symbool"] for r in con.execute("SELECT symbool FROM posities WHERE portefeuille='ai_rangorde'")}
    assert nu == set(SYMBOLEN[:10])
    assert con.execute("SELECT COUNT(*) n FROM trades WHERE portefeuille='ai_rangorde'").fetchone()["n"] == 18


def test_na_de_einddatum_bouwt_de_rangorde_af_via_de_nachtronde(con, cfg_min, monkeypatch):
    """De nachtronde na 23 november 2027: geen aanroepen meer, en de rangorde verkoopt na de houdtijd."""
    spec = importlib.util.spec_from_file_location("s_dagelijks_afgeleid_einde", PROJECT / "scripts" / "dagelijks.py")
    dagelijks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dagelijks)

    cfg = copy.deepcopy(cfg_min)
    cfg["modus"] = "echt"
    cfg["ai_watchlist"] = ["S00", "S01"]
    markt, dagen = _markt("2027-11-24")
    pf = nieuwe_portefeuille("ai_rangorde", "ai_rangorde", INSTELLINGEN, gestart_op=dagen[0])
    pf.koop("S00", dagen[-30], dagen[-31], 100.0, "USD", 1.10)   # houdtijd om
    pf.koop("S01", dagen[-3], dagen[-4], 100.0, "USD", 1.10)     # pas gekocht
    pf.bewaar(con)
    _portefeuilles(con, dagen, namen=[p["naam"] for p in ronde_mod.ai_portefeuilles(cfg)])

    def dicht(self):
        raise AssertionError("de echte API mag in een test nooit worden aangeroepen")

    monkeypatch.setattr(ClaudeClient, "_verbinding", dicht)
    monkeypatch.setattr(conf, "sleutel", lambda naam: "nep-sleutel-voor-de-test")
    clients: list[NepClient] = []

    def maak(con, cfg, vandaag=None):
        clients.append(NepClient(Budget(con, cfg, vandaag=vandaag), GOED))
        return clients[-1]

    monkeypatch.setattr(ronde_mod, "maak_client", maak)
    dagelijks._ai_ronde(con, cfg, markt, "2027-11-24", nieuw_nieuws=False, ingehaald=1)

    assert clients[0].aanroepen == []
    assert _wachtrij(con, "ai_rangorde") == [("S00", "VERKOOP")]
