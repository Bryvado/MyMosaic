"""Reweighting a run while it is running.

``run_algorithm`` used to read its score config once, at the top, so weights
and Goals changed during a run did nothing until the next Start. It now takes
a pending config at the top of an iteration, re-scores both the current and
the best plan on it, rebases the annealing temperature and carries on.

These run the real loop on the bundled North Carolina sample, at small
iteration counts, with no display anywhere.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from mosaic.gui.state import AlgorithmStatus
from mosaic.recom.annealing import AnnealingConfig
from mosaic.scoring import score_plan
from mosaic.scoring.score import ScoreConfig

ROOT = Path(__file__).resolve().parents[1]
SHAPEFILE = ROOT / "shapefiles" / "North_Carolina_Simplified.shp"

K = 8               # districts: small enough to partition quickly
TOL = 0.05
ITERS = 400
SEED = 1

# What a plain 400-iteration run of this configuration reaches. Frozen from
# the behaviour before live reweighting existed, so a run with nothing pending
# is held to the same chain it always produced.
GOLDEN = {
    "current_score": 2413.2742827776974,
    "best_score": 2084.093199848716,
    "best_iteration": 183,
    "current_iteration": 400,
    "cut_edges": 411,
}

START = ScoreConfig(weight_holistic_compactness=50.0)


@pytest.fixture(scope="module")
def runner():
    pytest.importorskip("geopandas")
    from mosaic.gui.runner import AlgorithmRunner
    from mosaic.gui.state import SharedState
    from mosaic.io.inspect import ShapefileConfig, inspect_shapefile

    state = SharedState()
    algo = AlgorithmRunner(state)
    inspection = inspect_shapefile(str(SHAPEFILE))
    config = ShapefileConfig(
        pop_col=inspection.hint_pop_col, id_col=inspection.hint_id_col,
        county_col=inspection.hint_county_col,
        elections=[inspection.hint_election] if inspection.hint_election else [],
        demographics=inspection.hint_race)
    assert algo.complete_load(inspection, config), state.error_message
    return algo


def _arm(runner, score_config, *, iterations=ITERS, annealing=False,
         seed=SEED):
    """Put one run's settings into shared state, as the interface does."""
    state = runner.state
    state.reset_run()
    state.update(
        num_districts=K, pop_tolerance=TOL, max_iterations=iterations,
        seed=seed, score_config=score_config,
        annealing_config=AnnealingConfig(enabled=annealing),
        tolerance_ratchet_mode="off", tree_mode="compiled",
        county_bias_enabled=False, county_bias=5.0,
        n3_probability=0.0, n3_max_attempts_per_stage=20,
        flip_enabled=False, flip_midpoint=0.835,
        hot_start_assignment=None, hot_start_locked_districts=(),
    )
    return state


