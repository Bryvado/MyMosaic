"""
Goals — a plain-language layer over the score weights.

The scoring panel exposes about twenty metrics, each with its own weight slider
on its own numeric scale. A goal bundles the metrics that serve one intent
("Compact districts", "Keep counties whole") and sets their weights together
from a single named strength: Off, Light, Moderate, Strong, Very strong,
Dominant.

Nothing here imports Dear PyGui and nothing here touches scoring math. A goal
is applied by rewriting a preset dict (``mosaic.presets`` sections), which the
GUI then pushes through its existing ``_apply_preset`` path, so the score
widgets stay the single source of truth and the runner is unchanged. A goal's
strength is likewise *derived* from the weights rather than stored, which is
why existing preset files keep loading and saving untouched.

── Why a calibration table is needed ────────────────────────────────────────

``PlanScore.total`` is a sum of ``weight_x * penalty_x``. The penalties are not
on one scale, so equal weights are not equal influence. Measured on the bundled
North Carolina sample (``shapefiles/North_Carolina_Simplified.shp``, 2,666
precincts, 14 districts, 5% tolerance) over three fresh partitions, the raw
penalty at weight 1 ranges from about 10 (Partisan Gini) to about 780 (Cut
Edges) — a factor of nearly 80. ``SCHEMA``'s defaults do not correct for this:
at its default of 50, Compactness contributes ~2,700 to the total, while
Mean-Median at its default of 1 contributes ~11. The same number means wildly
different things, which is the confusion this module hides.

So a strength is defined as a *contribution budget* in weighted-penalty units,
and each weight is derived from it:

    weight = budget * share / reference_penalty

``_REF`` holds the reference penalties (see the table below for provenance) and
each goal's ``share`` splits its budget among its scores. One word therefore
means roughly one amount of influence on the total, whichever goal it is set on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from mosaic.presets import SCHEMA, validate_preset

# ── Strength ladder ──────────────────────────────────────────────────────────
# Named steps sit on a 0-100 scale so one slider can move between them; the
# slider runs to STRENGTH_MAX so a weight can also be pushed past Dominant.
# Off is a real step, not a gap: it means every score of the goal is disabled.
LADDER: tuple[tuple[str, int], ...] = (
    ("Off", 0),
    ("Light", 20),
    ("Moderate", 40),
    ("Strong", 60),
    ("Very strong", 80),
    ("Dominant", 100),
)
STRENGTH_MAX = 120
_NAMES = {x: name for name, x in LADDER}
_ANCHORS = tuple(x for _, x in LADDER)

# Contribution budget at each named step, in weighted-penalty units, i.e. what
# the goal adds to PlanScore.total on a fresh North Carolina plan. Roughly
# geometric (x2.5, x2.5, x2, x1.8) so each step up is a clear change rather
# than a nudge, and 28x separates Light from Dominant.
#
# The ladder is anchored at Very strong = 2,800, because that is what today's
# Compactness default (weight 50 x ~54 penalty) already contributes: the one
# score the app ships enabled, whose influence users are used to, becomes
# "Very strong" rather than some unnamed middle. Dominant tops out at 5,000
# instead of continuing the geometric run, so that every goal can actually
# reach it without a weight clamping at the slider maximum of 100.
_BUDGET: dict[int, float] = {20: 180.0, 40: 550.0, 60: 1400.0, 80: 2800.0,
                             100: 5000.0}

# ── Reference penalties ──────────────────────────────────────────────────────
# Raw penalty at weight 1, measured over three fresh 14-district North Carolina
# partitions (seeds 1-3, 5% tolerance). Fresh plans, not optimized ones:
# weights do their steering early, when penalties are large, and an optimized
# plan's penalty is whatever the weights already drove it to, so calibrating on
# one would be circular.
#
#   score                     median   measured range   used   why not the median
#   holistic_compactness        53.7   49.6 -  57.7       56   see (a)
#   holistic_splitting         100.5   93.4 - 101.4       97
#   holistic_proportionality    21.6   15.9 -  30.3       26   see (b)
#   holistic_competitiveness    56.7   39.8 -  60.7       59
#   partisan_gini                8.5    1.6 -  12.9       10
#   representation              84.0   74.1 -  87.1       80
#   minority_cohesion           53.9   42.0 -  57.5       50
#   community_congruence        56.8   37.2 -  57.0       47
#   dem_seats                   57.8   56.2 -  60.8       60
#   majority_chance             80.0   64.3 -  83.8       82
#   pop_deviation               22.9   22.2 -  36.1       60   see (c)
#   mean_median      fair        7.8    2.2 -  14.4       11   see (d)
#   efficiency_gap   fair       12.7    8.6 -  19.8       16   see (d)
#   partisan_bias    fair       13.0    0.8 -  22.4       17   see (d)
#   mean_median      favor         --        --           50   see (d)
#   efficiency_gap   favor         --        --           50   see (d)
#   partisan_bias    favor         --        --           50   see (d)
#   alignment                     --        --          112   see (e)
#   cut_edges                  750.0  732.0 - 834.0        -   advanced, no goal
#   county_excess              590.0  560.0 - 610.0        -   advanced, no goal
#   county_unified              20.0   20.0 -  20.0        -   advanced, no goal
#
# Where the figure used is not the median, one of two constraints moved it: a
# SCHEMA default worth landing a word on, or the top of the ladder having to fit
# inside a slider that drags to 100.
#
# (a) 56, not 53.7, so that Very strong is weight 50 exactly -- the shipped
#     Compactness default, and the only score the app enables on launch. A
#     freshly started Mosaic therefore reads as "Very strong" rather than
#     "Custom (between Strong and Very strong)". 56 is inside the measured
#     spread. The other SCHEMA defaults never show on launch, since their
#     scores ship disabled and so read as Off.
# (b) 26, above the median, because Proportionality is the weakest primary
#     driver of any goal: at the median its Dominant weight would be 116 and
#     clamp at the slider maximum, costing the goal its top step.
# (c) 60, well above the fresh median, because this penalty is the one that
#     grows fastest as a plan drifts -- the same chain measured 119 after 400
#     compactness-driven steps. Calibrating on 22.9 would put Very strong at
#     weight 122, past the slider, and make the score overwhelming on exactly
#     the plans it is meant to nudge.
# (d) The three directional metrics are listed twice because their penalty is
#     mode-dependent: "fair" scores |raw| / bound, small on a plan that is
#     already near-symmetric, while "favor" scores (bound -+ raw) / 2 bound,
#     which sits at 0.5 -> 50 on a neutral plan and only falls as the map is
#     skewed. One reference for both would make favor mode about four times
#     too strong. The fair figures are nudged above their medians because those
#     medians sit near zero on a plan that happens to start symmetric, which
#     would make the weights enormous.
# (e) Not measured: the sample ships no reference plan, so there is nothing to
#     be continuous with. Anchored instead to its own SCHEMA default of 25 by
#     asserting that the existing default means Very strong -- the same
#     convention as (a). 2800 / 25 = 112.
_REF: dict[str, float] = {
    "holistic_compactness": 56.0,
    "holistic_splitting": 97.0,
    "holistic_proportionality": 26.0,
    "holistic_competitiveness": 59.0,
    "partisan_gini": 10.0,
    "representation": 80.0,
    "minority_cohesion": 50.0,
    "community_congruence": 47.0,
    "dem_seats": 60.0,
    "majority_chance": 82.0,
    "pop_deviation": 60.0,
    "alignment": 112.0,
    "mean_median_fair": 11.0,
    "efficiency_gap_fair": 16.0,
    "partisan_bias_fair": 17.0,
    "mean_median_favor": 50.0,
    "efficiency_gap_favor": 50.0,
    "partisan_bias_favor": 50.0,
}

# Weights live on sliders that drag to 100; a custom weight may exceed that in
# a hand-written preset, but nothing this module computes does.
_W_MAX = 100.0


# The place unit Mosaic's "county" column holds. The column keeps its name, but
# the unit is whatever was loaded into it, so the counties goal's wording is
# configurable. PLACE_UNIT_CHOICES only seeds the picker; any text is accepted.
PLACE_UNIT_DEFAULT = "counties"
PLACE_UNIT_CHOICES: tuple[str, ...] = (
    "counties", "cities", "towns", "places", "wards", "precincts",
)


@dataclass(frozen=True)
class GoalScore:
    """One score a goal drives: where its weight lives and how much it gets."""

    section: str        # presets.SCHEMA section
    key: str            # weight key within that section
    share: float        # fraction of the goal's budget
    ref: str            # key into _REF


@dataclass(frozen=True)
class Goal:
    """One plain-language intent and the scores it sets."""

    gid: str
    label: str              # user-facing wording for the panel row
    blurb: str              # one line of explanation under/beside the label
    scores: tuple[GoalScore, ...]
    conflicts: tuple[str, ...] = ()
    # What data a goal needs is NOT declared here. It follows from the sections
    # it drives, and menu_mixin._PRESET_NEEDS already maps section -> data, so
    # the GUI derives it from there rather than keeping a second list in step.

    @property
    def sections(self) -> tuple[str, ...]:
        """Sections this goal writes to, in order, without repeats."""
        out: list[str] = []
        for s in self.scores:
            if s.section not in out:
                out.append(s.section)
        return tuple(out)

    def text(self, place_unit: str = "") -> tuple[str, str]:
        """(label, blurb) with the place unit filled in."""
        unit = place_unit.strip() or PLACE_UNIT_DEFAULT
        return (self.label.replace("{unit}", unit),
                self.blurb.replace("{unit}", unit))

    def title(self, place_unit: str = "") -> str:
        return self.text(place_unit)[0]


# ── Goals ────────────────────────────────────────────────────────────────────
# Labels reuse the names already on the score rows (County Congruence, Plan
# Continuity, Electoral Opportunity) wherever a goal maps onto one score, so
# the two panels can be read against each other.
#
# Shares: where a goal has one obvious driver it takes the whole budget. Where
# it has several, the holistic composite leads, because it is the score that was
# built to summarize the intent, and the narrower classical metrics ride along
# at a fraction so they pull the same way without fighting it.

GOALS: tuple[Goal, ...] = (
    Goal(
        gid="favor_party",
        label="Favor a party",
        blurb="Win as many seats as possible for one party.",
        # Expected seats leads. Chance of Majority guards the chamber rather
        # than the seat count, so it takes a quarter. The three directional
        # metrics share the rest in favor mode: setting their direction without
        # any weight behind it would be a control that visibly does nothing.
        scores=(
            GoalScore("dem_seats", "weight", 0.40, "dem_seats"),
            GoalScore("majority_chance", "weight", 0.24, "majority_chance"),
            GoalScore("efficiency_gap", "weight", 0.12, "efficiency_gap_favor"),
            GoalScore("mean_median", "weight", 0.12, "mean_median_favor"),
            GoalScore("partisan_bias", "weight", 0.12, "partisan_bias_favor"),
        ),
        conflicts=("fairness",),
    ),
    Goal(
        gid="fairness",
        label="Partisan fairness",
        blurb="Seats should track votes, with no built-in edge either way.",
        scores=(
            GoalScore("holistic_proportionality", "weight", 0.50,
                      "holistic_proportionality"),
            GoalScore("partisan_gini", "weight", 0.15, "partisan_gini"),
            GoalScore("efficiency_gap", "weight", 0.13, "efficiency_gap_fair"),
            GoalScore("mean_median", "weight", 0.11, "mean_median_fair"),
            GoalScore("partisan_bias", "weight", 0.11, "partisan_bias_fair"),
        ),
        conflicts=("favor_party",),
    ),
    Goal(
        gid="competitive",
        label="Competitive districts",
        blurb="More districts that either party could win.",
        scores=(
            GoalScore("holistic_competitiveness", "weight", 1.0,
                      "holistic_competitiveness"),
        ),
    ),
    Goal(
        gid="minority",
        label="Minority representation",
        blurb="Districts where minority voters can elect their candidates, "
              "with their neighborhoods kept together.",
        # Electoral Opportunity is the outcome; Neighborhood Severance and
        # Community Dispersion are how the map treats the communities on the
        # way there, so they split a fifth each.
        scores=(
            GoalScore("representation", "weight", 0.60, "representation"),
            GoalScore("minority_cohesion", "weight", 0.20, "minority_cohesion"),
            GoalScore("community_congruence", "weight", 0.20,
                      "community_congruence"),
        ),
    ),
    Goal(
        gid="compact",
        label="Compact districts",
        blurb="Tidy, regularly shaped districts.",
        # Polsby-Popper, Reock and Cut Edges stay advanced: Compactness already
        # blends the first two, and adding them again would double-count.
        scores=(
            GoalScore("holistic_compactness", "weight", 1.0,
                      "holistic_compactness"),
        ),
    ),
    Goal(
        gid="counties",
        # {unit} is the place unit the "county" column actually holds. Mosaic's
        # column is named for counties, but any place unit can be loaded into
        # it -- a single-county map with city names in that column is scored for
        # keeping cities whole. The score names and preset keys stay as they
        # are; only this wording follows the unit. See PLACE_UNIT_DEFAULT.
        label="Keep {unit} whole",
        blurb="Split as few {unit} as the population rules allow.",
        # County Congruence alone. Classic Splitting stays advanced because
        # score_plan already folds its two counts into County Congruence's own
        # penalty (`hsplit_raw += cs_excess + cs_unified_score / 10`), so
        # weighting it as well would count the same splits twice — the same
        # reason Polsby-Popper and Reock stay out of the compactness goal.
        scores=(
            GoalScore("holistic_splitting", "weight", 1.0, "holistic_splitting"),
        ),
    ),
    Goal(
        gid="continuity",
        label="Stay close to the current map",
        blurb="Move as few people between districts as possible.",
        scores=(
            GoalScore("alignment", "weight", 1.0, "alignment"),
        ),
    ),
    Goal(
        gid="equal_pop",
        label="Equal population",
        blurb="Push district populations closer than the tolerance requires.",
        scores=(
            GoalScore("pop_deviation", "weight", 1.0, "pop_deviation"),
        ),
    ),
)

GOALS_BY_ID: dict[str, Goal] = {g.gid: g for g in GOALS}

# Sections no goal owns; the Goals panel leaves these to the detailed rows and
# the Score Contributors panel shows them outside any goal group.
ADVANCED_SECTIONS: tuple[str, ...] = (
    "cut_edges", "polsby_popper", "reock", "county_bias", "county_splits",
)


def goal_for_section(section: str) -> Goal | None:
    """The goal that owns ``section``, or None for an advanced-only score.

    ``mean_median``/``efficiency_gap``/``partisan_bias`` belong to two goals;
    they resolve by mode, so they are not answered here. Use
    ``goal_for_section_mode``.
    """
    owners = [g for g in GOALS if section in g.sections]
    return owners[0] if len(owners) == 1 else None


def goal_for_section_mode(section: str, mode: str | None = None) -> Goal | None:
    """Like ``goal_for_section``, resolving the shared directional metrics.

    A "fair" mode reads as Partisan fairness and a "favor_*" mode as Favor a
    party, which is the same rule the strength inverse uses.
    """
    owners = [g for g in GOALS if section in g.sections]
    if len(owners) <= 1:
        return owners[0] if owners else None
    want = "fairness" if (mode or "fair") == "fair" else "favor_party"
    return GOALS_BY_ID[want]


# ── Strength <-> budget ──────────────────────────────────────────────────────

def budget_for(strength: float) -> float:
    """Contribution budget for a point on the 0-STRENGTH_MAX strength scale.

    Linear from nothing up to Light, then log-linear between the named steps so
    that equal slider travel is an equal *ratio* of influence — the ladder is
    geometric, so interpolating it linearly would bunch every custom value near
    the bottom. Past Dominant it keeps the Very strong -> Dominant slope.
    """
    x = float(strength)
    if x <= 0.0:
        return 0.0
    lo_name = _ANCHORS[1]                      # Light
    if x < lo_name:
        return _BUDGET[lo_name] * x / lo_name
    pairs = [(a, b) for a, b in zip(_ANCHORS[1:], _ANCHORS[2:])]
    for a, b in pairs:
        if x <= b:
            t = (x - a) / (b - a)
            return _BUDGET[a] * (_BUDGET[b] / _BUDGET[a]) ** t
    a, b = pairs[-1]
    t = (x - b) / (b - a)
    return _BUDGET[b] * (_BUDGET[b] / _BUDGET[a]) ** t


def strength_for(budget: float) -> float:
    """Inverse of ``budget_for``, clamped to [0, STRENGTH_MAX]."""
    b = float(budget)
    if b <= 0.0:
        return 0.0
    lo_name = _ANCHORS[1]
    if b <= _BUDGET[lo_name]:
        return lo_name * b / _BUDGET[lo_name]
    pairs = [(a, bb) for a, bb in zip(_ANCHORS[1:], _ANCHORS[2:])]
    for a, bb in pairs:
        if b <= _BUDGET[bb]:
            t = math.log(b / _BUDGET[a]) / math.log(_BUDGET[bb] / _BUDGET[a])
            return a + t * (bb - a)
    a, bb = pairs[-1]
    t = math.log(b / _BUDGET[bb]) / math.log(_BUDGET[bb] / _BUDGET[a])
    return min(float(STRENGTH_MAX), bb + t * (bb - a))


def strength_label(strength: float, *, exact: bool = True) -> str:
    """Name a strength: a ladder word, or how a custom value sits between two.

    ``exact=False`` forces the custom wording even on an anchor, for a weight
    set that lands on an anchor's number without matching its weights.
    """
    x = float(strength)
    if x <= 0.0:
        return _NAMES[0] if exact else "Custom (nearly off)"
    if exact and int(round(x)) in _NAMES and math.isclose(x, round(x), abs_tol=1e-6):
        return _NAMES[int(round(x))]
    if x >= _ANCHORS[-1]:
        return f"Custom (beyond {_NAMES[_ANCHORS[-1]]})"
    for a, b in zip(_ANCHORS, _ANCHORS[1:]):
        if a <= x <= b:
            if a == 0:
                return f"Custom (below {_NAMES[b]})"
            return f"Custom (between {_NAMES[a]} and {_NAMES[b]})"
    return "Custom"


# ── Strength -> weights ──────────────────────────────────────────────────────

def _is_float_key(section: str, key: str) -> bool:
    default, _ = SCHEMA[section][key]
    return isinstance(default, float) and not isinstance(default, bool)


def _weight(gs: GoalScore, budget: float) -> float:
    """One score's weight for a budget, on its own slider's grid.

    Floored at the smallest value its slider can hold above zero, so a goal that
    is on never leaves one of its scores enabled at weight 0 — an enabled score
    with no weight is in the objective in name only, exactly the silence this
    layer exists to remove.
    """
    if budget <= 0.0:
        return 0.0
    raw = budget * gs.share / _REF[gs.ref]
    if _is_float_key(gs.section, gs.key):
        return min(_W_MAX, max(0.1, round(raw, 1)))
    return min(_W_MAX, float(max(1, int(round(raw)))))


def goal_weights(gid: str, strength: float) -> dict[str, dict[str, float]]:
    """``{section: {weight_key: value}}`` for one goal at one strength."""
    goal = GOALS_BY_ID[gid]
    budget = budget_for(strength)
    out: dict[str, dict[str, float]] = {}
    for gs in goal.scores:
        out.setdefault(gs.section, {})[gs.key] = _weight(gs, budget)
    return out


def apply_goal(preset: dict[str, dict[str, Any]], gid: str, strength: float, *,
               party: str | None = None,
               party_focus: str | None = None) -> dict[str, dict[str, Any]]:
    """Return a copy of ``preset`` with one goal set to ``strength``.

    Writes the weights, the enabled flags, and the few non-weight settings a
    goal owns (which party to favor, which voters continuity is measured over,
    the direction modes). A goal that conflicts with this one is switched off,
    so the directional metrics are never asked to pull two ways at once.
    """
    goal = GOALS_BY_ID[gid]
    out = {sec: dict(vals) for sec, vals in preset.items()}
    on = strength > 0.0

    if not on:
        # Hand the shared scores over rather than zeroing them: a conflicting
        # goal that is on owns the three directional metrics, and turning this
        # one off must not take its weights down with it.
        keep: set[str] = set()
        for other in goal.conflicts:
            if goal_state(preset, other).on:
                keep |= set(GOALS_BY_ID[other].sections)
        out = _switch_off(out, goal, keep=keep)
        if gid == "counties":
            out["county_bias"]["enabled"] = False
        return out

    for sec, weights in goal_weights(gid, strength).items():
        out[sec].update(weights)
        out[sec]["enabled"] = True

    # Non-weight settings are written only when the goal is on. At weight 0 they
    # are inert, and rewriting them would undo the goal that just displaced this
    # one -- switching off Partisan fairness would reset the very modes Favor a
    # party had set.
    if gid == "favor_party":
        p = party if party in ("dem", "rep") else out["dem_seats"]["favor"]
        out["dem_seats"]["favor"] = p
        out["majority_chance"]["party"] = p
        for sec in ("mean_median", "efficiency_gap", "partisan_bias"):
            out[sec]["mode"] = f"favor_{p}"
    elif gid == "fairness":
        for sec in ("mean_median", "efficiency_gap", "partisan_bias"):
            out[sec]["mode"] = "fair"
    elif gid == "counties":
        # County-Edge Bias is a move bias, not a weight: it pairs with County
        # Congruence, as the score row's own toggle already does.
        out["county_bias"]["enabled"] = True
    elif gid == "continuity" and party_focus is not None:
        out["alignment"]["party_focus"] = party_focus
        if party_focus == "none":
            out["alignment"]["restrict_to_party"] = False

    for other in goal.conflicts:
        out = _switch_off(out, GOALS_BY_ID[other], keep=set(goal.sections))
    return out


def _switch_off(preset: dict[str, dict[str, Any]], goal: Goal,
                keep: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """Disable one goal's scores, leaving any section in ``keep`` untouched.

    Two goals can own the same score: the three directional metrics belong to
    both Favor a party and Partisan fairness, which is precisely why those two
    conflict. Switching one of them off must therefore not zero the weights the
    other just set -- only the sections it alone owns. Which goal the shared
    scores now serve is settled by their mode, not by their weight.
    """
    keep = keep or set()
    out = {sec: dict(vals) for sec, vals in preset.items()}
    for gs in goal.scores:
        if gs.section in keep:
            continue
        out[gs.section][gs.key] = 0.0 if _is_float_key(gs.section, gs.key) else 0
        out[gs.section]["enabled"] = False
    return out


def apply_goals(preset: dict[str, dict[str, Any]],
                strengths: dict[str, float], **opts: Any
                ) -> dict[str, dict[str, Any]]:
    """Apply several goals in GOALS order, then validate.

    Goals that are off are applied too, so a starting point clears whatever the
    previous one had set rather than layering on top of it.
    """
    out = preset
    # Goals being switched off go first, so that where two goals share a score
    # the one that stays on writes last and keeps it.
    for want_on in (False, True):
        for goal in GOALS:
            if goal.gid not in strengths:
                continue
            if (strengths[goal.gid] > 0.0) is not want_on:
                continue
            out = apply_goal(out, goal.gid, strengths[goal.gid],
                             party=opts.get("party"),
                             party_focus=opts.get("party_focus"))
    return validate_preset(out)


# ── Weights -> strength ──────────────────────────────────────────────────────

def _ref_for(preset: dict[str, dict[str, Any]], gs: GoalScore) -> float:
    """Reference penalty for a score, reading the live mode where it matters."""
    if gs.ref.endswith(("_fair", "_favor")):
        mode = preset.get(gs.section, {}).get("mode", "fair")
        stem = gs.ref.rsplit("_", 1)[0]
        return _REF[f"{stem}_" + ("fair" if mode == "fair" else "favor")]
    return _REF[gs.ref]


def _active_scores(preset: dict[str, dict[str, Any]], goal: Goal
                   ) -> list[tuple[GoalScore, float]]:
    """The goal's scores that are enabled with a weight, with those weights.

    A directional metric counts for this goal only when its mode points the
    goal's way; the same slider in the other mode belongs to the other goal.
    """
    out: list[tuple[GoalScore, float]] = []
    for gs in goal.scores:
        sec = preset.get(gs.section)
        if not sec or not sec.get("enabled"):
            continue
        w = float(sec.get(gs.key, 0.0) or 0.0)
        if w <= 0.0:
            continue
        if gs.ref.endswith(("_fair", "_favor")):
            fair = sec.get("mode", "fair") == "fair"
            if fair != (goal.gid == "fairness"):
                continue
        out.append((gs, w))
    return out


def matches_strength(preset: dict[str, dict[str, Any]], gid: str,
                     strength: float) -> bool:
    """Are the goal's weights exactly what ``strength`` would set them to?

    The GUI asks this to decide whether its strength slider still agrees with
    the weights: while it does, the slider is left where the user put it, and
    when it stops agreeing -- because a weight was edited by hand on its own
    score row -- the slider follows the weights instead.
    """
    want = goal_weights(gid, strength)
    on = strength > 0.0
    for sec, keys in want.items():
        body = preset.get(sec)
        if body is None or bool(body.get("enabled")) is not on:
            return False
        for key, val in keys.items():
            if not math.isclose(float(body.get(key, 0.0) or 0.0), val,
                                rel_tol=1e-6, abs_tol=1e-9):
                return False
    return True


@dataclass(frozen=True)
class GoalState:
    """What one goal currently reads as, derived from the score weights."""

    gid: str
    strength: float      # point on the 0-STRENGTH_MAX scale
    exact: bool          # True when the weights match a ladder step exactly
    label: str           # "Strong", "Custom (between Strong and Very strong)"

    @property
    def on(self) -> bool:
        return self.strength > 0.0


def goal_state(preset: dict[str, dict[str, Any]], gid: str) -> GoalState:
    """Read one goal's strength back out of the weights.

    Exact first: if the weights are byte-for-byte what a ladder step produces,
    that step is the answer, so a goal set from the panel always reads back as
    the word it was set to despite the rounding on the way out. Otherwise the
    budget is estimated from whichever of the goal's scores are weighted —
    a share-weighted mean of what each weight implies — and reported as Custom.
    """
    goal = GOALS_BY_ID[gid]
    active = _active_scores(preset, goal)
    if not active:
        return GoalState(gid, 0.0, True, _NAMES[0])

    for _, anchor in LADDER:
        if anchor != 0 and matches_strength(preset, gid, anchor):
            return GoalState(gid, float(anchor), True, _NAMES[anchor])

    # implied budget per score = w * ref / share; average them by share.
    num = sum(w * _ref_for(preset, gs) for gs, w in active)
    den = sum(gs.share for gs, _ in active)
    x = strength_for(num / den) if den > 0.0 else 0.0
    return GoalState(gid, x, False, strength_label(x, exact=False))


def goal_states(preset: dict[str, dict[str, Any]]) -> dict[str, GoalState]:
    """Every goal's state, in GOALS order."""
    return {g.gid: goal_state(preset, g.gid) for g in GOALS}


