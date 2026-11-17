"""Tests for mosaic.goals — the plain-language layer over the score weights.

The module deliberately has no Dear PyGui import, so all of this runs without a
display. The GUI mixin that drives it is not covered here.
"""

from __future__ import annotations

import math

import pytest

from mosaic import goals
from mosaic.presets import (
    PRESET_MARKER,
    SCHEMA,
    default_preset,
    dumps_preset,
    parse_preset,
    to_score_config,
    validate_preset,
)

LADDER_ON = [(name, x) for name, x in goals.LADDER if x > 0]
ALL_GIDS = [g.gid for g in goals.GOALS]


# ── Shape of the tables ──────────────────────────────────────────────────────

def test_every_goal_score_points_at_a_real_preset_key():
    for goal in goals.GOALS:
        for gs in goal.scores:
            assert gs.section in SCHEMA, f"{goal.gid}: no section {gs.section}"
            assert gs.key in SCHEMA[gs.section], f"{goal.gid}: no {gs.section}.{gs.key}"
            assert gs.ref in goals._REF, f"{goal.gid}: no reference {gs.ref}"


def test_each_goal_shares_sum_to_one():
    for goal in goals.GOALS:
        total = sum(gs.share for gs in goal.scores)
        assert math.isclose(total, 1.0, abs_tol=1e-9), f"{goal.gid}: {total}"


def test_conflicts_are_symmetric():
    for goal in goals.GOALS:
        for other in goal.conflicts:
            assert goal.gid in goals.GOALS_BY_ID[other].conflicts


def test_advanced_sections_are_owned_by_no_goal():
    owned = {sec for g in goals.GOALS for sec in g.sections}
    for sec in goals.ADVANCED_SECTIONS:
        assert sec not in owned


# ── Strength -> weights ──────────────────────────────────────────────────────

def test_budget_is_strictly_increasing_across_the_ladder():
    budgets = [goals.budget_for(x) for _, x in LADDER_ON]
    assert budgets == sorted(budgets)
    assert all(b > 0 for b in budgets)


def test_budget_round_trips_through_strength():
    for x in (5, 20, 33, 40, 55, 60, 72, 80, 95, 100, 110):
        assert goals.strength_for(goals.budget_for(x)) == pytest.approx(x, abs=1e-6)


def test_off_sets_every_weight_to_zero_and_disables():
    for gid in ALL_GIDS:
        p = goals.apply_goal(default_preset(), gid, 0.0)
        for gs in goals.GOALS_BY_ID[gid].scores:
            assert float(p[gs.section][gs.key]) == 0.0
            assert p[gs.section]["enabled"] is False


def test_weights_rise_with_strength_and_never_hit_zero_while_on():
    for goal in goals.GOALS:
        for gs in goal.scores:
            seen = []
            for _, x in LADDER_ON:
                w = goals.goal_weights(goal.gid, x)[gs.section][gs.key]
                assert w > 0, f"{goal.gid}.{gs.section}.{gs.key} is on at weight 0"
                assert w <= 100, f"{goal.gid}.{gs.section}.{gs.key} past slider max"
                seen.append(w)
            assert seen == sorted(seen), f"{goal.gid}.{gs.section}.{gs.key}: {seen}"


def test_one_word_means_a_comparable_contribution_across_goals():
    """A strength word buys roughly the same influence whichever goal it is on.

    Contribution is weight x reference penalty, summed over the goal's scores.
    The floor that keeps a weight off zero, and integer sliders, mean this
    cannot be exact; a factor of two either side of the budget is the claim.
    """
    for name, x in LADDER_ON:
        budget = goals.budget_for(x)
        for goal in goals.GOALS:
            got = sum(
                goals.goal_weights(goal.gid, x)[gs.section][gs.key]
                * goals._REF[gs.ref]
                for gs in goal.scores
            )
            assert budget / 2 < got < budget * 2, (
                f"{goal.gid} at {name}: contributes {got:.0f}, budget {budget:.0f}"
            )


