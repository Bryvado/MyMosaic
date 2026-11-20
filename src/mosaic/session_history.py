"""Automatic configuration history.

Mosaic's settings are spread over a shapefile's column choices, twenty score
weights, the Goals that drive them, four settings popups, the annealing
schedule and the run parameters. A person who found a configuration that
worked and then changed it had no way back to it: presets cover the scores and
Open Recent covers the file, but nothing recorded the whole thing.

This module stores that whole thing, automatically, at the two moments it is
worth keeping: when a run starts, and when the app closes. Each snapshot is
one timestamped JSON file in a folder, with a small index beside it so the
history can be listed without reading them all.

Nothing here imports Dear PyGui and nothing here reads a widget. The interface
hands over a configuration dict and gets one back; what goes in it is the
interface's business, with two exceptions this module needs to understand to
describe a snapshot in a list: ``shapefile.path`` and the ``run`` block, and
the ``preset`` block, which is a ``mosaic.presets`` preset -- the same format
the preset files use, so a snapshot's scores can be saved out as one and
applied through the path a preset already takes. There is deliberately no
second format for any of it.

Layout of a configuration dict (the interface builds it; see
``gui/app/history_mixin.py``)::

    {
      "version":        1,
      "shapefile":      {"path": str, "config": {...ShapefileConfig fields}},
      "preset":         {...mosaic.presets sections...},
      "scores":         {...the few settings presets do not carry...},
      "run":            {...district count, tolerance, iterations, seed...},
      "annealing":      {...AnnealingConfig fields...},
      "reference_plan": {"path": str, "id_col": str, "district_col": str} | None,
      "starting_plan":  {"path": str, "id_col": str, "district_col": str} | None,
      "map_style":      {...},
      "place_unit":     str,
      "calibration":    {"source": str, "refs": {...}} | None,
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("mosaic")

# Bumped when the configuration layout changes in a way that cannot be read
# back. A snapshot from a newer version is listed but not restored.
SCHEMA_VERSION = 1

# Snapshots kept. Older unpinned ones are deleted as new ones arrive; a pinned
# snapshot is never deleted, however old.
DEFAULT_KEEP = 200

INDEX_NAME = "index.json"

# Why a snapshot was taken.
TRIGGERS = ("run", "exit")


@dataclass(frozen=True)
class Change:
    """One difference between two configurations."""

    label: str          # "Scores > holistic compactness > weight"
    before: str         # formatted for display, "" = absent
    after: str


@dataclass
class Snapshot:
    """One saved configuration, with what the list needs to show it."""

    id: str                     # the file stem, which is also the sort key
    saved_at: str               # ISO-8601 local time, seconds resolution
    trigger: str                # "run" | "exit"
    config: dict = field(default_factory=dict)
    pinned: bool = False
    name: str = ""              # the user's own name for it, "" = none
    assignment_csv: str = ""    # what the run this belongs to exported, if any

    # ── Storage ──────────────────────────────────────────────────────────────

    def to_json(self) -> dict:
        return {"id": self.id, "saved_at": self.saved_at,
                "trigger": self.trigger, "pinned": self.pinned,
                "name": self.name, "assignment_csv": self.assignment_csv,
                "config": self.config}

    @classmethod
    def from_json(cls, data: dict) -> "Snapshot":
        if not isinstance(data, dict):
            raise ValueError("a snapshot must be a JSON object")
        config = data.get("config")
        return cls(
            id=str(data.get("id", "")),
            saved_at=str(data.get("saved_at", "")),
            trigger=str(data.get("trigger", "")),
            config=config if isinstance(config, dict) else {},
            pinned=bool(data.get("pinned", False)),
            name=str(data.get("name", "")),
            assignment_csv=str(data.get("assignment_csv", "")),
        )

    # ── Display ──────────────────────────────────────────────────────────────

    @property
    def version(self) -> int:
        try:
            return int(self.config.get("version", 0))
        except (TypeError, ValueError):
            return 0

    @property
    def readable(self) -> bool:
        """Whether this snapshot's layout is one this version can restore."""
        return 0 < self.version <= SCHEMA_VERSION

    def index_entry(self) -> dict:
        """The row the index keeps for this snapshot: enough to list it."""
        entry = {"id": self.id, "saved_at": self.saved_at,
                 "trigger": self.trigger, "pinned": self.pinned,
                 "name": self.name, "assignment_csv": self.assignment_csv,
                 "digest": config_digest(self.config)}
        entry.update(describe(self.config))
        return entry


# ── Describing a configuration ───────────────────────────────────────────────

def shapefile_name(config: dict) -> str:
    path = ((config.get("shapefile") or {}).get("path") or "")
    return Path(path).name if path else ""


def enabled_goals(config: dict) -> list[str]:
    """The goals a configuration has switched on, by their panel names.

    Read out of the preset's weights rather than stored: a goal's strength is
    derived from the weights (see ``mosaic.goals``), so storing it as well
    would be a second source of truth that could disagree with the first.
    """
    preset = config.get("preset")
    if not isinstance(preset, dict):
        return []
    from mosaic import goals as G
    unit = str(config.get("place_unit") or "")
    try:
        states = G.goal_states(preset)
    except Exception:                       # a preset this version cannot read
        return []
    return [G.GOALS_BY_ID[gid].title(unit)
            for gid, state in states.items() if state.on]