def conflict_note(preset: dict[str, dict[str, Any]],
                  place_unit: str = "") -> str:
    """One line naming a live conflict between goals, or "" when there is none.

    Favor a party and Partisan fairness want opposite directions out of the same
    three metrics, so only one of them can be on. They are made exclusive when
    applied; this reports a conflict reached some other way, such as a preset
    file or hand-set weights.
    """
    live = [g for g in GOALS if goal_state(preset, g.gid).on]
    for goal in live:
        for other in goal.conflicts:
            if any(g.gid == other for g in live):
                return (f"{goal.title(place_unit)} and "
                        f"{GOALS_BY_ID[other].title(place_unit)} pull the same "
                        f"scores in opposite directions. Setting either one "
                        f"switches the other off.")
    return ""


# ── Built-in starting points ─────────────────────────────────────────────────
# Each is only a set of goal strengths, applied through apply_goals like any
# other change, so a starting point can do nothing a user could not do by hand.
# Goals left out are switched off.

STARTERS: tuple[tuple[str, str, dict[str, float]], ...] = (
    ("balanced", "Balanced and compact", {
        "compact": 60, "counties": 40, "equal_pop": 40, "fairness": 20,
        "favor_party": 0, "competitive": 0, "minority": 0, "continuity": 0,
    }),
    ("party", "Maximize one party's seats", {
        "favor_party": 100, "compact": 20, "equal_pop": 40,
        "fairness": 0, "competitive": 0, "minority": 0, "counties": 0,
        "continuity": 0,
    }),
    ("minority", "Maximize minority opportunity", {
        "minority": 60, "compact": 20, "counties": 20, "equal_pop": 40,
        "favor_party": 0, "fairness": 0, "competitive": 0, "continuity": 0,
    }),
    ("continuity", "Least change from current map", {
        "continuity": 80, "compact": 20, "equal_pop": 40,
        "favor_party": 0, "fairness": 0, "competitive": 0, "minority": 0,
        "counties": 0,
    }),
)

STARTERS_BY_ID: dict[str, dict[str, float]] = {k: v for k, _, v in STARTERS}


def calibration_rows() -> list[tuple[str, str, str, float]]:
    """(goal label, strength word, "section.key", weight) for every step.

    The calibration table in one callable form, for documentation and tests.
    """
    rows: list[tuple[str, str, str, float]] = []
    for goal in GOALS:
        label = goal.title()
        for name, x in LADDER:
            if x == 0:
                continue
            for sec, keys in goal_weights(goal.gid, x).items():
                for key, val in keys.items():
                    rows.append((label, name, f"{sec}.{key}", val))
    return rows
