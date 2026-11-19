"""Manual Electoral Opportunity targets.

Each group's target -- how many districts the score counts for it -- is the
group's statewide share times the district count, rounded. With few districts
that rounding is the whole decision, so ``ScoreConfig`` can name the number
instead. These tests run on the bundled North Carolina sample through the same
runner the app uses, and none of them needs a display.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mosaic.recom import create_initial_partition
from mosaic.scoring import score_plan
from mosaic.scoring.opportunity import (
    GROUPS,
    _get_prep,
    compute_opportunity,
    config_targets,
    opportunity_targets,
)
from mosaic.scoring.representation import representation_from_opportunity
from mosaic.scoring.score import ScoreConfig

ROOT = Path(__file__).resolve().parents[1]
SHAPEFILE = ROOT / "shapefiles" / "North_Carolina_Simplified.shp"

K = 14            # districts, as the sample's own calibration table uses
TOL = 0.05

# What the automatic rule produces on this sample, frozen from the behaviour
# before manual targets existed. Any drift here is a change to a published
# score, not a refactor.
AUTOMATIC = {
    "total": 834.0488560521572,
    "representation": 84.04885605215723,
    "rating": 15.951144652857053,
    "black": 15.951144652857053,
    "latino": -1.0,
    "asian": -1.0,
    "opp_black": 0.30817047236305145,
    "opp_latino": 0.0,
    "opp_asian": 0.0,
}
# Black clears the heuristic gate at three districts; Latino and Asian do not,
# which is what makes them the cases a manual target is for.
AUTOMATIC_TARGETS = {"black": 3, "latino": 1, "asian": 1}


@pytest.fixture(scope="module")
def nc():
    """(runner, one fixed partition) for the sample."""
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
    assignment = create_initial_partition(
        runner.graph, runner.populations, K, TOL, seed=1)
    assert assignment is not None
    return runner, assignment


def _score(nc, **config_kwargs):
    runner, assignment = nc
    cfg = ScoreConfig(weight_representation=1.0, **config_kwargs)
    return score_plan(runner.graph_ctx.compute_cut_edges(assignment), cfg,
                      assignment=assignment,
                      **runner.score_kwargs(K, TOL, cfg))


def _opportunity(nc, targets=None):
    runner, assignment = nc
    return compute_opportunity(
        assignment, runner.vap_data, K, coords=runner.opportunity_coords,
        smart_targets=True, targets=targets)


def _targets(nc, targets=None):
    runner, _assignment = nc
    return opportunity_targets(runner.vap_data, K,
                               coords=runner.opportunity_coords,
                               smart_targets=True, targets=targets)


# ── Automatic is unchanged ───────────────────────────────────────────────────

def test_automatic_scoring_is_unchanged(nc):
    """Every score field, to the bit, with no target named."""
    ps = _score(nc)
    assert ps.total == AUTOMATIC["total"]
    assert ps.representation == AUTOMATIC["representation"]
    assert ps.representation_rating == AUTOMATIC["rating"]
    assert ps.representation_black == AUTOMATIC["black"]
    assert ps.representation_latino == AUTOMATIC["latino"]
    assert ps.representation_asian == AUTOMATIC["asian"]
    assert ps.opportunity_black == AUTOMATIC["opp_black"]
    assert ps.opportunity_latino == AUTOMATIC["opp_latino"]
    assert ps.opportunity_asian == AUTOMATIC["opp_asian"]


def test_an_explicit_minus_one_is_the_same_as_saying_nothing(nc):
    assert config_targets(ScoreConfig()) == {g: -1 for g in GROUPS}
    explicit = _score(nc, opportunity_target_black=-1,
                      opportunity_target_latino=-1,
                      opportunity_target_asian=-1)
    assert explicit.total == AUTOMATIC["total"]
    assert _targets(nc, {g: -1 for g in GROUPS}) == _targets(nc)


def test_automatic_targets_are_the_rounded_proportional_share(nc):
    rows = _targets(nc)
    for group, want in AUTOMATIC_TARGETS.items():
        assert rows[group]["target"] == want
        assert rows[group]["auto_target"] == want
        assert rows[group]["manual"] is False
        assert rows[group]["target"] == round(rows[group]["T"])


# ── An override moves the target, the ceiling and the rating ─────────────────

def test_an_override_changes_the_target_the_ceiling_and_the_rating(nc):
    auto = _targets(nc)["black"]
    tight = _targets(nc, {"black": 1})["black"]
    wide = _targets(nc, {"black": 4})["black"]

    assert (tight["target"], wide["target"]) == (1, 4)
    assert tight["manual"] is wide["manual"] is True
    assert tight["auto_target"] == wide["auto_target"] == auto["target"]
    # The ceiling is the credit of the best `target` bundles, so asking for
    # fewer districts lowers it and asking for more raises it.
    assert tight["ceiling"] < auto["ceiling"] < wide["ceiling"]

    base = _score(nc).representation_black
    fewer = _score(nc, opportunity_target_black=1).representation_black
    more = _score(nc, opportunity_target_black=4).representation_black
    # The rating is counted credit over that ceiling. One district has to
    # carry the whole rating when only one is counted, and this plan's best
    # Black district is a strong one, so asking for fewer raises the rating
    # and asking for more lowers it. (Far above the automatic target the
    # heuristic runs out of bundles and the ceiling stops growing, so the
    # rating is not monotone in the target over the whole range -- that is the
    # existing ceiling heuristic, not the override.)
    assert fewer > base > more


def test_a_target_is_clamped_to_the_districts_that_exist(nc):
    rows = _targets(nc, {"black": K + 50})
    assert rows["black"]["target"] == K
    runner, _assignment = nc
    assert _get_prep(runner.vap_data, K, 0.44, 0.05, 0.55,
                     targets={"black": K + 50}).target["black"] == K


def test_zero_excludes_a_group(nc):
    rows = _targets(nc, {"black": 0})
    assert rows["black"]["target"] == 0
    assert rows["black"]["manual"] is True

    ps = _score(nc, opportunity_target_black=0)
    assert ps.representation_black == -1.0        # not applicable
    assert ps.opportunity_black == 0.0
    # Black was the one group being scored here, so excluding it leaves nothing
    # to measure and no penalty to charge.
    assert ps.representation == 0.0
    assert ps.representation_rating == 100.0


# ── A manual target overrides the feasibility gate ───────────────────────────

def test_a_manual_target_scores_a_group_the_gate_would_drop(nc):
    """Latino clears a proportional district but not the bundle heuristic, so
    automatically it is not applicable. Asking for one district scores it."""
    auto = _targets(nc)["latino"]
    assert auto["target"] >= 1 and auto["feasible"] == 0
    assert _score(nc).representation_latino == -1.0

    ps = _score(nc, opportunity_target_latino=1)
    assert ps.representation_latino >= 0.0
    assert ps.representation != AUTOMATIC["representation"]


def test_a_group_below_one_district_can_still_be_asked_for(nc):
    """Asian is 0.52 of a district here, so it rounds to one but fails the
    gate, and automatically it is not applicable."""
    assert _targets(nc)["asian"]["T"] < 1.0
    assert _score(nc).representation_asian == -1.0

    opp = _opportunity(nc, {"asian": 1})
    assert opp.target["asian"] == 1 and opp.manual["asian"] is True
    _rating, penalty, ratings, _eff = representation_from_opportunity(opp)
    assert ratings["asian"] is not None
    assert penalty > 0.0


def test_a_manual_group_is_weighted_by_the_district_it_asks_for():
    """The aggregate weighting rule, on figures chosen to show it.

    Two groups: one worth two proportional districts that this plan fully
    meets, one worth 0.3 of a district that it does not meet at all but that
    someone has asked for. Weighted by proportional seats the second would
    count for 0.3 against the first's 2 and the penalty would be 13; weighted
    by the district it asks for it counts 1 against 2, and the penalty is 33.
    """
    from mosaic.scoring.opportunity import OpportunityResult

    met = np.array([1.0, 1.0, 0.0, 0.0])      # two districts at full credit
    unmet = np.zeros(4)
    opp = OpportunityResult(
        T={"black": 2.0, "latino": 0.3, "asian": 0.0},
        P={"black": met, "latino": unmet, "asian": unmet},
        ref={g: 1.0 for g in GROUPS},
        feasible={"black": 1, "latino": 0, "asian": 0},
        ceiling={"black": 2.0, "latino": 1.0, "asian": 0.0},
        target={"black": 2, "latino": 1, "asian": 0},
        manual={"black": False, "latino": True, "asian": False})
    _rating, penalty, ratings, _eff = representation_from_opportunity(
        opp, unclipped=False)
    assert ratings["black"] == pytest.approx(100.0)
    assert ratings["latino"] == pytest.approx(0.0)
    assert ratings["asian"] is None               # nobody asked for it
    assert penalty == pytest.approx(100.0 / 3.0)  # 1 - 2 / (2 + max(0.3, 1))


def test_the_gate_still_drops_a_group_nobody_asked_for(nc):
    """The override is per group: naming Latino's target leaves Asian alone."""
    ps = _score(nc, opportunity_target_latino=1)
    assert ps.representation_asian == -1.0


