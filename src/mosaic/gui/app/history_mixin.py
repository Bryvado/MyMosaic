"""Automatic configuration history: snapshots, the History window, restore.

``mosaic.session_history`` stores whole configurations; this is the half that
knows the widgets. It gathers one configuration when a run starts and when the
app closes, lists the saved ones, shows what a saved one would change, and
puts one back.

Two rules shape all of it:

* **One format.** The scores are stored as a ``mosaic.presets`` preset -- the
  same dict the preset files hold and ``_apply_preset`` consumes -- and the
  map as the ``{path, config}`` entry Open Recent already keeps. Only the
  settings neither of those carries (the run parameters, the annealing
  schedule, Hinge, the manual opportunity targets, the two plan files, the map
  style, the place unit, the goal calibration) are written out here, and each
  of those is read and written through one table so the two directions cannot
  drift apart.
* **One path in.** Restoring sets widgets and calls the same callbacks a
  person's click would, then lets ``_apply_preset`` and the loaders report
  what they could not do. Nothing reaches into shared state behind the
  interface's back, so a restore cannot leave the app in a state a person
  could not have reached.
"""
from __future__ import annotations

from pathlib import Path

from mosaic import goals as G
from mosaic import session_history as SH

from ._common import _SETTINGS_DIR, AlgorithmStatus, dpg, log

_HISTORY_DIR = _SETTINGS_DIR / "history"

_TAG = "popup_history"
_OFFER_TAG = "popup_history_offer"
_RENAME_TAG = "popup_history_rename"

# Rows drawn in the list, and changes listed in the diff, at most.
_MAX_ROWS = 200
_MAX_CHANGES = 60

# ── The settings presets do not carry ────────────────────────────────────────
# (key in the snapshot, widget attribute). Gathering reads the widget;
# restoring writes it. One table for both directions.

_RUN_FIELDS = (
    ("num_districts", "_num_districts"),
    ("max_iterations", "_iterations"),
    ("tolerance_pct", "_tolerance"),
    ("tolerance_ratchet_mode", "_tolerance_ratchet_mode"),
    ("seed", "_seed"),
    ("fast_trees", "_fast_trees"),
    ("n3_pct", "_n3_pct"),
    ("flip_enabled", "_flip_enabled"),
    ("flip_midpoint", "_flip_midpoint"),
    ("map_render_interval", "_map_interval"),
)

_ANNEALING_FIELDS = (
    ("enabled", "_ann_enabled"),
    ("initial_temp_factor", "_temp_factor"),
    ("cooling_mode", "_cool_mode"),
    ("guide_fraction", "_guide_frac"),
    ("target_temp", "_target_temp"),
    ("cooling_rate", "_cooling_rate"),
    ("launch_watch", "_launch_watch_enabled"),
    ("launch_watch_iter", "_launch_watch_iter"),
)

# Hinge is deliberately outside presets (applying a preset switches it off),
# so the history carries it.
_HINGE_FIELDS = (
    ("enabled", "_hinge_enabled"),
    ("weight", "_w_hinge"),
    ("threshold", "_hinge_threshold"),
    ("dem", "_hinge_dem_chk"),
    ("rep", "_hinge_rep_chk"),
)


