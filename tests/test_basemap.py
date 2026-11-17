"""The basemap: tile decoding, tile arithmetic, and whether it lines up.

Nothing here touches the network. Tiles are written by tests/mvt.py and served
from a fake opener, so the alignment tests can put a feature at a known
longitude and latitude and ask where it was painted.
"""

from __future__ import annotations

import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from mvt import encode_tile
from pyproj import Transformer

from mosaic import basemap as B
from mosaic.gui.map_style import MapStyle
from mosaic.gui.map_view import MapView

ROOT = Path(__file__).resolve().parents[1]
NC = ROOT / "shapefiles" / "North_Carolina_Simplified.shp"
COLORS = {"ground": (238, 240, 243), "water": (0, 0, 255),
          "road_major": (255, 0, 0), "road_minor": (0, 255, 0),
          "label": (0, 0, 0), "halo": (238, 240, 243)}


# ── Decoding ─────────────────────────────────────────────────────────────────

def test_a_tile_round_trips_through_the_decoder():
    data = encode_tile({
        "water": [(3, {"class": "lake"},
                   [[(0.1, 0.1), (0.5, 0.1), (0.5, 0.4), (0.1, 0.4), (0.1, 0.1)]])],
        "transportation": [(2, {"class": "motorway"},
                            [[(0.0, 0.5), (0.25, 0.5), (1.0, 0.75)]])],
        "place": [(1, {"class": "city", "name": "Fort Worth"}, [[(0.5, 0.5)]])],
        "building": [(3, {"x": "y"}, [[(0, 0), (1, 0), (1, 1), (0, 0)]])],
    })
    tile = B.decode_tile(data)
    assert set(tile) == {"water", "transportation", "place"}    # building skipped
    road = tile["transportation"][0]
    assert road["type"] == 2 and road["props"] == {"class": "motorway"}
    assert np.allclose(road["paths"][0], [(0.0, 0.5), (0.25, 0.5), (1.0, 0.75)],
                       atol=1 / 4096)
    ring = tile["water"][0]["paths"][0]
    assert len(ring) == 5 and np.allclose(ring[0], ring[-1])
    assert B._ring_area(ring) > 0                                # an exterior
    assert tile["place"][0]["props"]["name"] == "Fort Worth"
    assert np.allclose(tile["place"][0]["paths"][0], [(0.5, 0.5)], atol=1 / 4096)


def test_an_empty_tile_decodes_to_nothing():
    assert B.decode_tile(b"") == {"water": [], "transportation": [], "place": []}


# ── Tile arithmetic ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("lon, lat", [(-97.33, 32.75), (-78.64, 35.78), (0, 0),
                                      (139.7, 35.7), (-0.1, 51.5)])
def test_tile_coordinates_invert(lon, lat):
    for zoom in (0, 5, 11, 14):
        x, y = B.lonlat_to_tile(lon, lat, zoom)
        back_lon, back_lat = B.tile_to_lonlat(x, y, zoom)
        assert float(back_lon) == pytest.approx(lon, abs=1e-9)
        assert float(back_lat) == pytest.approx(lat, abs=1e-9)


def test_known_tile():
    """z10/x235/y413 is the tile over downtown Fort Worth."""
    x, y = B.lonlat_to_tile(-97.33, 32.75, 10)
    assert (int(x), int(y)) == (235, 413)


def test_tiles_cover_the_box():
    box = (-97.6, 32.5, -97.0, 33.0)
    tiles = B.tiles_for(box, 10)
    assert (10, 235, 413) in tiles
    for z, x, y in tiles:
        west, north = B.tile_to_lonlat(x, y, z)
        east, south = B.tile_to_lonlat(x + 1, y + 1, z)
        assert west < box[2] and east > box[0] and south < box[3] and north > box[1]


def test_zoom_follows_the_map_and_the_tile_budget():
    county = (-97.55, 32.55, -97.03, 33.0)
    state = (-84.4, 33.8, -75.4, 36.6)
    assert B.choose_zoom(county, 900) > B.choose_zoom(state, 900)
    assert B.choose_zoom(county, 1800) >= B.choose_zoom(county, 450)
    for box in (county, state, (-180, -80, 180, 80)):
        for width in (300, 900, 4000):
            zoom = B.choose_zoom(box, width)
            assert 0 <= zoom <= B.MAX_ZOOM
            assert len(B.tiles_for(box, zoom)) <= B.MAX_TILES


