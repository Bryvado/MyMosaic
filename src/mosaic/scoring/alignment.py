"""Plan Continuity penalty against a reference plan loaded from CSV.

District labels are irrelevant; the score depends on the two partitions. For
each reference district ``a``, the scorer measures how its selected weight
(population or party votes) is distributed across proposed districts:

    cohesion_a = SUM_p f_{a,p}^2          # probability two weighted units remain
                                          # in the same proposed district
    continuity = SUM_a w_a * cohesion_a / SUM_a w_a
    penalty    = 100 * (1 - continuity / ceiling)

This is one-sided: it penalizes splitting reference districts but does not score
the purity of proposed districts. Merging a reference district whole into a
larger proposed district therefore incurs no continuity penalty. The returned
penalty is in ``[0, 100]`` with 0 best. The fixed population-capacity ceiling
removes unavoidable subdivision; it is an upper bound, not a geographic optimum.
Display components retain their unscaled percentages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from numba import njit

log = logging.getLogger("mosaic")


class AlignmentError(ValueError):
    """Raised when the reference plan CSV cannot be loaded/joined."""


@dataclass
class AlignmentData:
    """Reference plan, aligned to gdf row order.

    alt_assignment      -- (n,) int64 zero-indexed reference district per precinct
                           (int64 so the hot-path overlap pass never re-copies it)
    n_alt_districts     -- number of districts in the reference plan
    filename            -- source CSV name (for GUI display)
    alt_dem_by_district -- (n_alt,) D two-party votes per reference district, or
                           None if no election data was available at load. Used
                           to select which reference districts a party "wins"
                           without recomputing per iteration.
    alt_gop_by_district -- (n_alt,) R two-party votes per reference district, or None.
    alt_labels          -- (n_alt,) original district numbers from the CSV, indexed
                           by the densified 0..n_alt-1 id. Lets "Infer from
                           alignment" renumbering adopt the reference's own
                           numbers instead of the densified indices.
    """
    alt_assignment: np.ndarray
    n_alt_districts: int
    filename: str = ""
    alt_dem_by_district: Optional[np.ndarray] = None
    alt_gop_by_district: Optional[np.ndarray] = None
    alt_labels: Optional[np.ndarray] = None


def alignment_inputs(data, config, populations, dem_votes=None, gop_votes=None):
    """Resolve the same fixed weight and reference mask for scoring and scaling."""
    focus = config.alignment_party_focus
    if focus == "rep" and gop_votes is not None:
        weights = gop_votes
    elif focus == "dem" and dem_votes is not None:
        weights = dem_votes
    else:
        focus, weights = "none", populations
    mask = None
    if (config.alignment_restrict_to_party and focus in ("rep", "dem")
            and data.alt_dem_by_district is not None
            and data.alt_gop_by_district is not None):
        total = data.alt_dem_by_district + data.alt_gop_by_district
        votes = data.alt_gop_by_district if focus == "rep" else data.alt_dem_by_district
        share = np.divide(votes, total, out=np.zeros_like(total, dtype=float), where=total > 0)
        mask = share > config.alignment_win_threshold
    return weights, mask


def _column_split_loss(lower, upper, smallest, largest):
    """Lower bound on lost cohesion mass in one balanced proposed district."""
    # Relax reference sizes to any value in [smallest, largest], allowing reuse.
    # If whole references can fill any allowed size, no split is forced here.
    if np.ceil(lower / largest) <= np.floor(upper / smallest):
        return 0.0
    whole = np.floor(upper / smallest)
    lo = max(0.0, lower - whole * largest)
    hi = min(smallest, upper - whole * smallest)
    # A maximally concentrated packing has at most one partial reference.
    # Its concave loss is minimized at an endpoint of the remainder interval.
    return max(0.0, min(lo * (1.0 - lo / smallest), hi * (1.0 - hi / smallest)))


def precompute_alignment_ceiling(
    data, populations, n_districts, tolerance, *, weights=None, district_mask=None,
):
    """Fast upper bound on weighted mean cohesion; independent of the proposal.

    Each reference's weight is packed into chunks no larger than one district
    can hold. Party capacity uses fractional precincts in descending votes per
    resident order, a conservative bound even when precincts are indivisible.
    For residents, a second bound captures unavoidable splits when merging
    references into balanced proposed districts. Neither bound assumes geography.
    """
    pops = np.asarray(populations, dtype=np.float64)
    weights = pops if weights is None else np.asarray(weights, dtype=np.float64)
    alt = np.asarray(data.alt_assignment, dtype=np.int64)
    if (pops.shape != alt.shape or weights.shape != alt.shape
            or np.any(~np.isfinite(pops)) or np.any(pops < 0)
            or np.any(~np.isfinite(weights)) or np.any(weights < 0)):
        raise ValueError("Plan Continuity requires finite, nonnegative population and weights")
    if n_districts < 1 or not np.isfinite(tolerance) or not 0 <= tolerance <= 1:
        raise ValueError("Invalid district count or population tolerance")
    total_population = float(pops.sum())
    if total_population <= 0:
        return 1.0
    ideal = total_population / n_districts
    capacity = ideal * (1.0 + tolerance)
    masses = np.bincount(alt, weights=weights, minlength=data.n_alt_districts)
    selected = masses > 0
    if district_mask is not None:
        selected &= np.asarray(district_mask, dtype=bool)
    total_weight = float(masses[selected].sum())
    if total_weight <= 0:
        return 1.0
    residents = np.array_equal(weights, pops)
    numerator = 0.0
    for a in np.flatnonzero(selected):
        mass = masses[a]
        if residents:
            cap = min(mass, capacity)
        else:
            members = alt == a
            p, w = pops[members], weights[members]
            # Zero-population precincts consume no capacity in this relaxation.
            cap = float(w[p == 0].sum())
            positive = p > 0
            p, w = p[positive], w[positive]
            order = np.argsort(-(w / p), kind="stable")
            p, w = p[order], w[order]
            used = np.clip(capacity - (np.cumsum(p) - p), 0.0, p)
            cap += float(np.sum(w * (used / p)))
            cap = min(mass, cap)
        if cap <= 0:
            return 1.0
        whole = np.floor(mass / cap)
        remainder = max(0.0, mass - whole * cap)
        numerator += (whole * cap * cap + remainder * remainder) / mass
    ceiling = numerator / total_weight
    if residents and np.all(selected[masses > 0]):
        nonempty = masses[masses > 0]
        loss = _column_split_loss(
            ideal * (1.0 - tolerance), capacity, nonempty.min(), nonempty.max(),
        )
        ceiling = min(ceiling, 1.0 - n_districts * loss / total_population)
    # Round upward slightly so floating-point residue cannot understate the bound.
    return float(np.clip(ceiling + 1e-12, 1.0 / n_districts, 1.0))


def alignment_ceiling_for_config(
    data, config, populations, n_districts, tolerance, dem_votes=None, gop_votes=None,
):
    """Prepare normalization once per run, using its original tolerance."""
    if data is None or not config.weight_alignment:
        return 1.0
    weights, mask = alignment_inputs(data, config, populations, dem_votes, gop_votes)
    return precompute_alignment_ceiling(
        data, populations, n_districts, tolerance, weights=weights, district_mask=mask,
    )


def precompute_alignment_data(
    path: str | Path,
    *,
    gdf: gpd.GeoDataFrame,
    gdf_id_col: str,
    csv_id_col: str,
    csv_district_col: str,
    dem_votes: Optional[np.ndarray] = None,
    gop_votes: Optional[np.ndarray] = None,
) -> AlignmentData:
    """Load a reference plan CSV and align it to gdf row order.

    Precinct identifiers are joined as strings and reordered to match the
    GeoDataFrame. The reference plan may use a different district count; this
    loader checks the join but not contiguity or population tolerance.

    Raises AlignmentError with a user-facing message on any join failure.
    """
    path = Path(path)
    if not path.is_file():
        raise AlignmentError(f"File not found: {path}")

    # ID column as string so leading zeros survive (GEOID '08001...' must not
    # be coerced to int and silently break the join against the shapefile).
    try:
        df = pd.read_csv(path, dtype={csv_id_col: str})
    except Exception as e:
        raise AlignmentError(f"Could not read CSV: {e}") from e

    for col, kind in ((csv_id_col, "ID"), (csv_district_col, "district")):
        if col not in df.columns:
            raise AlignmentError(
                f"CSV does not have the selected {kind} column '{col}'. "
                f"Available: {list(df.columns)}"
            )

    n = len(gdf)
    if len(df) != n:
        raise AlignmentError(
            f"CSV has {len(df)} rows; shapefile has {n} precincts. "
            f"Row count must match exactly."
        )

    gdf_ids = gdf[gdf_id_col].astype(str).str.strip().values
    csv_ids = df[csv_id_col].astype(str).str.strip().values
    if set(gdf_ids) != set(csv_ids):
        missing = set(gdf_ids) - set(csv_ids)
        extra = set(csv_ids) - set(gdf_ids)
        bits = []
        if missing:
            bits.append(
                f"shapefile has {len(missing)} IDs not in CSV "
                f"(e.g. {sorted(missing)[:3]})"
            )
        if extra:
            bits.append(
                f"CSV has {len(extra)} IDs not in shapefile "
                f"(e.g. {sorted(extra)[:3]})"
            )
        raise AlignmentError(
            "Precinct IDs in reference CSV do not match shapefile: "
            + "; ".join(bits)
        )

    # Reorder CSV rows to gdf order so the assignment aligns to score inputs.
    order_map = {pid: i for i, pid in enumerate(gdf_ids)}
    df = df.assign(
        _pos=df[csv_id_col].astype(str).str.strip().map(order_map)
    ).sort_values("_pos")
    raw = df[csv_district_col].values

    if not np.issubdtype(raw.dtype, np.number):
        try:
            raw = raw.astype(np.int64)
        except (ValueError, TypeError) as e:
            raise AlignmentError(f"'district' column is not numeric: {e}") from e
    if np.isnan(raw.astype(np.float64)).any():
        raise AlignmentError("'district' column contains NaN / missing values")

    raw = raw.astype(np.int64)
    # Densify labels to a contiguous 0..k-1 range; we don't care what the
    # reference plan's labels are, only the partition they induce.
    # int64 (not int32): alt_assignment is static and feeds the numba overlap
    # pass every scored proposal, so storing it in the dtype the kernel wants
    # lets score_alignment's astype(copy=False) skip a per-iteration copy.
    # Keep the original labels (uniq) alongside the densified ids so the GUI can
    # renumber a plan to the reference's own district numbers ("Infer from
    # alignment"); the score itself only needs the partition.
    uniq, alt = np.unique(raw, return_inverse=True)
    alt = alt.astype(np.int64)
    alt_labels = uniq.astype(np.int64)
    n_alt = int(alt.max()) + 1

    # Per-reference-district two-party vote totals (if election data is present),
    # so the GUI can restrict scoring to the districts a party wins
    # without recomputing per iteration. Votes are in gdf row order, same as alt.
    alt_dem = alt_gop = None
    if dem_votes is not None and gop_votes is not None \
            and len(dem_votes) == n and len(gop_votes) == n:
        alt_dem = np.bincount(alt, weights=np.asarray(dem_votes, dtype=np.float64),
                              minlength=n_alt)
        alt_gop = np.bincount(alt, weights=np.asarray(gop_votes, dtype=np.float64),
                              minlength=n_alt)

    log.info(
        f"Plan Continuity reference loaded: {n} precincts, {n_alt} districts "
        f"(from {path.name}){' with votes' if alt_dem is not None else ''}"
    )
    return AlignmentData(
        alt_assignment=alt, n_alt_districts=n_alt, filename=path.name,
        alt_dem_by_district=alt_dem, alt_gop_by_district=alt_gop,
        alt_labels=alt_labels,
    )


@njit(cache=True)
def _overlap_matrix(
    alt: np.ndarray,
    prop: np.ndarray,
    pops: np.ndarray,
    n_alt: int,
    n_prop: int,
) -> np.ndarray:
    """Population-overlap matrix M[a, p] = pop in (alt district a AND proposed
    district p). One pass over precincts.
    """
    m = np.zeros((n_alt, n_prop), dtype=np.float64)
    for i in range(alt.shape[0]):
        m[alt[i], prop[i]] += pops[i]
    return m


def score_alignment(
    assignment: np.ndarray,
    alt_assignment: np.ndarray,
    weights: np.ndarray,
    n_alt_districts: int,
    n_districts: int,
    district_mask: Optional[np.ndarray] = None,
    return_components: bool = False,
    ceiling: float = 1.0,
):
    """Herfindahl cohesion penalty against the reference plan.

    Args:
        assignment:       (n,) proposed district indices 0..n_districts-1
        alt_assignment:   (n,) reference district indices 0..n_alt_districts-1
        weights:          (n,) per-precinct weight cohesion is measured in.
                          Population for plain alignment; a party's votes for
                          partisan alignment.
        n_alt_districts:  number of reference districts
        n_districts:      number of proposed districts
        district_mask:    optional (n_alt,) bool selecting which reference
                          districts to score. None = all. Unselected
                          reference districts simply don't contribute.
        return_components: also return (mean_cohesion_pct, min_cohesion_pct)
        ceiling: fixed run-level upper bound on mean cohesion, in (0, 1].

    Returns:
        penalty in [0, 100] (0 = at the ceiling), or
        (penalty, mean_cohesion_pct, min_cohesion_pct) when return_components=True.
    """
    # copy=False: skip the allocation when the input already has the kernel's
    # dtype. alt_assignment is stored int64 at load, so it never re-copies; the
    # proposed assignment / weights copy only if they arrive in another dtype.
    if not np.isfinite(ceiling) or not 0 < ceiling <= 1:
        raise ValueError("Plan Continuity ceiling must be in (0, 1]")
    m = _overlap_matrix(
        alt_assignment.astype(np.int64, copy=False),
        assignment.astype(np.int64, copy=False),
        weights.astype(np.float64, copy=False),
        n_alt_districts,
        n_districts,
    )
    pop_a = m.sum(axis=1)                       # (n_alt,) weighted ref-district mass

    # Restrict to the selected reference districts.
    if district_mask is not None:
        rows = np.where(district_mask)[0]
    else:
        rows = np.arange(n_alt_districts)
    if len(rows) == 0:
        return (0.0, 100.0, 100.0) if return_components else 0.0

    sub = m[rows, :]                            # (R, n_prop)
    sub_pop = pop_a[rows]
    total_pop = sub_pop.sum()
    if total_pop <= 0:
        return (0.0, 100.0, 100.0) if return_components else 0.0

    # Cohesion_a = sum_p f_ap^2 = P(two of district a's residents still together).
    # Full-distribution, label-free: a clean split scores better than a shatter.
    with np.errstate(invalid="ignore", divide="ignore"):
        f = np.where(sub_pop[:, None] > 0, sub / sub_pop[:, None], 0.0)
    cohesion = np.sum(f * f, axis=1)            # (R,) in [0, 1]
    mean_cohesion = float(np.sum(sub_pop * cohesion) / total_pop)
    penalty = 100.0 * np.clip(1.0 - mean_cohesion / ceiling, 0.0, 1.0)

    if not return_components:
        return float(penalty)

    mean_ret = 100.0 * mean_cohesion
    nonempty = sub_pop > 0
    min_ret = 100.0 * float(cohesion[nonempty].min()) if nonempty.any() else 100.0
    return float(penalty), float(mean_ret), float(min_ret)
