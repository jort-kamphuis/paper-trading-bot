"""Rekent na of de nep-broker klopt: kosten, slippage, grenzen en kas."""

from __future__ import annotations

import pytest

from bot.portefeuille import nieuwe_portefeuille
from tests.conftest import INSTELLINGEN


def pf():
    return nieuwe_portefeuille("test", "test", INSTELLINGEN, gestart_op="2025-01-01")


def test_koop_rekent_kosten_en_slippage_mee():
    p = pf()
    trade = p.koop(
        symbool="AAA", datum="2025-01-02", besluit_datum="2025-01-01",
        open_koers=100.0, valuta="USD", eurusd=1.10,
    )
    assert trade is not None

    # 0,05% slippage op de openingskoers.
    verwachte_koers = 100.0 * 1.0005
    assert trade.prijs_lokaal == pytest.approx(verwachte_koers)
    # In euro's: gedeeld door de wisselkoers.
    assert trade.prijs_eur == pytest.approx(verwachte_koers / 1.10)

    # Er mag maximaal 100 euro in, en de kosten komen er bovenop.
    assert trade.bedrag_eur <= 100.0 + 1e-9
    assert trade.kosten_eur == pytest.approx(trade.bedrag_eur * 0.001)
    # Kas: 1000 min inleg min kosten.
    assert p.kas_eur == pytest.approx(1000.0 - trade.bedrag_eur - trade.kosten_eur)
    # Het aantal stukken klopt met het bedrag.
    assert trade.aantal == pytest.approx(trade.bedrag_eur / trade.prijs_eur)


def test_nooit_meer_dan_max_per_trade():
    p = pf()
    for i in range(12):
        p.koop(
            symbool=f"S{i}", datum="2025-01-02", besluit_datum="2025-01-01",
            open_koers=100.0, valuta="USD", eurusd=1.10,
        )
    # Met 1000 euro en 100 euro per trade passen er maximaal 10 posities in,
    # en door de kosten zelfs dat laatste stukje niet helemaal.
    assert len(p.posities) <= 10
    assert p.kas_eur >= -1e-9, "de kas mag nooit negatief worden"


def test_kas_wordt_nooit_negatief_bij_veel_kopen():
    p = pf()
    for i in range(50):
        p.koop(
            symbool=f"S{i}", datum="2025-01-02", besluit_datum="2025-01-01",
            open_koers=10.0, valuta="USD", eurusd=1.10,
        )
        assert p.kas_eur >= -1e-9


def test_verkoop_berekent_het_resultaat_na_kosten():
    p = pf()
    p.koop(
        symbool="AAA", datum="2025-01-02", besluit_datum="2025-01-01",
        open_koers=100.0, valuta="USD", eurusd=1.10,
    )
    kostprijs = p.posities["AAA"].kostprijs_eur
    aantal = p.posities["AAA"].aantal

    # Koers verdubbelt.
    trade = p.verkoop(
        symbool="AAA", datum="2025-02-01", besluit_datum="2025-01-31",
        open_koers=200.0, valuta="USD", eurusd=1.10,
    )
    assert trade is not None
    verwachte_koers = 200.0 * (1 - 0.0005)
    assert trade.prijs_lokaal == pytest.approx(verwachte_koers)

    opbrengst = aantal * (verwachte_koers / 1.10)
    kosten = opbrengst * 0.001
    assert trade.kosten_eur == pytest.approx(kosten)
    assert trade.resultaat_eur == pytest.approx(opbrengst - kosten - kostprijs)
    assert "AAA" not in p.posities

    # Een verdubbeling min twee keer kosten en twee keer slippage ligt net onder 100%.
    assert 95.0 < trade.resultaat_pct < 100.0


def test_verlies_wordt_als_verlies_geboekt():
    p = pf()
    p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10)
    trade = p.verkoop("AAA", "2025-02-01", "2025-01-31", 50.0, "USD", 1.10)
    assert trade.resultaat_eur < 0
    assert trade.resultaat_pct < -45


def test_niet_twee_keer_hetzelfde_symbool_kopen():
    p = pf()
    assert p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10) is not None
    assert p.koop("AAA", "2025-01-03", "2025-01-02", 100.0, "USD", 1.10) is None


def test_verkoop_zonder_positie_doet_niets():
    p = pf()
    assert p.verkoop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10) is None


def test_wisselkoers_wordt_echt_gebruikt():
    """Een zwakkere euro maakt een dollarpositie in euro's meer waard."""
    p = pf()
    p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.20)
    aantal_bij_120 = p.posities["AAA"].aantal

    q = pf()
    q.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.05)
    aantal_bij_105 = q.posities["AAA"].aantal

    # Bij 1,20 dollar per euro koop je voor 100 euro meer stukken dan bij 1,05.
    assert aantal_bij_120 > aantal_bij_105


def test_waarde_is_kas_plus_posities():
    p = pf()
    p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10)
    aantal = p.posities["AAA"].aantal
    prijzen = {"AAA": 50.0}
    assert p.posities_waarde(prijzen) == pytest.approx(aantal * 50.0)
    assert p.totale_waarde(prijzen) == pytest.approx(p.kas_eur + aantal * 50.0)


def test_opslaan_en_weer_inlezen(con):
    p = pf()
    p.koop("AAA", "2025-01-02", "2025-01-01", 100.0, "USD", 1.10)
    p.bewaar(con)

    from bot.portefeuille import Portefeuille

    terug = Portefeuille.uit_database(con, "test", INSTELLINGEN)
    assert terug is not None
    assert terug.kas_eur == pytest.approx(p.kas_eur)
    assert "AAA" in terug.posities
    assert terug.posities["AAA"].aantal == pytest.approx(p.posities["AAA"].aantal)