def describe(config: dict) -> dict:
    """The summary fields the history list shows for a configuration."""
    run = config.get("run") if isinstance(config.get("run"), dict) else {}
    def _int(key: str) -> int:
        try:
            return int(run.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0
    return {"shapefile": shapefile_name(config),
            "districts": _int("num_districts"),
            "iterations": _int("max_iterations"),
            "goals": enabled_goals(config)}


def config_digest(config: dict) -> str:
    """A stable digest of a configuration, for "is this the same as the last
    one?". Keys are sorted, so a dict that only differs in order matches."""
    text = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ── Comparing two configurations ─────────────────────────────────────────────

# Section keys to the words the interface uses for them.
_SECTION_LABELS = {
    "shapefile": "Map", "preset": "Scores", "scores": "Scores",
    "run": "Run", "annealing": "Annealing", "map_style": "Map style",
    "reference_plan": "Reference plan", "starting_plan": "Starting plan",
    "calibration": "Goal calibration", "place_unit": "Place unit",
    "version": "Format version",
}


def _flatten(value: Any, prefix: tuple[str, ...] = ()) -> dict[tuple, Any]:
    """A nested configuration as {path tuple: leaf}. Lists are leaves: an
    election pair or a colour reads as one thing, not as three numbers."""
    if isinstance(value, dict):
        out: dict[tuple, Any] = {}
        for key, inner in value.items():
            out.update(_flatten(inner, prefix + (str(key),)))
        return out
    return {prefix: value}


def _format(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    if isinstance(value, (list, tuple)):
        return ", ".join(_format(v) for v in value) or "none"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_format(v)}" for k, v in sorted(value.items()))
    return str(value)


def _label(path: tuple[str, ...]) -> str:
    head = _SECTION_LABELS.get(path[0], path[0].replace("_", " "))
    rest = [part.replace("_", " ") for part in path[1:]]
    return " > ".join([head, *rest])


def diff(before: dict, after: dict) -> list[Change]:
    """What differs between two configurations, in a readable order.

    Both directions are reported: a setting present in one and absent from the
    other is a difference too, which is how a snapshot from a map with no
    election columns reads against one that has them.
    """
    flat_before = _flatten(before or {})
    flat_after = _flatten(after or {})
    changes: list[Change] = []
    for path in sorted(set(flat_before) | set(flat_after)):
        old = flat_before.get(path, None)
        new = flat_after.get(path, None)
        if path in flat_before and path in flat_after and old == new:
            continue
        if path not in flat_before and new is None:
            continue
        if path not in flat_after and old is None:
            continue
        changes.append(Change(_label(path),
                              _format(old) if path in flat_before else "",
                              _format(new) if path in flat_after else ""))
    return changes


def _seq(row: dict) -> int:
    """A row's sequence number: which of two snapshots was saved later."""
    try:
        return int(row.get("seq", 0) or 0)
    except (TypeError, ValueError):
        return 0


# ── The store ────────────────────────────────────────────────────────────────

class HistoryStore:
    """Snapshots in one folder, newest first, with an index.

    Every method is forgiving: a history that cannot be read or written is not
    worth an error to someone who was trying to start a run. Failures are
    logged and reported through the return value.
    """

    def __init__(self, folder: Path | str, keep: int = DEFAULT_KEEP):
        self.folder = Path(folder)
        self.keep = max(1, int(keep))

    # ── Index ────────────────────────────────────────────────────────────────

    @property
    def index_path(self) -> Path:
        return self.folder / INDEX_NAME

    def entries(self) -> list[dict]:
        """Index rows, newest first. Empty when there is no history yet."""
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except Exception:
            log.debug("History: unreadable index", exc_info=True)
            return []
        rows = data.get("snapshots") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        clean = [r for r in rows if isinstance(r, dict) and r.get("id")]
        # Newest first. The sequence number decides, not the id: ids carry a
        # timestamp to the second and the trigger, so two snapshots taken in
        # the same second would otherwise sort by the words "exit" and "run".
        # A hand-edited row with no sequence falls back to its id.
        clean.sort(key=lambda r: (_seq(r), str(r.get("id", ""))), reverse=True)
        return clean

    def _write_index(self, rows: list[dict]) -> bool:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.index_path.write_text(
                json.dumps({"version": SCHEMA_VERSION, "snapshots": rows},
                           indent=1),
                encoding="utf-8")
            return True
        except Exception:
            log.debug("History: could not write the index", exc_info=True)
            return False

    def latest(self) -> dict | None:
        rows = self.entries()
        return rows[0] if rows else None

    def entry(self, snapshot_id: str) -> dict | None:
        return next((r for r in self.entries() if r.get("id") == snapshot_id),
                    None)

    # ── Saving ───────────────────────────────────────────────────────────────

    def _new_id(self, trigger: str, when: datetime) -> str:
        stem = f"{when.strftime('%Y%m%d-%H%M%S')}-{trigger}"
        taken = {r.get("id") for r in self.entries()}
        if stem not in taken and not (self.folder / f"{stem}.json").exists():
            return stem
        for n in range(2, 100):             # same second, same trigger
            candidate = f"{stem}-{n}"
            if candidate not in taken \
                    and not (self.folder / f"{candidate}.json").exists():
                return candidate
        return f"{stem}-{len(taken) + 1}"

    def save(self, config: dict, *, trigger: str = "run",
             assignment_csv: str = "", when: datetime | None = None
             ) -> Snapshot | None:
        """Store one configuration. None when nothing was written.

        A configuration identical to the newest snapshot is not stored again:
        closing the app right after starting a run would otherwise leave two
        rows saying the same thing, and the history is meant to be a list of
        the configurations that were used, not of the times they were saved.
        """
        if trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger: {trigger!r}")
        if not isinstance(config, dict) or not config:
            return None
        rows = self.entries()
        if rows and rows[0].get("digest") == config_digest(config):
            log.debug("History: identical to the newest snapshot, not saved")
            return None
        when = when or datetime.now()
        snapshot = Snapshot(
            id=self._new_id(trigger, when),
            saved_at=when.isoformat(timespec="seconds"),
            trigger=trigger, config=config, assignment_csv=assignment_csv)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / f"{snapshot.id}.json").write_text(
                json.dumps(snapshot.to_json(), indent=1), encoding="utf-8")
        except Exception:
            log.debug("History: could not write a snapshot", exc_info=True)
            return None
        entry = snapshot.index_entry()
        entry["seq"] = max((_seq(r) for r in rows), default=0) + 1
        rows.insert(0, entry)
        rows = self._prune(rows)
        if not self._write_index(rows):
            return None
        return snapshot

    def _prune(self, rows: list[dict]) -> list[dict]:
        """Drop the oldest unpinned snapshots beyond `keep`.

        Pinned snapshots are kept whatever their age, and they do not push
        others out: the newest `keep` unpinned ones are kept as well, so
        pinning something old never costs recent history.
        """
        kept: list[dict] = []
        unpinned = 0
        for row in rows:                    # newest first
            if row.get("pinned"):
                kept.append(row)
                continue
            if unpinned < self.keep:
                kept.append(row)
                unpinned += 1
                continue
            self._delete_file(str(row.get("id", "")))
        return kept

    def _delete_file(self, snapshot_id: str) -> None:
        if not snapshot_id:
            return
        try:
            (self.folder / f"{snapshot_id}.json").unlink(missing_ok=True)
        except Exception:
            log.debug("History: could not delete %s", snapshot_id,
                      exc_info=True)

    # ── Reading ──────────────────────────────────────────────────────────────

    def load(self, snapshot_id: str) -> Snapshot | None:
        try:
            data = json.loads(
                (self.folder / f"{snapshot_id}.json").read_text(
                    encoding="utf-8"))
            snapshot = Snapshot.from_json(data)
        except Exception:
            log.debug("History: could not read %s", snapshot_id, exc_info=True)
            return None
        if not snapshot.id:
            snapshot.id = snapshot_id
        # The index owns pinning and naming, so a hand-edited file cannot
        # disagree with the list the user is looking at.
        row = self.entry(snapshot_id)
        if row is not None:
            snapshot.pinned = bool(row.get("pinned", snapshot.pinned))
            snapshot.name = str(row.get("name", snapshot.name))
            snapshot.assignment_csv = str(
                row.get("assignment_csv", snapshot.assignment_csv))
        return snapshot

    # ── Editing a row ────────────────────────────────────────────────────────

    def _update_row(self, snapshot_id: str, **fields) -> bool:
        rows = self.entries()
        for row in rows:
            if row.get("id") == snapshot_id:
                row.update(fields)
                return self._write_index(rows)
        return False

    def pin(self, snapshot_id: str, pinned: bool = True) -> bool:
        """Pin or unpin. Unpinning may make it eligible for pruning."""
        if not self._update_row(snapshot_id, pinned=bool(pinned)):
            return False
        if not pinned:
            rows = self._prune(self.entries())
            self._write_index(rows)
        return True

    def attach_assignment(self, snapshot_id: str, path: str) -> bool:
        """Note the assignment CSV a run exported against its snapshot.

        The snapshot is taken when the run starts, so what it produced is only
        known later; the index carries it, which is also where the list reads
        it from.
        """
        return self._update_row(snapshot_id, assignment_csv=str(path))

    def rename(self, snapshot_id: str, name: str) -> bool:
        return self._update_row(snapshot_id, name=str(name).strip()[:80])

    def delete(self, snapshot_id: str) -> bool:
        rows = [r for r in self.entries() if r.get("id") != snapshot_id]
        self._delete_file(snapshot_id)
        return self._write_index(rows)
