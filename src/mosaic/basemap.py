"""OpenFreeMap context under the map: fetch, decode, and paint a few layers.

OpenFreeMap serves vector tiles only (OpenMapTiles schema, no API key), so
there is no image to download. This module fetches the tiles covering a view,
decodes them, and paints a deliberately small set of layers into an array the
map composes its fill over: ground, water, major roads, minor roads when the
view is close enough for the tiles to carry them, and place names. It does not
try to reproduce an OpenFreeMap style.

No Dear PyGui here and no new dependency: a vector tile is a short protobuf
message, and the forty lines of decoder below read the four layers used and
skip the rest (buildings, land cover and points of interest are most of a
tile's bytes). Everything runs on a worker thread; the frame loop only ever
sees a finished array.

Tiles are cached under ``cache/basemap/{z}/{x}/{y}.pbf``. The tile data for a
place changes slowly and the cache is keyed by tile, not by OpenFreeMap's
weekly dataset version, so a map opened once draws its basemap offline after.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("mosaic")

TILEJSON_URL = "https://tiles.openfreemap.org/planet"
ATTRIBUTION = "Basemap: OpenFreeMap © OpenMapTiles, data from OpenStreetMap"
MAX_ZOOM = 14           # OpenFreeMap's deepest tiles
MAX_TILES = 40          # per view; the zoom drops until the view needs no more
_TIMEOUT = 12           # seconds per request
_USER_AGENT = "Mosaic-basemap"
_FONT = (Path(__file__).resolve().parent / "assets" / "fonts" / "inter"
         / "Inter-Regular.ttf")

# OpenMapTiles `transportation.class` values, by how they are drawn.
_MAJOR = {"motorway", "trunk", "primary"}
_MEDIUM = {"secondary", "tertiary"}
_MINOR = {"minor"}
# `place.class` values worth a label, most important first.
_PLACE_RANK = {"city": 0, "town": 1, "village": 2, "suburb": 3}
_LAYERS = ("water", "transportation", "place")


# ── Vector tile decoding ─────────────────────────────────────────────────────

def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7


def _fields(buf: bytes):
    """Yield (field number, wire type, value) for one protobuf message."""
    pos, end = 0, len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 2:
            size, pos = _varint(buf, pos)
            value = buf[pos:pos + size]
            pos += size
        elif wire == 5:
            value = buf[pos:pos + 4]
            pos += 4
        elif wire == 1:
            value = buf[pos:pos + 8]
            pos += 8
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield number, wire, value


def _packed(buf: bytes) -> list[int]:
    out, pos, end = [], 0, len(buf)
    while pos < end:
        value, pos = _varint(buf, pos)
        out.append(value)
    return out


def _value(buf: bytes):
    """A layer `Value`: only strings and integers are read; the rest is None."""
    for number, _wire, value in _fields(buf):
        if number == 1:
            return bytes(value).decode("utf-8", "replace")
        if number in (4, 5):
            return value
        if number == 6:
            return (value >> 1) ^ -(value & 1)
        if number == 7:
            return bool(value)
    return None


def _geometry(commands: list[int]) -> list[np.ndarray]:
    """Decode MVT drawing commands into paths of tile-unit (x, y) points."""
    paths: list[np.ndarray] = []
    current: list[tuple[int, int]] = []
    x = y = 0
    i, n = 0, len(commands)
    while i < n:
        header = commands[i]
        i += 1
        command, count = header & 7, header >> 3
        if command == 7:                      # ClosePath
            if current:
                current.append(current[0])
            continue
        for _ in range(count):
            dx, dy = commands[i], commands[i + 1]
            i += 2
            x += (dx >> 1) ^ -(dx & 1)
            y += (dy >> 1) ^ -(dy & 1)
            if command == 1:                  # MoveTo starts a new path
                if current:
                    paths.append(np.array(current, dtype=np.float64))
                current = [(x, y)]
            else:                             # LineTo
                current.append((x, y))
    if current:
        paths.append(np.array(current, dtype=np.float64))
    return paths


def decode_tile(data: bytes, layers=_LAYERS) -> dict[str, list[dict]]:
    """``{layer: [{"type", "props", "paths"}]}`` for the wanted layers.

    ``paths`` are in tile units scaled to 0-1 across the tile, y down. Layers
    not asked for are skipped without being parsed.
    """
    out: dict[str, list[dict]] = {name: [] for name in layers}
    for number, _wire, layer in _fields(data):
        if number != 3:
            continue
        name, extent = None, 4096
        keys: list[str] = []
        values: list[bytes] = []
        features: list[bytes] = []
        for f_number, _w, value in _fields(layer):
            if f_number == 1:
                name = bytes(value).decode("utf-8", "replace")
                if name not in out:
                    break
            elif f_number == 2:
                features.append(value)
            elif f_number == 3:
                keys.append(bytes(value).decode("utf-8", "replace"))
            elif f_number == 4:
                values.append(value)
            elif f_number == 5:
                extent = value
        if name not in out:
            continue
        decoded: dict[int, object] = {}
        for feature in features:
            tags: list[int] = []
            kind = 0
            commands: list[int] = []
            for f_number, _w, value in _fields(feature):
                if f_number == 2:
                    tags = _packed(value)
                elif f_number == 3:
                    kind = value
                elif f_number == 4:
                    commands = _packed(value)
            props = {}
            for k, v in zip(tags[::2], tags[1::2]):
                if v not in decoded:
                    decoded[v] = _value(values[v])
                props[keys[k]] = decoded[v]
            out[name].append({
                "type": kind, "props": props,
                "paths": [path / float(extent) for path in _geometry(commands)],
            })
    return out


# ── Tile arithmetic ──────────────────────────────────────────────────────────

def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Fractional tile x, y of a point in the Web Mercator tile grid."""
    lat = max(-85.0511, min(85.0511, lat))
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tile_to_lonlat(x, y, zoom: int):
    """Inverse of ``lonlat_to_tile``; accepts arrays of fractional tile x, y."""
    n = 2.0 ** zoom
    lon = np.asarray(x, dtype=np.float64) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(
        math.pi * (1.0 - 2.0 * np.asarray(y, dtype=np.float64) / n))))
    return lon, lat


