"""Calibrating the Goals ladder to the loaded map.

``mosaic.calibration`` measures a map's own reference penalties and
``mosaic.goals`` derives weights from whichever table is in force. Neither
imports Dear PyGui, so all of this runs without a display. The measurement
test loads the bundled North Carolina sample through the same runner the app
uses, and checks the medians against the table of provenance in ``goals``.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from mosaic import calibration as C
from mosaic import goals as G

ROOT = Path(__file__).resolve().parents[1]
SHAPEFILE = ROOT / "shapefiles" / "North_Carolina_Simplified.shp"

# The spread each reference was measured over, transcribed from the table in
# goals.py. A median taken the same way has to land inside its own range; a
# measurement that drifts out of one means the two no longer describe the same
# calculation. Keys the sample cannot measure (alignment, which needs a
# reference plan) and the favor-mode figures (never measured; see note (d))
# are absent on purpose.
NC_RANGES = {
    "holistic_compactness": (49.6, 57.7),
    "holistic_splitting": (93.4, 101.4),
    "holistic_proportionality": (15.9, 30.3),
    "holistic_competitiveness": (39.8, 60.7),
    "partisan_gini": (1.6, 12.9),
    "representation": (74.1, 87.1),
    "minority_cohesion": (42.0, 57.5),
    "community_congruence": (37.2, 57.0),
    "dem_seats": (56.2, 60.8),
    "majority_chance": (64.3, 83.8),
    "pop_deviation": (22.2, 36.1),
    "mean_median_fair": (2.2, 14.4),
    "efficiency_gap_fair": (8.6, 19.8),
    "partisan_bias_fair": (0.8, 22.4),
}

ALL_GIDS = [g.gid for g in G.GOALS]
LADDER_ON = [x for _name, x in G.LADDER if x > 0]


@pytest.fixture(autouse=True)
def default_table():
    """Every test starts and ends on the shipped table: the active one is
    module state, and a leaked custom table would quietly change the goals
    tests that run after it."""
    G.reset_reference()
    yield
    G.reset_reference()


def _doubled() -> dict[str, float]:
    """A custom table: every reference twice the default, which halves every
    weight without changing which goals can reach which steps."""
    return {k: v * 2.0 for k, v in G.default_reference().items()}


# ── The active table ─────────────────────────────────────────────────────────

def test_default_table_is_in_force_until_it_is_replaced():
    assert G.active_reference() == G.default_reference()
    assert G.reference_source() == ""


def test_goal_weights_follow_the_active_table():
    before = G.goal_weights("compact", 60)
    G.set_reference(_doubled(), "twice")
    after = G.goal_weights("compact", 60)
    assert G.reference_source() == "twice"
    for sec, keys in before.items():
        for key, value in keys.items():
            assert after[sec][key] == pytest.approx(value / 2.0, abs=0.51)
            assert after[sec][key] < value


def test_goal_state_reads_the_active_table():
    """Weights written under one table read back as a different word under
    another: the strength is derived, never stored."""
    from mosaic.presets import default_preset
    preset = G.apply_goals(default_preset(), {"compact": 60})
    assert G.goal_state(preset, "compact").label == "Strong"
    G.set_reference(_doubled(), "twice")
    state = G.goal_state(preset, "compact")
    # The weight did not move; halving the reference doubles what it buys, so
    # the same slider now reads as a stronger word.
    assert state.strength > 60.0
    assert state.label != "Strong"


def test_strength_round_trip_holds_under_a_custom_table():
    from mosaic.presets import default_preset
    G.set_reference(_doubled(), "twice")
    for gid in ALL_GIDS:
        for x in LADDER_ON:
            preset = G.apply_goals(default_preset(), {gid: x})
            state = G.goal_state(preset, gid)
            assert state.exact, f"{gid} at {x} did not read back exactly"
            assert state.strength == pytest.approx(float(x))


def test_reset_restores_the_shipped_table():
    G.set_reference(_doubled(), "twice")
    G.reset_reference()
    assert G.active_reference() == G.default_reference()
    assert G.reference_source() == ""


def test_an_unknown_or_impossible_reference_is_refused():
    with pytest.raises(ValueError):
        G.set_reference({"not_a_score": 10.0}, "bad")
    with pytest.raises(ValueError):
        G.set_reference({"holistic_compactness": 0.0}, "bad")
    with pytest.raises(ValueError):
        G.set_reference({"holistic_compactness": float("inf")}, "bad")
    assert G.active_reference() == G.default_reference()


# ── The floor ────────────────────────────────────────────────────────────────

def test_the_floor_keeps_every_dominant_weight_on_the_slider():
    """A near-zero measurement is the case the floor exists for: without it a
    budget of 5,000 divided by 0.2 would ask a slider that stops at 100 for a
    weight of 25,000, and Dominant would clamp."""
    tiny = {k: 0.2 for k in G.default_reference()}
    floored = {k: max(v, G.min_reference(k)) for k, v in tiny.items()}
    G.set_reference(floored, "tiny")
    for gid in ALL_GIDS:
        for sec, keys in G.goal_weights(gid, 100).items():
            for key, value in keys.items():
                assert value <= 100.0, f"{gid}: {sec}.{key} = {value}"


def test_min_reference_covers_every_reference_key():
    for key in G.default_reference():
        assert G.min_reference(key) > 0.0
        assert math.isfinite(G.min_reference(key))


# ── Measuring the sample ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def nc_runner():
    """The North Carolina sample, loaded through the app's own runner."""
    pytest.importorskip("geopandas")
    from mosaic.gui.runner import AlgorithmRunner
    from mosaic.gui.state import SharedState
    from mosaic.io.inspect import ShapefileConfig, inspect_shapefile

    state = SharedState()
    runner = AlgorithmRunner(state)
    inspection = inspect_shapefile(str(SHAPEFILE))
    config = ShapefileConfig(
        pop_col=inspection.hint_pop_col, id_col=inspection.hint_id_col,
        county_col=inspection.hint_county_col,
        elections=[inspection.hint_election] if inspection.hint_election else [],
        demographics=inspection.hint_race)
    assert runner.complete_load(inspection, config), state.error_message
    return runner


