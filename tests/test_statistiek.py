"""Controleert de rekensommen over de resultaten."""

from __future__ import annotations

import math

import pytest

from bot import statistiek
from tests.conftest import werkdagen


def test_rendement():
    assert statistiek.rendement_pct(1000, 1100) == pytest.approx(10.0)
    assert statistiek.rendement_pct(1000, 900) == pytest.approx(-10.0)
    assert statistiek.rendement_pct(1000, 1000) == pytest.approx(0.0)


def test_grootste_daling():
    # Van 120 naar 60 is een daling van 50%.
    assert statistiek.max_daling_pct([100, 120, 60, 90]) == pytest.approx(50.0)
    # Alleen maar stijgen betekent geen daling.
    assert statistiek.max_daling_pct([100, 110, 120]) == pytest.approx(0.0)
    assert statistiek.max_daling_pct([]) == 0.0


def test_winstpercentage():
    assert statistiek.win_pct([10, -5, 20, -1]) == pytest.approx(50.0)
    assert statistiek.win_pct([]) is None
    assert statistiek.win_pct([1, 2, 3]) == pytest.approx(100.0)


def test_sharpe_is_hoger_bij_minder_schommeling():
    datums = werkdagen(260)
    rustig = [1000.0 * (1.0003**i) for i in range(260)]
    onrustig = [1000.0 * (1.0003**i) * (1.03 if i % 2 else 0.97) for i in range(260)]
    s_rustig = statistiek.sharpe(rustig, datums)
    s_onrustig = statistiek.sharpe(onrustig, datums)
    assert s_rustig is not None and s_onrustig is not None
    assert s_rustig > s_onrustig


def test_sharpe_geeft_niets_bij_te_weinig_data():
    assert statistiek.sharpe([1000, 1010], ["2025-01-01", "2025-01-02"]) is None


def test_metingen_per_jaar():
    # Een jaar aan beursdagen levert ongeveer 252 metingen per jaar op.
    datums = werkdagen(252)
    assert 240 < statistiek.metingen_per_jaar(datums) < 275


def test_trades_nodig_wordt_kleiner_bij_een_groter_effect():
    klein_effect = [1.0, -0.5, 2.0, 0.5, -1.0, 1.5, 0.2, -0.3, 1.1, 0.4] * 3
    groot_effect = [x + 5.0 for x in klein_effect]
    n_klein = statistiek.trades_nodig_voor_uitspraak(klein_effect)
    n_groot = statistiek.trades_nodig_voor_uitspraak(groot_effect)
    assert n_klein is not None and n_groot is not None
    assert n_groot < n_klein


def test_trades_nodig_is_none_bij_verlies():
    assert statistiek.trades_nodig_voor_uitspraak([-1.0, -2.0, -0.5, -3.0, -1.5]) is None


def test_beoordeel_zegt_eerlijk_dat_het_geluk_kan_zijn():
    # Vijf winstgevende trades is veel te weinig voor een uitspraak.
    oordeel = statistiek.beoordeel([5.0, 4.0, 6.0, 3.0, 5.0], min_trades=30)
    assert oordeel.betekenisvol is False
    assert "geluk" in oordeel.uitleg
    assert oordeel.aantal == 5


def test_beoordeel_herkent_een_duidelijk_resultaat():
    # 100 trades die allemaal rond dezelfde winst zitten.
    resultaten = [5.0 + (i % 5) * 0.1 for i in range(100)]
    oordeel = statistiek.beoordeel(resultaten, min_trades=30)
    assert oordeel.betekenisvol is True
    assert oordeel.p_waarde is not None and oordeel.p_waarde <= 0.05


def test_beoordeel_zonder_trades():
    oordeel = statistiek.beoordeel([])
    assert oordeel.aantal == 0
    assert oordeel.betekenisvol is False


def test_beoordeel_verliesgevende_strategie_is_niet_betekenisvol():
    resultaten = [-3.0 + (i % 4) * 0.2 for i in range(100)]
    oordeel = statistiek.beoordeel(resultaten, min_trades=30)
    assert oordeel.betekenisvol is False


def test_p_waarde_ligt_tussen_nul_en_een():
    p = statistiek.bootstrap_p_waarde([1.0, -1.0, 2.0, -0.5, 0.3, 1.2, -0.8])
    assert p is not None
    assert 0.0 < p <= 1.0


def test_p_waarde_is_none_bij_te_weinig_waarnemingen():
    assert statistiek.bootstrap_p_waarde([1.0, 2.0]) is None


def test_dagrendementen():
    r = statistiek.dagrendementen([100.0, 110.0, 99.0])
    assert len(r) == 2
    assert r[0] == pytest.approx(0.10)
    assert r[1] == pytest.approx(-0.10)


def test_vergelijken_met_de_meetlat():
    # Een strategie die elke dag 0,1 procentpunt beter doet dan de meetlat.
    strategie = [0.002 + (i % 3) * 0.001 for i in range(200)]
    meetlat = [0.001 + (i % 3) * 0.001 for i in range(200)]
    p = statistiek.vergelijk_reeksen(strategie, meetlat)
    assert p is not None and p < 0.05


def test_vergelijken_geeft_niets_bij_te_korte_reeks():
    assert statistiek.vergelijk_reeksen([0.01] * 5, [0.01] * 5) is None