def test_compactness_very_strong_matches_the_shipped_default():
    """The ladder is anchored on the one score the app ships enabled, so a
    freshly launched app reads as a word rather than as Custom."""
    assert (goals.goal_weights("compact", 80)["holistic_compactness"]["weight"]
            == SCHEMA["holistic_compactness"]["weight"][0])


def test_the_shipped_defaults_read_as_a_named_strength():
    p = validate_preset(default_preset())
    state = goals.goal_state(p, "compact")
    assert state.label == "Very strong"
    assert state.exact is True


def test_continuity_very_strong_matches_its_schema_default():
    assert (goals.goal_weights("continuity", 80)["alignment"]["weight"]
            == SCHEMA["alignment"]["weight"][0])


# ── Weights -> label ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("gid", ALL_GIDS)
def test_every_ladder_step_reads_back_as_its_own_word(gid):
    for name, x in goals.LADDER:
        p = goals.apply_goal(default_preset(), gid, x, party="rep",
                             party_focus="dem")
        state = goals.goal_state(p, gid)
        assert state.label == name
        assert state.exact is True
        assert state.strength == pytest.approx(x)


def test_a_hand_edited_weight_reads_back_as_custom():
    p = goals.apply_goal(default_preset(), "compact", 60)
    assert goals.goal_state(p, "compact").label == "Strong"
    strong = p["holistic_compactness"]["weight"]
    very = goals.goal_weights("compact", 80)["holistic_compactness"]["weight"]
    p["holistic_compactness"]["weight"] = int((strong + very) / 2)
    state = goals.goal_state(p, "compact")
    assert state.exact is False
    assert state.label == "Custom (between Strong and Very strong)"
    assert 60 < state.strength < 80


def test_a_weight_past_the_top_of_the_ladder_says_so():
    p = goals.apply_goal(default_preset(), "compact", 100)
    p["holistic_compactness"]["weight"] = 100
    assert goals.goal_state(p, "compact").label == "Custom (beyond Dominant)"


def test_a_weight_below_light_says_so():
    p = goals.apply_goal(default_preset(), "counties", 20)
    p["holistic_splitting"]["weight"] = 1
    state = goals.goal_state(p, "counties")
    assert state.exact is False
    assert state.label == "Custom (below Light)"


def test_label_wording_covers_the_whole_slider_range():
    for x in range(0, goals.STRENGTH_MAX + 1):
        assert goals.strength_label(x, exact=False)
        assert goals.strength_label(x)


def test_a_disabled_score_reads_as_off_however_big_its_weight():
    p = goals.apply_goal(default_preset(), "compact", 100)
    p["holistic_compactness"]["enabled"] = False
    state = goals.goal_state(p, "compact")
    assert state.label == "Off"
    assert state.on is False


def test_matches_strength_tracks_the_weights():
    p = goals.apply_goal(default_preset(), "minority", 60)
    assert goals.matches_strength(p, "minority", 60)
    assert not goals.matches_strength(p, "minority", 80)
    p["minority_cohesion"]["weight"] += 1
    assert not goals.matches_strength(p, "minority", 60)


# ── The shared directional metrics resolve by mode ───────────────────────────

def test_fair_mode_weights_count_for_fairness_not_for_favor():
    p = goals.apply_goal(default_preset(), "fairness", 60)
    assert p["mean_median"]["mode"] == "fair"
    assert goals.goal_state(p, "fairness").label == "Strong"
    assert goals.goal_state(p, "favor_party").label == "Off"


def test_favor_mode_weights_count_for_favor_not_for_fairness():
    p = goals.apply_goal(default_preset(), "favor_party", 60, party="rep")
    assert p["efficiency_gap"]["mode"] == "favor_rep"
    assert goals.goal_state(p, "favor_party").label == "Strong"
    assert goals.goal_state(p, "fairness").label == "Off"


def test_the_directional_metrics_are_calibrated_per_mode():
    """Favor mode penalises from about 50, fair mode from about 15.

    One reference for both would make the favor-mode weights several times too
    strong, so each mode has its own.
    """
    for stem in ("mean_median", "efficiency_gap", "partisan_bias"):
        assert goals._REF[f"{stem}_favor"] > goals._REF[f"{stem}_fair"] * 2


# ── Party / fairness conflict ────────────────────────────────────────────────

