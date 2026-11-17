"""Likely seats: the chance of each Democratic seat count for one plan.

Uses the win-probability model the scorers already use (`build_p_wins_matrix`:
a district-level error plus a statewide swing shared by every district), so
the chart agrees with Win Chance and with the majority-chance score. Nothing
here feeds back into scoring, and nothing imports Dear PyGui.
"""
from __future__ import annotations

import numpy as np

from mosaic.scoring.partisan import (
    _GH_NORM,
    _GH_WEIGHTS,
    build_p_wins_matrix,
    k_to_sigma,
)


def poisson_binomial_pmf(p: np.ndarray) -> np.ndarray:
    """P(exactly k successes), k = 0..n, for independent trials with
    probabilities ``p``. ``p`` may be (n,) or (M, n); the result is (n + 1,)
    or (M, n + 1)."""
    p = np.asarray(p, dtype=np.float64)
    single = p.ndim == 1
    rows = p[None, :] if single else p
    n = rows.shape[1]
    pmf = np.zeros((rows.shape[0], n + 1))
    pmf[:, 0] = 1.0
    for i in range(n):
        win = rows[:, i:i + 1]
        nxt = pmf[:, :i + 2] * (1.0 - win)
        nxt[:, 1:] += pmf[:, :i + 1] * win
        pmf[:, :i + 2] = nxt
    return pmf[0] if single else pmf


def seat_distribution(shares: np.ndarray, win_prob_at_55: float,
                      swing_sigma: float) -> np.ndarray:
    """Probability of each Democratic seat count, 0..n districts.

    ``shares`` are the districts' Democratic two-party shares. Districts are
    independent only once the statewide swing is fixed, so the Poisson-binomial
    is taken at each swing node and the nodes are averaged with their weights.
    """
    shares = np.asarray(shares, dtype=np.float64)
    if shares.size == 0:
        return np.ones(1)
    p_wins = build_p_wins_matrix(shares, k_to_sigma(win_prob_at_55), swing_sigma)
    per_node = poisson_binomial_pmf(p_wins)                 # (M, n + 1)
    dist = (_GH_WEIGHTS @ per_node) / _GH_NORM
    total = dist.sum()
    return dist / total if total > 0 else dist


def district_shares(assignment: np.ndarray, dem: np.ndarray, gop: np.ndarray,
                    n_districts: int) -> np.ndarray:
    """Democratic two-party share per district; 0.5 where nobody voted."""
    d = np.bincount(assignment, weights=np.asarray(dem, np.float64),
                    minlength=n_districts)
    g = np.bincount(assignment, weights=np.asarray(gop, np.float64),
                    minlength=n_districts)
    tot = d + g
    return np.where(tot > 0, d / np.where(tot > 0, tot, 1.0), 0.5)


def summary(dist: np.ndarray) -> tuple[int, float, float]:
    """(most likely seat count, its probability, expected seats)."""
    k = int(np.argmax(dist))
    return k, float(dist[k]), float(np.dot(np.arange(len(dist)), dist))