def _wait(predicate, timeout=180.0, step=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def _start(runner):
    thread = threading.Thread(target=runner.run_algorithm, daemon=True,
                              name="algo-test")
    thread.start()
    return thread


def _paused_run(runner, score_config, **arm):
    """Start a run and hold it, paused, at a known point."""
    state = _arm(runner, score_config, **arm)
    thread = _start(runner)
    assert _wait(lambda: state.status is AlgorithmStatus.RUNNING
                 and state.current_iteration > 5), state.error_message
    state.request_pause()
    assert _wait(lambda: state.status is AlgorithmStatus.PAUSED), state.error_message
    return state, thread


def _finish(state, thread):
    state.request_stop()
    state.request_resume()
    thread.join(timeout=120.0)
    assert not thread.is_alive()
    assert state.status is not AlgorithmStatus.ERROR, state.error_message


def _score_of(runner, assignment, score_config):
    """score_plan of one plan under one config, prepared the way the run
    prepares it."""
    kwargs, _cache = runner._prepare_scoring(score_config, assignment)
    return score_plan(runner.graph_ctx.compute_cut_edges(assignment),
                      score_config, assignment=assignment, **kwargs)


# ── A run with nothing pending is the run it always was ──────────────────────

def test_a_run_with_no_pending_config_is_unchanged(runner):
    state = _arm(runner, START)
    runner.run_algorithm()
    assert state.status is AlgorithmStatus.COMPLETED, state.error_message
    assert state.reweight_count == 0
    assert state.pending_score_config is None
    assert state.current_iteration == GOLDEN["current_iteration"]
    assert state.current_score == GOLDEN["current_score"]
    assert state.best_score == GOLDEN["best_score"]
    assert state.best_iteration == GOLDEN["best_iteration"]
    assert state.current_cut_edges == GOLDEN["cut_edges"]
    assert state.history.marks() == ()


# ── A pending config is taken, applied once, and cleared ─────────────────────

def test_a_pending_config_is_picked_up_and_cleared(runner):
    state, thread = _paused_run(runner, START)
    heavier = ScoreConfig(weight_holistic_compactness=50.0,
                          weight_pop_deviation=10.0)
    state.request_reweight(heavier)
    assert _wait(lambda: state.reweight_count == 1), "the reweight was not taken"
    assert state.pending_score_config is None
    assert state.score_config is heavier
    assert state.history.marks() != ()
    assert state.status is AlgorithmStatus.PAUSED      # still paused
    assert "Weights updated at iteration" in state.status_message
    _finish(state, thread)


def test_the_scores_on_screen_are_the_new_config_scores(runner):
    """current_score and best_score are score_plan of their own plans under
    the config now in force -- not the old totals left behind."""
    state, thread = _paused_run(runner, START)
    heavier = ScoreConfig(weight_holistic_compactness=50.0,
                          weight_pop_deviation=10.0,
                          weight_cut_edges=1.0)
    state.request_reweight(heavier)
    assert _wait(lambda: state.reweight_count == 1)
    current = state.current_assignment.copy()
    best = state.best_assignment.copy()
    assert state.current_score == pytest.approx(
        _score_of(runner, current, heavier).total)
    assert state.best_score == pytest.approx(
        _score_of(runner, best, heavier).total)
    # The best plan is the better of the two under the new weights.
    assert state.best_score <= state.current_score
    assert state.score_breakdown and state.best_score_breakdown
    _finish(state, thread)


def test_the_current_plan_can_become_the_best_one(runner):
    """Re-scoring can reorder the two: whichever is lower under the new
    weights is the best plan, and the iteration is now."""
    state, thread = _paused_run(runner, START)
    # Population deviation at a large weight is a different objective; on a
    # compactness-driven chain the newest plan is usually the tighter one.
    state.request_reweight(ScoreConfig(weight_pop_deviation=60.0))
    assert _wait(lambda: state.reweight_count == 1)
    best = state.best_assignment.copy()
    current = state.current_assignment.copy()
    if state.best_iteration == state.current_iteration:
        assert (best == current).all()
    assert state.best_score == pytest.approx(
        _score_of(runner, best, state.score_config).total)
    _finish(state, thread)


# ── Switching a score on mid-run ─────────────────────────────────────────────

@pytest.mark.parametrize("extra", [
    pytest.param({"weight_representation": 20.0}, id="electoral-opportunity"),
    pytest.param({"weight_holistic_compactness": 50.0}, id="compactness"),
    pytest.param({"weight_minority_cohesion": 10.0}, id="neighborhood-severance"),
    pytest.param({"weight_community_congruence": 10.0}, id="community-dispersion"),
    pytest.param({"weight_holistic_splitting": 30.0}, id="county-congruence"),
    pytest.param({"weight_polsby_popper": 10.0, "weight_reock": 10.0},
                 id="polsby-popper-and-reock"),
    pytest.param({"weight_holistic_proportionality": 20.0}, id="proportionality"),
])
def test_a_score_that_was_off_at_start_can_be_switched_on(runner, extra):
    """Nothing a score needs is prepared when its weight is zero -- the
    opportunity geography, the compactness cache -- so switching one on is the
    case that has to keep working."""
    state, thread = _paused_run(runner, ScoreConfig(weight_cut_edges=1.0))
    before = state.current_score
    config = ScoreConfig(weight_cut_edges=1.0, **extra)
    state.request_reweight(config)
    assert _wait(lambda: state.reweight_count == 1), state.error_message
    assert state.status is not AlgorithmStatus.ERROR, state.error_message
    assert state.current_score > before          # the new penalty is charged
    assert state.current_score == pytest.approx(
        _score_of(runner, state.current_assignment.copy(), config).total)
    # ... and the chain keeps going on the new objective.
    state.request_resume()
    assert _wait(lambda: state.current_iteration > 20
                 or state.status is AlgorithmStatus.COMPLETED), state.error_message
    assert state.status is not AlgorithmStatus.ERROR, state.error_message
    _finish(state, thread)


def test_a_score_can_be_switched_off_mid_run(runner):
    state, thread = _paused_run(runner, ScoreConfig(
        weight_cut_edges=1.0, weight_holistic_compactness=50.0))
    bare = ScoreConfig(weight_cut_edges=1.0)
    state.request_reweight(bare)
    assert _wait(lambda: state.reweight_count == 1)
    assert state.current_score == pytest.approx(
        _score_of(runner, state.current_assignment.copy(), bare).total)
    _finish(state, thread)


# ── Temperature ──────────────────────────────────────────────────────────────

def test_the_temperature_is_rescaled_by_the_score_ratio(runner):
    """Temperature is a scale on score differences, so it is multiplied by the
    ratio the current plan's total moved by (the cooling rate, a
    per-iteration ratio, is left alone)."""
    # Compactness alone, with Cut Edges off (it is on at weight 1 by default),
    # so doubling the one weight doubles the total exactly and the expected
    # factor is known rather than merely observed.
    only = ScoreConfig(weight_cut_edges=0.0, weight_holistic_compactness=50.0)
    state, thread = _paused_run(runner, only, annealing=True)
    old_total = state.current_score
    old_temp = state.current_temperature
    assert old_temp > 0.0
    state.request_reweight(ScoreConfig(weight_cut_edges=0.0,
                                       weight_holistic_compactness=100.0))
    assert _wait(lambda: state.reweight_count == 1)
    ratio = state.current_score / old_total
    assert ratio == pytest.approx(2.0)
    assert state.current_temperature == pytest.approx(old_temp * ratio)
    _finish(state, thread)


# ── Reweighting while the chain is running, not paused ───────────────────────

def test_a_reweight_lands_on_a_running_chain(runner):
    state = _arm(runner, START, iterations=4000)
    thread = _start(runner)
    assert _wait(lambda: state.status is AlgorithmStatus.RUNNING
                 and state.current_iteration > 5), state.error_message
    config = ScoreConfig(weight_holistic_compactness=50.0,
                         weight_pop_deviation=10.0)
    state.request_reweight(config)
    assert _wait(lambda: state.reweight_count == 1), "the reweight was not taken"
    assert state.score_config is config
    marks = state.history.marks()
    assert len(marks) == 1 and marks[0] >= 1
    _finish(state, thread)


def test_only_the_newest_pending_config_is_applied(runner):
    state, thread = _paused_run(runner, START)
    state.request_reweight(ScoreConfig(weight_cut_edges=1.0))
    final = ScoreConfig(weight_cut_edges=2.0)
    state.request_reweight(final)
    assert _wait(lambda: state.reweight_count == 1)
    time.sleep(0.2)
    assert state.reweight_count == 1
    assert state.score_config is final
    _finish(state, thread)
