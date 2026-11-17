"""A tiny Mapbox Vector Tile writer, so basemap tests need no network.

The inverse of mosaic.basemap.decode_tile for the handful of things the
basemap reads: named layers of point, line and polygon features with string
properties.
"""

from __future__ import annotations


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _field(number: int, payload: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def _zigzag(value: int) -> int:
    return (value << 1) ^ (value >> 63)


def _commands(kind: int, paths, extent: int) -> bytes:
    out, x, y = [], 0, 0
    for path in paths:
        pts = [(int(round(px * extent)), int(round(py * extent))) for px, py in path]
        if kind == 3 and pts[0] == pts[-1]:
            pts = pts[:-1]
        out += [1 << 3 | 1, _zigzag(pts[0][0] - x), _zigzag(pts[0][1] - y)]
        x, y = pts[0]
        if len(pts) > 1:
            out.append((len(pts) - 1) << 3 | 2)
            for px, py in pts[1:]:
                out += [_zigzag(px - x), _zigzag(py - y)]
                x, y = px, py
        if kind == 3:
            out.append(1 << 3 | 7)
    return b"".join(_varint(v) for v in out)


def encode_tile(layers: dict, extent: int = 4096) -> bytes:
    """``{layer: [(kind, props, paths)]}`` -> tile bytes.

    ``kind`` is 1 point, 2 line, 3 polygon; ``paths`` are lists of (x, y) in
    0-1 across the tile, y down, as decode_tile returns them.
    """
    tile = b""
    for name, features in layers.items():
        keys: list[str] = []
        values: list[str] = []
        body = _field(1, name.encode())
        for kind, props, paths in features:
            tags = []
            for key, value in props.items():
                if key not in keys:
                    keys.append(key)
                if value not in values:
                    values.append(value)
                tags += [keys.index(key), values.index(value)]
            feature = (_field(2, b"".join(_varint(t) for t in tags))
                       + _varint(3 << 3) + _varint(kind)
                       + _field(4, _commands(kind, paths, extent)))
            body += _field(2, feature)
        for key in keys:
            body += _field(3, key.encode())
        for value in values:
            body += _field(4, _field(1, str(value).encode()))
        body += _varint(5 << 3) + _varint(extent) + _varint(15 << 3) + _varint(2)
        tile += _field(3, body)
    return tile