def tiles_for(bounds, zoom: int) -> list[tuple[int, int, int]]:
    """(z, x, y) of every tile touching a (west, south, east, north) box."""
    west, south, east, north = bounds
    x0, y0 = lonlat_to_tile(west, north, zoom)
    x1, y1 = lonlat_to_tile(east, south, zoom)
    top = int(2 ** zoom) - 1
    xs = range(max(0, int(math.floor(x0))), min(top, int(math.floor(x1))) + 1)
    ys = range(max(0, int(math.floor(y0))), min(top, int(math.floor(y1))) + 1)
    return [(zoom, x, y) for y in ys for x in xs]


def choose_zoom(bounds, width_px: int, max_tiles: int = MAX_TILES) -> int:
    """The zoom whose tiles are about as detailed as the map's pixels.

    A tile is drawn about 256px across at its native zoom. Going further adds
    bytes the screen cannot show; stopping short leaves roads out. The count
    of tiles is capped, so a wide view steps down until it fits.
    """
    west, _south, east, _north = bounds
    span = max(east - west, 1e-6)
    zoom = int(round(math.log2(max(width_px, 64) / 256.0 * 360.0 / span)))
    zoom = max(0, min(MAX_ZOOM, zoom))
    while zoom > 0 and len(tiles_for(bounds, zoom)) > max_tiles:
        zoom -= 1
    return zoom