# ── Caching ──────────────────────────────────────────────────────────────────

def test_the_prep_cache_does_not_serve_a_stale_target(nc):
    runner, _assignment = nc
    args = (runner.vap_data, K, 0.44, 0.05, 0.55)
    first = _get_prep(*args, coords=runner.opportunity_coords,
                      smart_targets=True, targets={"black": 1})
    second = _get_prep(*args, coords=runner.opportunity_coords,
                       smart_targets=True, targets={"black": 4})
    again = _get_prep(*args, coords=runner.opportunity_coords,
                      smart_targets=True, targets={"black": 1})
    assert first.target["black"] == 1
    assert second.target["black"] == 4
    assert second.ceiling["black"] != first.ceiling["black"]
    assert again is first                   # same key, still cached


def test_the_geo_cache_does_not_serve_a_stale_target(nc):
    """The feasibility/ceiling cache is keyed separately, so it is checked on
    its own: a target change has to move the ceiling it hands back."""
    runner, _assignment = nc
    from mosaic.scoring.opportunity import _geo_feasibility
    total = np.ascontiguousarray(runner.vap_data["total"], dtype=np.float64)
    total_sw = float(total.sum())
    kwargs = dict(coords=runner.opportunity_coords, smart_targets=True)
    _f1, c1 = _geo_feasibility(runner.vap_data, total, total_sw, K,
                               0.44, 0.05, 0.55, targets={"black": 1}, **kwargs)
    _f2, c2 = _geo_feasibility(runner.vap_data, total, total_sw, K,
                               0.44, 0.05, 0.55, targets={"black": 4}, **kwargs)
    assert c1["black"] < c2["black"]