@pytest.fixture(scope="module")
def nc_reference(nc_runner):
    """Measured the way the table in goals says it was: three fresh plans,
    14 districts, 5% tolerance."""
    return C.measure_reference_penalties(nc_runner, 14, 0.05, n_plans=3)


def test_the_sample_measures_inside_its_documented_ranges(nc_reference):
    for key, (low, high) in NC_RANGES.items():
        assert key in nc_reference.raw, f"{key} was not measured"
        value = nc_reference.raw[key]
        assert low <= value <= high, f"{key} measured {value:.2f}, not in {low}-{high}"


def test_the_sample_keeps_defaults_for_what_it_cannot_measure(nc_reference):
    # No reference plan ships with the sample, so Plan Continuity has nothing
    # to be continuous with and keeps its shipped figure.
    assert "alignment" not in nc_reference.raw
    assert nc_reference.refs["alignment"] == G.default_reference()["alignment"]
    assert "alignment" not in nc_reference.measured


def test_the_sample_measures_both_modes_of_the_directional_three(nc_reference):
    for stem in ("mean_median", "efficiency_gap", "partisan_bias"):
        fair = nc_reference.raw[f"{stem}_fair"]
        favor = nc_reference.raw[f"{stem}_favor"]
        # Favor mode scores the distance from a fully skewed map, which sits
        # near the middle of its range on a plan that is not skewed; fair mode
        # scores the distance from symmetry, which is small there.
        assert favor > fair

def test_the_measured_table_is_usable_and_keeps_every_word(nc_reference):
    from mosaic.presets import default_preset
    G.set_reference(nc_reference.refs, nc_reference.source)
    assert "14 districts" in G.reference_source()
    for gid in ALL_GIDS:
        for x in LADDER_ON:
            preset = G.apply_goals(default_preset(), {gid: x})
            assert G.goal_state(preset, gid).exact
            for sec, keys in G.goal_weights(gid, x).items():
                for value in keys.values():
                    assert 0.0 < value <= 100.0


def test_measuring_refuses_an_unloaded_or_degenerate_map(nc_runner):
    with pytest.raises(ValueError):
        C.measure_reference_penalties(None, 14, 0.05)
    with pytest.raises(ValueError):
        C.measure_reference_penalties(nc_runner, 1, 0.05)
    with pytest.raises(ValueError):
        C.measure_reference_penalties(nc_runner, 14, 0.05, n_plans=0)