# ── Projection ───────────────────────────────────────────────────────────────

def test_projection_is_the_one_mapview_uses():
    """The basemap is painted with Projection; the precincts were rasterised
    by MapView.load. They agree, for the fitted view and a zoomed one: a point
    inside a precinct projects onto a pixel that precinct owns."""
    gdf = gpd.read_file(NC)
    inside = gdf.geometry.representative_point()
    big = np.flatnonzero((gdf.geometry.area > gdf.geometry.area.median()).values)
    for view in ((0.0, 0.0, 1.0, 1.0), (0.3, 0.2, 0.7, 0.6)):
        raster = MapView("unused", 1600, 900)
        assert raster.load(gdf, view_bounds=view)
        px, py = raster.projection().to_pixel(inside.x.values, inside.y.values)
        hits = total = 0
        for i in big:
            x, y = int(px[i]), int(py[i])
            if 2 <= x < 1598 and 2 <= y < 898:
                total += 1
                hits += raster._pixel_map[y, x] == i
        assert total > 100 and hits / total > 0.99, (view, hits, total)


def test_projection_inverts():
    projection = B.Projection((100.0, 200.0, 900.0, 500.0), 640, 480,
                              (0.2, 0.1, 0.8, 0.7))
    x, y = projection.to_map(*projection.to_pixel(417.0, 333.0))
    assert float(x) == pytest.approx(417.0) and float(y) == pytest.approx(333.0)


# ── Alignment ────────────────────────────────────────────────────────────────

class FakeStore(B.TileStore):
    """Serves one synthetic world: a road along a meridian and a parallel
    through ``point``, and a lake with a corner on it."""

    def __init__(self, tmp_path, point):
        super().__init__(tmp_path, opener=self._refuse)
        self.point = point
        self.requested = []

    @staticmethod
    def _refuse(url):
        raise OSError("tests do not use the network")

    def tile(self, z, x, y):
        self.requested.append((z, x, y))
        fx, fy = B.lonlat_to_tile(*self.point, z)
        lx, ly = fx - x, fy - y           # the point, in this tile's 0-1 units
        features = {"transportation": [], "water": [], "place": []}
        if 0 <= lx <= 1:
            features["transportation"].append(
                (2, {"class": "motorway"}, [[(lx, 0.0), (lx, 1.0)]]))
        if 0 <= ly <= 1:
            features["transportation"].append(
                (2, {"class": "motorway"}, [[(0.0, ly), (1.0, ly)]]))
        if 0 <= lx <= 1 and 0 <= ly <= 1:
            features["place"].append((1, {"class": "city", "name": "Here"},
                                      [[(lx, ly)]]))
        return encode_tile(features)


def _red(rgb):
    return (rgb[..., 0] > 200) & (rgb[..., 1] < 60) & (rgb[..., 2] < 60)


@pytest.mark.parametrize("name, crs, bounds, point", [
    # North Carolina, as shipped: geographic NAD83.
    ("north carolina", "EPSG:4269", (-84.32, 33.84, -75.46, 36.59), (-78.64, 35.78)),
    # Tarrant County, Texas, in the state plane its shapefiles usually use
    # (NAD83 / Texas North Central, US survey feet).
    ("tarrant county", "EPSG:2276", None, (-97.33, 32.75)),
    # The same county in UTM 14N metres, and in Web Mercator.
    ("tarrant utm", "EPSG:32614", None, (-97.33, 32.75)),
    ("tarrant mercator", "EPSG:3857", None, (-97.33, 32.75)),
])
def test_a_feature_lands_where_the_crs_puts_it(tmp_path, name, crs, bounds, point):
    """A road through a known longitude and latitude is painted on the pixel
    that the map's own CRS and projection give for that point."""
    to_map = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    if bounds is None:
        west, south, east, north = -97.55, 32.55, -97.03, 33.0
        xs, ys = to_map.transform([west, east, west, east],
                                  [south, south, north, north])
        bounds = (min(xs), min(ys), max(xs), max(ys))
    projection = B.Projection(bounds, 900, 620)
    store = FakeStore(tmp_path, point)
    raster = B.render(store, crs, projection, COLORS)
    assert raster.drawn and not raster.error

    x, y = to_map.transform(*point)
    want_x, want_y = projection.to_pixel(x, y)
    red = _red(raster.rgb)
    assert red.sum() > 500, f"{name}: no roads painted"
    # Both roads pass through the point, so the pixel it projects to is on
    # them. (A parallel is not a row of pixels in a projected CRS, so this
    # looks at the point itself and not at a whole row or column.)
    cx, cy = int(round(float(want_x))), int(round(float(want_y)))
    assert red[cy - 1:cy + 2, cx - 1:cx + 2].any(), f"{name}: road misses the point"
    # And they are nowhere else near it: no red a few pixels off the diagonal.
    assert not red[cy - 12:cy - 6, cx + 6:cx + 12].any()
    assert not red[cy + 6:cy + 12, cx - 12:cx - 6].any()
    # The place name sits on the same point.
    assert raster.places and raster.places[0][0] == "Here"
    assert abs(raster.places[0][1] - float(want_x)) <= 1.0
    assert abs(raster.places[0][2] - float(want_y)) <= 1.0