# ── Map projection ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Projection:
    """How MapView turns shapefile coordinates into raster pixels.

    The same arithmetic as ``MapView.load``, kept here so the basemap is
    painted with the transform the precincts were, and the two line up.
    ``bounds`` is the shapefile's total bounds in its own CRS.
    """

    bounds: tuple[float, float, float, float]
    width: int
    height: int
    view: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)

    def _fit(self) -> tuple[float, float, float]:
        gw = max(self.bounds[2] - self.bounds[0], 1e-9)
        gh = max(self.bounds[3] - self.bounds[1], 1e-9)
        scale = min(self.width / gw, self.height / gh) * 0.96
        return (scale, (self.width - gw * scale) / 2.0,
                (self.height - gh * scale) / 2.0)

    def to_pixel(self, x, y):
        scale, ox, oy = self._fit()
        vx0, vy0, vx1, vy1 = self.view
        px = ((np.asarray(x) - self.bounds[0]) * scale + ox
              - vx0 * self.width) / (vx1 - vx0)
        py = (self.height - ((np.asarray(y) - self.bounds[1]) * scale + oy)
              - vy0 * self.height) / (vy1 - vy0)
        return px, py

    def to_map(self, px, py):
        """Inverse of ``to_pixel``: raster pixels back to shapefile coordinates."""
        scale, ox, oy = self._fit()
        vx0, vy0, vx1, vy1 = self.view
        x = (np.asarray(px) * (vx1 - vx0) + vx0 * self.width - ox) / scale
        y = (self.height - np.asarray(py) * (vy1 - vy0) - vy0 * self.height
             - oy) / scale
        return x + self.bounds[0], y + self.bounds[1]

    def visible_bounds(self) -> tuple[float, float, float, float]:
        """(left, bottom, right, top) of the raster, in shapefile coordinates."""
        x, y = self.to_map(np.array([0.0, self.width]),
                           np.array([self.height, 0.0]))
        return float(x[0]), float(y[0]), float(x[1]), float(y[1])


def _transformers(crs):
    """(lon/lat -> crs, crs -> lon/lat), or None when the map has no usable CRS.

    A shapefile with no .prj is taken as lon/lat when its numbers look like
    degrees; anything else cannot be placed on the earth, so it gets no
    basemap rather than a wrong one.
    """
    from pyproj import CRS, Transformer

    if crs is None:
        return "degrees"
    try:
        target = CRS.from_user_input(crs)
    except Exception:
        return None
    wgs = CRS.from_epsg(4326)
    return (Transformer.from_crs(wgs, target, always_xy=True),
            Transformer.from_crs(target, wgs, always_xy=True))


# ── Fetch and cache ──────────────────────────────────────────────────────────

class TileStore:
    """Tiles on disk, fetched on demand. Safe to share between threads."""

    def __init__(self, cache_dir: Path, opener=None):
        self.cache_dir = Path(cache_dir)
        self._template: str | None = None
        self._lock = threading.Lock()
        self._open = opener or self._http
        self._decoded: OrderedDict = OrderedDict()

    @staticmethod
    def _http(url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read()

    def _path(self, z: int, x: int, y: int) -> Path:
        return self.cache_dir / str(z) / str(x) / f"{y}.pbf"

    def _url(self, z: int, x: int, y: int) -> str:
        """The tile's URL, resolving OpenFreeMap's dated template once."""
        with self._lock:
            if self._template is None:
                meta = json.loads(self._open(TILEJSON_URL).decode("utf-8"))
                self._template = meta["tiles"][0]
            return (self._template.replace("{z}", str(z))
                    .replace("{x}", str(x)).replace("{y}", str(y)))

    def tile(self, z: int, x: int, y: int) -> bytes | None:
        """Tile bytes from the cache, else the network; None if unreachable."""
        path = self._path(z, x, y)
        if path.is_file():
            return path.read_bytes()
        try:
            data = self._open(self._url(z, x, y))
        except urllib.error.HTTPError as exc:
            if exc.code != 404:           # a 404 is an empty tile, not an outage
                raise
            data = b""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data

    def features(self, z: int, x: int, y: int) -> dict[str, list[dict]]:
        key = (z, x, y)
        with self._lock:
            if key in self._decoded:
                self._decoded.move_to_end(key)
                return self._decoded[key]
        data = self.tile(z, x, y)
        decoded = decode_tile(data) if data else {name: [] for name in _LAYERS}
        with self._lock:
            self._decoded[key] = decoded
            while len(self._decoded) > 160:
                self._decoded.popitem(last=False)
        return decoded


# ── Rendering ────────────────────────────────────────────────────────────────

@dataclass
class BasemapRaster:
    """A painted basemap for one raster: what the map composes over."""

    rgb: np.ndarray                       # (H, W, 3) uint8, ground/water/roads
    labels: np.ndarray | None             # (H, W, 4) uint8 place names, or None
    key: tuple                            # (width, height, view) it was made for
    zoom: int = 0
    missing: int = 0                      # tiles that could not be fetched
    drawn: bool = False                   # at least one tile was painted
    error: str = ""
    places: list = field(default_factory=list)


def _ring_area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])) / 2.0


