"""
Starting plan: load a precinct -> district assignment from a CSV and validate it
against the currently-loaded shapefile + run parameters.

Expected CSV format (matches save_assignments() output):
    <id_col>, district
    <id>,    1
    <id>,    2
    ...

district values are 1-indexed in the file, converted to 0-indexed internally.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

log = logging.getLogger("mosaic")


class HotStartError(ValueError):
    """Raised when the uploaded assignment fails validation."""


class DistrictCountMismatch(HotStartError):
    """A valid district count can replace the current run setting."""

    def __init__(self, message: str, district_count: int):
        super().__init__(message)
        self.district_count = district_count


def read_csv_columns(path: str | Path) -> list[str]:
    """Return the CSV's header row so the caller can offer a column picker."""
    path = Path(path)
    if not path.is_file():
        raise HotStartError(f"File not found: {path}")
    try:
        df = pd.read_csv(path, nrows=0)
    except Exception as e:
        raise HotStartError(f"Could not read CSV header: {e}") from e
    return list(df.columns)


def validate_district_locks(
    assignment: np.ndarray | None, locked_districts: tuple[int, ...],
    populations: np.ndarray, graph: nx.Graph, num_districts: int, tolerance: float,
    *, require_movable: bool = True,
) -> bool:
    """Validate a locked starting plan; return whether three-district moves fit."""
    if not locked_districts:
        return True
    if assignment is None:
        raise HotStartError("Load a starting plan before locking districts.")
    if assignment.shape != populations.shape:
        raise HotStartError("The starting plan does not match the loaded map or district count.")
    count = len(np.unique(assignment))
    if count != num_districts:
        raise DistrictCountMismatch(
            f"The starting plan has {count} districts; {num_districts} are selected.", count)
    if not np.array_equal(np.unique(assignment), np.arange(num_districts)):
        raise HotStartError("Starting-plan district numbers do not match the district count.")
    if any(d < 0 or d >= num_districts for d in locked_districts):
        raise HotStartError("A locked district is not present in the starting-plan assignment.")
    locked = set(locked_districts)
    if require_movable and num_districts - len(locked) < 2:
        raise HotStartError("Leave at least two adjacent districts unlocked to optimize.")
    ideal = float(populations.sum()) / num_districts
    if not np.isfinite(ideal) or ideal <= 0:
        raise HotStartError("Cannot lock districts without a positive total population.")
    totals = np.bincount(assignment, weights=populations, minlength=num_districts)
    deviations = np.abs(totals / ideal - 1)
    for d in range(num_districts):
        name = f"Locked district {d + 1}" if d in locked else f"District {d + 1}"
        if deviations[d] > tolerance + 1e-12:
            raise HotStartError(
                f"{name} deviates by {deviations[d] * 100:.2f}%; "
                f"the population tolerance is {tolerance * 100:.2f}%. "
                "Raise the tolerance or load another assignment.")
        if not nx.is_connected(graph.subgraph(np.flatnonzero(assignment == d))):
            raise HotStartError(f"{name} is not contiguous.")
    unlocked_graph = nx.Graph()
    unlocked_graph.add_nodes_from(set(range(num_districts)) - locked)
    for u, v in graph.edges:
        a, b = int(assignment[u]), int(assignment[v])
        if a != b and a not in locked and b not in locked:
            unlocked_graph.add_edge(a, b)
    if require_movable and not unlocked_graph.number_of_edges():
        raise HotStartError("No unlocked districts share a boundary; unlock adjacent districts.")
    return any(len(c) >= 3 for c in nx.connected_components(unlocked_graph))