class HistoryMixin:
    """Configuration snapshots, the History window, and restoring one."""

    # ── The store ─────────────────────────────────────────────────────────────

    @property
    def history(self) -> SH.HistoryStore:
        """The snapshot store, made on first use.

        Assignable, so a test can point the history somewhere temporary rather
        than at the user's own.
        """
        store = getattr(self, "_history", None)
        if store is None:
            store = SH.HistoryStore(_HISTORY_DIR)
            self._history = store
        return store

    @history.setter
    def history(self, store: SH.HistoryStore) -> None:
        self._history = store

    # ── Gathering ─────────────────────────────────────────────────────────────

    def _snapshot_config(self) -> dict:
        """Everything that makes up the current configuration.

        Read off the widgets, like every other reader in the app, so what is
        stored is what is on screen -- including edits that have not been used
        for a run yet.
        """
        shapefile = None
        path = self.state.shapefile_path
        if path and self._loaded_config is not None:
            shapefile = {"path": path,
                         "config": self._shapefile_config_json(
                             self._loaded_config)}
        calibration = None
        if getattr(self, "_calib_ref", None) is not None:
            calibration = {"source": G.reference_source(),
                           "refs": G.active_reference()}
        return {
            "version": SH.SCHEMA_VERSION,
            "shapefile": shapefile,
            "preset": self._preset_from_widgets(),
            "scores": {
                "hinge": self._gather_fields(_HINGE_FIELDS),
                "opportunity_targets": {
                    group: self._opportunity_target_value(group)
                    for group in ("black", "latino", "asian")},
            },
            "run": self._gather_fields(_RUN_FIELDS),
            "annealing": self._gather_fields(_ANNEALING_FIELDS),
            "reference_plan": dict(self._alignment_source)
                              if getattr(self, "_alignment_source", None) else None,
            "starting_plan": dict(self._hot_start_source)
                             if getattr(self, "_hot_start_source", None) else None,
            "map_style": self._map_style_payload(),
            "place_unit": self._place_unit,
            "calibration": calibration,
        }

    def _gather_fields(self, fields) -> dict:
        return {key: dpg.get_value(getattr(self, attr)) for key, attr in fields}

    def _apply_fields(self, fields, values) -> None:
        """Write one block back onto its widgets, skipping what is absent.

        A snapshot written by an older version may not have every key; the
        widget keeps whatever it already holds rather than being zeroed.
        """
        if not isinstance(values, dict):
            return
        for key, attr in fields:
            if key not in values:
                continue
            item = getattr(self, attr, None)
            if item and dpg.does_item_exist(item):
                self._preset_set(item, values[key])

    # ── Saving ────────────────────────────────────────────────────────────────

    def _save_history_snapshot(self, trigger: str) -> "SH.Snapshot | None":
        """Store the configuration now. Never raises: a history that cannot be
        written must not stop a run from starting or the app from closing."""
        try:
            config = self._snapshot_config()
        except Exception:
            log.debug("History: could not read the configuration", exc_info=True)
            return None
        try:
            snapshot = self.history.save(config, trigger=trigger)
        except Exception:
            log.debug("History: could not save a snapshot", exc_info=True)
            return None
        if snapshot is not None:
            log.info("History: saved %s (%s)", snapshot.id, trigger)
            if trigger == "run":
                self._run_snapshot_id = snapshot.id
            self._history_sig = None
        return snapshot

    def _record_assignment_csv(self, path) -> None:
        """Note the file a run exported against that run's own snapshot."""
        snapshot_id = getattr(self, "_run_snapshot_id", "")
        if not snapshot_id:
            return
        try:
            self.history.attach_assignment(snapshot_id, str(path))
        except Exception:
            log.debug("History: could not record an export", exc_info=True)
            return
        self._history_sig = None

    # ── Restoring ─────────────────────────────────────────────────────────────

    def _restore_blocked(self) -> str:
        """Why a restore cannot run now, or "" when it can.

        Restoring reloads the map and rewrites every scoring control; a run or
        a batch is using both.
        """
        if self._ensemble_active:
            return "Finish or stop the Batch Runs before restoring."
        if self.state.status in (AlgorithmStatus.RUNNING, AlgorithmStatus.PAUSED,
                                 AlgorithmStatus.PARTITIONING):
            return "Stop the run before restoring a configuration."
        if self._session_action_pending() or self._session_workers_busy():
            return "Wait for the current work to finish, then try again."
        return ""

    def _restore_snapshot(self, snapshot_id: str) -> None:
        """Put one saved configuration back.

        The map comes first and on its own schedule: loading a shapefile is a
        background job, so when one is needed the rest of the configuration is
        held until it finishes (see _finish_pending_restore). Everything else
        is applied here and now.
        """
        if self._queue_session_action(self._restore_snapshot, snapshot_id):
            return
        blocked = self._restore_blocked()
        if blocked:
            self._history_note(blocked)
            return
        snapshot = self.history.load(snapshot_id)
        if snapshot is None:
            self._history_note("That snapshot could not be read.")
            return
        if not snapshot.readable:
            self._history_note(
                "That snapshot was written by a newer version of Mosaic.")
            return

        config = snapshot.config
        wanted = (config.get("shapefile") or {})
        wanted_path = str(wanted.get("path") or "")
        notes: list[str] = []
        if wanted_path and not Path(wanted_path).exists():
            notes.append(f"map not found at {wanted_path}; kept the open one")
            wanted_path = ""
        if wanted_path and wanted_path != self.state.shapefile_path:
            # The same path Open Recent takes, so the column dialog is skipped
            # and the unsaved-plan guard still asks.
            self._pending_restore = (wanted_path, config)
            self._history_note(f"Opening {Path(wanted_path).name}...")
            self._request_open_shapefile(
                wanted_path, self._shapefile_config_from_json(
                    wanted.get("config") or {}))
            return
        self._finish_restore(config, notes)

    def _drop_stale_restore(self, path: str) -> None:
        """Forget a held restore when a different map is being opened.

        A configuration waiting for its own map must not be applied to
        another one, however that open was started -- the File menu, Open
        Recent, or a second restore.
        """
        pending = getattr(self, "_pending_restore", None)
        if pending is not None and pending[0] != path:
            log.debug("History: dropping a restore held for %s", pending[0])
            self._pending_restore = None

    def _finish_pending_restore(self) -> None:
        """Apply a held restore once its map has finished loading."""
        pending = getattr(self, "_pending_restore", None)
        if pending is None:
            return
        path, config = pending
        if self.state.shapefile_path != path:
            return                      # a different file won the race
        self._pending_restore = None
        self._finish_restore(config, [])

    def _finish_restore(self, config: dict, notes: list[str]) -> None:
        """Apply everything but the map, then report what did not take."""
        notes = list(notes) + self._apply_snapshot_config(config)
        self._history_sig = None
        if notes:
            message = "Restored, except: " + "; ".join(notes)
        else:
            message = "Configuration restored."
        # The window's diff described the settings as they were a moment ago.
        if dpg.does_item_exist(_TAG) and dpg.is_item_shown(_TAG):
            self._show_history_detail(self._history_selected)
        self._history_note(message)
        self.state.update(status_message=message)
        log.info("History: %s", message)

    def _apply_snapshot_config(self, config: dict) -> list[str]:
        """Write one configuration onto the controls. Returns what did not take.

        Every step is the one the interface itself uses, so a half-applied
        configuration is not reachable: either a step applies, or it reports.
        """
        notes: list[str] = []

        # Columns the map no longer has. The loader simply skips a missing
        # column, so this is said out loud rather than left to be noticed.
        wanted_cols = (config.get("shapefile") or {}).get("config") or {}
        if self.runner is not None and self.runner.gdf is not None:
            have = set(self.runner.gdf.columns)
            missing = sorted({
                str(col) for col in self._columns_named(wanted_cols)
                if col and col not in have})
            if missing:
                notes.append("columns gone from the map: " + ", ".join(missing))

        # The place unit first: the goal's name, the score phrases, the
        # overlay checkbox and the map-style line names all follow it, and its
        # own callback is what moves them.
        unit = config.get("place_unit")
        if isinstance(unit, str) and unit.strip() and dpg.does_item_exist(
                self._place_unit_input):
            dpg.set_value(self._place_unit_input, unit.strip()[:24])
            self._on_place_unit_change()

        # The scores, through the preset path, which reports what this map
        # cannot support.
        preset = config.get("preset")
        if isinstance(preset, dict) and preset:
            try:
                missing, other = self._apply_preset(preset)
            except Exception as exc:
                log.exception("History: could not apply the stored scores")
                notes.append(f"scores not applied ({exc})")
            else:
                self._set_preset_info(missing, other)
                if missing:
                    notes.append("scores needing " + ", ".join(missing))

        # Hinge and the manual opportunity targets sit outside presets, and
        # _apply_preset switches Hinge off, so they go on after it.
        scores = config.get("scores") if isinstance(config.get("scores"), dict) else {}
        self._apply_fields(_HINGE_FIELDS, scores.get("hinge"))
        self._on_hinge_toggle()
        targets = scores.get("opportunity_targets")
        if isinstance(targets, dict):
            for group in ("black", "latino", "asian"):
                value = targets.get(group, -1)
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
                dpg.set_value(self._opportunity_target_auto[group], value < 0)
                if value >= 0:
                    dpg.set_value(self._opportunity_target_count[group], value)
            self._on_opportunity_target_change()

        # Run and annealing settings, then the callbacks that reveal or hide
        # the controls each one owns.
        self._apply_fields(_RUN_FIELDS, config.get("run"))
        self._sync_tolerance_people()
        self._apply_fields(_ANNEALING_FIELDS, config.get("annealing"))
        self._on_ann_toggle()
        self._on_cool_mode()
        self._on_launch_watch_toggle()

        # The two plan files, each through its own loader.
        notes += self._restore_plan_file(
            config.get("reference_plan"), "reference plan",
            self._apply_alignment, self._on_clear_alignment)
        notes += self._restore_plan_file(
            config.get("starting_plan"), "starting plan",
            self._apply_hot_start, self._on_clear_hot_start)

        # Map style, which the map draws from directly.
        style = config.get("map_style")
        if isinstance(style, dict) and style:
            self._apply_map_style_payload(style)
            self._sync_map_style_widgets()
            self._apply_fill_opacity()
            self._redraw_map_now()

        # The goal calibration belongs to one map at one district count, so it
        # only comes back when that map is the one open.
        notes += self._restore_calibration(config)
        return notes

    @staticmethod
    def _columns_named(config_json: dict) -> list[str]:
        """Every column a stored ShapefileConfig names."""
        out = [config_json.get("pop_col"), config_json.get("id_col"),
               config_json.get("county_col")]
        for pair in config_json.get("elections") or ():
            out.extend(list(pair)[:2])
        for col in (config_json.get("demographics") or {}).values():
            out.append(col)
        return [str(c) for c in out if c]

    def _restore_plan_file(self, stored, what: str, apply, clear) -> list[str]:
        """Reload one CSV-backed plan, or say why it could not be."""
        if not isinstance(stored, dict) or not stored.get("path"):
            if clear is not None:
                clear()
            return []
        path = str(stored["path"])
        if not Path(path).exists():
            return [f"{what} not found at {path}"]
        if self.runner is None or self.runner.gdf is None:
            return [f"{what} needs a map"]
        try:
            apply(path, str(stored.get("id_col", "")),
                  str(stored.get("district_col", "")))
        except Exception as exc:          # the loaders report their own errors
            log.exception("History: could not reload the %s", what)
            return [f"{what} not loaded ({exc})"]
        return []

    def _restore_calibration(self, config: dict) -> list[str]:
        calibration = config.get("calibration")
        if not isinstance(calibration, dict) or not calibration.get("refs"):
            return []
        stored_path = str((config.get("shapefile") or {}).get("path") or "")
        if not stored_path or stored_path != self.state.shapefile_path:
            return ["goal calibration (it belongs to the saved map)"]
        try:
            G.set_reference(calibration["refs"],
                            str(calibration.get("source", "")))
        except Exception:
            log.debug("History: unusable stored calibration", exc_info=True)
            return ["goal calibration (unreadable)"]
        self._calib_sig = None
        self._goals_sig = None
        return []

    # ── The window ────────────────────────────────────────────────────────────

    def _on_open_history(self) -> None:
        if self._queue_session_action(self._on_open_history):
            return
        if dpg.does_item_exist(_TAG):
            dpg.configure_item(_TAG, show=True)
            self._history_sig = None
            self._refresh_history_window()
            return
        self._history_selected = ""
        self._history_sig = None
        # Not modal: the list is something to read beside the settings it
        # would change, and Restore is gated on its own.
        with self._dialog("History", _TAG, (620, 520), modal=False,
                          autosize=False,
                          buttons=[("Restore", self._on_history_restore, "primary"),
                                   ("Pin", self._on_history_pin),
                                   ("Rename…", self._on_history_rename),
                                   ("Save as preset…", self._on_history_preset),
                                   ("Close", lambda: dpg.configure_item(
                                       _TAG, show=False))]):
            self.theme.text(
                "Every run and every close saves the whole configuration: the "
                "map and its columns, the scores and Goals, the run and "
                "annealing settings, both plan files and the map style.",
                "muted", wrap=580)
            dpg.add_spacer(height=4)
            dpg.add_separator()
            with dpg.child_window(width=-1, height=210, border=False,
                                  tag="history_list"):
                self.theme.text("No history yet.", "muted",
                                tag="history_empty")
            dpg.add_separator()
            self._history_detail = self.theme.text("", "body", wrap=580)
            self.theme.text("Changes this would make", "subheading")
            with dpg.child_window(width=-1, height=150, border=False,
                                  tag="history_diff"):
                pass
            self._history_msg = self.theme.text("", "warning", wrap=580)
        self._build_history_rename_popup()
        self._refresh_history_window()

    def _build_history_rename_popup(self) -> None:
        if dpg.does_item_exist(_RENAME_TAG):
            return
        with self._dialog("Name this snapshot", _RENAME_TAG, (380, 150),
                          show=False,
                          primary=("Save", self._on_history_rename_save),
                          secondary=("Cancel", lambda: dpg.configure_item(
                              _RENAME_TAG, show=False))):
            self.theme.text("A named snapshot is kept until you delete it.",
                            "muted", wrap=350)
            self._history_name_input = dpg.add_input_text(width=-1)

    def _history_note(self, message: str) -> None:
        """Say something in the History window if it is open, and in the status
        line either way."""
        message_item = getattr(self, "_history_msg", 0)
        if message_item and dpg.does_item_exist(message_item):
            dpg.set_value(message_item, message)
        self.state.update(status_message=message)

    def _refresh_history_window(self) -> None:
        """Redraw the list when it has changed. Called on open and after every
        edit; the window is not refreshed per frame."""
        if not dpg.does_item_exist(_TAG) or not dpg.is_item_shown(_TAG):
            return
        rows = self.history.entries()[:_MAX_ROWS]
        sig = tuple(
            (r.get("id"), r.get("pinned"), r.get("name"), r.get("assignment_csv"))
            for r in rows)
        if sig == getattr(self, "_history_sig", None):
            return
        self._history_sig = sig
        dpg.delete_item("history_list", children_only=True)
        if not rows:
            self.theme.text("No history yet.", "muted", parent="history_list")
            self._history_selected = ""
            self._show_history_detail(None)
            return
        if self._history_selected not in {r.get("id") for r in rows}:
            self._history_selected = str(rows[0].get("id", ""))
        for row in rows:
            snapshot_id = str(row.get("id", ""))
            dpg.add_selectable(
                label=self._history_row_label(row),
                default_value=snapshot_id == self._history_selected,
                parent="history_list", user_data=snapshot_id,
                tag=f"history_row_{snapshot_id}",
                callback=lambda s, a, u: self._on_history_select(u))
        self._show_history_detail(self._history_selected)

    @staticmethod
    def _history_row_label(row: dict) -> str:
        """One line per snapshot: when, why, which map, and the headline
        settings. Dear PyGui has no multi-line selectable and a table cannot
        be clicked as a row, so the row is one string."""
        when = str(row.get("saved_at", ""))[:16].replace("T", " ")
        parts = [when, str(row.get("trigger", ""))]
        name = str(row.get("name", ""))
        if name:
            parts.append(f'"{name}"')
        shapefile = str(row.get("shapefile", ""))
        if shapefile:
            parts.append(shapefile)
        districts = row.get("districts") or 0
        if districts:
            parts.append(f"{districts} districts")
        iterations = row.get("iterations") or 0
        if iterations:
            parts.append(f"{iterations:,} iters")
        goals = row.get("goals") or []
        if goals:
            parts.append(", ".join(goals))
        line = "  ·  ".join(parts)
        return ("* " if row.get("pinned") else "") + line

    def _on_history_select(self, snapshot_id: str) -> None:
        self._history_selected = str(snapshot_id)
        for row in self.history.entries()[:_MAX_ROWS]:
            tag = f"history_row_{row.get('id')}"
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, row.get("id") == self._history_selected)
        self._show_history_detail(self._history_selected)

    def _show_history_detail(self, snapshot_id: str | None) -> None:
        """The selected snapshot's own line, and its diff against now."""
        dpg.delete_item("history_diff", children_only=True)
        message_item = getattr(self, "_history_msg", 0)
        if message_item and dpg.does_item_exist(message_item):
            dpg.set_value(message_item, "")
        if not snapshot_id:
            dpg.set_value(self._history_detail, "")
            return
        snapshot = self.history.load(snapshot_id)
        if snapshot is None:
            dpg.set_value(self._history_detail, "This snapshot cannot be read.")
            return
        row = self.history.entry(snapshot_id) or {}
        exported = str(row.get("assignment_csv", ""))
        detail = [f"Saved {snapshot.saved_at.replace('T', ' ')} ({snapshot.trigger})"]
        if snapshot.name:
            detail.append(f'named "{snapshot.name}"')
        if snapshot.pinned:
            detail.append("pinned")
        if exported:
            detail.append(f"exported {Path(exported).name}")
        dpg.set_value(self._history_detail, "  ·  ".join(detail))
        if not snapshot.readable:
            self.theme.text("Written by a newer version of Mosaic; it can be "
                            "listed but not restored.", "warning", wrap=580,
                            parent="history_diff")
            return
        changes = SH.diff(self._snapshot_config(), snapshot.config)
        if not changes:
            self.theme.text("Identical to the current settings.", "muted",
                            parent="history_diff")
            return
        for change in changes[:_MAX_CHANGES]:
            before = change.before or "—"
            after = change.after or "—"
            self.theme.text(f"{change.label}: {before} → {after}", "body",
                            wrap=560, parent="history_diff")
        if len(changes) > _MAX_CHANGES:
            self.theme.text(f"... and {len(changes) - _MAX_CHANGES} more",
                            "muted", parent="history_diff")

    # ── The window's actions ──────────────────────────────────────────────────

    def _on_history_restore(self) -> None:
        if not self._history_selected:
            self._history_note("Choose a snapshot first.")
            return
        self._restore_snapshot(self._history_selected)

    def _on_history_pin(self) -> None:
        snapshot_id = self._history_selected
        row = self.history.entry(snapshot_id) if snapshot_id else None
        if row is None:
            self._history_note("Choose a snapshot first.")
            return
        if self.history.pin(snapshot_id, not row.get("pinned")):
            self._history_sig = None
            self._refresh_history_window()

    def _on_history_rename(self) -> None:
        if not self._history_selected:
            self._history_note("Choose a snapshot first.")
            return
        row = self.history.entry(self._history_selected) or {}
        dpg.set_value(self._history_name_input, str(row.get("name", "")))
        dpg.configure_item(_RENAME_TAG, show=True)

    def _on_history_rename_save(self) -> None:
        dpg.configure_item(_RENAME_TAG, show=False)
        name = dpg.get_value(self._history_name_input) or ""
        if not self._history_selected:
            return
        if self.history.rename(self._history_selected, name):
            # A named snapshot is worth keeping, so naming pins it.
            if name.strip():
                self.history.pin(self._history_selected, True)
            self._history_sig = None
            self._refresh_history_window()

    def _on_history_preset(self) -> None:
        """Write the selected snapshot's scores out as a preset file."""
        if not self._history_selected:
            self._history_note("Choose a snapshot first.")
            return
        snapshot = self.history.load(self._history_selected)
        preset = snapshot.config.get("preset") if snapshot else None
        if not isinstance(preset, dict) or not preset:
            self._history_note("That snapshot has no scores to save.")
            return
        self._pick_preset_path(
            True, lambda path: self._save_preset_to(path, preset=preset))

    # ── The offer on launch ───────────────────────────────────────────────────

    def _offer_restore_last_session(self) -> None:
        """Offer the newest snapshot, once, on the first frame.

        Non-modal and dismissible: someone who opened Mosaic to start
        something else should not have to answer a question first.
        """
        if not getattr(self, "_history_offer_pending", False):
            return
        self._history_offer_pending = False
        row = self.history.latest()
        if not row:
            return
        summary = self._history_row_label(row)
        with self._dialog(
            "Restore last session", _OFFER_TAG, (480, 180), modal=False,
            buttons=[("Restore", self._on_history_offer_restore, "primary"),
                     ("Not now", lambda: dpg.configure_item(
                         _OFFER_TAG, show=False))],
        ):
            self.theme.text("The settings Mosaic last used:", "muted", wrap=450)
            self.theme.text(summary, "body", wrap=450)
        self._history_offer_id = str(row.get("id", ""))

    def _on_history_offer_restore(self) -> None:
        dpg.configure_item(_OFFER_TAG, show=False)
        snapshot_id = getattr(self, "_history_offer_id", "")
        if snapshot_id:
            self._restore_snapshot(snapshot_id)
