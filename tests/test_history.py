"""Bounded, decimating run history (mosaic.history)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from mosaic import history as H
from mosaic.history import RunHistory
from mosaic.scoring.score import PlanScore, ScoreConfig

ROOT = Path(__file__).resolve().parents[1]


def _fill(h: RunHistory, n: int, name: str = "a", start: int = 0) -> None:
    """Record every iteration from `start` to `n`, as the run does."""
    for i in range(start, n + 1):
        if h.due(i):
            h.record(i, {name: float(i)})


def test_nothing_is_dropped_until_full():
    h = RunHistory(capacity=1000)
    _fill(h, 999)
    iters, values = h.series("a")
    assert h.stride == 1 and h.n == 1000
    assert list(iters) == list(range(1000))
    assert list(values) == [float(i) for i in range(1000)]


def test_decimation_keeps_the_first_point_and_halves_the_rate():
    h = RunHistory(capacity=1000)
    _fill(h, 1000)                     # the 1001st point triggers it
    iters, values = h.series("a")
    assert h.stride == 2
    assert iters[0] == 0 and values[0] == 0.0
    assert list(iters) == list(range(0, 1002, 2))[:len(iters)]
    # every kept point still carries its own iteration's value
    assert np.array_equal(values, iters.astype(float))
    assert h.n == 501


def test_series_span_the_whole_run_however_long():
    h = RunHistory(capacity=1000)
    _fill(h, 50_000)
    iters, values = h.series("a")
    assert h.n <= 1000
    assert iters[0] == 0
    # the last recorded point is within one stride of the end
    assert 50_000 - iters[-1] < h.stride
    # evenly spaced: every gap is the stride
    assert set(np.diff(iters).tolist()) == {h.stride}
    assert np.array_equal(values, iters.astype(float))
    assert h.stride & (h.stride - 1) == 0          # a power of two


def test_the_last_point_is_exact_through_the_tail():
    h = RunHistory(capacity=1000)
    cfg = ScoreConfig()
    h.begin_run(cfg)
    last = 0
    for i in range(0, 12_345):
        h.tick(i, PlanScore(total=float(i), cut_edges=i))
        last = i
    iters, cols = h.columns((H.SCORE,), with_tail=True)
    assert iters[-1] == last and cols[H.SCORE][-1] == float(last)
    without, _ = h.columns((H.SCORE,), with_tail=False)
    assert without[-1] < last                       # thinned: tail adds the end
    assert iters[0] == 0 and cols[H.SCORE][0] == 0.0
    assert h.tail()[0] == last


def test_x_indices_are_iterations_not_positions():
    h = RunHistory(capacity=100)
    _fill(h, 1_000)
    gen, pos, xs, ys = h.delta("a", -1, 0)
    assert gen == h.generation and pos == h.n
    assert xs[0] == 0 and xs[1] == h.stride
    assert np.array_equal(xs.astype(float), ys)


def test_a_late_series_is_nan_before_it_starts_and_aligned_after():
    h = RunHistory(capacity=100)
    for i in range(10):
        h.record(i, {"a": 1.0})
    for i in range(10, 20):
        h.record(i, {"a": 2.0, "b": float(i)})
    iters, cols = h.columns(("a", "b"))
    assert len(iters) == len(cols["a"]) == len(cols["b"]) == 20
    assert np.isnan(cols["b"][:10]).all()
    assert list(cols["b"][10:]) == [float(i) for i in range(10, 20)]
    h.record(20, {"a": 3.0})                       # b stops: NaN again
    assert np.isnan(h.series("b")[1][-1])


def test_unknown_series_reads_as_nan():
    h = RunHistory(capacity=100)
    h.record(0, {"a": 1.0})
    assert np.isnan(h.series("nope")[1]).all()


def test_delta_is_incremental_and_restarts_after_a_rewrite():
    h = RunHistory(capacity=100)
    gen, pos = -1, 0
    seen_x = []
    for i in range(0, 5_000):
        if h.due(i):
            h.record(i, {"a": float(i)})
        if i % 7 == 0:
            gen2, pos, xs, _ = h.delta("a", gen, pos)
            if gen2 != gen:
                seen_x = []                         # a rewrite: start over
            seen_x.extend(xs.tolist())
            gen = gen2
    gen, pos, xs, _ = h.delta("a", gen, pos)
    seen_x.extend(xs.tolist() if gen == h.generation else [])
    assert seen_x == h.series("a")[0].tolist()


def test_truncate_after_keeps_points_up_to_the_iteration():
    h = RunHistory(capacity=100)
    _fill(h, 1_000)                                 # stride 16 by now
    before = h.generation
    h.truncate_after(500)
    iters, _ = h.series("a")
    assert iters[-1] <= 500 and 500 - iters[-1] < h.stride
    assert h.generation != before                   # readers reload


def test_truncate_drops_a_newer_tail():
    h = RunHistory(capacity=100)
    h.begin_run(ScoreConfig())
    for i in range(300):
        h.tick(i, PlanScore(total=float(i), cut_edges=0))
    h.truncate_after(100)
    assert h.tail() is None


def test_reset_forgets_the_run_but_not_what_charts_asked_for():
    h = RunHistory(capacity=100)
    h.extra = frozenset({"mm_history"})
    _fill(h, 500)
    gen = h.generation
    h.reset()
    assert h.n == 0 and h.stride == 1 and h.names() == []
    assert h.generation != gen
    assert h.extra == frozenset({"mm_history"})
    _fill(h, 10)
    assert h.n == 11


def test_non_increasing_iterations_are_ignored():
    h = RunHistory(capacity=100)
    h.record(5, {"a": 1.0})
    assert not h.record(5, {"a": 2.0})
    assert not h.record(3, {"a": 2.0})
    assert h.n == 1


def test_capacity_must_be_even():
    with pytest.raises(ValueError):
        RunHistory(capacity=101)


# ── Which series a run records ───────────────────────────────────────────────

def test_only_scores_that_are_on_are_recorded():
    cfg = ScoreConfig()                             # cut edges only
    assert H.wanted_keys(cfg) == H.ALWAYS
    cfg = ScoreConfig(weight_mean_median=1.0, weight_reock=2.0)
    keys = H.wanted_keys(cfg)
    assert {"mm_history", "reock_history"} <= keys
    assert "eg_history" not in keys and "pp_history" not in keys
    # compactness drives both its components
    keys = H.wanted_keys(ScoreConfig(weight_holistic_compactness=1.0))
    assert {"holistic_compactness_history", "pp_history", "reock_history"} <= keys


def test_an_open_chart_asks_for_its_series():
    keys = H.wanted_keys(ScoreConfig(), {"eg_history", "not_a_series"})
    assert "eg_history" in keys and "not_a_series" not in keys


def test_every_series_has_a_reader_and_a_gate():
    assert set(H.SERIES_WEIGHTS) <= set(H._FIELDS)
    assert set(H._FIELDS) - set(H.SERIES_WEIGHTS) == {H.SCORE, H.CUT_EDGES}
    ps = PlanScore(total=3.0, cut_edges=7)
    values = H.plan_values(ps, ScoreConfig(), H._FIELDS)
    assert values[H.SCORE] == 3.0 and values[H.CUT_EDGES] == 7.0


def test_unweighted_gated_scores_record_a_gap_not_a_false_zero():
    ps = PlanScore(total=1.0, cut_edges=0, hinge_chance=0.4)
    off = H.plan_values(ps, ScoreConfig(), ["hinge_history"])
    on = H.plan_values(ps, ScoreConfig(weight_hinge=1.0), ["hinge_history"])
    assert np.isnan(off["hinge_history"]) and on["hinge_history"] == 0.4


def test_the_run_records_through_tick_with_the_right_series():
    h = RunHistory(capacity=100)
    h.begin_run(ScoreConfig(weight_mean_median=1.0))
    h.extra = frozenset({"eg_history"})
    rates = iter([None] * 5 + [0.25] * 1000)
    for i in range(0, 20):
        h.tick(i, PlanScore(total=10.0 - i, cut_edges=i, mean_median=0.5,
                            efficiency_gap=0.1),
               temperature=2.0, accept_rate=lambda: next(rates))
    assert set(h.names()) == {H.SCORE, H.CUT_EDGES, "mm_history", "eg_history",
                              H.TEMPERATURE, H.ACCEPTANCE}
    assert h.series(H.TEMPERATURE)[1][0] == 2.0
    # the acceptance rate appears once enough worse moves exist
    rate = h.series(H.ACCEPTANCE)[1]
    assert np.isnan(rate[:5]).all() and rate[-1] == 0.25


def test_without_annealing_there_is_no_temperature_series():
    h = RunHistory(capacity=100)
    h.begin_run(ScoreConfig())
    for i in range(5):
        h.tick(i, PlanScore(total=1.0, cut_edges=0))
    assert not h.has(H.TEMPERATURE)


# ── Memory ───────────────────────────────────────────────────────────────────

def test_arrays_are_fixed_size_float64():
    h = RunHistory(capacity=1000)
    h.record(0, {"a": 1.0, "b": 2.0})
    assert h.nbytes == 3 * 1000 * 8
    _fill(h, 100_000, "a")
    assert h.nbytes == 3 * 1000 * 8             # bounded, however long


_LONG_RUN = r"""
import resource, sys, math
sys.path.insert(0, {src!r})
from mosaic import history as H
from mosaic.scoring.score import PlanScore, ScoreConfig
cfg = ScoreConfig(weight_mean_median=1.0, weight_efficiency_gap=1.0,
                  weight_holistic_compactness=1.0, weight_county_excess=1.0,
                  weight_pop_deviation=1.0, weight_hinge=1.0)
