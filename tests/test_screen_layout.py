"""The score catalogue behind the redesigned main screen.

Pure data: the row list, its grouping and its wording. Importing
``_common`` pulls in Dear PyGui but never opens a window, so this runs
headless; everything that needs a viewport is checked by hand instead.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from mosaic import goals as G
from mosaic.gui.app._common import (
    _GOAL_ORDER,
    _SCORE_GROUPS,
    _SCORE_NEED_PHRASE,
    _SCORE_PANELS,
    _SCORE_ROWS,
)
from mosaic.presets import SCHEMA

DESIGN = Path(__file__).resolve().parents[1] / "docs" / "design" / "screen.html"
HINGE = ""          # the one row with no preset section


# ── Shape ────────────────────────────────────────────────────────────────────

def test_the_list_holds_twenty_scores():
    assert len(_SCORE_ROWS) == 20
    assert len({r[0] for r in _SCORE_ROWS}) == 20


def test_every_score_names_a_real_preset_section():
    for sec, label, *_rest in _SCORE_ROWS:
        assert sec in SCHEMA or sec == HINGE, label


def test_every_weighted_preset_section_is_listed():
    listed = {r[0] for r in _SCORE_ROWS}
    scored = {s for s in SCHEMA
              if any(k.startswith("weight") for k in SCHEMA[s])}
    assert not scored - listed


def test_the_county_bias_is_not_one_of_the_twenty():
    """It is a move bias, not a score: it carries a multiplier rather than a
    weight and is not in the objective, so it rides in the County Congruence
    row instead of taking a row of its own."""
    assert "county_bias" not in {r[0] for r in _SCORE_ROWS}
    assert not any(k.startswith("weight") for k in SCHEMA["county_bias"])
    assert "multiplier" in SCHEMA["county_bias"]


def test_each_row_points_at_a_weight_and_a_chart():
    for sec, label, _phrase, group, weights, _extras in _SCORE_ROWS:
        assert weights, label
        assert 0 <= group < len(_SCORE_GROUPS), label
        assert sec in _SCORE_PANELS, label
        assert _SCORE_PANELS[sec][0].startswith("panel_"), label


def test_the_groups_are_the_three_the_design_shows():
    assert _SCORE_GROUPS == ("Geography and population", "Elections",
                             "Demographics")
    sizes = [sum(1 for r in _SCORE_ROWS if r[3] == i)
             for i in range(len(_SCORE_GROUPS))]
    assert sizes == [8, 9, 3]


def test_rows_are_in_group_order():
    """The column is built by walking the list once per group, so a row out of
    order would silently land under the wrong subheading."""
    groups = [r[3] for r in _SCORE_ROWS]
    assert groups == sorted(groups)


# ── Wording ──────────────────────────────────────────────────────────────────

def _design_phrases() -> dict[str, str]:
    """Score name -> phrase, read out of the mockup."""
    text = DESIGN.read_text(encoding="utf-8")
    start = text.index("Geography and population")
    end = text.index("Community Dispersion")
    lines = [ln.strip() for ln in
             re.sub(r"<[^>]+>", "\n", html.unescape(text[start:end])).splitlines()
             if ln.strip()]
    out, skip = {}, {"Weight", "Open chart", "Aim", "Fair", "D", "R", "off"}
    names = {r[1] for r in _SCORE_ROWS}
    for i, line in enumerate(lines):
        if line in names and i + 1 < len(lines) and lines[i + 1] not in skip:
            out.setdefault(line, lines[i + 1])
    return out


def test_phrases_match_the_mockup():
    design = _design_phrases()
    for sec, label, phrase, *_rest in _SCORE_ROWS:
        if label not in design:
            continue
        want = design[label]
        if want in _SCORE_NEED_PHRASE.values():
            # The mockup shows this score unavailable, so its phrase there is
            # what it needs, not what it favors.
            continue
        assert phrase.replace("{unit}", "cities") == want, label


def test_the_mockup_covered_most_of_the_list():
    """Guard the guard: a parse that found nothing would pass the test above."""
    assert len(_design_phrases()) >= 18


def test_phrases_are_short_enough_not_to_wrap():
    for _sec, label, phrase, *_rest in _SCORE_ROWS:
        assert len(phrase) <= 45, f"{label}: {len(phrase)}"


def test_wording_is_sentence_case_after_the_first_word():
    """The design system asks for sentence case; proper nouns and the metric
    names inside a phrase are the exceptions."""
    allowed = {"Democratic", "Fewer", "Shape", "Partisan", "Less", "Keeps",
               "Count", "Populations", "Needs", "Seat", "Moves", "Chance",
               "Group", "Avoids", "Aim"}
    for _sec, label, phrase, *_rest in _SCORE_ROWS:
        for word in phrase.split()[1:]:
            assert not word[0].isupper() or word.strip(":,") in allowed, (
                f"{label}: {word}")


def test_the_place_unit_only_appears_where_the_column_decides_it():
    unit_rows = [r[1] for r in _SCORE_ROWS if "{unit}" in r[2]]
    assert unit_rows == ["County Congruence"]
    assert "{unit}" in _SCORE_NEED_PHRASE["county"]


# ── Unavailable scores ───────────────────────────────────────────────────────

def test_need_phrases_cover_the_preset_needs_vocabulary():
    """_PRESET_NEEDS is the single source of which score needs what; this only
    supplies the wording, so it has to answer every value it can produce."""
    needs = {"election", "demographic", "county", "reference plan"}
    assert needs <= set(_SCORE_NEED_PHRASE)
    assert "" in _SCORE_NEED_PHRASE      # data present, nothing to measure
    for phrase in _SCORE_NEED_PHRASE.values():
        assert len(phrase.replace("{unit}", "counties")) <= 45, phrase


# ── Goals ────────────────────────────────────────────────────────────────────

def test_the_goal_order_lists_every_goal_once():
    assert sorted(_GOAL_ORDER) == sorted(g.gid for g in G.GOALS)


def test_the_goal_order_is_the_screen_order_not_the_module_order():
    """goals.GOALS also fixes the Score Contributors grouping, so the screen
    keeps its own order rather than reordering that tuple."""
    assert _GOAL_ORDER != tuple(g.gid for g in G.GOALS)
    assert _GOAL_ORDER[0] == "compact"


@pytest.mark.parametrize("gid", _GOAL_ORDER)
def test_every_goal_drives_scores_that_are_listed(gid):
    listed = {r[0] for r in _SCORE_ROWS}
    for section in G.GOALS_BY_ID[gid].sections:
        assert section in listed, f"{gid} drives unlisted {section}"