def test_setting_favor_a_party_switches_partisan_fairness_off():
    p = goals.apply_goal(default_preset(), "fairness", 80)
    p = goals.apply_goal(p, "favor_party", 60, party="dem")
    assert goals.goal_state(p, "favor_party").label == "Strong"
    assert goals.goal_state(p, "fairness").label == "Off"
    assert p["holistic_proportionality"]["enabled"] is False
    assert p["partisan_gini"]["enabled"] is False


def test_setting_partisan_fairness_switches_favor_a_party_off():
    p = goals.apply_goal(default_preset(), "favor_party", 100, party="rep")
    p = goals.apply_goal(p, "fairness", 40)
    assert goals.goal_state(p, "fairness").label == "Moderate"
    assert goals.goal_state(p, "favor_party").label == "Off"
    assert p["dem_seats"]["enabled"] is False
    assert p["majority_chance"]["enabled"] is False


def test_the_two_are_never_on_together_whatever_the_order():
    for first, second in (("favor_party", "fairness"), ("fairness", "favor_party")):
        p = goals.apply_goal(default_preset(), first, 60, party="rep")
        p = goals.apply_goal(p, second, 60, party="rep")
        live = [gid for gid in ("favor_party", "fairness")
                if goals.goal_state(p, gid).on]
        assert live == [second]


def test_switching_a_goal_off_leaves_its_rival_intact():
    """The three shared metrics must not go down with the goal being switched
    off, since the rival goal is what owns them now."""
    p = goals.apply_goal(default_preset(), "fairness", 60)
    p = goals.apply_goal(p, "favor_party", 0.0)
    assert goals.goal_state(p, "fairness").label == "Strong"
    assert p["mean_median"]["enabled"] is True


def test_conflict_note_is_silent_until_a_preset_puts_both_on():
    p = goals.apply_goal(default_preset(), "fairness", 60)
    assert goals.conflict_note(p) == ""
    # Only a hand-written preset can reach this; the panel cannot.
    p["dem_seats"].update(enabled=True, weight=9)
    p["majority_chance"].update(enabled=True, weight=4)
    note = goals.conflict_note(p)
    assert "Favor a party" in note and "Partisan fairness" in note


def test_conflict_note_follows_the_place_unit():
    p = goals.apply_goal(default_preset(), "counties", 60)
    assert goals.conflict_note(p, "cities") == ""


# ── Presets round-trip ───────────────────────────────────────────────────────

@pytest.mark.parametrize("gid", ALL_GIDS)
def test_applied_goals_stay_valid_presets(gid):
    for _, x in goals.LADDER:
        p = goals.apply_goal(default_preset(), gid, x, party="rep",
                             party_focus="dem")
        assert validate_preset(p) is not None


def test_a_goal_survives_a_write_and_read_of_the_preset_file():
    p = goals.apply_goals(default_preset(),
                          {"compact": 60, "counties": 40, "minority": 80})
    text = dumps_preset(p)
    back, warnings = parse_preset({**parse_preset(_as_dict(text))[0],
                                   PRESET_MARKER: 1})
    assert warnings == []
    before = {g.gid: goals.goal_state(p, g.gid).label for g in goals.GOALS}
    after = {g.gid: goals.goal_state(back, g.gid).label for g in goals.GOALS}
    assert before == after
    assert after["compact"] == "Strong"
    assert after["counties"] == "Moderate"
    assert after["minority"] == "Very strong"


def _as_dict(text: str) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:   # Python 3.10
        import tomli as tomllib
    return tomllib.loads(text)


def test_a_preset_written_before_goals_existed_still_reads_as_goals():
    """Goal state is derived, so no schema change and no migration: the shipped
    defaults already read as a compactness goal."""
    p = validate_preset(default_preset())
    assert p["holistic_compactness"]["enabled"] is True
    assert goals.goal_state(p, "compact").on


def test_goals_reach_score_config_through_the_normal_path():
    p = goals.apply_goals(default_preset(), {"compact": 60, "equal_pop": 40})
    cfg = to_score_config(p)
    assert cfg.weight_holistic_compactness == pytest.approx(
        goals.goal_weights("compact", 60)["holistic_compactness"]["weight"])
    assert cfg.weight_pop_deviation > 0
    assert cfg.weight_holistic_splitting == 0.0