def validate_start_assignment(
    assignment: np.ndarray, populations: np.ndarray, graph: nx.Graph,
    num_districts: int, tolerance: float,
) -> dict:
    """Validate an in-memory starting plan, using displayed numbers minus one."""
    if assignment.shape != populations.shape:
        raise HotStartError(
            "The starting plan does not match the loaded shapefile. "
            "Reload the shapefile and generate or load a matching plan.")
    labels = np.unique(assignment)
    if len(labels) != num_districts:
        raise DistrictCountMismatch(
            f"The starting plan has {len(labels)} districts; {num_districts} are selected. "
            "Change the Districts setting before using this plan.", len(labels))
    if not np.array_equal(labels, np.arange(num_districts)):
        raise HotStartError(
            f"The starting plan must use district numbers 1 through {num_districts}. "
            "Choose geographic numbering or None in Renumber Options first.")
    for d in range(num_districts):
        if not nx.is_connected(graph.subgraph(np.flatnonzero(assignment == d))):
            raise HotStartError(
                f"District {d + 1} is not contiguous. Generate or load a contiguous plan first.")
    ideal = float(populations.sum()) / num_districts
    if not np.isfinite(ideal) or ideal <= 0:
        raise HotStartError("Total population must be positive to use a starting plan.")
    totals = np.bincount(assignment, weights=populations, minlength=num_districts)
    deviations = np.abs(totals / ideal - 1)
    worst = int(np.argmax(deviations))
    if deviations[worst] > tolerance + 1e-12:
        raise HotStartError(
            f"District {worst + 1} deviates by {deviations[worst] * 100:.2f}%; "
            f"the population tolerance is {tolerance * 100:.2f}%. "
            "Raise the tolerance before using this plan.")
    return {"n_districts": num_districts, "max_dev_pct": float(deviations[worst] * 100),
            "max_dev_idx": worst}


