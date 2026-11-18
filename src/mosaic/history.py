"""Bounded run history.

A run records one value per series per iteration for the charts. That is fine
for 100,000 iterations and not for 10,000,000: as Python lists of floats the
~45 series cost ~1.5 KB per iteration and were never trimmed, so a long run on
a small server was killed for memory (about 3.1 GB at 2M iterations).

`RunHistory` keeps memory fixed instead. Each series is a preallocated float64
array of at most `capacity` points, sharing one int64 array of iteration
indices. When the arrays fill, every second point is dropped and the recording
stride doubles, so the history always spans the whole run evenly, at a
resolution that halves each time the run doubles. Recorded iterations are
always multiples of the stride, so points thin out and never shuffle.

No Dear PyGui here, and no locking: the caller holds `SharedState._lock`.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping

import numpy as np

DEFAULT_CAPACITY = 200_000      # points per series
_NAN = math.nan


class RunHistory:
    """Decimating history for many named series that share one iteration axis.

    `generation` changes whenever recorded points are rewritten or dropped
    (decimation, truncation, reset). A reader holding a position from an older
    generation must start over; `delta` does that for it.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        if capacity < 4 or capacity % 2:
            raise ValueError("capacity must be an even number, at least 4")
        self.capacity = capacity
        self.n = 0                  # points recorded
        self.stride = 1             # a point is recorded every `stride` iterations
        self.generation = 0
        self._iters: np.ndarray | None = None    # int64, allocated on first use
        self._cols: dict[str, np.ndarray] = {}
        # The newest iteration, kept exactly even when it is not a recorded
        # point: (iteration, plan score, extras). See `tail`.
        self._tail: tuple | None = None
        # Plan series: set by `begin_run`, used by `tick` and `tail`.
        self._config = None
        self._keys: frozenset = frozenset()
        self._keys_for: tuple | None = None
        # Series a chart or visual that is open needs even when its score is
        # off. Written by the interface, read by the run.
        self.extra: frozenset = frozenset()

    # ── Recording ────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Forget the run. Keeps what the interface asked for (`extra`)."""
        self.n = 0
        self.stride = 1
        self.generation += 1
        self._cols = {}
        self._tail = None
        self._config = None
        self._keys_for = None

    def due(self, iteration: int) -> bool:
        """Whether `iteration` is a recorded one at the current stride."""
        return iteration % self.stride == 0

    def record(self, iteration: int, values: Mapping[str, float]) -> bool:
        """Record one point. A series missing from `values` gets NaN, so every
        series stays aligned to the shared iteration axis. Returns whether a
        point was stored (a point that stops being due once the full arrays are
        decimated is not)."""
        n = self.n
        if self._iters is None:
            self._iters = np.empty(self.capacity, dtype=np.int64)
        elif n and iteration <= self._iters[n - 1]:
            return False
        if n == self.capacity:
            self._decimate()
            n = self.n
            if iteration % self.stride:
                return False
        self._iters[n] = iteration
        for name, col in self._cols.items():
            col[n] = values.get(name, _NAN)
        for name in values.keys() - self._cols.keys():
            col = np.empty(self.capacity, dtype=np.float64)
            col[:n] = _NAN              # started after the run did
            col[n] = values[name]
            self._cols[name] = col
        self.n = n + 1
        return True

    def _decimate(self) -> None:
        """Keep every second point; from now on record every second stride.
        The first point survives, and so does an even spacing."""
        half = self.capacity // 2
        self._iters[:half] = self._iters[0:self.capacity:2]
        for col in self._cols.values():
            col[:half] = col[0:self.capacity:2]
        self.n = half
        self.stride *= 2
        self.generation += 1

    def truncate_after(self, iteration: int) -> None:
        """Drop points after `iteration` (Revert to best). The series then
        ends at the last recorded point at or before it."""
        if self._iters is not None and self.n:
            keep = int(np.searchsorted(self._iters[:self.n], iteration,
                                       side="right"))
            if keep < self.n:
                self.n = keep
        if self._tail is not None and self._tail[0] > iteration:
            self._tail = None
        self.generation += 1

    # ── Reading ──────────────────────────────────────────────────────────────

    def names(self) -> list[str]:
        return list(self._cols)

    def has(self, name: str) -> bool:
        return name in self._cols

    @property
    def nbytes(self) -> int:
        """Memory held by the recorded arrays."""
        iters = self._iters.nbytes if self._iters is not None else 0
        return iters + sum(col.nbytes for col in self._cols.values())

    def delta(self, name: str, generation: int, pos: int
              ) -> tuple[int, int, np.ndarray, np.ndarray]:
        """Points of one series recorded since a reader's last call.

        Returns (generation, new position, iterations, values), as copies. A
        reader from an older generation gets the whole series again."""
        if generation != self.generation:
            pos = 0
        n = self.n
        col = self._cols.get(name)
        if col is None or pos >= n:
            return self.generation, n, _EMPTY_I, _EMPTY_F
        return (self.generation, n,
                self._iters[pos:n].copy(), col[pos:n].copy())

    def series(self, name: str, *, with_tail: bool = False
               ) -> tuple[np.ndarray, np.ndarray]:
        """(iterations, values) for one series, as copies."""
        iters, cols = self.columns((name,), with_tail=with_tail)
        return iters, cols[name]

    def columns(self, names: Iterable[str], *, with_tail: bool = False
                ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Iterations and the named series, aligned and as copies. A series
        that was never recorded is all NaN. With `with_tail` the newest
        iteration is appended when it is not already the last point."""
        n = self.n
        names = list(names)
        if self._iters is None or n == 0:
            iters = _EMPTY_I
        else:
            iters = self._iters[:n].copy()
        out = {}
        for name in names:
            col = self._cols.get(name)
            out[name] = (col[:n].copy() if col is not None and n
                         else np.full(n, _NAN))
        if with_tail:
            tail = self.tail()
            if tail is not None and (n == 0 or tail[0] > iters[-1]):
                iters = np.append(iters, tail[0])
                for name in names:
                    out[name] = np.append(out[name], tail[1].get(name, _NAN))
        return iters, out

    def span(self) -> tuple[int, int]:
        """(first, last) iteration recorded, or (0, 0)."""
        if self._iters is None or self.n == 0:
            return 0, 0
        return int(self._iters[0]), int(self._iters[self.n - 1])

    # ── Plan series ──────────────────────────────────────────────────────────
    # The run hands over a plan score per iteration; the history decides which
    # series that is worth recording and only does so when a point is due.

    def begin_run(self, score_config) -> None:
        """Start a run: which scores are on decides what is recorded."""
        self.reset()
        self._config = score_config

    def tick(self, iteration: int, plan_score, temperature: float | None = None,
             accept_rate: Callable[[], float | None] | None = None) -> None:
        """Offer one iteration. Cheap when it is not due: it only remembers
        the newest iteration so the series can still end exactly there."""
        if self.due(iteration):
            rate = accept_rate() if accept_rate is not None else None
            self.record(iteration,
                        self._values(plan_score, temperature, rate))
        self._tail = (iteration, plan_score, temperature)

    def tail(self) -> tuple[int, dict[str, float]] | None:
        """The newest iteration's values, recorded or not: (iteration, values).
        The chart of the live plan (the Comet Plot's dot) ends here."""
        if self._tail is None or self._config is None:
            return None
        iteration, plan_score, temperature = self._tail
        return iteration, self._values(plan_score, temperature, None)

    def wanted(self) -> frozenset:
        """Series to record now: the always-on ones, those whose score has a
        weight, and those an open chart asked for."""
        key = (id(self._config), self.extra)
        if key != self._keys_for:
            self._keys_for = key
            self._keys = wanted_keys(self._config, self.extra)
        return self._keys

    def _values(self, plan_score, temperature, accept_rate) -> dict:
        values = plan_values(plan_score, self._config, self.wanted())
        if temperature is not None:
            values[TEMPERATURE] = temperature
        if accept_rate is not None and ACCEPTANCE in self.wanted():
            values[ACCEPTANCE] = accept_rate
        return values


_EMPTY_I = np.empty(0, dtype=np.int64)
_EMPTY_F = np.empty(0, dtype=np.float64)


# ── What a run records ───────────────────────────────────────────────────────
# Series keep the names the interface already used for them.

SCORE = "score_history"
TEMPERATURE = "temperature_history"
ACCEPTANCE = "acceptance_rate_history"   # fraction of worse moves accepted, 0-1
CUT_EDGES = "cut_edges_history"

# Recorded whatever is switched on: the score itself, the annealing readouts,
# and the one measure the Comet Plot always offers.
ALWAYS = frozenset({SCORE, TEMPERATURE, ACCEPTANCE, CUT_EDGES})

# Series -> the score weights that switch it on (any one non-zero).
_COUNTIES = ("weight_county_excess", "weight_county_unified")
_COMPACT = ("weight_holistic_compactness",)
_REPRESENTATION = ("weight_representation",)
_COHESION = ("weight_minority_cohesion",)
_CONGRUENCE = ("weight_community_congruence",)
SERIES_WEIGHTS: dict[str, tuple[str, ...]] = {
    "county_splits_score_history": _COUNTIES,
    "county_excess_splits_history": _COUNTIES,
    "county_unified_districts_history": _COUNTIES,
    "mm_history": ("weight_mean_median",),
    "eg_history": ("weight_efficiency_gap",),
    "partisan_bias_history": ("weight_partisan_bias",),
    "partisan_gini_history": ("weight_partisan_gini",),
    "dem_seats_history": ("weight_dem_seats",),
    "pp_history": ("weight_polsby_popper",) + _COMPACT,
    "reock_history": ("weight_reock",) + _COMPACT,
    "holistic_compactness_history": _COMPACT,
    "holistic_splitting_history": ("weight_holistic_splitting",),
    "holistic_proportionality_history": ("weight_holistic_proportionality",),
    "inversion_history": ("weight_holistic_proportionality",),
    "holistic_competitiveness_history": ("weight_holistic_competitiveness",),
    "pop_deviation_history": ("weight_pop_deviation",),
    "pop_dev_max_history": ("weight_pop_deviation",),
    "pop_dev_mean_history": ("weight_pop_deviation",),
    "alignment_penalty_history": ("weight_alignment",),
    "alignment_mean_ret_history": ("weight_alignment",),
    "alignment_min_ret_history": ("weight_alignment",),
    "majority_dem_history": ("weight_majority_chance_dem",),
    "majority_rep_history": ("weight_majority_chance_rep",),
    "hinge_history": ("weight_hinge",),
    "representation_overall_history": _REPRESENTATION,
    "representation_black_history": _REPRESENTATION,
    "representation_latino_history": _REPRESENTATION,
    "representation_asian_history": _REPRESENTATION,
    "representation_black_seats_history": _REPRESENTATION,
    "representation_latino_seats_history": _REPRESENTATION,
    "representation_asian_seats_history": _REPRESENTATION,
    "minority_cohesion_overall_history": _COHESION,
    "cohesion_black_history": _COHESION,
    "cohesion_latino_history": _COHESION,
    "cohesion_asian_history": _COHESION,
    "community_congruence_overall_history": _CONGRUENCE,
    "congruence_black_history": _CONGRUENCE,
    "congruence_latino_history": _CONGRUENCE,
    "congruence_asian_history": _CONGRUENCE,
}

# Series -> how to read it off a plan score. `score_plan` skips the "gated"
# ones when their weight is 0, leaving a meaningless 0 in the field; those are
# recorded as NaN then, which charts as a gap instead of a false zero.
_GATED = {
    "partisan_gini_history": "weight_partisan_gini",
    "holistic_proportionality_history": "weight_holistic_proportionality",
    "holistic_competitiveness_history": "weight_holistic_competitiveness",
    "majority_dem_history": "weight_majority_chance_dem",
    "majority_rep_history": "weight_majority_chance_rep",
    "hinge_history": "weight_hinge",
    "inversion_history": "weight_holistic_proportionality",
}
_FIELDS: dict[str, Callable] = {
    SCORE: lambda p: p.total,
    CUT_EDGES: lambda p: p.cut_edges,
    "county_splits_score_history":
        lambda p: p.county_excess_score + p.county_unified_score,
    "county_excess_splits_history": lambda p: p.county_excess_splits,
    "county_unified_districts_history": lambda p: p.county_unified_districts,
    "mm_history": lambda p: p.mean_median,
    "eg_history": lambda p: p.efficiency_gap,
    "partisan_bias_history": lambda p: p.partisan_bias,
    "partisan_gini_history": lambda p: p.partisan_gini,
    "dem_seats_history": lambda p: p.dem_seats,
    "pp_history": lambda p: 1.0 - p.polsby_popper / 100.0,
    "reock_history": lambda p: 1.0 - p.reock / 100.0,
    "holistic_compactness_history": lambda p: p.holistic_compactness,
    "holistic_splitting_history": lambda p: p.holistic_splitting,
    "holistic_proportionality_history": lambda p: p.holistic_proportionality,
    "inversion_history": lambda p: p.inversion_chance,
    "holistic_competitiveness_history": lambda p: p.holistic_competitiveness,
    "pop_deviation_history": lambda p: p.pop_deviation,
    "pop_dev_max_history": lambda p: p.pop_dev_max,
    "pop_dev_mean_history": lambda p: p.pop_dev_mean,
    "alignment_penalty_history": lambda p: p.alignment,
    "alignment_mean_ret_history": lambda p: p.alignment_mean_ret,
    "alignment_min_ret_history": lambda p: p.alignment_min_ret,
    "majority_dem_history": lambda p: p.majority_chance_dem,
    "majority_rep_history": lambda p: p.majority_chance_rep,
    "hinge_history": lambda p: p.hinge_chance,
    "representation_overall_history": lambda p: p.representation,
    "representation_black_history": lambda p: p.representation_black,
    "representation_latino_history": lambda p: p.representation_latino,
    "representation_asian_history": lambda p: p.representation_asian,
    "representation_black_seats_history": lambda p: p.opportunity_black,
    "representation_latino_seats_history": lambda p: p.opportunity_latino,
    "representation_asian_seats_history": lambda p: p.opportunity_asian,
    "minority_cohesion_overall_history": lambda p: p.minority_cohesion,
    "cohesion_black_history": lambda p: p.cohesion_black,
    "cohesion_latino_history": lambda p: p.cohesion_latino,
    "cohesion_asian_history": lambda p: p.cohesion_asian,
    "community_congruence_overall_history": lambda p: p.community_congruence,
    "congruence_black_history": lambda p: p.congruence_black,
    "congruence_latino_history": lambda p: p.congruence_latino,
    "congruence_asian_history": lambda p: p.congruence_asian,
}

# Every series a run can record.
ALL_SERIES: frozenset = frozenset(_FIELDS) | {TEMPERATURE, ACCEPTANCE}


def wanted_keys(score_config, extra: Iterable[str] = ()) -> frozenset:
    """Series to record for this configuration: the always-on ones, those of
    scores with a weight, and `extra` (what an open chart is showing)."""
    keys = set(ALWAYS)
    if score_config is not None:
        for name, weights in SERIES_WEIGHTS.items():
            if any(getattr(score_config, w, 0) for w in weights):
                keys.add(name)
    keys.update(k for k in extra if k in ALL_SERIES)
    return frozenset(keys)


def plan_values(plan_score, score_config, keys: Iterable[str]) -> dict:
    """The named series' values for one plan score."""
    values = {}
    for key in keys:
        read = _FIELDS.get(key)
        if read is None:
            continue
        gate = _GATED.get(key)
        if gate is not None and not getattr(score_config, gate, 0):
            values[key] = _NAN
        else:
            values[key] = float(read(plan_score))
    return values
