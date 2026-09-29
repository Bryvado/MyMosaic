"""
County-splits scoring (lower = better; 0 = no avoidable splits and no missed
unified districts).

Two penalty terms, returned separately so the GUI / ScoreConfig can weight
them independently:

  excess_score = SCORE_SCALE * sum_c max(0, splits[c] - allowances[c])
      "this county touches too many districts"
      allowance[c] = ceil(county_pop[c] / ideal_pop)

  unified_score = SCORE_SCALE * (max_unified - unified_districts)
      "we built fewer single-county districts than were geometrically possible"
      max_unified       = sum_c floor(county_pop[c] / min_district_pop)
      unified_districts = districts whose precincts all live in one county

SCORE_SCALE = 10 sets the magnitude of both county terms. Equal numeric weights
do not guarantee equal optimization pressure relative to other metrics.
"""

from __future__ import annotations

import numpy as np
from numba import njit

_SCORE_SCALE = 10.0


@njit(cache=True)
def _county_counts(co_di_pop, allowances):
    n_counties, n_districts = co_di_pop.shape
    county_counts = np.zeros(n_districts, dtype=np.int32)
    excess = 0
    for c in range(n_counties):
        touches = 0
        for d in range(n_districts):
            if co_di_pop[c, d] > 0:
                touches += 1
                county_counts[d] += 1
        excess += max(0, touches - allowances[c])
    unified = 0
    for d in range(n_districts):
        if county_counts[d] == 1:
            unified += 1
    return excess, unified


def score_county_splits(
    assignment: np.ndarray,
    county_ids: np.ndarray,
    populations: np.ndarray,
    ideal_pop: float,
    tolerance: float,
    n_districts: int,
    county_data=None,
    co_di_pop=None,
) -> tuple[float, float, int, int]:
    """
    Args:
        assignment:   (n,) int32 district indices 0..k-1
        county_ids:   (n,) int32 county indices 0..C-1
        populations:  (n,) int/float precinct populations
        ideal_pop:    target population per district
        tolerance:    fractional pdev tolerance (e.g. 0.025)
        n_districts:  number of districts k
        county_data:  optional CountyData precomputed at startup; if provided,
                      the constant parts (county_pops, allowances, max_unified)
                      are taken from there instead of being recomputed.

    Returns:
        tuple of (excess_score, unified_score, excess_splits, unified_districts)
            excess_score      -- SCORE_SCALE * sum of over-allowance splits
            unified_score     -- SCORE_SCALE * (max_unified - unified_districts)
            excess_splits     -- raw integer count of over-allowance splits
            unified_districts -- raw integer count of single-county districts
    """
    if county_data is not None:
        pops_f      = county_data.pops_f
        n_counties  = county_data.n_counties
        county_pops = county_data.county_pops
        allowances  = county_data.allowances
        max_unified = county_data.max_clean
    else:
        n_counties  = int(county_ids.max()) + 1
        pops_f      = populations.astype(np.float64)
        county_pops = np.bincount(county_ids, weights=pops_f, minlength=n_counties)
        safe_ideal  = max(ideal_pop, 1.0)
        allowances  = np.ceil(county_pops / safe_ideal).astype(np.int32)
        min_dp      = max(ideal_pop * (1.0 - tolerance), 1.0)
        max_unified = int(np.floor(county_pops / min_dp).sum())

    # co_di_pop may be supplied by score_plan (shared with holistic_splitting)
    # to avoid rebuilding the identical CxD matrix twice per iteration.
    if co_di_pop is None:
        flat_idx   = (county_ids * n_districts + assignment).astype(np.int64)
        co_di_flat = np.bincount(flat_idx, weights=pops_f,
                                 minlength=n_counties * n_districts)
        co_di_pop  = co_di_flat.reshape(n_counties, n_districts)

    excess_total, unified_districts = _county_counts(co_di_pop, allowances)

    excess_score  = _SCORE_SCALE * float(excess_total)
    unified_score = _SCORE_SCALE * float(max_unified - unified_districts)
    return excess_score, unified_score, excess_total, unified_districts
