"""Measure a map's own reference penalties for ``mosaic.goals``.

A goal turns a word into weights with ``weight = budget * share / reference``.
The references in ``goals._REF`` were measured once, on the bundled North
Carolina sample at 14 districts and 5% tolerance (see the table in that
module). They are the right numbers for that map and only roughly right for
any other: a state whose precincts are twice as numerous cuts about twice as
many edges, so "Strong" on it does not carry the influence the word names.

This module measures the same penalties on the loaded map, so a strength means
the same thing wherever it is set. It builds a few fresh partitions -- fresh,
not optimized, for the reason the table gives: weights do their steering early,
when penalties are large, and an optimized plan's penalty is whatever the
weights already drove it to -- scores each one with every available score
weighted at 1, and takes the median raw penalty per reference key.

Nothing here imports Dear PyGui and nothing here changes scoring math: every
penalty comes back out of ``score_plan``. The three directional metrics are
the one exception to "read it off PlanScore", because their penalty is
mode-dependent and PlanScore stores only the raw value; they are re-derived
from that raw value exactly as the Score Contributors panel derives the
weighted contribution it draws.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field

from mosaic import goals as G
from mosaic.recom import create_initial_partition
from mosaic.scoring import score_plan
from mosaic.scoring.score import ScoreConfig

log = logging.getLogger("mosaic")


class Cancelled(Exception):
    """Raised when a measurement is stopped before it finished."""

# Measurement settings are deliberately the ScoreConfig defaults, not the
# user's current ones: the result is cached against the map, the district count,
# the tolerance and the chosen columns, so it must not also depend on a curve
# slider that is not in that key. The one setting swept is the directional
# mode, which is what the _fair / _favor pairs in _REF exist for.
_FAVOR_MODE = "favor_dem"

# Scores measured, as (reference key, which data the map must supply, the
# ScoreConfig weight that switches the score on, how to read its penalty).
# "" needs nothing beyond populations.
_SIMPLE: tuple[tuple[str, str, str], ...] = (
    ("holistic_compactness", "geometry", "weight_holistic_compactness"),
    ("holistic_splitting", "county", "weight_holistic_splitting"),
    ("holistic_proportionality", "election", "weight_holistic_proportionality"),
    ("holistic_competitiveness", "election", "weight_holistic_competitiveness"),
    ("partisan_gini", "election", "weight_partisan_gini"),
    ("representation", "demographic", "weight_representation"),
    ("minority_cohesion", "demographic", "weight_minority_cohesion"),
    ("community_congruence", "demographic", "weight_community_congruence"),
    ("dem_seats", "election", "weight_dem_seats"),
    ("majority_chance", "election", "weight_majority_chance_dem"),
    ("pop_deviation", "", "weight_pop_deviation"),
    ("alignment", "reference", "weight_alignment"),
)

# The directional three, measured once per mode. (stem, weight field, the
# PlanScore attribute holding the raw value, the ScoreConfig mode field, the
# ScoreConfig bound field).
_DIRECTIONAL: tuple[tuple[str, str, str, str, str], ...] = (
    ("mean_median", "weight_mean_median", "mean_median", "mm_mode", "mm_bound"),
    ("efficiency_gap", "weight_efficiency_gap", "efficiency_gap", "eg_mode",
     "eg_bound"),
    ("partisan_bias", "weight_partisan_bias", "partisan_bias", "pbias_mode",
     "pbias_bound"),
)


@dataclass(frozen=True)
class Reference:
    """One measurement of a map's reference penalties.

    ``refs`` is the table to hand to ``goals.set_reference``: medians with the
    floor applied, and the shipped default kept for every score this map could
    not measure. ``raw`` is the medians before the floor, which is what a test
    or a curious user wants to compare against the table in ``goals``.
    """

    refs: dict[str, float]
    raw: dict[str, float] = field(default_factory=dict)
    measured: tuple[str, ...] = ()
    num_districts: int = 0
    tolerance: float = 0.0
    n_plans: int = 0

    @property
    def source(self) -> str:
        """The line the Goals panel shows under its calibrate button."""
        return (f"this map at {self.num_districts} districts, "
                f"{self.tolerance * 100:g}% tolerance")


def _available(runner) -> dict[str, bool]:
    """What the loaded map supplies, in the vocabulary of _SIMPLE's second
    column. A score whose data is absent keeps its shipped reference: it has
    nothing to measure here, and a measured 0 would mean "weightless", not
    "neutral"."""
    return {
        "": True,
        "geometry": runner.pp_data is not None and runner.reock_data is not None,
        "county": runner.county_array is not None,
        "election": bool(runner.election_arrays),
        "demographic": runner.vap_data is not None,
        # Not measured without a reference plan to be continuous with -- the
        # same reason note (e) in goals gives for the shipped figure.
        "reference": runner.alignment_data is not None,
    }


def _measure_config(have: dict[str, bool], mode: str) -> ScoreConfig:
    """Every available score at weight 1, the directional three in one mode.

    Weight 1 is what makes the penalty read directly as "penalty at weight 1",
    which is what a reference is. Everything else is a ScoreConfig default.
    """
    weights = {w: 1.0 for _ref, need, w in _SIMPLE if have[need]}
    if have["election"]:
        weights.update({w: 1.0 for _stem, w, *_rest in _DIRECTIONAL})
    modes = ({f: mode for _stem, _w, _raw, f, _b in _DIRECTIONAL}
             if have["election"] else {})
    return ScoreConfig(**weights, **modes)


def _directional_penalty(raw: float, mode: str, bound: float,
                         quadratic: bool) -> float:
    """The penalty score_plan charges for one directional metric.

    PlanScore keeps only the raw value, so this re-derives the penalty from it,
    with the same arithmetic the Score Contributors panel uses to draw the
    metric's weighted contribution: a distance in [0, 1] from the aim, squared
    when the quadratic option is on, scaled to 100.
    """
    if mode == "favor_dem":
        d = max(0.0, min(1.0, (raw + bound) / (2.0 * bound)))
    elif mode == "favor_rep":
        d = max(0.0, min(1.0, (bound - raw) / (2.0 * bound)))
    else:
        d = min(1.0, abs(raw) / bound)
    return (d * d if quadratic else d) * 100.0


def _penalties(ps, cfg: ScoreConfig, have: dict[str, bool],
               mode: str) -> dict[str, float]:
    """Raw penalty per reference key for one scored plan in one mode."""
    out: dict[str, float] = {}
    reads = {
        "holistic_compactness": ps.holistic_compactness,
        "holistic_splitting": ps.holistic_splitting,
        "holistic_proportionality": ps.holistic_proportionality,
        "holistic_competitiveness": ps.holistic_competitiveness,
        "partisan_gini": ps.partisan_gini,
        "representation": ps.representation,
        "minority_cohesion": ps.minority_cohesion,
        "community_congruence": ps.community_congruence,
        "dem_seats": ps.dem_seats_penalty,
        # Chance of Majority is charged as its shortfall, raised to 1.5 and
        # scaled -- the same form the contributions panel draws.
        "majority_chance": (1.0 - ps.majority_chance_dem) ** 1.5 * 100.0,
        "pop_deviation": ps.pop_deviation,
        "alignment": ps.alignment,
    }
    for ref, need, _w in _SIMPLE:
        if have[need]:
            out[ref] = float(reads[ref])
    if have["election"]:
        suffix = "fair" if mode == "fair" else "favor"
        for stem, _w, attr, mode_field, bound_field in _DIRECTIONAL:
            out[f"{stem}_{suffix}"] = _directional_penalty(
                float(getattr(ps, attr)), getattr(cfg, mode_field),
                float(getattr(cfg, bound_field)),
                cfg.partisan_quadratic_penalty)
    return out


def _prepare(runner, num_districts: int, tolerance: float,
             cfg: ScoreConfig) -> dict:
    """Run the per-run precompute a real run does; return score_plan's inputs.

    The two community scores are precomputed per run rather than per load,
    because their expectations need the district and precinct counts. Without
    this they would measure 0 -- their best value -- and the minority goal
    would be calibrated against nothing. Electoral Opportunity's geography is
    warmed for the same reason, and its prepared bundle is handed to score_plan
    the way the runner hands it over, so the measurement matches a run.
    """
    from mosaic.scoring.community_congruence import (
        precompute_community_congruence_data,
    )
    from mosaic.scoring.minority_cohesion import precompute_minority_cohesion_data
    from mosaic.scoring.opportunity import _get_prep, warm_opportunity_geo

    ctx = runner.graph_ctx
    runner.minority_cohesion_data = precompute_minority_cohesion_data(
        runner.vap_data, ctx.edge_u, ctx.edge_v, ctx.real_edge_mask)
    runner.community_congruence_data = precompute_community_congruence_data(
        runner.vap_data, runner.populations,
        ctx.edge_u, ctx.edge_v, ctx.real_edge_mask)

    skw = runner.score_kwargs(num_districts, tolerance, cfg)
    if runner.vap_data is not None and cfg.weight_representation:
        geo = dict(midpoint=cfg.opportunity_midpoint,
                   steepness=cfg.opportunity_steepness,
                   solid=cfg.opportunity_solid,
                   coords=runner.opportunity_coords,
                   smart_targets=cfg.opportunity_smart_targets)
        warm_opportunity_geo(runner.vap_data, num_districts, **geo)
        skw["_opportunity_prep"] = _get_prep(
            runner.vap_data, num_districts, cfg.opportunity_midpoint,
            cfg.opportunity_steepness, cfg.opportunity_solid,
            coords=runner.opportunity_coords,
            smart_targets=cfg.opportunity_smart_targets)
    return skw


def measure_reference_penalties(runner, num_districts: int, tolerance: float,
                                n_plans: int = 3,
                                on_progress=None,
                                should_cancel=None) -> Reference:
    """Measure this map's reference penalties over ``n_plans`` fresh plans.

    Seeds are 1..n_plans, so the measurement is reproducible: the same map at
    the same district count and tolerance gives the same table, which is what
    lets the result be cached.

    ``on_progress(done, total)`` is called as each plan is scored, and
    ``should_cancel()`` is polled while partitioning; a cancelled measurement
    raises ``Cancelled``.
    """
    if runner is None or runner.graph is None or runner.populations is None \
            or runner.graph_ctx is None:
        raise ValueError("Load a map before calibrating.")
    if num_districts < 2:
        raise ValueError("Calibration needs at least two districts.")
    if n_plans < 1:
        raise ValueError("Calibration needs at least one plan.")

    have = _available(runner)
    cfgs = {"fair": _measure_config(have, "fair")}
    if have["election"]:
        cfgs[_FAVOR_MODE] = _measure_config(have, _FAVOR_MODE)
    # One precompute for the whole measurement: every config shares the same
    # map, district count and tolerance, and score_kwargs depends on no mode.
    skws = {mode: _prepare(runner, num_districts, tolerance, cfg)
            for mode, cfg in cfgs.items()}

    samples: dict[str, list[float]] = {}
    for i in range(1, n_plans + 1):
        assignment = create_initial_partition(
            runner.graph, runner.populations, num_districts, tolerance,
            seed=i, should_cancel=should_cancel)
        if assignment is None:
            raise Cancelled("Calibration cancelled.")
        cut_edges = runner.graph_ctx.compute_cut_edges(assignment)
        for mode, cfg in cfgs.items():
            ps = score_plan(cut_edges, cfg, assignment=assignment, **skws[mode])
            for key, value in _penalties(ps, cfg, have, mode).items():
                samples.setdefault(key, []).append(value)
        if on_progress is not None:
            on_progress(i, n_plans)

    raw = {key: float(statistics.median(vals)) for key, vals in samples.items()}
    refs = dict(G.default_reference())
    for key, value in raw.items():
        refs[key] = max(value, G.min_reference(key))
    measured = tuple(k for k in G.default_reference() if k in raw)
    log.info("Calibration at %d districts, %.3f tolerance over %d plans: %s",
             num_districts, tolerance, n_plans,
             ", ".join(f"{k}={refs[k]:.1f}" for k in measured))
    return Reference(refs=refs, raw=raw, measured=measured,
                     num_districts=num_districts, tolerance=tolerance,
                     n_plans=n_plans)