def render(store: TileStore, crs, projection: Projection, colors: dict,
           cancelled=None, max_tiles: int = MAX_TILES) -> BasemapRaster | None:
    """Paint the basemap for one raster. Worker thread; no GUI calls.

    ``colors`` maps "ground", "water", "road_major", "road_minor", "label" and
    "halo" to RGB tuples. Returns None if cancelled, and a raster with
    ``error`` set (and plain ground) when nothing could be drawn.
    """
    width, height = projection.width, projection.height
    key = (width, height, tuple(projection.view))
    image = Image.new("RGB", (width, height), tuple(colors["ground"]))
    blank = BasemapRaster(np.array(image, dtype=np.uint8), None, key)

    transforms = _transformers(crs)
    if transforms is None:
        blank.error = "this map's coordinate system is not recognised"
        return blank
    left, bottom, right, top = projection.visible_bounds()
    if transforms == "degrees":
        if not (-180 <= left and right <= 180 and -90 <= bottom and top <= 90):
            blank.error = "this map has no coordinate system"
            return blank
        to_map = from_map = None
        bounds = (left, bottom, right, top)
    else:
        to_map, from_map = transforms
        try:
            bounds = from_map.transform_bounds(left, bottom, right, top,
                                               densify_pts=21)
        except Exception as exc:
            blank.error = f"could not place this map on the earth ({exc})"
            return blank
    if not all(math.isfinite(v) for v in bounds):
        blank.error = "this map's coordinates are outside the basemap"
        return blank

    zoom = choose_zoom(bounds, width, max_tiles)
    draw = ImageDraw.Draw(image)
    water, minor, medium, major, places = [], [], [], [], []
    missing, last_error = 0, ""

    def to_pixels(path: np.ndarray, tx: int, ty: int) -> np.ndarray:
        lon, lat = tile_to_lonlat(tx + path[:, 0], ty + path[:, 1], zoom)
        if to_map is not None:
            lon, lat = to_map.transform(lon, lat)
        px, py = projection.to_pixel(lon, lat)
        return np.column_stack((px, py))

    for z, tx, ty in tiles_for(bounds, zoom):
        if cancelled is not None and cancelled():
            return None
        try:
            layers = store.features(z, tx, ty)
        except Exception as exc:          # offline, DNS, timeout, bad tile
            missing += 1
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        for feature in layers["water"]:
            if feature["type"] != 3:
                continue
            for ring in feature["paths"]:
                if len(ring) >= 4:
                    # Tile y runs down, so an exterior ring has positive area.
                    water.append((_ring_area(ring) > 0, to_pixels(ring, tx, ty)))
        for feature in layers["transportation"]:
            if feature["type"] != 2:
                continue
            props = feature["props"]
            kind = props.get("class")
            if props.get("brunnel") == "tunnel":
                continue
            bucket = (major if kind in _MAJOR else medium if kind in _MEDIUM
                      else minor if kind in _MINOR else None)
            if bucket is None:
                continue
            for line in feature["paths"]:
                if len(line) >= 2:
                    bucket.append(to_pixels(line, tx, ty))
        for feature in layers["place"]:
            props = feature["props"]
            name = props.get("name:latin") or props.get("name")
            kind = props.get("class")
            if feature["type"] != 1 or not name or kind not in _PLACE_RANK:
                continue
            for point in feature["paths"]:
                px, py = to_pixels(point[:1], tx, ty)[0]
                if 0 <= px < width and 0 <= py < height:
                    rank = props.get("rank")
                    places.append((_PLACE_RANK[kind],
                                   rank if isinstance(rank, int) else 99,
                                   str(name), float(px), float(py)))

    for exterior, ring in water:
        draw.polygon(ring.ravel().tolist(),
                     fill=tuple(colors["water" if exterior else "ground"]))
    for line in minor:
        draw.line(line.ravel().tolist(), fill=tuple(colors["road_minor"]), width=1)
    # Secondary and tertiary roads crowd a statewide view into a grey mesh, so
    # they wait for a closer one, as minor roads wait for the tiles to have them.
    for line in (medium if zoom >= 9 else ()):
        draw.line(line.ravel().tolist(), fill=tuple(colors["road_major"]), width=1)
    for line in major:
        draw.line(line.ravel().tolist(), fill=tuple(colors["road_major"]), width=2)

    total = len(tiles_for(bounds, zoom))
    raster = BasemapRaster(np.array(image, dtype=np.uint8), None, key, zoom=zoom,
                           missing=missing, drawn=missing < total)
    if missing and missing == total:
        raster.error = f"could not reach OpenFreeMap ({last_error})"
    elif missing:
        raster.error = f"{missing} of {total} basemap tiles could not be fetched"
    raster.labels, raster.places = _place_labels(places, width, height, colors)
    return raster


