"""Tests voor de derde review (november 2026), de laatste voor de start van de meting."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from bot import config as conf
from bot import rapport, statistiek, volledigheid
from bot.ai import beslisregel, kalibratie, kostenprognose, rollen
from bot.ai.analist import Beoordeling
from bot.ai.budget import Budget
from bot.ai.client import Antwoord, NepClient
from bot.ai.kenmerken import basiskans
from bot.ai.ronde import denk_na
from bot.markt import Markt
from bot.portefeuille import Positie, nieuwe_portefeuille
from tests.conftest import INSTELLINGEN, maak_df, maak_markt, werkdagen

ECHT = conf.laad()
REGELS = ECHT["ai"]["beslisregel"]
PROJECT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------ volledigheid


def _cfg(aandelen, crypto=()):
    return {"watchlist": {"aandelen": list(aandelen), "etfs": ["SPY"], "crypto": list(crypto)},
            "rapport": {"kalender_symbool": "SPY"}, "beurskalender": ECHT["beurskalender"]}


def test_verdwenen_aandeel_over_meerdere_nachten_loopt_niet_vast():
    """De gemiste dagen stapelen op in de lijst; na de derde mag geen ervan meer blokkeren."""
    dagen = werkdagen(8, start="2026-11-02")
    markt = Markt({"SPY": maak_df([1.0] * 8, dagen), "KO": maak_df([1.0] * 5, dagen[:5])})
    cfg = _cfg(["KO"])
    assert volledigheid.controleer(markt, cfg, dagen[5:6]) != [], "nacht 1: een gemiste dag blokkeert"
    markt2 = Markt({"SPY": maak_df([1.0] * 7, dagen[:7]), "KO": maak_df([1.0] * 5, dagen[:5])})
    assert volledigheid.controleer(markt2, cfg, dagen[5:7]) != [], "nacht 2: twee gemiste dagen blokkeren"
    assert volledigheid.controleer(markt, cfg, dagen[5:8]) == [], "nacht 3: verdwenen, de handel gaat door"


def test_een_eerder_gat_van_een_verdwenen_aandeel_blijft_een_probleem():
    dagen = werkdagen(10, start="2026-11-02")
    ko = maak_df([1.0] * 4, [dagen[0], dagen[1], dagen[3], dagen[4]])  # dag 3 mist, daarna weer koersen
    markt = Markt({"SPY": maak_df([1.0] * 10, dagen), "KO": ko})
    problemen = volledigheid.controleer(markt, _cfg(["KO"]), dagen[2:10])
    assert problemen == [f"KO: geen koers op {dagen[2]}, terwijl SPY die dag wel handelde"]


def test_dag_zonder_enige_cryptokoers_blokkeert():
    dagen = werkdagen(3, start="2026-11-02")
    markt = Markt({"SPY": maak_df([1.0] * 3, dagen), "BTC/USDT": maak_df([1.0] * 2, dagen[:2])})
    assert volledigheid.controleer(markt, _cfg([], ["BTC/USDT"]), [dagen[2]]) == [f"BTC/USDT: geen koers op {dagen[2]}"]


# ------------------------------------------------------------ prompts


def _kenmerken(symbool, ref, vraag):
    return {"symbool": symbool, "referentie": ref, "vraag": vraag,
            "basiskans": basiskans(symbool, ECHT["ai"]["basiskansen"])}


def test_de_vraag_zegt_vanaf_wanneer_en_noemt_de_basiskans():
    aapl = rollen.vraag_tekst("AAPL", _kenmerken("AAPL", "SPY", "relatief"))
    assert "beter dan SPY" in aapl
    assert "openingskoers van de eerstvolgende handelsdag na de beslisdag" in aapl
    assert "Nieuws van na het slot" in aapl
    assert "ongeveer 46%" in aapl
    assert "ongeveer 38%" in rollen.vraag_tekst("ETH/USDT", _kenmerken("ETH/USDT", "BTC/USDT", "relatief"))
    spy = rollen.vraag_tekst("SPY", _kenmerken("SPY", "kas", "absoluut"))
    assert "de referentie is kas" in spy.replace("\n", " ") and "ongeveer 53%" in spy
    assert "ongeveer 45%" in rollen.vraag_tekst("BTC/USDT", _kenmerken("BTC/USDT", "kas", "absoluut"))


def test_geen_anker_op_een_half_en_eerst_de_onderbouwing():
    tekst = json.dumps(rollen.SCHEMA_KANS, ensure_ascii=False) + rollen.SYSTEEM_RISK + rollen.SYSTEEM_ENKEL
    assert "0,5 betekent" not in tekst and "ver van 0,5" not in tekst
    assert "basiskans" in rollen.SYSTEEM_RISK and "basiskans" in rollen.SYSTEEM_ENKEL
    assert list(rollen.SCHEMA_KANS["properties"])[:2] == ["onderbouwing", "belangrijkste_risico"]
    assert "Tel alleen wat over het symbool zelf gaat" in rollen.SYSTEEM_NIEUWS


def test_de_vastgelegde_basiskansen():
    assert ECHT["ai"]["basiskansen"] == {
        "aandeel_tegen_spy": 0.46, "crypto_tegen_btc": 0.38, "spy_tegen_kas": 0.53, "btc_tegen_kas": 0.45}


# ------------------------------------------------- de AI-ronde


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
    "risk": {"onderbouwing": "x", "belangrijkste_risico": "y", "kans": 0.7, "verwacht_extra_rendement_pct": 2.0},
}


def _markt():
    datums = werkdagen(130)
    return maak_markt({s: [100.0 + i * 0.1 for i in range(130)] for s in ("AAA", "SPY")}, datums), datums[-1]


def test_basiskans_staat_in_het_bericht_en_wordt_opgeslagen(con, cfg_ai):
    markt, datum = _markt()
    nieuwe_portefeuille("ai_analist", "ai_analist", INSTELLINGEN, gestart_op="2025-01-01").bewaar(con)
    con.commit()
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), GOED)
    denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    risk = next(c for c in client.aanroepen if c["rol"] == "risk")
    assert "Basiskans" in risk["bericht"]
    stappen = json.loads(con.execute("SELECT stappen FROM ai_besluiten").fetchone()["stappen"])
    assert stappen["kenmerken"]["basiskans"] == 0.46


def test_een_mislukt_antwoord_krijgt_een_herkansing(con, cfg_ai):
    markt, datum = _markt()
    nieuwe_portefeuille("ai_analist", "ai_analist", INSTELLINGEN, gestart_op="2025-01-01").bewaar(con)
    con.commit()

    class EersteKeerAfgekapt(NepClient):
        keren = 0

        def vraag(self, rol, model, systeem, bericht, max_tokens, schema=None, denken="aan", inspanning="low"):
            a = super().vraag(rol, model, systeem, bericht, max_tokens, schema, denken, inspanning)
            if rol == "risk":
                self.keren += 1
                if self.keren == 1:
                    return Antwoord(rol=rol, model=model, tekst="{", usd=a.usd, afgekapt=True)
            return a

    client = EersteKeerAfgekapt(Budget(con, cfg_ai, vandaag=datum), GOED)
    uit = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    assert client.keren == 2 and uit.compleet and uit.onbruikbaar == []
    rij = con.execute("SELECT kans, kosten_usd FROM ai_besluiten").fetchone()
    assert rij["kans"] == pytest.approx(0.7)
    betaald = con.execute("SELECT SUM(kosten_usd) s FROM ai_kosten").fetchone()["s"]
    assert rij["kosten_usd"] == pytest.approx(betaald), "ook de mislukte poging is betaald en geteld"


def _b(symbool, kans, verwacht, schommeling=None, ref="SPY"):
    stappen = {"kenmerken": {"schommeling_per_dag_28d_pct": schommeling}} if schommeling else {}
    return Beoordeling(symbool, "2026-11-23", ref, kans=kans, verwacht_pct=verwacht, stappen=stappen)


def test_hooguit_twee_crypto_voor_de_ai():
    b = {s: _b(s, 0.7, 2.0, 4.0, "BTC/USDT") for s in ("ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT")}
    b["AAPL"] = _b("AAPL", 0.6, 1.0, 1.0)
    u = beslisregel.kies(b, {}, set(), set(), "2026-11-23", REGELS, 10)
    gekocht = [o.symbool for o in u.orders]
    assert sum("/" in s for s in gekocht) == 2 and "AAPL" in gekocht


def test_bestaande_crypto_telt_mee_voor_de_grens():
    b = {"SOL/USDT": _b("SOL/USDT", 0.7, 2.0, 4.0, "BTC/USDT")}
    posities = {s: Positie(s, 1.0, 100.0, "2026-11-20", koopprijs=100.0) for s in ("ETH/USDT", "XRP/USDT")}
    b.update({s: _b(s, 0.6, 1.0, 4.0, "BTC/USDT") for s in posities})
    u = beslisregel.kies(b, posities, set(), set(), "2026-11-23", REGELS, 10)
    assert u.orders == []


def test_bij_gelijke_kans_niet_vanzelf_het_beweeglijkste_symbool():
    b = {"DOGE/USDT": _b("DOGE/USDT", 0.6, 3.0, 6.0, "BTC/USDT"),   # 3,0 / 6,0 = 0,5
         "KO": _b("KO", 0.6, 1.0, 0.8)}                              # 1,0 / 0,8 = 1,25
    u = beslisregel.kies(b, {}, set(), set(), "2026-11-23", REGELS, 1)
    assert [o.symbool for o in u.orders] == ["KO"]


# ------------------------------------------------------ budget


def _nacht(con, pf, datum, usd):
    con.execute("""INSERT INTO ai_besluiten (portefeuille, symbool, datum, actie, kosten_usd, nieuws_tot, gemaakt_op)
                   VALUES (?, 'AAA', ?, 'NIETS', ?, 'x', 'x')""", (pf, datum, usd))


def test_reserve_voor_de_hoofdvraag(con, cfg_ai):
    cfg = dict(cfg_ai)
    cfg["beurskalender"] = ECHT["beurskalender"]
    for d in ("2026-12-01", "2026-12-02"):
        _nacht(con, "ai_analist", d, 1.00)
        _nacht(con, "ai_enkel", d, 0.40)
    con.commit()
    budget = Budget(con, cfg, vandaag="2026-12-03")  # de ronde over beslisdag 2 december
    assert budget.nachtkosten("ai_enkel") == pytest.approx(0.40)
    # Beslisdagen 3 t/m 30 december tellen nog voor december (de ronde draait de dag erna);
    # 31 december valt in de ronde van 1 januari. Kerst (25 dec) is een feestdag.
    assert budget.reserve_voor("ai_analist", "2026-12-02", cfg) == pytest.approx(1.00 * 19 * 1.15), "met 15% marge"


def test_de_zwaarste_maand_is_december():
    assert kostenprognose.zwaarste_maand(ECHT) == ("2026-12", 22)


def test_ai_stopt_na_de_einddatum():
    spec = importlib.util.spec_from_file_location("s_dagelijks3", PROJECT / "scripts" / "dagelijks.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    echt = {"modus": "echt", "meting": {"einde": "2027-02-23"}}
    assert not m._meting_voorbij(echt, "2027-02-23")
    assert m._meting_voorbij(echt, "2027-02-24")
    assert not m._meting_voorbij({**echt, "modus": "proefweek"}, "2027-03-01")


# ------------------------------------------- meetlatten per soort


def test_meetlatten_per_soort_en_crypto_in_de_waarde(con, cfg_min, vaste_wisselkoers):
    from bot.opzet import bouw_motor

    dagen = werkdagen(6)
    markt = maak_markt({s: [100.0] * 6 for s in ("SPY", "AAPL", "BTC/USDT", "ETH/USDT")}, dagen)
    cfg = dict(cfg_min)
    cfg["watchlist"] = {"aandelen": ["AAPL"], "etfs": ["SPY"], "crypto": ["BTC/USDT", "ETH/USDT"]}
    cfg["ai_watchlist"] = ["SPY", "AAPL", "BTC/USDT", "ETH/USDT"]
    motor = bouw_motor(con, cfg, markt, vaste_wisselkoers, begin=dagen[0])
    motor.draai_periode(dagen[0], dagen[-1])
    namen = {d.portefeuille.naam for d in motor.deelnemers}
    assert {"gelijk_alle", "gelijk_aandelen", "gelijk_crypto"} <= namen
    rij = con.execute("SELECT posities_usd, crypto_usd FROM waarde WHERE portefeuille='gelijk_crypto' AND datum=?",
                      (dagen[-1],)).fetchone()
    assert rij["crypto_usd"] == pytest.approx(rij["posities_usd"]) and rij["crypto_usd"] > 900
    rij = con.execute("SELECT crypto_usd FROM waarde WHERE portefeuille='gelijk_aandelen' AND datum=?",
                      (dagen[-1],)).fetchone()
    assert rij["crypto_usd"] == 0.0


def _waarde(con, pf, d, totaal, posities, crypto):
    con.execute("""INSERT INTO waarde (portefeuille, datum, kas_usd, posities_usd, totaal_usd, crypto_usd)
                   VALUES (?,?,?,?,?,?)""", (pf, d, totaal - posities, posities, totaal, crypto))


def test_alleen_crypto_kopen_is_geen_keuzekunst_tegen_dezelfde_soort(con):
    dagen = werkdagen(40)
    aandelen = crypto = alles = ai = 1000.0
    for i, d in enumerate(dagen):
        if i:
            ra, rc = 0.001, (0.03 if i % 2 else -0.01)
            aandelen *= 1 + ra
            crypto *= 1 + rc
            alles *= 1 + (38 * ra + 8 * rc) / 46
            ai *= 1 + 0.5 * rc  # half in crypto, de rest kas, precies de cryptomeetlat
        _waarde(con, "gelijk_aandelen", d, aandelen, aandelen, 0.0)
        _waarde(con, "gelijk_crypto", d, crypto, crypto, crypto)
        _waarde(con, "gelijk_alle", d, alles, alles, alles * 8 / 46)
        _waarde(con, "ai_analist", d, ai, ai * 0.5, ai * 0.5)
    con.commit()
    soort = rapport.vergelijk_zelfde_soort(con, "ai_analist", dagen[-1], dagen)
    hoofd = rapport.vergelijk_even_belegd(con, "ai_analist", "gelijk_alle", dagen[-1], dagen)
    assert soort.tegen == rapport.ZELFDE_SOORT and soort.verschil_pp == pytest.approx(0.0, abs=1e-9)
    assert hoofd.verschil_pp > 5.0, "tegen alle symbolen lijkt de cryptovoorkeur op goed kiezen"


# ------------------------------------------------- kansen


def test_toets_tegen_de_basiskans():
    import numpy as np

    rng = np.random.default_rng(4)
    goed, basis = {}, {}
    for i, d in enumerate(werkdagen(60)):
        uitkomsten = [int(rng.random() < 0.46) for _ in range(40)]
        # Een voorspeller met een echt signaal: hoger bij wat uitkomt.
        goed[d] = [(0.60 if g else 0.35, g) for g in uitkomsten]
        basis[d] = [(0.46, g) for g in uitkomsten]
    assert kalibratie.toets_tegen_basiskans(goed) < 0.01
    assert kalibratie.toets_tegen_basiskans(basis) > 0.05, "de basiskans zeggen is niet 'beter dan de basiskans'"
    assert kalibratie.toets_tegen_basiskans({d: goed[d] for d in list(goed)[:20]}) is None, "te weinig beslisdagen"


def test_venster_alleen_in_de_echte_meting():
    assert kalibratie.venster({"modus": "echt", "meting": ECHT["meting"]}) == ("2026-11-23", "2027-02-23")
    assert kalibratie.venster({"modus": "proefweek", "meting": ECHT["meting"]}) == (None, None)


# ------------------------------------------------- teksten en workflow


def test_geen_waarschijnlijk_geen_geluk_meer():
    z = statistiek.beoordeel([5.0, 6.0, 4.0, 7.0, 5.5] * 8, min_trades=30, betrouwbaarheid=0.95,
                             onderscheidend_vermogen=0.8)
    assert "waarschijnlijk geen geluk" not in z.uitleg and "verkennend" in z.uitleg


def test_workflow_altijd_main_en_melding_bij_afbreken():
    tekst = (PROJECT / ".github" / "workflows" / "dagelijks.yml").read_text(encoding="utf-8")
    assert "ref: main" in tekst
    assert "if: ${{ failure() || cancelled() }}" in tekst


# ------------------------------------------------- eindreview (14 november)


def test_verdwenen_aandeel_dat_terugkomt_blokkeert_niet_alsnog():
    """Na 3 beursdagen zonder koers komt het aandeel terug; de al verwerkte dagen in het gat blokkeren niet."""
    dagen = werkdagen(10, start="2026-11-02")
    ko = maak_df([1.0] * 7, dagen[:3] + dagen[6:])  # dag 4, 5 en 6 missen, daarna weer koersen
    markt = Markt({"SPY": maak_df([1.0] * 10, dagen), "KO": ko})
    cfg = _cfg(["KO"])
    assert volledigheid.gat_lengte(markt, "KO", dagen[4], "SPY") == 3
    # De nachtloop controleert de laatst verwerkte dag (in het gat) elke nacht opnieuw.
    for einde in (7, 8, 10):
        assert volledigheid.controleer(markt, cfg, dagen[5:einde]) == [], f"t/m dag {einde}"


def test_een_kort_gat_blijft_wel_blokkeren():
    dagen = werkdagen(10, start="2026-11-02")
    ko = maak_df([1.0] * 8, dagen[:3] + dagen[5:])  # twee dagen missen
    markt = Markt({"SPY": maak_df([1.0] * 10, dagen), "KO": ko})
    assert volledigheid.gat_lengte(markt, "KO", dagen[3], "SPY") == 2
    assert len(volledigheid.controleer(markt, _cfg(["KO"]), dagen[3:8])) == 2


def test_reserve_rekent_met_de_recente_nachten_als_die_duurder_zijn(con, cfg_ai):
    cfg = dict(cfg_ai)
    cfg["beurskalender"] = ECHT["beurskalender"]
    for i, d in enumerate(werkdagen(10, start="2026-12-01")):
        _nacht(con, "ai_analist", d, 1.00 if i < 5 else 2.00)  # de laatste vijf nachten zijn duurder
    con.commit()
    budget = Budget(con, cfg, vandaag="2026-12-15")
    # Beslisdagen 15 t/m 30 december, zonder kerst: 15-18 (4), 21-24 (4), 28-30 (3) = 11.
    assert budget.reserve_voor("ai_analist", "2026-12-14", cfg) == pytest.approx(2.00 * 11 * 1.15)


# ------------------------------- verkopen onder de basiskans (bevroren op 14 november)


def _houd(symbool):
    return {symbool: Positie(symbool, 1.0, 100.0, "2026-11-20", koopprijs=100.0)}


@pytest.mark.parametrize("symbool, ref, basis", [
    ("AAPL", "SPY", 0.46), ("QQQ", "SPY", 0.46), ("ETH/USDT", "BTC/USDT", 0.38),
    ("SPY", "kas", 0.53), ("BTC/USDT", "kas", 0.45),
])
def test_verkopen_alleen_onder_de_basiskans_van_het_symbool(symbool, ref, basis):
    kansen = ECHT["ai"]["basiskansen"]

    def uitkomst(kans):
        b = {symbool: Beoordeling(symbool, "2026-11-23", ref, kans=kans, verwacht_pct=0.0)}
        return beslisregel.kies(b, _houd(symbool), set(), set(), "2026-11-23", REGELS, 10, basiskansen=kansen)

    assert uitkomst(basis).orders == [], "precies op de basiskans is vasthouden"
    assert uitkomst(basis + 0.01).orders == []
    onder = uitkomst(basis - 0.01)
    assert [(o.symbool, o.actie) for o in onder.orders] == [(symbool, "VERKOOP")]
    assert "onder de basiskans van" in onder.orders[0].reden


def test_geen_mening_is_vasthouden_niet_verkopen():
    """Het model zegt bij twijfel de basiskans (0,46 voor een aandeel): dan niet verkopen, ook al is dat onder 0,50."""
    b = {"AAPL": Beoordeling("AAPL", "2026-11-23", "SPY", kans=0.46, verwacht_pct=0.0,
                             stappen={"kenmerken": {"basiskans": 0.46}})}
    u = beslisregel.kies(b, _houd("AAPL"), set(), set(), "2026-11-23", REGELS, 10)
    assert u.orders == []


def test_de_basiskans_die_het_model_zag_gaat_voor():
    """De grens is de basiskans uit de opgeslagen beoordeling; die hoort bij de kans die het model gaf."""
    b = Beoordeling("AAPL", "2026-11-23", "SPY", kans=0.47, verwacht_pct=0.0,
                    stappen={"kenmerken": {"basiskans": 0.50}})
    assert beslisregel.verkoopgrens(b, REGELS, ECHT["ai"]["basiskansen"]) == 0.50
    zonder = Beoordeling("AAPL", "2026-11-23", "SPY", kans=0.47, verwacht_pct=0.0)
    assert beslisregel.verkoopgrens(zonder, REGELS, ECHT["ai"]["basiskansen"]) == 0.46
    assert beslisregel.verkoopgrens(zonder, REGELS, None) == 0.50, "zonder basiskans de vaste grens"


def test_koopgrens_blijft_055():
    assert REGELS["koop_kans"] == 0.55 and REGELS["koop_verwacht_pct"] == 0.5
    b = {"AAPL": Beoordeling("AAPL", "2026-11-23", "SPY", kans=0.54, verwacht_pct=2.0)}
    assert beslisregel.kies(b, {}, set(), set(), "2026-11-23", REGELS, 10,
                            basiskansen=ECHT["ai"]["basiskansen"]).orders == []


def test_in_de_ronde_wordt_een_positie_op_de_basiskans_vastgehouden(con, cfg_ai):
    markt, datum = _markt()
    pf = nieuwe_portefeuille("ai_analist", "ai_analist", INSTELLINGEN, gestart_op="2025-01-01")
    pf.koop("AAA", datum, datum, 100.0, "USD", 1.10)
    pf.bewaar(con)
    con.commit()
    antwoorden = json.loads(json.dumps(GOED))
    antwoorden["risk"].update(kans=0.46, verwacht_extra_rendement_pct=0.0)
    client = NepClient(Budget(con, cfg_ai, vandaag=datum), antwoorden)
    uit = denk_na(con, cfg_ai, client, markt, "ai_analist", datum, ["AAA"], stil=True)
    assert uit.orders == [], "kans 0,46 = de basiskans van een aandeel: vasthouden"
    antwoorden["risk"]["kans"] = 0.45
    con.execute("DELETE FROM ai_besluiten")
    con.commit()
    uit = denk_na(con, cfg_ai, NepClient(Budget(con, cfg_ai, vandaag=datum), antwoorden), markt,
                  "ai_analist", datum, ["AAA"], stil=True)
    assert [(o.symbool, o.actie) for o in uit.orders] == [("AAA", "VERKOOP")]