def load_hot_start(
    path: str | Path,
    *,
    gdf: gpd.GeoDataFrame,
    gdf_id_col: str,
    csv_id_col: str,
    csv_district_col: str,
    populations: np.ndarray,
    graph: nx.Graph,
    num_districts: int,
    tolerance: float,
) -> tuple[np.ndarray, dict]:
    """Parse a starting-plan CSV and validate it against the loaded plan parameters.

    The CSV's id column is matched against gdf[gdf_id_col] by value (as strings),
    so the CSV header doesn't need to match the shapefile's column name.

    Returns (assignment, info) on success, where:
        assignment -- (n,) int32 zero-indexed district array aligned to gdf rows
        info       -- {
            "filename":     str,
            "n_districts":  int,
            "max_dev_pct":  float,   # max(|pop - ideal| / ideal) * 100
            "max_dev_idx":  int,     # district index (0-based) with largest dev
            "csv_id_col":      str,
            "csv_district_col": str,
        }

    Raises HotStartError with a user-facing message on any validation failure.
    """
    path = Path(path)
    if not path.is_file():
        raise HotStartError(f"File not found: {path}")

    # Read the ID column as string so leading zeros survive. Pandas otherwise
    # auto-infers int for all-digit columns (e.g. GEOID '08001001001' becomes
    # 8001001001), which silently breaks the match against the shapefile.
    try:
        df = pd.read_csv(path, dtype={csv_id_col: str})
    except Exception as e:
        raise HotStartError(f"Could not read CSV: {e}") from e

    if csv_id_col not in df.columns:
        raise HotStartError(
            f"CSV does not have the selected ID column '{csv_id_col}'. "
            f"Available: {list(df.columns)}"
        )
    if csv_district_col not in df.columns:
        raise HotStartError(
            f"CSV does not have the selected district column "
            f"'{csv_district_col}'. Available: {list(df.columns)}"
        )

    n = len(gdf)
    if len(df) != n:
        raise HotStartError(
            f"CSV has {len(df)} rows; shapefile has {n} precincts. "
            f"Row count must match exactly."
        )

    gdf_ids = gdf[gdf_id_col].astype(str).str.strip().values
    csv_ids = df[csv_id_col].astype(str).str.strip().values

    if set(gdf_ids) != set(csv_ids):
        missing = set(gdf_ids) - set(csv_ids)
        extra   = set(csv_ids) - set(gdf_ids)
        bits = []
        if missing:
            bits.append(
                f"shapefile has {len(missing)} IDs not in CSV "
                f"(e.g. {sorted(missing)[:3]})"
            )
        if extra:
            bits.append(
                f"CSV has {len(extra)} IDs not in shapefile "
                f"(e.g. {sorted(extra)[:3]})"
            )
        bits.append(
            f"sample shapefile IDs: {sorted(set(gdf_ids))[:3]}; "
            f"sample CSV IDs: {sorted(set(csv_ids))[:3]}"
        )
        raise HotStartError(
            "Precinct IDs in CSV do not match shapefile: " + "; ".join(bits)
        )

    # Reorder CSV rows to match gdf order so the assignment array is correctly aligned.
    # Match on the same stripped-string representation used for the set-equality check above.
    order_map = {pid: i for i, pid in enumerate(gdf_ids)}
    df = df.assign(
        _pos=df[csv_id_col].astype(str).str.strip().map(order_map)
    ).sort_values("_pos")
    raw_districts = df[csv_district_col].values

    if not np.issubdtype(raw_districts.dtype, np.number):
        try:
            raw_districts = raw_districts.astype(np.int64)
        except (ValueError, TypeError) as e:
            raise HotStartError(f"'district' column is not numeric: {e}") from e

    if np.isnan(raw_districts.astype(np.float64)).any():
        raise HotStartError("'district' column contains NaN / missing values")

    raw_districts = raw_districts.astype(np.int64)
    unique_d = np.unique(raw_districts)
    n_dist_csv = len(unique_d)

    if n_dist_csv != num_districts:
        raise DistrictCountMismatch(
            f"CSV defines {n_dist_csv} districts; current run is set to "
            f"{num_districts}. Adjust the Districts slider or fix the CSV.", n_dist_csv
        )

    # Accept either 1-indexed (export convention) or 0-indexed input.
    lo, hi = int(unique_d.min()), int(unique_d.max())
    if lo == 1 and hi == num_districts:
        assignment = (raw_districts - 1).astype(np.int32)
    elif lo == 0 and hi == num_districts - 1:
        assignment = raw_districts.astype(np.int32)
    else:
        raise HotStartError(
            f"District labels should be 1..{num_districts} (or 0..{num_districts-1}). "
            f"Got min={lo}, max={hi}."
        )

    # Contiguity: every district's induced subgraph must be connected
    nodes_arr = np.array(list(graph.nodes()))
    for d in range(num_districts):
        members = nodes_arr[assignment == d]
        if len(members) == 0:
            raise HotStartError(f"District {d + 1} is empty")
        sub = graph.subgraph(members)
        if not nx.is_connected(sub):
            n_components = nx.number_connected_components(sub)
            raise HotStartError(
                f"District {d + 1} is not contiguous "
                f"({n_components} disconnected components)"
            )

    # Population deviation
    pop_f = populations.astype(np.float64)
    ideal_pop = pop_f.sum() / num_districts
    if ideal_pop <= 0:
        raise HotStartError("Total population is zero; cannot compute deviation")

    dist_pop = np.bincount(assignment, weights=pop_f, minlength=num_districts)
    dev = (dist_pop - ideal_pop) / ideal_pop
    abs_dev = np.abs(dev)
    max_dev_idx = int(np.argmax(abs_dev))
    max_dev = float(abs_dev[max_dev_idx])

    if max_dev > tolerance:
        raise HotStartError(
            f"District {max_dev_idx + 1} population deviation is "
            f"{max_dev * 100:.2f}%, exceeding tolerance "
            f"{tolerance * 100:.2f}%. Tighten the CSV or relax tolerance."
        )

    log.info(
        f"Starting plan loaded: {n} precincts, {num_districts} districts, "
        f"max pop dev {max_dev * 100:.2f}% (district {max_dev_idx + 1})"
    )

    return assignment, {
        "filename":         path.name,
        "n_districts":      num_districts,
        "max_dev_pct":      max_dev * 100.0,
        "max_dev_idx":      max_dev_idx,
        "csv_id_col":       csv_id_col,
        "csv_district_col": csv_district_col,
    }