def _place_labels(places, width: int, height: int, colors: dict):
    """Place names on a transparent layer, biggest places first, no overlaps."""
    if not places:
        return None, []
    try:
        font = ImageFont.truetype(str(_FONT), 12)
    except OSError:
        font = ImageFont.load_default()
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    taken: list[tuple[float, float, float, float]] = []
    drawn = []
    limit = max(6, (width * height) // 30_000)
    for _kind, _rank, name, px, py in sorted(set(places)):
        if len(drawn) >= limit:
            break
        box = draw.textbbox((px, py), name, font=font, anchor="mm", stroke_width=2)
        box = (box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2)
        if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
            continue
        if any(box[0] < t[2] and t[0] < box[2] and box[1] < t[3] and t[1] < box[3]
               for t in taken):
            continue
        taken.append(box)
        draw.text((px, py), name, font=font, anchor="mm",
                  fill=tuple(colors["label"]) + (255,), stroke_width=2,
                  stroke_fill=tuple(colors["halo"]) + (255,))
        drawn.append((name, px, py))
    return (np.array(layer, dtype=np.uint8) if drawn else None), drawn


def stamp_attribution(rgba: np.ndarray, colors: dict, scale: float = 1.0) -> None:
    """Write the attribution line into the bottom-left corner, in place.

    Exports carry it in the image itself, since a saved PNG or PDF leaves the
    window whose status line shows it on screen.
    """
    height, width = rgba.shape[:2]
    try:
        font = ImageFont.truetype(str(_FONT), max(9, int(round(11 * scale))))
    except OSError:
        font = ImageFont.load_default()
    image = Image.fromarray(rgba, mode="RGBA")
    draw = ImageDraw.Draw(image)
    pad = max(4, int(round(6 * scale)))
    draw.text((pad, height - pad), ATTRIBUTION, font=font, anchor="ls",
              fill=tuple(colors["label"]) + (255,),
              stroke_width=max(1, int(round(2 * scale))),
              stroke_fill=tuple(colors["halo"]) + (255,))
    rgba[...] = np.array(image, dtype=np.uint8)
