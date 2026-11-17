"""Map style: three lines, each with its own width and colour."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest

from mosaic.gui import map_style
from mosaic.gui.map_style import MapStyle, default_colors, line_label, singular, stroke
from mosaic.gui.map_view import MapView
from mosaic.gui.theme import PALETTES

ROOT = Path(__file__).resolve().parents[1]
RED = (255, 0, 0)


def _canvas():
    rgba = np.full((21, 21, 4), 200, dtype=np.uint8)
    mask = np.zeros((21, 21), dtype=bool)
    mask[:, 10] = True                      # one vertical one-pixel line
    return rgba, mask


def _profile(rgba):
    """Red-ness across the middle row: 1 where solid red, 0 where untouched."""
    row = rgba[10, :, :3].astype(float)
    return np.round((200 - row[:, 1]) / 200, 2)


# ── stroke ───────────────────────────────────────────────────────────────────

def test_width_zero_draws_nothing():
    rgba, mask = _canvas()
    before = rgba.copy()
    stroke(rgba, mask, 0.0, RED)
    stroke(rgba, mask, -1.0, RED)
    assert (rgba == before).all()


@pytest.mark.parametrize("width", [1, 2, 3, 5, 8])
def test_whole_widths_are_that_many_solid_pixels(width):
    rgba, mask = _canvas()
    stroke(rgba, mask, width, RED)
    profile = _profile(rgba)
    assert (profile == 1).sum() == width
    assert ((profile > 0) & (profile < 1)).sum() == 0


def test_a_fractional_width_adds_a_partial_rim():
    rgba, mask = _canvas()
    stroke(rgba, mask, 2.5, RED)
    profile = _profile(rgba)
    assert (profile == 1).sum() == 2
    assert sorted(profile[(profile > 0) & (profile < 1)].tolist()) == [0.5]
    # Total ink is the width asked for.
    assert profile.sum() == pytest.approx(2.5)


def test_a_width_under_one_is_one_faint_pixel():
    rgba, mask = _canvas()
    stroke(rgba, mask, 0.5, RED)
    profile = _profile(rgba)
    assert (profile > 0).sum() == 1 and profile.max() == 0.5


def test_wider_is_never_lighter():
    ink = []
    for width in np.arange(0, 8.01, 0.25):
        rgba, mask = _canvas()
        stroke(rgba, mask, float(width), RED)
        ink.append(_profile(rgba).sum())
    assert ink == sorted(ink)
    assert ink[0] == 0 and ink[-1] == pytest.approx(8.0)


def test_alpha_is_left_alone():
    rgba, mask = _canvas()
    stroke(rgba, mask, 3.5, RED)
    assert (rgba[..., 3] == 200).all()


# ── MapStyle ─────────────────────────────────────────────────────────────────

def test_defaults_are_the_ones_in_the_design():
    style = MapStyle()
    assert style.widths == {"district": 2.5, "place": 1.5, "precinct": 0.5}
    assert all(style.colors[kind] is None for kind in map_style.KINDS)
    assert map_style.MAX_WIDTH == 8.0


def test_default_colours_are_the_design_tokens_for_each_theme():
    dark, light = default_colors(PALETTES["dark"]), default_colors(PALETTES["light"])
    assert dark == {"district": (0xe8, 0xea, 0xf0), "place": (0xdc, 0xc8, 0x6e),
                    "precinct": (0x96, 0x9c, 0xa8)}
    assert light == {"district": (0x1c, 0x1e, 0x22), "place": (0x8c, 0x5f, 0x00),
                     "precinct": (0x6e, 0x74, 0x7c)}


def test_an_unchosen_colour_follows_the_theme_and_a_chosen_one_does_not():
    style = MapStyle()
    dark, light = default_colors(PALETTES["dark"]), default_colors(PALETTES["light"])
    assert style.color("district", dark) != style.color("district", light)
    style.colors["district"] = (1, 2, 3)
    assert style.color("district", dark) == style.color("district", light) == (1, 2, 3)


def test_style_round_trips_through_its_saved_form():
    style = MapStyle()
    style.widths.update(district=4.0, precinct=0.0)
    style.colors["place"] = (10, 20, 30)
    back = MapStyle.from_json(style.to_json())
    assert back.widths == style.widths and back.colors == style.colors


@pytest.mark.parametrize("data", [
    None, [], "x", {"district": 3}, {"district": {"width": -1}},
    {"district": {"width": 9}}, {"district": {"width": True}},
    {"place": {"color": [1, 2]}}, {"place": {"color": [1, 2, 999]}},
    {"precinct": {"color": "red"}},
])
def test_unusable_saved_values_fall_back_to_defaults(data):
    style = MapStyle.from_json(data)
    assert style.widths == map_style.DEFAULT_WIDTHS
    assert all(c is None for c in style.colors.values())


# ── Wording ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("unit, one", [
    ("cities", "city"), ("counties", "county"), ("towns", "town"),
    ("places", "place"), ("wards", "ward"), ("precincts", "precinct"),
    ("parish", "parish"), ("Cities", "City"),
])
def test_singular(unit, one):
    assert singular(unit) == one


def test_place_lines_are_named_for_the_place_unit():
    assert line_label("cities") == "City lines"
    assert line_label("counties") == "County lines"


# ── On the map ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def view():
    gdf = gpd.read_file(ROOT / "shapefiles" / "North_Carolina_Simplified.shp")
    _, county = np.unique(gdf["CTY"].values, return_inverse=True)
    raster = MapView("unused", 500, 250)
    assert raster.load(gdf, county_array=county.astype(np.int32))
    order = np.argsort(gdf.geometry.bounds["minx"].values)
    assignment = np.empty(len(gdf), dtype=np.int32)
    assignment[order] = np.arange(len(gdf)) * 5 // len(gdf)
    raster.line_colors = {"district": (255, 0, 0), "place": (0, 255, 0),
                          "precinct": (0, 0, 255)}
    return raster, assignment


def _draw(view, assignment, **widths):
    view.style = MapStyle(widths={"district": 0.0, "place": 0.0, "precinct": 0.0,
                                  **widths})
    view.county_overlay = view.precinct_overlay = True
    return view.compose_rgba(assignment, 5)[..., :3]


def _count(frame, rgb):
    return int((frame == rgb).all(axis=-1).sum())


def test_width_zero_hides_each_line(view):
    raster, assignment = view
    frame = _draw(raster, assignment)
    for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
        assert _count(frame, rgb) == 0


def test_each_line_takes_its_own_colour_and_width(view):
    raster, assignment = view
    for kind, rgb in (("district", (255, 0, 0)), ("place", (0, 255, 0)),
                      ("precinct", (0, 0, 255))):
        thin = _count(_draw(raster, assignment, **{kind: 1.0}), rgb)
        thick = _count(_draw(raster, assignment, **{kind: 3.0}), rgb)
        assert thin > 100, kind
        assert thick > thin * 1.8, kind
        # Only this line was drawn.
        frame = _draw(raster, assignment, **{kind: 1.0})
        others = {(255, 0, 0), (0, 255, 0), (0, 0, 255)} - {rgb}
        assert all(_count(frame, other) == 0 for other in others)


def test_a_chosen_colour_replaces_the_default(view):
    raster, assignment = view
    raster.style = MapStyle(widths={"district": 2.0, "place": 0.0, "precinct": 0.0})
    raster.style.colors["district"] = (12, 34, 56)
    frame = raster.compose_rgba(assignment, 5)[..., :3]
    assert _count(frame, (12, 34, 56)) > 100 and _count(frame, (255, 0, 0)) == 0


def test_district_borders_draw_over_place_lines_over_precinct_lines(view):
    """Layer order from docs/design/map.md."""
    raster, assignment = view
    frame = _draw(raster, assignment, district=3.0, place=3.0, precinct=3.0)
    only_place = _draw(raster, assignment, place=3.0)
    only_district = _draw(raster, assignment, district=3.0)
    red = (only_district == (255, 0, 0)).all(axis=-1)
    green = (only_place == (0, 255, 0)).all(axis=-1)
    assert (frame[red] == (255, 0, 0)).all()            # nothing covers districts
    assert (frame[green & ~red] == (0, 255, 0)).all()   # precincts do not cover places


def test_exports_scale_every_line(view):
    raster, assignment = view
    one = _count(_draw(raster, assignment, district=1.0), (255, 0, 0))
    raster.line_scale = 3
    three = _count(_draw(raster, assignment, district=1.0), (255, 0, 0))
    raster.line_scale = 1.0
    assert three > one * 1.8


def test_exports_take_the_style_and_light_colours():
    from mosaic.gui.app import export_mixin
    text = Path(export_mixin.__file__).read_text(encoding="utf-8")
    assert "offscreen.style = src.style" in text
    assert "offscreen.line_colors = _export_line_colors()" in text
    assert export_mixin._export_line_colors() == default_colors(PALETTES["light"])
    # The PDF's stroke widths follow the style too, at the old default weights.
    assert export_mixin._PT_PER_PX * 2.5 == pytest.approx(0.75)


def test_presets_do_not_carry_map_style():
    from mosaic.presets import SCHEMA
    flat = {key for keys in SCHEMA.values() for key in keys} | set(SCHEMA)
    assert not any("line" in k or "border" in k or "basemap" in k for k in flat)