h = H.RunHistory()
h.begin_run(cfg)
# Every series on, as an open chart would ask for.
h.extra = H.ALL_SERIES
ps = PlanScore(total=1.0, cut_edges=1)
N = {n}
def rss():
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * 4096
rate = lambda: 0.5
base = None
for i in range(N + 1):
    ps.total = float(i)
    h.tick(i, ps, 1.0, rate)
    if i == 2_000_000:
        base = rss()
print(h.n, h.stride, rss(), base, h.nbytes)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_ten_million_iterations_stay_inside_a_fixed_memory_bound():
    """The failure this guards: ~45 lists of floats reached 3.1 GB by 2M
    iterations. Here every series is on, ten times as long, in a fresh
    interpreter so the figure is the history's and not the test suite's."""
    code = _LONG_RUN.format(src=str(ROOT / "src"), n=10_000_000)
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                         text=True, timeout=600, env={**os.environ, "NUMBA_DISABLE_JIT": "1"})
    assert out.returncode == 0, out.stderr
    n, stride, rss, at_2m, held = (int(x) for x in out.stdout.split())
    assert n <= H.DEFAULT_CAPACITY
    assert stride == 64                              # 10M / 200k, rounded up to 2^k
    # Every series on: 43 float64 columns + iterations, at capacity.
    assert held <= (len(H.ALL_SERIES) + 1) * H.DEFAULT_CAPACITY * 8
    assert rss < 400 * 1024 * 1024                   # the whole process
    # And it levels off: 8M more iterations add almost nothing.
    assert rss - at_2m < 30 * 1024 * 1024