def test_an_off_goal_contributes_nothing_to_score_config():
    p = goals.apply_goals(default_preset(), {g.gid: 0 for g in goals.GOALS})
    cfg = to_score_config(p)
    for goal in goals.GOALS:
        for gs in goal.scores:
            assert getattr(cfg, _cfg_attr(gs), 0.0) == 0.0


def _cfg_attr(gs: goals.GoalScore) -> str:
    """ScoreConfig field for a goal score, where the names differ."""
    special = {
        ("county_splits", "weight_excess"): "weight_county_excess",
        ("county_splits", "weight_unified"): "weight_county_unified",
        ("majority_chance", "weight"): "weight_majority_chance_dem",
    }
    return special.get((gs.section, gs.key), f"weight_{gs.section}")


# ── Starting points ──────────────────────────────────────────────────────────

def test_every_starter_names_every_goal_so_it_clears_the_last_one():
    for sid, name, strengths in goals.STARTERS:
        assert set(strengths) == set(ALL_GIDS), f"{sid} ({name}) is incomplete"


def test_every_starter_applies_to_exactly_what_it_asks_for():
    for sid, _name, strengths in goals.STARTERS:
        p = goals.apply_goals(default_preset(), strengths, party="dem")
        for gid, x in strengths.items():
            assert goals.goal_state(p, gid).label == goals.strength_label(x), sid


def test_no_starter_sets_a_conflicting_pair():
    for sid, _name, strengths in goals.STARTERS:
        for goal in goals.GOALS:
            if strengths.get(goal.gid, 0) <= 0:
                continue
            for other in goal.conflicts:
                assert strengths.get(other, 0) <= 0, sid


def test_starters_by_id_agrees_with_starters():
    assert goals.STARTERS_BY_ID == {sid: st for sid, _n, st in goals.STARTERS}


# ── Place unit wording ───────────────────────────────────────────────────────

def test_the_counties_goal_wording_follows_the_place_unit():
    goal = goals.GOALS_BY_ID["counties"]
    assert goal.title("cities") == "Keep cities whole"
    assert "cities" in goal.text("cities")[1]
    assert goal.title() == "Keep counties whole"
    assert goal.title("  ") == "Keep counties whole"


def test_the_place_unit_changes_no_other_goal_and_no_preset_key():
    before = goals.apply_goal(default_preset(), "counties", 60)
    for unit in goals.PLACE_UNIT_CHOICES:
        assert goals.GOALS_BY_ID["counties"].title(unit).startswith("Keep ")
        assert unit in goals.GOALS_BY_ID["counties"].title(unit)
    # Wording only: the score and its preset key are untouched.
    assert before["holistic_splitting"]["enabled"] is True
    for goal in goals.GOALS:
        if goal.gid != "counties":
            assert "{unit}" not in goal.label


def test_the_default_place_unit_is_in_the_choices():
    assert goals.PLACE_UNIT_DEFAULT in goals.PLACE_UNIT_CHOICES


# ── Section ownership lookup ─────────────────────────────────────────────────

def test_sections_map_back_to_their_goal():
    for goal in goals.GOALS:
        for sec in goal.sections:
            if len(goal.conflicts):      # shared metrics resolve by mode
                continue
            assert goals.goal_for_section(sec) is goal


def test_shared_sections_resolve_by_mode():
    assert goals.goal_for_section("mean_median") is None
    assert goals.goal_for_section_mode("mean_median", "fair").gid == "fairness"
    assert goals.goal_for_section_mode("mean_median", "favor_dem").gid == "favor_party"
    assert goals.goal_for_section_mode("holistic_compactness").gid == "compact"
    assert goals.goal_for_section_mode("cut_edges") is None


def test_calibration_rows_cover_every_goal_and_step():
    rows = goals.calibration_rows()
    assert {r[0] for r in rows} == {g.title() for g in goals.GOALS}
    assert {r[1] for r in rows} == {name for name, x in goals.LADDER if x > 0}
