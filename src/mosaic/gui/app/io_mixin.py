"""Shapefile / starting-plan / alignment loading, pickers, and seeding."""
from ._common import (
    AlgorithmRunner,
    AlgorithmStatus,
    Path,
    ShapefileConfig,
    ShapefileInspection,
    dpg,
    log,
    np,
    threading,
)


class IOMixin:
    """Shapefile / starting-plan / alignment loading, pickers, and seeding."""

    def _on_import_shapefile(self):
        if self._ensemble_active:
            return
        # Platform split: Windows uses a PowerShell OpenFileDialog (tkinter's
        # native picker throws "Catastrophic failure" against DPG's Win32 loop).
        # mac + Linux use DPG's own dialog: tkinter fights DPG for the Cocoa run
        # loop on macOS, and PowerShell doesn't exist on either.
        import os
        if os.name == "nt":
            self._pick_shapefile_windows()
        else:
            self._pick_shapefile_dpg()

    def _pick_shapefile_windows(self):
        from mosaic.gui.file_dialog import windows_file_dialog
        from mosaic.paths import mosaic_data_dir, shapefiles_dir
        shp_dir = shapefiles_dir()
        try:
            path = windows_file_dialog(
                title="Select Shapefile",
                file_filter="Shapefiles (*.shp)|*.shp|All files (*.*)|*.*",
                initial_dir=shp_dir if shp_dir.is_dir() else mosaic_data_dir(),
            )
        except Exception:
            log.exception("Shapefile picker failed")
            self.state.update(status_message="Could not open file picker. See log.")
            return
        if path:
            self._on_shapefile_selected(None, {"file_path_name": path})

    def _pick_shapefile_dpg(self):
        if dpg.does_item_exist("__shp_file_dialog"):
            dpg.delete_item("__shp_file_dialog")
        from mosaic.paths import mosaic_data_dir, shapefiles_dir
        shp_dir = shapefiles_dir()
        default_path = str(shp_dir if shp_dir.is_dir() else mosaic_data_dir())
        with dpg.file_dialog(
            directory_selector=False,
            show=True,
            # Not modal: in Dear PyGui 2.3 a modal window opened after a
            # modal file dialog is dismissed on creation -- DPG clears its
            # show flag and it never appears, with no error. The column
            # pickers and the discard confirm this dialog leads to are
            # modal, and those matter more than dimming the background
            # behind a transient picker. See _pick_alignment_dpg.
            modal=False,
            callback=self._on_shapefile_selected,
            cancel_callback=lambda *_: None,
            default_path=default_path,
            width=700,
            height=450,
            tag="__shp_file_dialog",
            label="Select Shapefile",
        ):
            dpg.add_file_extension(".shp", color=(120, 220, 120, 255))
            dpg.add_file_extension(".*")

    # ── Starting plan (Advanced menu) ────────────────────────────────────────────────

    def _on_load_hot_start(self):
        if self._queue_session_action(self._on_load_hot_start):
            return
        if not self._hot_start_editable():
            return
        if self.runner is None or self.runner.gdf is None or self.runner.graph is None:
            self._show_hot_start_error(
                "Load a shapefile before choosing a starting plan.",
            )
            return
        self._hot_start_pending_path = ""
        self._clear_hot_start_count_fix()
        with self._dialog(
            "Start from Plan", "hot_start_window", (560, 530),
            secondary=("Close", lambda: dpg.delete_item("hot_start_window")),
        ):
            self.theme.text("Starting assignment", "heading")
            self.theme.text("Use a CSV or the current map as the starting plan for each run.",
                            "muted", wrap=520)
            dpg.add_text("", tag="hot_start_loaded", wrap=520)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Choose CSV...", callback=self._browse_hot_start)
                dpg.add_button(label="Use Current Map", tag="hot_start_use_current",
                               callback=self._on_hot_start_use_current)
            with dpg.group(tag="hot_start_csv_controls", show=False):
                dpg.add_text("", tag="hot_start_csv_name", wrap=520)
                dpg.add_combo([], tag="hot_start_id_column", label="Precinct ID", width=260)
                dpg.add_combo([], tag="hot_start_district_column", label="District", width=260)
                dpg.add_button(label="Load Assignment", callback=self._load_hot_start_csv)
            dpg.add_spacer(height=6)
            dpg.add_separator()
            locked, = self.state.get("hot_start_locked_districts")
            dpg.add_checkbox(label="Lock districts", tag="hot_start_lock_enabled",
                             default_value=bool(locked), callback=self._on_hot_start_lock_toggle)
            with dpg.group(tag="hot_start_lock_controls", show=bool(locked)):
                self.theme.text("Lock districts to keep their precincts while the rest change.",
                                "muted", wrap=520)
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Lock All", callback=self._on_hot_start_lock_all)
                    dpg.add_button(label="Unlock All", callback=self._on_hot_start_unlock_all)
                dpg.add_text("", tag="hot_start_lock_summary", wrap=520)
                with dpg.child_window(tag="hot_start_lock_list", width=-1, height=190):
                    pass
            self.theme.text("", "error", tag="hot_start_message", wrap=520, show=False)
            dpg.add_button(label="", tag="hot_start_resolve_count",
                           show=False, callback=self._on_resolve_hot_start_count)
        self._refresh_hot_start_window()

    def _hot_start_editable(self):
        return (not self._ensemble_active and not self._fine_tune_active
                and not self._session_action_pending() and not self._session_workers_busy())

    def _browse_hot_start(self):
        if self._queue_session_action(self._browse_hot_start):
            return
        if not self._hot_start_editable():
            return
        import sys
        if sys.platform == "win32":
            self._pick_hot_start_windows()
        else:
            self._pick_hot_start_dpg()

    def _load_hot_start_csv(self):
        self._apply_hot_start(self._hot_start_pending_path,
                             dpg.get_value("hot_start_id_column"),
                             dpg.get_value("hot_start_district_column"))

    def _on_hot_start_use_current(self):
        if self._queue_session_action(self._on_hot_start_use_current):
            return
        reason = self._current_map_unavailable_reason()
        if reason:
            self._show_hot_start_error(f"Could not use current map: {reason}")
            return
        with self.state._lock:
            current = (self.state.current_assignment.copy()
                       if self.state.current_assignment is not None else None)
        if current is None:
            self._show_hot_start_error("There is no current plan. Run Mosaic or load a CSV first.")
            return
        from mosaic.io.hot_start import (
            DistrictCountMismatch,
            HotStartError,
            validate_start_assignment,
        )
        try:
            assignment = self._export_labeled_assignment(current).astype(np.int32, copy=True)
            info = validate_start_assignment(
                assignment, self.runner.populations, self.runner.graph,
                dpg.get_value(self._num_districts), dpg.get_value(self._tolerance) / 100.0)
        except DistrictCountMismatch as exc:
            self._show_hot_start_error(
                f"Could not use current map: {exc}", district_count=exc.district_count,
                retry=self._on_hot_start_use_current)
            return
        except HotStartError as exc:
            self._show_hot_start_error(f"Could not use current map: {exc}")
            return
        except Exception:
            log.exception("Could not capture the current map for Start from Plan")
            self._show_hot_start_error(
                "Could not use current map: an unexpected error occurred. "
                "Your existing starting plan is unchanged. "
                "Try again; if it repeats, check the log.")
            return
        info["filename"] = "Current Map"
        if self._install_hot_start(assignment, info):
            self.theme.retoken("hot_start_message", "body")
            dpg.set_value("hot_start_message", "Current map loaded as the starting plan.")
            dpg.configure_item("hot_start_message", show=True)

    def _current_map_unavailable_reason(self):
        if self._ensemble_active:
            return "Finish or stop the Batch Runs before using its map."
        if self._fine_tune_active:
            return "Stop Fine-Tuning before choosing a starting plan."
        if self.state.status == AlgorithmStatus.PAUSED:
            return "Resume and finish the paused run before using its map."
        if self._session_action_pending() or self._session_workers_busy():
            return "Wait for the current work to finish, then try again."
        if (self.runner is None or self.runner.graph is None
                or self.runner.populations is None):
            return "Load a shapefile first."
        current, = self.state.get("current_assignment")
        if current is None:
            return "No plan is displayed. Run Mosaic or load a starting-plan CSV first."
        return ""

    def _sync_current_map_availability(self):
        if not dpg.does_item_exist("hot_start_use_current"):
            return
        dpg.configure_item("hot_start_use_current", enabled=self._hot_start_editable())

    def _refresh_hot_start_window(self):
        if not dpg.does_item_exist("hot_start_window"):
            return
        assignment, filename, locked = self.state.get(
            "hot_start_assignment", "hot_start_filename", "hot_start_locked_districts")
        dpg.set_value("hot_start_loaded",
                      f"Loaded: {filename}" if filename else "No assignment loaded.")
        self._sync_current_map_availability()
        dpg.delete_item("hot_start_lock_list", children_only=True)
        self._hot_start_lock_checks = {}
        dpg.configure_item("hot_start_lock_enabled", enabled=assignment is not None)
        if assignment is None:
            dpg.set_value("hot_start_lock_enabled", False)
            dpg.configure_item("hot_start_lock_controls", show=False)
            dpg.set_value("hot_start_lock_summary", "Load an assignment to select districts.")
            return
        k = int(assignment.max()) + 1
        totals = np.bincount(assignment, weights=self.runner.populations, minlength=k)
        ideal = float(totals.sum()) / k
        with dpg.table(parent="hot_start_lock_list", header_row=True,
                       policy=dpg.mvTable_SizingStretchProp):
            dpg.add_table_column(label="Lock")
            dpg.add_table_column(label="District")
            dpg.add_table_column(label="Population")
            dpg.add_table_column(label="Deviation")
            for district in range(k):
                with dpg.table_row():
                    self._hot_start_lock_checks[district] = dpg.add_checkbox(
                        default_value=district in locked,
                        callback=self._on_hot_start_lock_change, user_data=district)
                    dpg.add_text(str(district + 1))
                    dpg.add_text(f"{round(totals[district]):,}")
                    dpg.add_text(f"{(totals[district] / ideal - 1) * 100:+.2f}%")
        dpg.set_value("hot_start_lock_summary",
                      f"{len(locked)} locked / {k - len(locked)} unlocked")

    def _on_hot_start_lock_change(self, sender, app_data, user_data):
        if self._queue_session_action(self._on_hot_start_lock_change, sender, app_data, user_data):
            return
        assignment, locked = self.state.get("hot_start_assignment", "hot_start_locked_districts")
        if assignment is None or not self._hot_start_editable():
            self._refresh_hot_start_window()
            return
        selected = set(locked)
        if app_data:
            selected.add(user_data)
        else:
            selected.discard(user_data)
        if not self._set_hot_start_locks(tuple(sorted(selected))):
            dpg.set_value(sender, user_data in locked)

    def _on_hot_start_lock_toggle(self, sender, app_data):
        if self._queue_session_action(self._on_hot_start_lock_toggle, sender, app_data):
            return
        if not self._hot_start_editable():
            return
        dpg.configure_item("hot_start_lock_controls", show=bool(app_data))
        if not app_data:
            self._set_hot_start_locks(())

    def _on_hot_start_lock_all(self):
        if self._queue_session_action(self._on_hot_start_lock_all):
            return
        assignment, = self.state.get("hot_start_assignment")
        if assignment is not None:
            self._set_hot_start_locks(tuple(range(int(assignment.max()) + 1)))

    def _on_hot_start_unlock_all(self):
        if self._queue_session_action(self._on_hot_start_unlock_all):
            return
        self._set_hot_start_locks(())

    def _set_hot_start_locks(self, selected):
        assignment, = self.state.get("hot_start_assignment")
        if assignment is None or not self._hot_start_editable():
            return False
        from mosaic.io.hot_start import HotStartError, validate_district_locks
        try:
            validate_district_locks(
                assignment, selected, self.runner.populations, self.runner.graph,
                dpg.get_value(self._num_districts), dpg.get_value(self._tolerance) / 100.0,
                require_movable=False)
        except HotStartError as exc:
            self._show_hot_start_error(str(exc))
            return False
        self.state.update(hot_start_locked_districts=selected)
        self._clear_hot_start_count_fix()
        with self.state._lock:
            if (self.state.best_assignment is None
                    and np.array_equal(self.state.current_assignment, assignment)):
                self.state.run_locked_districts = selected
        for district, checkbox in self._hot_start_lock_checks.items():
            dpg.set_value(checkbox, district in selected)
        dpg.configure_item("hot_start_message", show=False)
        k = int(assignment.max()) + 1
        dpg.set_value("hot_start_lock_summary",
                      f"{len(selected)} locked / {k - len(selected)} unlocked")
        self._update_hot_start_display(self._hot_start_summary())
        return True

    def _hot_start_summary(self):
        assignment, filename = self.state.get("hot_start_assignment", "hot_start_filename")
        if assignment is None or not filename:
            return None
        k = int(assignment.max()) + 1
        totals = np.bincount(assignment, weights=self.runner.populations, minlength=k)
        return {"filename": filename, "n_districts": k,
                "max_dev_pct": float(np.abs(totals / (totals.sum() / k) - 1).max() * 100)}

    def _pick_hot_start_windows(self):
        from mosaic.gui.file_dialog import windows_file_dialog
        from mosaic.paths import mosaic_data_dir
        try:
            path = windows_file_dialog(
                title="Select Starting Plan CSV",
                file_filter="CSV files (*.csv)|*.csv|All files (*.*)|*.*",
                initial_dir=mosaic_data_dir(),
            )
        except Exception:
            log.exception("Starting plan file picker failed")
            self._show_hot_start_error("Could not open file picker. See log.")
            return
        if not path:
            return
        self._show_hot_start_column_picker(path)

    def _pick_hot_start_dpg(self):
        if dpg.does_item_exist("__hot_start_file_dialog"):
            dpg.delete_item("__hot_start_file_dialog")
        from mosaic.paths import mosaic_data_dir
        default_path = str(mosaic_data_dir())
        with dpg.file_dialog(
            directory_selector=False,
            show=True,
            # Not modal: in Dear PyGui 2.3 a modal window opened after a
            # modal file dialog is dismissed on creation -- DPG clears its
            # show flag and it never appears, with no error. The column
            # pickers and the discard confirm this dialog leads to are
            # modal, and those matter more than dimming the background
            # behind a transient picker. See _pick_alignment_dpg.
            modal=False,
            callback=lambda s, d: self._show_hot_start_column_picker(
                d.get("file_path_name", "") if isinstance(d, dict) else ""
            ),
            cancel_callback=lambda *_: None,
            default_path=default_path,
            width=700,
            height=450,
            tag="__hot_start_file_dialog",
            label="Select Starting Plan CSV",
        ):
            dpg.add_file_extension(".csv", color=(120, 220, 120, 255))
            dpg.add_file_extension(".*")

    def _show_hot_start_column_picker(self, path: str) -> None:
        if self._queue_session_action(self._show_hot_start_column_picker, path):
            return
        if not self._hot_start_editable() or not dpg.does_item_exist("hot_start_window"):
            return
        log.info(f"Starting plan: column picker invoked with path={path!r}")
        if not path:
            return
        from mosaic.io.hot_start import HotStartError, read_csv_columns
        try:
            columns = read_csv_columns(path)
        except HotStartError as e:
            self._show_hot_start_error(str(e))
            return
        except Exception as e:
            log.exception("Starting plan: read_csv_columns raised unexpectedly")
            self._show_hot_start_error(f"Could not read CSV header: {e}")
            return
        if not columns:
            self._show_hot_start_error("CSV appears to be empty.")
            return

        gdf_id_col = self.runner.id_col_name if self.runner else ""
        default_id = gdf_id_col if gdf_id_col in columns else columns[0]
        default_district = next(
            (c for c in columns if c.lower() == "district"),
            columns[-1] if len(columns) > 1 else columns[0],
        )

        self._hot_start_pending_path = path
        dpg.set_value("hot_start_csv_name", f"{Path(path).name} (match IDs to {gdf_id_col})")
        dpg.configure_item("hot_start_id_column", items=columns)
        dpg.set_value("hot_start_id_column", default_id)
        dpg.configure_item("hot_start_district_column", items=columns)
        dpg.set_value("hot_start_district_column", default_district)
        dpg.configure_item("hot_start_csv_controls", show=True)
        dpg.configure_item("hot_start_message", show=False)

    def _apply_hot_start(
        self, path: str, csv_id_col: str, csv_district_col: str,
    ) -> None:
        if self._queue_session_action(self._apply_hot_start, path, csv_id_col, csv_district_col):
            return

        self._clear_hot_start_count_fix()
        if not self._hot_start_editable():
            return
        log.info(
            f"Starting plan: apply path={path!r} id_col={csv_id_col!r} "
            f"district_col={csv_district_col!r}"
        )
        if not path:
            return
        from mosaic.io.hot_start import DistrictCountMismatch, HotStartError, load_hot_start
        try:
            assignment, info = load_hot_start(
                path,
                gdf=self.runner.gdf,
                gdf_id_col=self.runner.id_col_name,
                csv_id_col=csv_id_col,
                csv_district_col=csv_district_col,
                populations=self.runner.populations,
                graph=self.runner.graph,
                num_districts=dpg.get_value(self._num_districts),
                tolerance=dpg.get_value(self._tolerance) / 100.0,
            )
        except DistrictCountMismatch as exc:
            self._show_hot_start_error(
                str(exc), district_count=exc.district_count,
                retry=lambda: self._apply_hot_start(path, csv_id_col, csv_district_col))
            return
        except HotStartError as e:
            log.info(f"Starting plan: validation failed -- {e}")
            self._show_hot_start_error(str(e))
            return
        except Exception as e:
            log.exception("Unexpected starting plan error")
            self._show_hot_start_error(f"Unexpected error: {e}")
            return

        self._install_hot_start(assignment, info)

    def _install_hot_start(self, assignment, info):
        """Publish a validated seed without replacing an existing displayed plan."""
        self._clear_hot_start_count_fix()
        self.state.update(
            hot_start_assignment=assignment,
            hot_start_filename=info["filename"],
            hot_start_locked_districts=(),
        )
        with self.state._lock:
            if self.state.current_assignment is None:
                self.state.current_assignment = assignment.copy()
                self.state.initial_assignment = assignment.copy()
                self.state.num_districts = info["n_districts"]
                self.state.district_label_map = None
                self.state.run_locked_districts = ()
                self.state.map_needs_update = True
        try:
            self._update_hot_start_display(info)
            self._refresh_hot_start_window()
            if dpg.does_item_exist("hot_start_csv_controls"):
                dpg.set_value("hot_start_lock_enabled", False)
                dpg.configure_item("hot_start_lock_controls", show=False)
                dpg.configure_item("hot_start_csv_controls", show=False)
                dpg.configure_item("hot_start_message", show=False)
        except Exception:
            log.exception("Starting plan: display update failed (state is loaded)")
            self._show_hot_start_error(
                "Starting plan loaded, but the status text could not be updated. "
                "See log for details."
            )
            return False
        return True

    def _on_clear_hot_start(self):
        if self._queue_session_action(self._on_clear_hot_start):
            return
        if not self._hot_start_editable():
            return
        self._clear_hot_start_count_fix()
        self.state.update(
            hot_start_assignment=None,
            hot_start_filename="",
            hot_start_locked_districts=(),
        )
        with self.state._lock:
            if self.state.best_assignment is None:
                self.state.run_locked_districts = ()
        self._update_hot_start_display(None)
        self._refresh_hot_start_window()

    # ── Fine Tune ──────────────────────────────────────────────────────────────
    # Fine Tune is a "continue refining" mode: each Start reseeds from the current
    # on-screen map (so runs chain, unlike a fixed starting plan) and a
    # polish preset is applied to the annealing controls. The pre-arm control
    # values are snapshotted so Clear restores them. Mutually exclusive with Hot
    # Start; cleared (with restore) on Reset or when the shapefile or district
    # count changes, since each invalidates the on-screen map it reseeds from.
    _FINE_TUNE_PRESET = {
        "ann": True, "cool": "Guided (recommended)", "temp": 0.01,
        "guide": 0.80, "watch": False, "n3": 50, "flip_en": True, "flip_mid": 75,
    }

    def _on_fine_tune_toggle(self) -> None:
        if dpg.get_value(self._fine_tune_item):
            self._arm_fine_tune()
        else:
            self._clear_fine_tune()

    def _on_num_districts_change(self, *_args) -> None:
        """Rescale the people tolerance when the district count changes.

        If Fine Tune is active, clear its seed and restore the saved annealing
        settings because the seed no longer matches the requested plan size.
        """
        if self._fine_tune_active:
            self._clear_fine_tune()
        self._sync_tolerance_people()
        fix = getattr(self, "_hot_start_count_fix", None)
        if fix is not None:
            for tag in ("hot_start_resolve_count", "hot_start_error_resolve_count"):
                if dpg.does_item_exist(tag):
                    dpg.configure_item(tag, label=self._hot_start_count_fix_label(fix[0]))

    def _fine_tune_snapshot(self) -> dict:
        return {
            "ann":      dpg.get_value(self._ann_enabled),
            "cool":     dpg.get_value(self._cool_mode),
            "temp":     dpg.get_value(self._temp_factor),
            "guide":    dpg.get_value(self._guide_frac),
            "watch":    dpg.get_value(self._launch_watch_enabled),
            "n3":       dpg.get_value(self._n3_pct),
            "flip_en":  dpg.get_value(self._flip_enabled),
            "flip_mid": dpg.get_value(self._flip_midpoint),
        }

    def _fine_tune_write(self, s: dict) -> None:
        """Push a snapshot dict into the annealing controls, then re-run the
        toggle callbacks so the sub-control groups show/hide correctly."""
        dpg.set_value(self._ann_enabled, s["ann"])
        dpg.set_value(self._cool_mode, s["cool"])
        dpg.set_value(self._temp_factor, s["temp"])
        dpg.set_value(self._guide_frac, s["guide"])
        dpg.set_value(self._launch_watch_enabled, s["watch"])
        dpg.set_value(self._n3_pct, s["n3"])
        dpg.set_value(self._flip_enabled, s["flip_en"])
        dpg.set_value(self._flip_midpoint, s["flip_mid"])
        self._on_ann_toggle()
        self._on_cool_mode()
        self._on_launch_watch_toggle()

    def _arm_fine_tune(self) -> None:
        # The menu item is gated, but double-check: needs a map on screen and no
        # starting plan selected.
        with self.state._lock:
            has_map = self.state.current_assignment is not None
        real_hot = self.state.get("hot_start_filename")[0] != ""
        if not has_map or real_hot:
            dpg.set_value(self._fine_tune_item, False)   # refuse; snap back
            return
        self._fine_tune_saved = self._fine_tune_snapshot()
        self._fine_tune_write(self._FINE_TUNE_PRESET)
        # Fine Tune chains short runs; show the full trajectory, not just last 10k.
        dpg.set_value(self._limit_plots, False)
        self._fine_tune_active = True
        dpg.set_value(self._fine_tune_item, True)
        self._update_fine_tune_display()

    def _clear_fine_tune(self) -> None:
        if not self._fine_tune_active and self._fine_tune_saved is None:
            return
        if self._fine_tune_saved is not None:
            self._fine_tune_write(self._fine_tune_saved)
            self._fine_tune_saved = None
        self._fine_tune_active = False
        # Drop the reseed the run path injected, so a later non-Fine Tune run
        # doesn't inherit a stale seed. Only touch the slot when no starting plan
        # owns it (the two are mutually exclusive, but guard anyway).
        if self.state.get("hot_start_filename")[0] == "":
            self.state.update(hot_start_assignment=None)
        dpg.set_value(self._fine_tune_item, False)
        self._update_fine_tune_display()

    def _update_fine_tune_display(self) -> None:
        self._update_start_mode_display()

    def _update_start_mode_display(self) -> None:
        filename, locked = self.state.get("hot_start_filename", "hot_start_locked_districts")
        if self._fine_tune_active:
            text = "Current map"
            detail = "Advanced > Stop Fine-Tuning"
        elif filename:
            text = "Captured plan" if filename == "Current Map" else "CSV plan"
            detail = "Advanced > Clear Starting Plan"
            if locked:
                noun = "district" if len(locked) == 1 else "districts"
                text += f" -- {len(locked)} {noun} locked"
        else:
            text = "Random partition"
            detail = ""
        dpg.set_value(self._hot_start_info, text)
        if detail:
            if self._start_mode_tooltip is None:
                with dpg.tooltip(self._hot_start_info) as tooltip:
                    self._start_mode_tooltip = tooltip
                    self._start_mode_detail = dpg.add_text(detail)
            else:
                dpg.set_value(self._start_mode_detail, detail)
        elif self._start_mode_tooltip is not None:
            dpg.delete_item(self._start_mode_tooltip)
            self._start_mode_tooltip = None
        self.theme.retoken(self._hot_start_info, "warning" if filename or self._fine_tune_active
                           else "muted")

    def _sync_seed_controls(self) -> None:
        """Enable-state for the Fine Tune / Start from Plan menu items: mutually
        exclusive. Fine Tune needs a map in IDLE, PAUSED, COMPLETED, or ERROR
        status. Called every frame."""
        self._sync_current_map_availability()
        has_map = self.state.current_assignment is not None
        status = self.state.status
        real_hot = self.state.get("hot_start_filename")[0] != ""
        idle = status in (AlgorithmStatus.IDLE, AlgorithmStatus.PAUSED,
                          AlgorithmStatus.COMPLETED, AlgorithmStatus.ERROR)
        can_arm = has_map and idle and not real_hot
        dpg.configure_item(self._fine_tune_item,
                           enabled=self._fine_tune_active or can_arm)
        dpg.configure_item(self._fine_tune_clear_item, enabled=self._fine_tune_active)
        # Start from Plan is locked out while Fine Tune is armed.
        dpg.configure_item(self._hot_start_load_item,
                           enabled=self._hot_start_editable())
        dpg.configure_item(self._hot_start_clear_item,
                           enabled=real_hot and self._hot_start_editable())

    # ── Plan Continuity reference plan ─────────────────────────────────────────
    # Mirrors the starting-plan CSV flow, but loads a *reference* plan for the
    # Plan Continuity score. Deliberately permissive: a reference plan may have a
    # different district count and need not be contiguous/pop-balanced.

    def _on_load_alignment(self):
        if self.runner is None or self.runner.gdf is None:
            self._show_alignment_error(
                "Load a shapefile before loading a reference plan.")
            return
        import sys
        if sys.platform == "win32":
            self._pick_alignment_windows()
        else:
            self._pick_alignment_dpg()

    def _pick_alignment_windows(self):
        from mosaic.gui.file_dialog import windows_file_dialog
        from mosaic.paths import mosaic_data_dir
        try:
            path = windows_file_dialog(
                title="Select Reference Plan CSV",
                file_filter="CSV files (*.csv)|*.csv|All files (*.*)|*.*",
                initial_dir=mosaic_data_dir(),
            )
        except Exception:
            log.exception("Reference plan file picker failed")
            self._show_alignment_error("Could not open file picker. See log.")
            return
        if not path:
            return
        self._show_alignment_column_picker(path)

    def _pick_alignment_dpg(self):
        if dpg.does_item_exist("__alignment_file_dialog"):
            dpg.delete_item("__alignment_file_dialog")
        from mosaic.paths import mosaic_data_dir, output_dir
        # Start where the exports are: Save Assignments writes the assignment
        # CSVs most likely to be picked here into output/.
        out = output_dir()
        default_path = str(out if out.is_dir() else mosaic_data_dir())
        with dpg.file_dialog(
            directory_selector=False, show=True,
            # Not modal: in Dear PyGui 2.3 a modal window opened after a modal
            # file dialog is dismissed on creation -- DPG clears its show flag
            # and it never appears, with no error anywhere. That silently broke
            # this loader, whose next step (_show_alignment_column_picker) is a
            # modal. Deferring to a later frame does not help; only breaking
            # the modal-on-modal does. The column picker stays modal, since a
            # required choice matters more than dimming behind this dialog.
            modal=False,
            callback=lambda s, d: self._show_alignment_column_picker(
                d.get("file_path_name", "") if isinstance(d, dict) else ""),
            cancel_callback=lambda *_: None,
            default_path=default_path,
            width=700, height=450, tag="__alignment_file_dialog",
            label="Select Reference Plan CSV",
        ):
            dpg.add_file_extension(".csv", color=(120, 220, 120, 255))
            dpg.add_file_extension(".*")

    def _show_alignment_column_picker(self, path: str) -> None:
        if not path:
            return
        from mosaic.io.hot_start import HotStartError, read_csv_columns
        try:
            columns = read_csv_columns(path)
        except (HotStartError, Exception) as e:
            self._show_alignment_error(f"Could not read CSV header: {e}")
            return
        if not columns:
            self._show_alignment_error("CSV appears to be empty.")
            return

        gdf_id_col = self.runner.id_col_name if self.runner else ""
        default_id = gdf_id_col if gdf_id_col in columns else columns[0]
        default_district = next(
            (c for c in columns if c.lower() == "district"),
            columns[-1] if len(columns) > 1 else columns[0],
        )
        id_combo = district_combo = None
        with self._dialog(
            "Reference Plan", "popup_alignment_picker", (520, 240),
            primary=("Load", lambda: self._apply_alignment(
                path, dpg.get_value(id_combo), dpg.get_value(district_combo))),
            secondary=("Cancel",
                       lambda: dpg.delete_item("popup_alignment_picker")),
        ):
            dpg.add_text(f"File: {Path(path).name}", wrap=500)
            dpg.add_text(
                f"Match CSV precinct IDs to shapefile column: {gdf_id_col}", wrap=500,
            )
            dpg.add_separator()
            id_combo = dpg.add_combo(
                items=columns, default_value=default_id,
                label="CSV precinct ID column", width=240,
            )
            district_combo = dpg.add_combo(
                items=columns, default_value=default_district,
                label="CSV district column", width=240,
            )

    def _apply_alignment(self, path, csv_id_col, csv_district_col) -> None:
        if not path:
            return
        if dpg.does_item_exist("popup_alignment_picker"):
            dpg.delete_item("popup_alignment_picker")
        from mosaic.scoring.alignment import (
            AlignmentError,
            precompute_alignment_data,
        )
        # Pass election votes (if loaded) so the reference's per-district
        # partisan shares are cached for the "only districts that party wins"
        # restriction. Votes are in gdf row order, matching the reference.
        _dem = _gop = None
        if self.runner.election_arrays:
            _dem, _gop = self.runner.election_arrays[0]
        try:
            data = precompute_alignment_data(
                path, gdf=self.runner.gdf,
                gdf_id_col=self.runner.id_col_name,
                csv_id_col=csv_id_col, csv_district_col=csv_district_col,
                dem_votes=_dem, gop_votes=_gop,
            )
        except AlignmentError as e:
            self._show_alignment_error(str(e))
            return
        except Exception as e:
            log.exception("Unexpected Plan Continuity reference load error")
            self._show_alignment_error(f"Unexpected error: {e}")
            return

        self.runner.alignment_data = data
        # Plan Continuity is gated on a reference plan existing; one does now.
        dpg.configure_item(self._alignment_enabled, enabled=True)
        self._scores_sig = None
        n_run = dpg.get_value(self._num_districts)
        note = f", run {n_run}" if data.n_alt_districts != n_run else ""
        dpg.set_value(
            self._alignment_info,
            f"{data.filename} ({data.n_alt_districts} dist{note})",
        )
        # `body`, not green: accent_green means "this score is on", and a
        # loaded file is not a score (design-system.md).
        self.theme.retoken(self._alignment_info, "body")
        log.info(
            f"Plan Continuity reference set: {data.filename}, "
            f"{data.n_alt_districts} districts"
        )
        if not dpg.get_value(self._alignment_enabled):
            dpg.set_value(self._alignment_enabled, True)
        self._on_alignment_toggle()

    def _on_clear_alignment(self):
        if self.runner is not None:
            self.runner.alignment_data = None
        dpg.configure_item(self._alignment_enabled, enabled=False)
        self._scores_sig = None
        dpg.set_value(self._alignment_info, "none")
        self.theme.retoken(self._alignment_info, "muted")
        dpg.set_value(self._alignment_enabled, False)
        self._on_alignment_toggle()

    def _show_alignment_error(self, message: str) -> None:
        # Non-modal (like the starting-plan error): it can be raised in the same frame
        # a modal picker was deleted, and a new modal would hide behind DPG's
        # defunct overlay.
        with self._dialog(
            "Plan Continuity Error", "popup_alignment_error", (620, 320),
            modal=False,
            secondary=("Close",
                       lambda: dpg.delete_item("popup_alignment_error")),
        ):
            dpg.add_text(message, wrap=600)

    def _update_hot_start_display(self, info) -> None:
        dpg.configure_item(self._hot_start_clear_item, enabled=info is not None)
        self._update_start_mode_display()

    def _clear_hot_start_count_fix(self):
        self._hot_start_count_fix = None
        for tag in ("hot_start_resolve_count", "hot_start_error_resolve_count"):
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, show=False)

    def _hot_start_count_fix_label(self, count):
        return f"Switch from {dpg.get_value(self._num_districts)} to {count} districts"

    def _on_resolve_hot_start_count(self):
        if self._queue_session_action(self._on_resolve_hot_start_count):
            return
        fix = getattr(self, "_hot_start_count_fix", None)
        if fix is None:
            return
        if not self._hot_start_editable():
            self._show_hot_start_error("Wait for the current work to finish, then try again.")
            return
        count, retry = fix
        self._clear_hot_start_count_fix()
        dpg.set_value(self._num_districts, count)
        self._on_num_districts_change()
        if dpg.does_item_exist("popup_hot_start_error"):
            dpg.delete_item("popup_hot_start_error")
        if retry is not None:
            retry()
        else:
            message = f"District count set to {count}. Press Start again."
            self.state.update(status_message=message)
            if dpg.does_item_exist("hot_start_message"):
                self.theme.retoken("hot_start_message", "body")
                dpg.set_value("hot_start_message", message)
                dpg.configure_item("hot_start_message", show=True)

    def _show_hot_start_error(self, message: str, *, district_count=None, retry=None) -> None:
        log.info(f"Starting plan error popup: {message[:200]}")
        self._clear_hot_start_count_fix()
        can_fix = district_count is not None and 2 <= district_count <= 500
        if can_fix:
            self._hot_start_count_fix = (district_count, retry)
        if dpg.does_item_exist("hot_start_message"):
            self.theme.retoken("hot_start_message", "error")
            dpg.set_value("hot_start_message", message)
            dpg.configure_item("hot_start_message", show=True)
            if can_fix:
                dpg.configure_item("hot_start_resolve_count", show=True,
                                   label=self._hot_start_count_fix_label(district_count))
            return
        with self._dialog(
            "Start from Plan", "popup_hot_start_error", (620, 320),
            modal=False,
            secondary=("Close",
                       lambda: dpg.delete_item("popup_hot_start_error")),
        ):
            dpg.add_text(message, wrap=600)
            if can_fix:
                dpg.add_button(label=self._hot_start_count_fix_label(district_count),
                               tag="hot_start_error_resolve_count",
                               callback=self._on_resolve_hot_start_count)
        try:
            dpg.focus_item("popup_hot_start_error")
        except Exception:
            pass

    def _on_shapefile_selected(self, sender, app_data):
        if self._queue_session_action(self._on_shapefile_selected, sender, app_data):
            return
        path = app_data.get("file_path_name", "") if isinstance(app_data, dict) else ""
        if not path:
            return
        self._request_open_shapefile(path)

    def _request_open_shapefile(self, path, config=None):
        """Ask before discarding a plan, including when opening a recent file."""
        if self._ensemble_active or self._session_action_pending():
            return
        if self._plan_unsaved():
            def discard():
                dpg.delete_item("popup_open_confirm")
                self._begin_open_shapefile(path, config)

            with self._dialog(
                "Open Map", "popup_open_confirm", (380, 120),
                primary=("Discard & Open", discard),
                secondary=("Cancel", lambda: dpg.delete_item("popup_open_confirm")),
            ):
                dpg.add_text("Discard unsaved results and open this map?", wrap=348)
            return
        self._begin_open_shapefile(path, config)

    def _clear_file_state(self):
        """Clear file-specific signals only after their publishers have stopped."""
        self._on_clear_alignment()
        self._clear_hot_start_count_fix()
        self._pending_recent_config = None
        self._restore_partisan_on_load = False
        self._saved_plan = None
        self._map_loaded_path = ""
        self._map_loaded_gdf_id = self._map_data_gdf_id = 0
        self._map_loading = self._map_ready = False
        self.state.update(shp_inspect_ready=False, gdf_ready=False,
                          map_needs_update=False, initial_assignment=None,
                          hot_start_assignment=None, hot_start_filename="",
                          hot_start_locked_districts=(),
                          run_locked_districts=(),
                          shapefile_path="", error_message="")
        if dpg.does_item_exist("shp_dialog"):
            dpg.configure_item("shp_dialog", show=False)
        if dpg.does_item_exist("hot_start_window"):
            dpg.delete_item("hot_start_window")
        if self._shp_dialog is not None:
            self._shp_dialog._inspection = None

    def _begin_open_shapefile(self, path, config=None):
        if self._queue_session_action(self._begin_open_shapefile, path, config):
            return
        if self._ensemble_active:
            return
        if self._wait_for_workers(lambda: self._begin_open_shapefile(path, config)):
            return
        self._on_reset()
        self._clear_file_state()
        self._pending_recent_config = config
        self._restore_partisan_on_load = bool(config and config.elections)
        self._reset_map_navigation()
        self.runner = AlgorithmRunner(self.state)
        self._sync_tolerance_people()
        self._loaded_config = None
        self._has_elections = False
        # Drop the map fill immediately so no stale overlay from the previous
        # file is visible while the new one loads. _sync_fill_availability
        # relabels the combo once the new file's data is known.
        self._fill_avail = None
        self._clear_fill()
        if self.map_view is not None:
            self.map_view.wipe()
            self._update_map_preview()
        # Starting plan (and Fine Tune) were tied to the previous shapefile's map.
        self.state.update(
            current_assignment=None,
            best_assignment=None,
            initial_assignment=None,
            district_label_map=None,
            hot_start_assignment=None,
            hot_start_filename="",
            hot_start_locked_districts=(),
        )
        self._update_hot_start_display(None)
        self._clear_fine_tune()   # turn off + restore the pre-Fine Tune settings
        dpg.set_value(self._shp_info, "Reading shapefile...")
        self.theme.retoken(self._shp_info, "muted")
        self._data_thread = threading.Thread(
            target=self.runner.start_inspection, args=(path,), daemon=True,
        )
        self._data_thread.start()

    def _on_shp_confirm(self, inspection: ShapefileInspection,
                        config: ShapefileConfig) -> None:
        """Called by ShapefileDialog when the user clicks Confirm and Load."""
        if self._queue_session_action(self._on_shp_confirm, inspection, config):
            return
        if self._ensemble_active or self._session_action_pending():
            return
        if self.runner is None or inspection is not self.runner._pending_inspection:
            return
        if self._wait_for_workers(lambda: self._on_shp_confirm(inspection, config)):
            return
        self._loaded_config = config
        self._push_recent_shapefile(inspection.path, config)
        # Flush all history and series before the new load so charts start
        # fresh and the previous file's data doesn't bleed through.
        with self.state._lock:
            self.state.history.reset()
            self.state.opportunity_targets = {}
            self.state.race_score_applicable = {}
        self._clear_all_series()
        dpg.set_value(self._shp_info, "Building graph...")
        self.theme.retoken(self._shp_info, "muted")
        self._data_thread = threading.Thread(
            target=self.runner.complete_load,
            args=(inspection, config),
            daemon=True,
        )
        self._data_thread.start()

    def _on_shp_cancel(self) -> None:
        """Called by ShapefileDialog when the user clicks Cancel."""
        if self._queue_session_action(self._on_shp_cancel):
            return
        if self._session_action_pending():
            return
        self.state.update(
            status=AlgorithmStatus.IDLE,
            status_message="Load cancelled",
        )
        dpg.set_value(self._shp_info, "No shapefile loaded")
        self.theme.retoken(self._shp_info, "muted")