def test_a_zoomed_view_stays_aligned(tmp_path):
    to_map = Transformer.from_crs("EPSG:4326", "EPSG:2276", always_xy=True)
    xs, ys = to_map.transform([-97.55, -97.03], [32.55, 33.0])
    projection = B.Projection((xs[0], ys[0], xs[1], ys[1]), 800, 600,
                              (0.25, 0.3, 0.65, 0.7))
    raster = B.render(FakeStore(tmp_path, (-97.33, 32.75)), "EPSG:2276",
                      projection, COLORS)
    want_x, want_y = projection.to_pixel(*to_map.transform(-97.33, 32.75))
    cx, cy = int(round(float(want_x))), int(round(float(want_y)))
    assert _red(raster.rgb)[cy - 1:cy + 2, cx - 1:cx + 2].any()
    assert abs(raster.places[0][1] - float(want_x)) <= 1.0
    assert abs(raster.places[0][2] - float(want_y)) <= 1.0


def test_a_map_without_a_crs_in_degrees_is_taken_as_lon_lat(tmp_path):
    projection = B.Projection((-97.55, 32.55, -97.03, 33.0), 600, 500)
    raster = B.render(FakeStore(tmp_path, (-97.33, 32.75)), None, projection, COLORS)
    assert raster.drawn and not raster.error


def test_a_map_without_a_crs_in_other_units_gets_no_basemap(tmp_path):
    projection = B.Projection((2_300_000, 6_900_000, 2_450_000, 7_050_000), 600, 500)
    raster = B.render(FakeStore(tmp_path, (-97.33, 32.75)), None, projection, COLORS)
    assert raster.error and not raster.drawn
    assert (raster.rgb == COLORS["ground"]).all()


# ── Offline, and the cache ───────────────────────────────────────────────────

def test_offline_is_reported_not_raised(tmp_path):
    def offline(url):
        raise OSError("Network is unreachable")

    raster = B.render(B.TileStore(tmp_path, opener=offline), "EPSG:4326",
                      B.Projection((-97.55, 32.55, -97.03, 33.0), 400, 300), COLORS)
    assert not raster.drawn
    assert "could not reach OpenFreeMap" in raster.error
    assert (raster.rgb == COLORS["ground"]).all()


def test_tiles_are_cached_on_disk_and_reused_offline(tmp_path):
    served = []
    tile = encode_tile({"transportation": [
        (2, {"class": "motorway"}, [[(0.0, 0.5), (1.0, 0.5)]])]})

    def online(url):
        served.append(url)
        if url == B.TILEJSON_URL:
            return b'{"tiles": ["https://example.invalid/v1/{z}/{x}/{y}.pbf"]}'
        return tile

    projection = B.Projection((-97.55, 32.55, -97.03, 33.0), 400, 300)
    first = B.render(B.TileStore(tmp_path, opener=online), "EPSG:4326",
                     projection, COLORS)
    assert first.drawn and served[0] == B.TILEJSON_URL
    assert all(u.startswith("https://example.invalid/v1/") for u in served[1:])
    assert list(tmp_path.rglob("*.pbf"))

    def offline(url):
        raise OSError("no network")

    second = B.render(B.TileStore(tmp_path, opener=offline), "EPSG:4326",
                      projection, COLORS)
    assert second.drawn and not second.error
    assert (second.rgb == first.rgb).all()


def test_render_can_be_cancelled(tmp_path):
    raster = B.render(FakeStore(tmp_path, (-97.33, 32.75)), "EPSG:4326",
                      B.Projection((-97.55, 32.55, -97.03, 33.0), 400, 300),
                      COLORS, cancelled=lambda: True)
    assert raster is None


