"""Rekent na of de nep-broker klopt: dollars, kosten, slippage, verdeling, dividend en splitsingen."""

from __future__ import annotations

import pytest

from bot.portefeuille import Portefeuille, nieuwe_portefeuille
from tests.conftest import INSTELLINGEN


def pf():
    return nieuwe_portefeuille("test", "test", INSTELLINGEN, gestart_op="2025-01-01")


def test_koop_rekent_kosten_en_slippage_mee():
    p = pf()
    trade = p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10, bedrag_usd=100.0)
    assert trade is not None
    assert trade.prijs_lokaal == pytest.approx(100.0 * 1.0005)
    assert trade.prijs_usd == pytest.approx(100.0 * 1.0005), "in dollars wordt er niets omgerekend"
    assert trade.bedrag_usd == pytest.approx(100.0)
    assert trade.kosten_usd == pytest.approx(0.10)
    assert p.kas_usd == pytest.approx(1000.0 - 100.0 - 0.10)
    assert trade.aantal == pytest.approx(100.0 / (100.0 * 1.0005))
    assert trade.eurusd == pytest.approx(1.10), "de wisselkoers wordt alleen bewaard voor de weergave"


def test_alleen_dollars():
    with pytest.raises(ValueError):
        pf().koop("AAA", "2025-01-02", "2025-01-01", 100.0, "EUR", 1.10, bedrag_usd=100.0)


def test_zonder_bedrag_tien_procent():
    p = pf()
    trade = p.koop("AAA", "2025-01-02", "2025-01-01", 50.0, "USD", 1.10)
    assert trade.bedrag_usd == pytest.approx(100.0), "10% van 1.000 dollar"


def test_hooguit_tien_posities():
    p = pf()
    for i in range(15):
        p.koop(f"S{i}", "2025-01-02", "2025-01-01", 10.0, "USD", 1.10, bedrag_usd=50.0)
    assert len(p.posities) == 10
    assert p.kas_usd >= 0


def test_meetlat_heeft_geen_grens_op_posities():
    p = pf()
    p.max_posities = None
    for i in range(46):
        p.koop(f"S{i}", "2025-01-02", "2025-01-01", 10.0, "USD", 1.10, bedrag_usd=1000.0 / 46)
    assert len(p.posities) == 46


def test_kas_wordt_nooit_negatief():
    p = pf()
    trade = p.koop("AAA", "2025-01-02", "2025-01-01", 10.0, "USD", 1.10, bedrag_usd=5000.0)
    assert trade.bedrag_usd + trade.kosten_usd <= 1000.0 + 1e-9
    assert p.kas_usd == 0.0
    assert f"{p.kas_usd:.2f}" == "0.00", "geen -0,00 door rekenruis"


def test_verkoop_berekent_het_resultaat_na_kosten():
    p = pf()
    p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10, bedrag_usd=100.0)
    kostprijs, aantal = p.posities["AAA"].kostprijs_usd, p.posities["AAA"].aantal
    trade = p.verkoop("AAA", "2025-02-01", "2025-01-31", 200.0, "USD", 1.10)
    opbrengst = aantal * 200.0 * (1 - 0.0005)
    assert trade.kosten_usd == pytest.approx(opbrengst * 0.001)
    assert trade.resultaat_usd == pytest.approx(opbrengst * 0.999 - kostprijs)
    assert 95.0 < trade.resultaat_pct < 100.0
    assert "AAA" not in p.posities


def test_dividend_gaat_naar_de_kas_en_telt_mee_in_het_resultaat():
    p = pf()
    p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10, bedrag_usd=100.0)
    aantal = p.posities["AAA"].aantal
    kas = p.kas_usd
    bedrag = p.ontvang_dividend("AAA", 2.0, "2025-01-15")
    assert bedrag == pytest.approx(aantal * 2.0)
    assert p.kas_usd == pytest.approx(kas + aantal * 2.0)
    trade = p.verkoop("AAA", "2025-02-01", "2025-01-31", 100.0, "USD", 1.10)
    zonder = aantal * 100.0 * 0.9995 * 0.999 - p.nieuwe_trades[0].kosten_usd * 0  # alleen ter vergelijking
    assert trade.resultaat_usd == pytest.approx(
        aantal * 100.0 * 0.9995 * 0.999 + aantal * 2.0 - (100.0 + 0.10)
    )
    assert trade.resultaat_usd > zonder - (100.0 + 0.10)


def test_splitsing_meer_stukken_zelfde_waarde():
    p = pf()
    p.koop("AAA", "2025-01-02", "2025-01-01", 1000.0, "USD", 1.10, bedrag_usd=100.0)
    voor = p.posities["AAA"].aantal
    waarde_voor = p.posities_waarde({"AAA": 1000.0})
    p.pas_splitsing_toe("AAA", 10.0, "2025-01-10")
    assert p.posities["AAA"].aantal == pytest.approx(voor * 10)
    assert p.posities_waarde({"AAA": 100.0}) == pytest.approx(waarde_voor)
    assert p.posities["AAA"].koopprijs == pytest.approx(1000.0 * 1.0005 / 10)


def test_niet_twee_keer_hetzelfde_symbool_kopen():
    p = pf()
    assert p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10) is not None
    assert p.koop("AAA", "2025-01-03", "2025-01-02", 100.0, "USD", 1.10) is None


def test_verkoop_zonder_positie_doet_niets():
    assert pf().verkoop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10) is None


def test_opslaan_en_weer_inlezen(con):
    p = pf()
    p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10, reden="test")
    p.ontvang_dividend("AAA", 1.0, "2025-01-10")
    p.bewaar(con)
    terug = Portefeuille.uit_database(con, "test", INSTELLINGEN)
    assert terug.kas_usd == pytest.approx(p.kas_usd)
    pos = terug.posities["AAA"]
    assert pos.aantal == pytest.approx(p.posities["AAA"].aantal)
    assert pos.koopprijs == pytest.approx(100.0 * 1.0005)
    assert pos.dividend_usd == pytest.approx(p.posities["AAA"].dividend_usd)
    assert pos.koopreden == "test"
    assert con.execute("SELECT COUNT(*) n FROM uitkeringen").fetchone()["n"] == 1
