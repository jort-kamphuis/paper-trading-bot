"""De toets van de hoofdvraag: klopt de t-verdeling, en zegt hij bij geen verschil hooguit ~5% 'ja'?"""

from __future__ import annotations

import numpy as np
import pytest

from bot import statistiek


@pytest.mark.parametrize("t, df, verwacht", [
    (2.015, 5, 0.05),    # tabelwaarden van de t-verdeling (eenzijdig)
    (2.571, 5, 0.025),
    (1.833, 9, 0.05),
    (2.920, 2, 0.05),
    (0.0, 4, 0.5),
])
def test_t_verdeling_klopt_met_de_tabel(t, df, verwacht):
    assert statistiek.t_overschrijdingskans(t, df) == pytest.approx(verwacht, abs=6e-4)


def test_negatieve_t_geeft_grote_p():
    assert statistiek.t_overschrijdingskans(-2.015, 5) == pytest.approx(0.95, abs=6e-4)


def _vals_alarm(n: int, maak, herhalingen: int = 4000, zaad: int = 7) -> float:
    rng = np.random.default_rng(zaad)
    raak = 0
    for _ in range(herhalingen):
        p = statistiek.blokgemiddelden_p_waarde(list(maak(rng, n)), blok=10)
        raak += p is not None and p <= 0.05
    return raak / herhalingen


def _ar1(rng, n, phi=0.3):
    x = np.empty(n)
    x[0] = rng.normal()
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal()
    return x * 0.01


@pytest.mark.parametrize("n", [30, 40, 61])
@pytest.mark.parametrize("soort", ["normaal", "dikke_staarten", "samenhang"])
def test_bij_geen_verschil_hooguit_vijf_procent_betekenisvol(n, soort):
    maak = {
        "normaal": lambda rng, n: rng.normal(0, 0.01, n),
        "dikke_staarten": lambda rng, n: rng.standard_t(3, n) * 0.01,
        "samenhang": _ar1,
    }[soort]
    kans = _vals_alarm(n, maak)
    # 4000 herhalingen: de meetfout rond 5% is ongeveer 0,35 procentpunt.
    assert kans <= 0.062, f"{soort}, n={n}: {100 * kans:.1f}% vals alarm"


def test_een_echt_verschil_wordt_wel_gezien():
    rng = np.random.default_rng(3)
    raak = sum(
        (statistiek.blokgemiddelden_p_waarde(list(rng.normal(0.004, 0.01, 61))) or 1) <= 0.05
        for _ in range(500)
    )
    assert raak / 500 > 0.5


def test_te_kort_geeft_geen_uitspraak():
    assert statistiek.blokgemiddelden_p_waarde([0.01] * 29) is None
    assert statistiek.blokgemiddelden_p_waarde([0.01, -0.01] * 15) is not None


def test_vergelijk_reeksen_gebruikt_de_nieuwe_toets():
    rng = np.random.default_rng(5)
    a = list(rng.normal(0.003, 0.01, 61))
    b = list(rng.normal(0.0, 0.01, 61))
    verschil = [x - y for x, y in zip(a, b)]
    assert statistiek.vergelijk_reeksen(a, b) == pytest.approx(statistiek.blokgemiddelden_p_waarde(verschil))