# ── Compositing ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def nc_view():
    gdf = gpd.read_file(NC)
    view = MapView("unused", 400, 200)
    assert view.load(gdf)
    order = np.argsort(gdf.geometry.bounds["minx"].values)
    assignment = np.empty(len(gdf), dtype=np.int32)
    assignment[order] = np.arange(len(gdf)) * 6 // len(gdf)
    # Whole-pixel black borders, so a border pixel is told from fill by colour.
    view.style = MapStyle(widths={"district": 2.0, "place": 1.0, "precinct": 1.0})
    view.line_colors = {"district": (0, 0, 0), "place": (180, 180, 180),
                        "precinct": (255, 0, 255)}
    return view, assignment


def _basemap_for(view):
    rgb = np.zeros((view._h, view._w, 3), dtype=np.uint8)
    rgb[...] = (10, 200, 30)
    labels = np.zeros((view._h, view._w, 4), dtype=np.uint8)
    labels[5:9, 5:9] = (255, 255, 255, 255)
    return B.BasemapRaster(rgb, labels, (view._w, view._h, tuple(view._view_bounds)),
                           drawn=True)


def test_the_fill_is_composited_over_the_basemap(nc_view):
    view, assignment = nc_view
    view.basemap, view.basemap_on, view.fill_opacity = None, False, 1.0
    plain = view.compose_rgba(assignment, 6).astype(np.int32)
    view.basemap, view.basemap_on, view.fill_opacity = _basemap_for(view), True, 0.45
    try:
        over = view.compose_rgba(assignment, 6).astype(np.int32)
    finally:
        view.basemap, view.basemap_on, view.fill_opacity = None, False, 1.0
    inside = view._pixel_map >= 0
    lines = (plain[..., :3] == 0).all(axis=-1)
    fill = inside & ~lines
    fill[5:9, 5:9] = False
    want = np.rint(np.array([10, 200, 30]) + (plain[fill][:, :3] - [10, 200, 30]) * 0.45)
    assert np.abs(over[fill][:, :3] - want).max() <= 1
    # Outside the precincts the basemap shows through as context.
    outside = ~inside
    outside[5:9, 5:9] = False
    # (A border's second pixel can fall just outside where it meets the coast.)
    beyond = over[outside][:, :3]
    plain_basemap = (beyond == [10, 200, 30]).all(axis=-1)
    assert plain_basemap.mean() > 0.999
    assert (beyond[~plain_basemap] == 0).all()
    # District borders are untouched; place names sit on top of everything.
    assert (over[lines & inside][:, :3] == 0).all()
    assert (over[5:9, 5:9, :3] == 255).all()


def test_a_basemap_for_another_raster_is_ignored(nc_view):
    view, assignment = nc_view
    plain = view.compose_rgba(assignment, 6)
    stale = _basemap_for(view)
    stale.key = (view._w + 1, view._h, tuple(view._view_bounds))
    view.basemap, view.basemap_on = stale, True
    assert (view.compose_rgba(assignment, 6) == plain).all()
    view.basemap, view.basemap_on = None, False


def test_attribution_is_stamped_into_the_image():
    rgba = np.full((120, 600, 4), 255, dtype=np.uint8)
    B.stamp_attribution(rgba, COLORS)
    assert "OpenFreeMap" in B.ATTRIBUTION and "OpenStreetMap" in B.ATTRIBUTION
    changed = (rgba[..., :3] != 255).any(axis=-1)
    assert changed[-30:, :400].sum() > 300          # bottom-left corner
    assert not changed[:60].any()


def test_fills_default_to_the_basemap_only_for_districts():
    from mosaic.gui.app.basemap_mixin import _BASEMAP_DEFAULT_ON, BASEMAP_OPACITY
    assert _BASEMAP_DEFAULT_ON == {"districts"}
    assert BASEMAP_OPACITY == 0.45


def test_colours_come_from_palette_tokens_in_both_themes():
    from mosaic.gui.app.basemap_mixin import BasemapMixin
    from mosaic.gui.theme import PALETTES
    seen = set()
    for palette in PALETTES.values():
        colors = BasemapMixin._basemap_colors(palette)
        assert colors["ground"] == tuple(palette.basemap_ground[:3])
        assert colors["water"] == tuple(palette.basemap_water[:3])
        seen.add(colors["ground"])
    assert len(seen) == 2
    assert math.isfinite(sum(seen.pop()))
