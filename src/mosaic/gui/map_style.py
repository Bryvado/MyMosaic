"""How the map's lines draw: width and colour for each kind, set by the user.

MapView had one ``border_thickness`` multiplier and drew every border black.
This replaces it with three independently styled lines -- district borders,
place lines (counties, or cities, or whatever the place column holds) and
precinct lines -- each with a width from 0 to 8 pixels and a colour. A width
of 0 hides the line. Defaults are in docs/design/map.md.

No Dear PyGui here. The style is saved in ``.mosaic/map_style.json`` with the
other app settings, not in presets: it is how a map looks on one machine, not
part of a scoring recipe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

KINDS = ("district", "place", "precinct")
MAX_WIDTH = 8.0
DEFAULT_WIDTHS = {"district": 2.5, "place": 1.5, "precinct": 0.5}
# Palette token holding each line's default colour (docs/design/tokens.json).
COLOR_TOKENS = {"district": "map_district_line", "place": "map_place_line",
                "precinct": "map_precinct_line"}


@dataclass
class MapStyle:
    """Width per line kind, and a colour where the user has chosen one.

    A colour of None means "the theme's default for this line", so an unstyled
    line follows a light/dark switch and an export can use the light token on
    its white page. A chosen colour is used everywhere as chosen.
    """

    widths: dict = field(default_factory=lambda: dict(DEFAULT_WIDTHS))
    colors: dict = field(default_factory=lambda: {k: None for k in KINDS})

    def width(self, kind: str) -> float:
        return float(self.widths.get(kind, DEFAULT_WIDTHS[kind]))

    def color(self, kind: str, defaults: dict) -> tuple[int, int, int]:
        chosen = self.colors.get(kind)
        return tuple(chosen) if chosen is not None else tuple(defaults[kind])

    def to_json(self) -> dict:
        return {kind: {"width": self.width(kind),
                       "color": (list(self.colors[kind])
                                 if self.colors.get(kind) is not None else None)}
                for kind in KINDS}

    @classmethod
    def from_json(cls, data) -> "MapStyle":
        """Read a saved style, keeping the default for anything unusable."""
        style = cls()
        if not isinstance(data, dict):
            return style
        for kind in KINDS:
            entry = data.get(kind)
            if not isinstance(entry, dict):
                continue
            width = entry.get("width")
            if (isinstance(width, (int, float)) and not isinstance(width, bool)
                    and 0.0 <= width <= MAX_WIDTH):
                style.widths[kind] = float(width)
            color = entry.get("color")
            if (isinstance(color, (list, tuple)) and len(color) >= 3
                    and all(isinstance(c, (int, float)) and not isinstance(c, bool)
                            and 0 <= c <= 255 for c in color[:3])):
                style.colors[kind] = tuple(int(c) for c in color[:3])
        return style


def default_colors(palette) -> dict:
    """Each line's default colour from a theme palette."""
    return {kind: tuple(int(v) for v in getattr(palette, token)[:3])
            for kind, token in COLOR_TOKENS.items()}


def singular(unit: str) -> str:
    """"cities" -> "city", "counties" -> "county", "wards" -> "ward".

    For the "City lines" label, which names one of whatever the place column
    holds. Free text that is not a plural is returned as it is.
    """
    word = unit.strip()
    lower = word.lower()
    if lower.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if lower.endswith("s") and not lower.endswith("ss") and len(word) > 1:
        return word[:-1]
    return word


def line_label(unit: str) -> str:
    """The Map style label for place lines: "City lines", "County lines"."""
    return f"{singular(unit).capitalize()} lines"


def stroke(rgba: np.ndarray, mask: np.ndarray, width: float, rgb) -> None:
    """Draw a one-pixel-wide boolean line mask at ``width`` pixels, in place.

    Whole pixels are drawn solid by growing the mask; the fractional part is a
    one-pixel rim blended at that fraction, so 2.5 is two solid pixels and a
    half-strength third, and 0.5 is a single pixel at half strength. Width 0
    (or less) draws nothing. Only RGB is written; alpha is left alone.
    """
    width = min(float(width), 64.0)
    if width <= 0.0 or not mask.any():
        return
    whole = int(np.floor(width + 1e-9))
    part = width - whole
    color = np.asarray(rgb[:3], dtype=np.float32)
    core = _grow(mask, whole) if whole >= 1 else None
    if core is not None:
        rgba[core, :3] = color.astype(np.uint8)
    if part > 1e-6:
        rim = _grow(mask, whole + 1)
        if core is not None:
            rim &= ~core
        if rim.any():
            under = rgba[rim, :3].astype(np.float32)
            rgba[rim, :3] = np.rint(under + (color - under) * part).astype(np.uint8)


def _grow(mask: np.ndarray, pixels: int) -> np.ndarray:
    """A one-pixel line grown to ``pixels`` wide."""
    if pixels <= 1:
        return mask.copy()
    from scipy.ndimage import binary_dilation

    return binary_dilation(mask, structure=np.ones((pixels, pixels), dtype=bool))
