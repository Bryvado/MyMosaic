"""Likely seats: the seat-count distribution behind the chart."""
from __future__ import annotations

import numpy as np
import pytest

from mosaic import likely_seats as LS
from mosaic.scoring.partisan import score_majority_chance


def test_pmf_of_known_cases():
    assert np.allclose(LS.poisson_binomial_pmf(np.array([0.5, 0.5])),
                       [0.25, 0.5, 0.25])
    assert np.allclose(LS.poisson_binomial_pmf(np.array([1.0, 0.0, 1.0])),
                       [0, 0, 1, 0])
    assert np.allclose(LS.poisson_binomial_pmf(np.array([0.2, 0.7])),
                       [0.8 * 0.3, 0.2 * 0.3 + 0.8 * 0.7, 0.2 * 0.7])


def test_pmf_matches_brute_force():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.02, 0.98, size=7)
    want = np.zeros(8)
    for mask in range(1 << 7):
        bits = np.array([(mask >> i) & 1 for i in range(7)])
        want[bits.sum()] += np.prod(np.where(bits, p, 1 - p))
    assert np.allclose(LS.poisson_binomial_pmf(p), want)


def test_pmf_rows_are_independent():
    p = np.array([[0.1, 0.9, 0.4], [0.5, 0.5, 0.5]])
    out = LS.poisson_binomial_pmf(p)
    assert out.shape == (2, 4)
    for row, got in zip(p, out, strict=True):
        assert np.allclose(got, LS.poisson_binomial_pmf(row))


@pytest.mark.parametrize("n", [1, 4, 13, 14, 38])
def test_distribution_is_a_distribution(n):
    rng = np.random.default_rng(n)
    dist = LS.seat_distribution(rng.uniform(0.3, 0.7, size=n), 0.9, 0.03)
    assert dist.shape == (n + 1,)
    assert np.all(dist >= 0) and np.isclose(dist.sum(), 1.0)


def test_safe_seats_are_certain():
    dist = LS.seat_distribution(np.array([0.95, 0.9, 0.05, 0.1, 0.92]), 0.9, 0.03)
    assert np.argmax(dist) == 3 and dist[3] > 0.999


def test_even_plan_is_symmetric():
    shares = np.array([0.42, 0.47, 0.5, 0.53, 0.58])
    dist = LS.seat_distribution(shares, 0.9, 0.03)
    assert np.allclose(dist, dist[::-1], atol=1e-9)
    assert np.isclose(LS.summary(dist)[2], 2.5)


def test_statewide_swing_widens_the_spread():
    shares = np.full(10, 0.5)
    no_swing = LS.seat_distribution(shares, 0.9, 1e-6)
    swing = LS.seat_distribution(shares, 0.9, 0.05)
    # Districts that move together make lopsided outcomes more likely.
    assert swing[0] + swing[10] > 10 * (no_swing[0] + no_swing[10])


@pytest.mark.parametrize("n", [13, 14])
def test_agrees_with_the_majority_chance_score(n):
    """The chart and the score read the same model: summing the chart's bars
    for a Democratic majority gives the score's probability."""
    rng = np.random.default_rng(11)
    units = 400
    assignment = np.arange(units) % n
    dem = rng.integers(100, 900, size=units)
    gop = rng.integers(100, 900, size=units)
    p_dem, p_rep, _, _ = score_majority_chance(assignment, dem, gop, n, 0.9, 0.03)
    dist = LS.seat_distribution(LS.district_shares(assignment, dem, gop, n),
                                0.9, 0.03)
    assert np.isclose(dist[n // 2 + 1:].sum(), p_dem, atol=1e-9)
    assert np.isclose(dist[:(n + 1) // 2].sum(), p_rep, atol=1e-9)


def test_district_shares_handle_an_empty_district():
    shares = LS.district_shares(np.array([0, 0, 2]), np.array([30, 10, 0]),
                                np.array([10, 30, 0]), 3)
    assert np.allclose(shares, [0.5, 0.5, 0.5])


def test_no_dearpygui_import():
    import sys
    assert "dearpygui" not in getattr(sys.modules["mosaic.likely_seats"],
                                      "__dict__", {})
    src = open(LS.__file__, encoding="utf-8").read()
    assert "dearpygui" not in src
