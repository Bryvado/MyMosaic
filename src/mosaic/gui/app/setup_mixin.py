"""Builds the main three-column window layout (one large builder)."""
from contextlib import contextmanager

from mosaic import basemap as B
from mosaic.gui.layout import column_widths, map_box_height
from mosaic.gui.map_navigation import MAX_ZOOM
from mosaic.gui.map_style import MAX_WIDTH, default_colors, line_label

from ._common import (
    _APP_ICON,
    _ASSETS_DIR,
    _CAMERA_ICON_SIZE,
    _FILL_NONE,
    _HALF_PLOT_H,
    _HALF_PLOT_W,
    _MAP_DH,
    _MAP_DW,
    _MAP_H,
    _MIN_CLIENT_W,
    _RUN_TILES,
    _SCORES_W,
    _SETUP_W,
    _VP_H,
    _VP_W,
    MapView,
    Path,
    ShapefileDialog,
    _build_camera_icon,
    dpg,
    np,
)

_STARTUP_W = 1300


def _initial_viewport_bounds():
    """Fit initial height to the work area while retaining the required width."""
    import sys
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            work = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0):
                height = min(_VP_H, work.bottom - work.top - 40)
                return dict(width=_STARTUP_W, height=max(560, height),
                            x_pos=work.left + 20, y_pos=work.top + 20)
        except (AttributeError, OSError):
            pass
    return dict(width=_STARTUP_W, height=700)


class SetupMixin:
    """Builds the main three-column window layout (one large builder)."""

    def setup(self):
        # On Windows the taskbar groups by host process (python.exe) unless an
        # explicit AppUserModelID is set — without this, the taskbar shows the
        # Python logo even when the window's icon is correct.
        import sys as _sys
        if _sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "MattMohn.Mosaic"
                )
            except Exception:
                pass

        dpg.create_context()
        _icon_path = str(_APP_ICON) if _APP_ICON.exists() else ""
        dpg.create_viewport(
            title="Mosaic",
            **_initial_viewport_bounds(),
            min_width=_MIN_CLIENT_W, min_height=560,
            small_icon=_icon_path,
            large_icon=_icon_path,
        )

        # ── Texture registry ──────────────────────────────────────────────────
        with dpg.texture_registry():
            dpg.add_raw_texture(
                width=_MAP_DW, height=_MAP_DH,
                default_value=np.zeros(_MAP_DW * _MAP_DH * 4, dtype=np.float32),
                format=dpg.mvFormat_Float_rgba,
                tag="map_texture",
            )
            dpg.add_raw_texture(
                width=_CAMERA_ICON_SIZE, height=_CAMERA_ICON_SIZE,
                default_value=_build_camera_icon((220, 220, 220)),
                format=dpg.mvFormat_Float_rgba,
                tag="camera_icon_texture",
            )

        # ── Build palette themes and bind initial ─────────────────────────────
        self.theme.build()
        self.theme.apply(self.theme.palette.name)
        self._sync_map_bg_to_theme()
        self._sync_camera_icon_to_theme()

        # Smaller font for compact toolbars (map overlay checkbox row).
        self._small_font = 0
        _inter_regular = _ASSETS_DIR / "fonts" / "inter" / "Inter-Regular.ttf"
        if _inter_regular.exists():
            with dpg.font_registry():
                self._small_font = dpg.add_font(str(_inter_regular), 13)

        self._shp_dialog = ShapefileDialog(
            confirm_cb=self._on_shp_confirm,
            cancel_cb=self._on_shp_cancel,
            theme=self.theme,
        )
        self._shp_dialog.build(_VP_W, _VP_H)

        self._load_recent_shapefiles()
        self._load_recent_presets()
        self._load_place_unit()
        self._load_map_style()

        self._build_population_popup()
        self._build_seed_popup()
        self._build_new_confirm_popup()
        self._build_close_confirm_popup()
        self._build_advanced_save_popup()
        self._build_opt_popup()
        self._build_partisan_popup()
        self._build_representation_popup()
        self._build_alignment_settings_popup()
        self._build_help_popup()
        self._build_run_details_popup()
        self._build_temperature_panel()
        self._build_score_contrib_panel()
        self._build_ref_line_themes()
        self._build_county_splits_panel()
        self._build_partisanship_panel()
        self._build_win_chance_panel()
        self._build_mm_panel()
        self._build_eg_panel()
        self._build_pb_panel()
        self._build_pg_panel()
        self._build_dem_seats_panel()
        self._build_pp_panel()
        self._build_reock_panel()
        self._build_representation_panel()
        self._build_minority_cohesion_panel()
        self._build_community_congruence_panel()
        self._build_alignment_panel()
        self._build_hc_panel()
        self._build_hsplit_panel()
        self._build_hprop_panel()
        self._build_hcmp_panel()
        self._build_popdev_panel()
        self._build_cut_edges_panel()
        self._build_majority_panel()
        self._build_hinge_panel()
        self._build_district_info_panel()
        self._build_phase_panel()

        # ── Main window ───────────────────────────────────────────────────────
        # The page may scroll on short displays. Map children block wheel
        # propagation locally, so zooming never scrolls the page/header.
        with dpg.window(tag="main_window"):


            with dpg.menu_bar(tag="main_menu_bar"):
                with dpg.menu(label="File"):
                    dpg.add_menu_item(
                        label="New", shortcut="Ctrl+N",
                        callback=self._on_new,
                    )
                    dpg.add_menu_item(
                        label="Open...", shortcut="Ctrl+O",
                        callback=self._on_import_shapefile,
                    )
                    with dpg.menu(label="Open Recent",
                                  tag="file_recent_menu"):
                        if self._recent_shapefiles:
                            for _re in self._recent_shapefiles:
                                dpg.add_menu_item(
                                    label=Path(_re["path"]).name,
                                    callback=self._on_open_recent,
                                    user_data=_re,
                                )
                        else:
                            dpg.add_menu_item(
                                label="(no recent files)", enabled=False,
                            )
                    dpg.add_separator()
                    self._file_save_asgn_item = dpg.add_menu_item(
                        label="Save Assignments", shortcut="Ctrl+S",
                        enabled=False,
                        callback=self._on_file_save_assignments,
                    )
                    self._file_save_metrics_item = dpg.add_menu_item(
                        label="Save District Info",
                        enabled=False,
                        callback=self._on_file_save_metrics,
                    )
                    dpg.add_separator()
                    dpg.add_menu_item(
                        label="Open output directory...",
                        callback=self._on_open_output_dir,
                    )
                    dpg.add_menu_item(
                        label="Check for updates...",
                        callback=self._on_check_updates,
                    )
                    dpg.add_separator()
                    dpg.add_menu_item(
                        label="Close", shortcut="Ctrl+W",
                        callback=self._on_close,
                    )

                with dpg.menu(label="Configuration"):
                    dpg.add_menu_item(
                        label="Population...",
                        callback=lambda: dpg.configure_item(
                            "popup_population", show=True),
                    )
                    dpg.add_menu_item(
                        label="Seed...",
                        callback=lambda: dpg.configure_item(
                            "popup_seed", show=True),
                    )
                    dpg.add_separator()
                    dpg.add_menu_item(
                        label="Annealing Settings...",
                        callback=lambda: dpg.configure_item(
                            "popup_opt", show=True),
                    )
                    dpg.add_menu_item(
                        label="Partisanship Settings...",
                        callback=lambda: dpg.configure_item(
                            "popup_partisan", show=True),
                    )
                    dpg.add_separator()
                    dpg.add_menu_item(label="Save Preset...",
                                      callback=self._on_save_preset)
                    self._preset_apply_item = dpg.add_menu_item(
                        label="Apply Preset...", enabled=False,
                        callback=self._on_apply_preset)
                    dpg.add_menu(label="Apply Recent Preset", enabled=False,
                                 tag="cfg_preset_recent_menu")
                    dpg.add_separator()
                    dpg.add_menu_item(
                        label="Clear All Scores",
                        callback=self._on_clear_scores)
                with dpg.menu(label="Appearance"):
                    dpg.add_text("Theme")
                    self._theme_radio = dpg.add_radio_button(
                        items=["Light", "Dark"],
                        default_value="Light",
                        horizontal=True,
                        callback=self._on_theme_change,
                    )
                    dpg.add_separator()
                    self._limit_plots = dpg.add_checkbox(
                        label="Limit plots to last 10,000 iterations",
                        default_value=True,
                    )
                    self._tooltip(
                        self._limit_plots,
                        "Draw only recent chart history. This does not change the search.",
                    )
                    dpg.add_separator()
                    dpg.add_text("Map Render Interval:")
                    self._map_interval = dpg.add_slider_float(
                        label="sec",
                        default_value=0.75, min_value=0.25, max_value=5.0,
                        format="%.2f s", width=160,
                        callback=lambda s, d: self.state.update(
                            map_render_interval=d),
                    )
                    self._tooltip(
                        self._map_interval,
                        "How often the map redraws during a run. This does not "
                        "change the search.",
                    )
                # Score rows and corresponding metric views use the same group
                # order: geography | misc | partisanship | demographics. Views
                # omits County-Edge Bias (not a score chart) and appends panels
                # with no score row, such as Temperature and District Info.
                with dpg.menu(label="Views"):
                    self._charts_item = dpg.add_menu_item(
                        label="Score and entropy charts", check=True,
                        default_value=False,
                        callback=lambda: dpg.configure_item(
                            "main_charts",
                            show=dpg.get_value(self._charts_item)),
                    )
                    dpg.add_separator()
                    # ── Geography ── (mirrors the Scores menu 1:1)
                    self._panel_cuts_item = dpg.add_menu_item(
                        label="Cut Edges", check=True, default_value=False,
                        callback=self._on_panel_cuts_toggle,
                    )
                    self._panel_hc_item = dpg.add_menu_item(
                        label="Compactness", check=True, default_value=False,
                        callback=self._on_panel_hc_toggle,
                    )
                    self._panel_pp_item = dpg.add_menu_item(
                        label="Polsby-Popper", check=True, default_value=False,
                        callback=self._on_panel_pp_toggle,
                    )
                    self._panel_reock_item = dpg.add_menu_item(
                        label="Reock", check=True, default_value=False,
                        callback=self._on_panel_reock_toggle,
                    )
                    dpg.add_separator()
                    # ── Misc: county, population, least-change ──
                    self._panel_hsplit_item = dpg.add_menu_item(
                        label="County Congruence", check=True, default_value=False,
                        callback=self._on_panel_hsplit_toggle,
                    )
                    self._panel_cs_item = dpg.add_menu_item(
                        label="Classic Splitting", check=True, default_value=False,
                        callback=self._on_panel_cs_toggle,
                    )
                    self._panel_popdev_item = dpg.add_menu_item(
                        label="Population Deviation", check=True, default_value=False,
                        callback=self._on_panel_popdev_toggle,
                    )
                    self._panel_alignment_item = dpg.add_menu_item(
                        label="Plan Continuity", check=True, default_value=False,
                        callback=self._on_panel_alignment_toggle,
                    )
                    dpg.add_separator()
                    # ── Partisanship ──
                    self._panel_mm_item = dpg.add_menu_item(
                        label="Mean-Median", check=True, default_value=False,
                        callback=self._on_panel_mm_toggle,
                    )
                    self._panel_eg_item = dpg.add_menu_item(
                        label="Efficiency Gap", check=True, default_value=False,
                        callback=self._on_panel_eg_toggle,
                    )
                    self._panel_pb_item = dpg.add_menu_item(
                        label="Partisan Bias", check=True, default_value=False,
                        callback=self._on_panel_pb_toggle,
                    )
                    self._panel_pg_item = dpg.add_menu_item(
                        label="Partisan Gini", check=True, default_value=False,
                        callback=self._on_panel_pg_toggle,
                    )
                    self._panel_hprop_item = dpg.add_menu_item(
                        label="Proportionality", check=True, default_value=False,
                        callback=self._on_panel_hprop_toggle,
                    )
                    self._panel_hcmp_item = dpg.add_menu_item(
                        label="Competitiveness", check=True, default_value=False,
                        callback=self._on_panel_hcmp_toggle,
                    )
                    self._panel_seats_item = dpg.add_menu_item(
                        label="Expected Dem Seats", check=True, default_value=False,
                        callback=self._on_panel_seats_toggle,
                    )
                    self._panel_majority_item = dpg.add_menu_item(
                        label="Chance of Majority", check=True, default_value=False,
                        callback=self._on_panel_majority_toggle,
                    )
                    self._panel_hinge_item = dpg.add_menu_item(
                        label="Supermajority/Hinge", check=True, default_value=False,
                        callback=self._on_panel_hinge_toggle,
                    )
                    dpg.add_separator()
                    # ── Demographics ──
                    self._panel_representation_item = dpg.add_menu_item(
                        label="Electoral Opportunity", check=True, default_value=False,
                        callback=self._on_panel_representation_toggle,
                    )
                    self._panel_minority_cohesion_item = dpg.add_menu_item(
                        label="Neighborhood Severance", check=True, default_value=False,
                        callback=self._on_panel_minority_cohesion_toggle,
                    )
                    self._panel_community_congruence_item = dpg.add_menu_item(
                        label="Community Dispersion", check=True,
                        default_value=False,
                        callback=self._on_panel_community_congruence_toggle,
                    )
                    dpg.add_separator()
                    # ── Views-only: panels with no score behind them. Everything
                    # Above this line are panels backed by score metrics.
                    self._panel_temp_item = dpg.add_menu_item(
                        label="Temperature", check=True, default_value=False,
                        callback=self._on_panel_temp_toggle,
                    )
                    self._panel_partisan_item = dpg.add_menu_item(
                        label="Partisanship", check=True, default_value=False,
                        callback=self._on_panel_partisan_toggle,
                    )
                    self._panel_win_chance_item = dpg.add_menu_item(
                        label="Win Chance", check=True, default_value=False,
                        callback=self._on_panel_win_chance_toggle,
                    )
                    self._panel_contrib_item = dpg.add_menu_item(
                        label="Score Contributors", check=True, default_value=False,
                        callback=self._on_panel_contrib_toggle,
                    )
                    self._panel_district_item = dpg.add_menu_item(
                        label="District Info", check=True, default_value=False,
                        callback=self._on_panel_district_toggle,
                    )
                    self._panel_phase_item = dpg.add_menu_item(
                        label="Comet Plot", check=True, default_value=False,
                        callback=self._on_panel_phase_toggle,
                    )

                with dpg.menu(label="Help"):
                    dpg.add_menu_item(
                        label="Open Help...",
                        callback=lambda: dpg.configure_item("popup_help", show=True),
                    )

                with dpg.menu(label="Advanced", tag="menu_debug"):
                    self._ensemble_item = dpg.add_menu_item(
                        label="Batch Runs...", enabled=False,
                        callback=self._on_open_ensemble)
                    dpg.add_separator()
                    self._hot_start_load_item = dpg.add_menu_item(
                        label="Start from Plan...",
                        callback=self._on_load_hot_start,
                    )
                    self._tooltip(
                        self._hot_start_load_item,
                        "Start from a CSV or the current map, with optional district locks.",
                    )
                    self._hot_start_clear_item = dpg.add_menu_item(
                        label="Clear Starting Plan",
                        enabled=False,
                        callback=self._on_clear_hot_start,
                    )
                    self._tooltip(
                        self._hot_start_clear_item,
                        "Clear the loaded plan. Mosaic will generate a starting "
                        "map for the next run.",
                    )

                    dpg.add_separator()
                    self._fine_tune_item = dpg.add_menu_item(
                        label="Fine Tune",
                        check=True, default_value=False, enabled=False,
                        callback=self._on_fine_tune_toggle,
                    )
                    self._tooltip(
                        self._fine_tune_item,
                        "Start each new run from the map on screen and apply "
                        "refinement settings. Cannot be used with Start from Plan.",
                    )
                    self._fine_tune_clear_item = dpg.add_menu_item(
                        label="Stop Fine-Tuning",
                        enabled=False,
                        callback=lambda: self._clear_fine_tune(),
                    )
                    self._tooltip(
                        self._fine_tune_clear_item,
                        "Turn off Fine Tune and restore the previous search settings.",
                    )

                    dpg.add_separator()
                    self._renumber_after_run = dpg.add_menu_item(
                        label="Renumber districts after run",
                        check=True, default_value=True,
                        callback=self._on_renumber_after_run_toggle,
                    )
                    self._tooltip(
                        self._renumber_after_run,
                        "After a run, reorder district numbers geographically. "
                        "Boundaries and colors do not change.",
                    )
                    self._renumber_options_item = dpg.add_menu_item(
                        label="Renumber options...",
                        callback=self._on_open_renumber_options,
                    )
                    self._tooltip(
                        self._renumber_options_item,
                        "Choose the rule used to assign district numbers.",
                    )

            # ── Three columns: setup, map, scores ─────────────────────────
            # The order a person works in: say what map and what goals, watch
            # the run, then reach for an individual score. Spare width goes to
            # the centre, spare height to the map.
            with dpg.group(horizontal=True, tag="main_columns"):

                # ── Left: Map, Goals, Results ─────────────────────────────
                with dpg.child_window(width=_SETUP_W, border=False,
                                      tag="setup_column"):
                    # auto_resize_y: the panel is as tall as its rows, so it
                    # never shows a scrollbar the column has room to avoid.
                    with dpg.child_window(width=-1, auto_resize_y=True,
                                          border=True, tag="panel_map_setup"):
                        with self._heading_row("Map"):
                            dpg.add_button(
                                label="Open shapefile\u2026",
                                callback=self._on_import_shapefile,
                            )
                        dpg.add_separator()
                        self._build_setup_line(
                            "Loaded", lambda: self.theme.text(
                                "no map", "muted"), "_shp_info")
                        self._build_setup_line(
                            "Start from", lambda: self.theme.text(
                                "Random partition", "body"), "_hot_start_info")
                        self._start_mode_tooltip = None
                        # Plan Continuity's reference plan: which file is
                        # loaded, or "none". Loading one is what makes that
                        # score available, so its status sits with the map.
                        self._build_setup_line(
                            "Reference plan", lambda: self.theme.text(
                                "none", "muted"), "_alignment_info")
                        # The "county" column can hold any place unit; its name
                        # follows into the goal and the score phrases.
                        self._build_setup_line(
                            "Places are", lambda: dpg.add_input_text(
                                default_value=self._place_unit, width=-1,
                                callback=self._on_place_unit_change),
                            "_place_unit_input")
                        self._tooltip(
                            self._place_unit_input,
                            "What the column you loaded as \"county\" actually "
                            "holds. Redistricting one county with city names in "
                            "that column? Set this to \"cities\" and the goal "
                            "reads \"Keep cities whole\". Wording only -- the "
                            "score and its settings do not change.")
                        # Two equal columns that follow the panel's width.
                        with dpg.table(header_row=False, width=-1,
                                       policy=dpg.mvTable_SizingStretchSame,
                                       borders_innerH=False, borders_outerH=False,
                                       borders_innerV=False, borders_outerV=False):
                            dpg.add_table_column()
                            dpg.add_table_column()
                            with dpg.table_row():
                                with dpg.group():
                                    self.theme.text("Districts", "subheading")
                                    self._num_districts = dpg.add_input_int(
                                        label="##dist", default_value=5,
                                        min_value=2, max_value=500,
                                        min_clamped=True, max_clamped=True,
                                        width=-1, step=0,
                                        callback=self._on_num_districts_change,
                                    )
                                    self._tooltip(self._num_districts,
                                                  "Number of districts in the plan.")
                                with dpg.group():
                                    self.theme.text("Iterations", "subheading")
                                    self._iterations = dpg.add_input_int(
                                        label="##iter", default_value=5000,
                                        min_value=1, max_value=1_000_000,
                                        width=-1, step=0,
                                    )
                                    self._tooltip(
                                        self._iterations,
                                        "Maximum number of proposed map changes. "
                                        "More iterations take longer but do not "
                                        "guarantee a better map.")

                    dpg.add_spacer(height=8)
                    with dpg.child_window(width=-1, auto_resize_y=True,
                                          border=True, tag="panel_goals"):
                        self._build_goals_column()

                    dpg.add_spacer(height=8)
                    with dpg.child_window(width=-1, height=-1, border=True,
                                          tag="panel_results"):
                        # The button rides in the heading row: a row of its
                        # own under the tiles cost the height that made this
                        # panel scroll on a 925px window.
                        with self._heading_row("Results"):
                            dpg.add_button(label="Run details\u2026",
                                           callback=lambda: dpg.configure_item(
                                               "popup_run_details", show=True))
                        dpg.add_separator()
                        self._build_result_tiles()

                # ── Centre: map toolbar, map, run strip ───────────────────
                # no_scroll_with_mouse: the column is sized to fit exactly,
                # and a wheel turn over it must never slide the toolbar out of
                # view if a frame's measurements are a few pixels out.
                with dpg.child_window(width=-1, border=False,
                                      no_scrollbar=True,
                                      no_scroll_with_mouse=True,
                                      tag="map_column"):
                    self._build_map_toolbar()
                    with dpg.child_window(
                        height=_MAP_H, width=-1, border=True,
                        tag="map_container", no_scrollbar=True,
                        no_scroll_with_mouse=True,
                    ):
                        # The spacer centres the map when it is narrower than
                        # the box (see MapMixin._sync_map_size).
                        with dpg.group(horizontal=True):
                            dpg.add_spacer(width=1, tag="map_center_pad",
                                           show=False)
                            self._build_map_canvas()
                    # Required wherever the basemap is drawn.
                    # In a group so its measured height is the whole line:
                    # a bare text item reports its glyph height, not the frame
                    # padding Dear PyGui lays it out with.
                    self._build_map_style_row()
                    with dpg.group(tag="basemap_credit", show=False) as credit:
                        self._basemap_credit_text = self.theme.text(
                            B.ATTRIBUTION, "muted")
                    self._basemap_credit = credit
                    self._build_run_strip()
                    # Score and Entropy are hidden until step 3 turns them into
                    # Visuals cards, so the map gets the height; Views > Score
                    # and entropy charts brings them back.
                    with dpg.group(horizontal=True, tag="main_charts",
                                   show=False):
                        with dpg.group():
                            self._hint(self.theme.text("Score", "heading"),
                                       "score")
                            with dpg.plot(height=_HALF_PLOT_H,
                                          tag="score_half_plot",
                                          width=_HALF_PLOT_W, no_menus=True):
                                dpg.add_plot_legend()
                                dpg.add_plot_axis(dpg.mvXAxis, label="Iteration",
                                                  tag="score_x")
                                with dpg.plot_axis(dpg.mvYAxis, label="Score",
                                                   tag="score_y"):
                                    dpg.add_line_series([], [], label="Score",
                                                        tag="score_series")
                        with dpg.group():
                            self._hint(self.theme.text("Entropy", "heading"),
                                       "entropy")
                            with dpg.plot(height=_HALF_PLOT_H, width=-1,
                                          no_menus=True,
                                          tag="entropy_half_plot"):
                                dpg.add_plot_legend()
                                dpg.add_plot_axis(dpg.mvXAxis, label="Iteration",
                                                  tag="acc_x")
                                with dpg.plot_axis(dpg.mvYAxis, label="Rate (%)",
                                                   tag="acc_y"):
                                    dpg.add_line_series([], [], label="Accepted",
                                                        tag="acc_series")

                # ── Right: every score, grouped, one line each ────────────
                with dpg.child_window(width=_SCORES_W, border=True,
                                      tag="scores_column"):
                    self._build_scores_column()

        self._refresh_recent_presets_menu()
        dpg.set_primary_window("main_window", True)
        dpg.setup_dearpygui()

        self.map_view = MapView("map_texture", _MAP_DW, _MAP_DH)
        self.map_view.style = self._map_style
        self.map_view.line_colors = default_colors(self.theme.palette)
        self._sync_basemap_to_fill()
        # MapView is constructed after the initial theme application, so copy
        # the active background into it before a shapefile builds its LUT.
        self._sync_map_bg_to_theme()

    # ── Left column pieces ────────────────────────────────────────────────────

    @contextmanager
    def _heading_row(self, label: str):
        """A panel heading on the left; whatever the block adds on the right.

        The right-hand cell fits its content and the heading takes the rest,
        so nothing here depends on how wide the panel happens to be.
        """
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit,
                       borders_innerH=False, borders_outerH=False,
                       borders_innerV=False, borders_outerV=False, width=-1):
            dpg.add_table_column(width_stretch=True, init_width_or_weight=1.0)
            dpg.add_table_column(width_fixed=True)
            with dpg.table_row():
                self.theme.text(label, "heading")
                with dpg.group(horizontal=True):
                    yield

    def _build_setup_line(self, label: str, factory, attr: str) -> None:
        """A `muted` label on the left, its value on the right.

        Dear PyGui has no right alignment, so the value sits in a fixed column
        of a two-column table, which lines the values up with each other.
        """
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit,
                       borders_innerH=False, borders_outerH=False,
                       borders_innerV=False, borders_outerV=False, width=-1):
            dpg.add_table_column(width_fixed=True, init_width_or_weight=96)
            dpg.add_table_column(width_stretch=True, init_width_or_weight=1.0)
            with dpg.table_row():
                self.theme.text(label, "muted")
                setattr(self, attr, factory())

    def _build_result_tiles(self) -> None:
        """Four stat tiles: a `muted` label over the value in the title size.

        Replaces nine loose "Label: value" lines that read at one weight. The
        four here are what a person watches; the rest moved to Run details.
        """
        value_font = self.theme.value_font()

        def tile(label: str, attr: str, note_attr: str | None = None) -> None:
            with dpg.group():
                self.theme.text(label, "muted")
                item = self.theme.text("--", "body")
                setattr(self, attr, item)
                if value_font:
                    dpg.bind_item_font(item, value_font)
                if note_attr:
                    setattr(self, note_attr, self.theme.text("", "muted"))
                else:
                    dpg.add_spacer(height=2)

        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchSame,
                       borders_innerH=True, borders_outerH=False,
                       borders_innerV=True, borders_outerV=False, width=-1):
            dpg.add_table_column()
            dpg.add_table_column()
            with dpg.table_row():
                tile("Score", "_score_txt")
                tile("Best", "_best_txt", "_best_note")
            with dpg.table_row():
                tile("Time", "_time_txt")
                tile("Speed", "_ips_txt", "_ips_note")
        dpg.set_value(self._ips_note, "iterations / s")

    def _build_run_details_popup(self) -> None:
        """The readouts a person checks rather than watches."""
        with self._dialog(
            "Run details", "popup_run_details", (360, 220), show=False,
            primary=("Close", lambda: dpg.configure_item(
                "popup_run_details", show=False)),
        ):
            self._status_txt = dpg.add_text("Status: Idle", wrap=330)
            dpg.add_separator()
            self._temp_txt = dpg.add_text("Temperature: --")
            self._acc_txt = dpg.add_text("Entropy: --")
            self._succ_txt = dpg.add_text("Accepted steps: --")
            self._flip_txt = dpg.add_text("Flip rate: 0.0%")

    # ── Centre column pieces ──────────────────────────────────────────────────

    def _build_run_strip(self) -> None:
        """One primary action, two secondary, a tiled progress bar, and where
        the run has got to."""
        with dpg.child_window(width=-1, height=74, border=True,
                              tag="run_strip", no_scrollbar=True):
            with dpg.group(horizontal=True):
                # One nudge per view: whichever of Start / Pause / Resume is
                # the next step. _sync_run_strip keeps its label in step.
                self._run_btn = dpg.add_button(label="Start", width=96,
                                               callback=self._on_run_strip)
                dpg.bind_item_theme(self._run_btn, self.theme.nudge_theme)
                self._reset_btn = dpg.add_button(label="Reset", width=76,
                                                 callback=self._on_reset)
                self._revert_btn = dpg.add_button(
                    label="Revert to best", width=120,
                    callback=self._on_revert_to_best, enabled=False)
                dpg.add_spacer(width=4)
                with dpg.drawlist(width=_RUN_TILES * 13, height=16,
                                  tag="run_tiles"):
                    for i in range(_RUN_TILES):
                        dpg.draw_rectangle(
                            (i * 13, 2), (i * 13 + 10, 14), fill=(0, 0, 0, 0),
                            color=(0, 0, 0, 0), tag=f"run_tile_{i}")
            with dpg.group(horizontal=True):
                self._iter_txt = self.theme.text("0 / 0", "muted")
                dpg.add_spacer(width=6)
                self._eta_txt = self.theme.text("--", "body")
                if self.theme.value_font():
                    pass   # the ETA uses body weight; semibold is not separate
            # Start and Pause were two buttons that swapped which was greyed.
            # They are one button now, so this holds the old pause handler.
            self._pause_btn = self._run_btn

    def _on_run_strip(self) -> None:
        """The single primary button: start, pause or resume by run state."""
        from mosaic.gui.state import AlgorithmStatus
        status = self.state.get("status")[0]
        if status in (AlgorithmStatus.RUNNING, AlgorithmStatus.PAUSED,
                      AlgorithmStatus.PARTITIONING):
            self._on_pause()
        else:
            self._on_run()

    def _build_map_toolbar(self) -> None:
        """Fill, overlays, fill opacity and export, directly above the map.

        Two plain rows rather than one table row: the controls do not fit one
        line beside a 400px scores column at 1440px, and Dear PyGui does not
        wrap a row. The group's measured height feeds the map's.
        """
        with dpg.group(tag="map_toolbar"):
            with dpg.group(horizontal=True):
                self.theme.text("Fill", "muted", tag="map_fill_label")
                self._fill_combo = dpg.add_combo(
                    items=self._fill_labels(), default_value=_FILL_NONE,
                    width=190, callback=self._on_fill_combo)
                self._tooltip(
                    self._fill_combo,
                    "Color the map by one measure. Unavailable choices show "
                    "which data they require.")
                with dpg.group(horizontal=True, tag="map_overlay_controls"):
                    self._basemap_chk = dpg.add_checkbox(
                        label="Basemap", default_value=True,
                        callback=self._on_basemap_toggle)
                    self._tooltip(
                        self._basemap_chk,
                        "Draw roads, water and place names from OpenFreeMap "
                        "under the fill. Needs a connection the first time a "
                        "map is opened; tiles are kept on disk after that.")
                    self._county_overlay = dpg.add_checkbox(
                        label=self._place_unit.capitalize(),
                        default_value=False,
                        callback=self._on_county_overlay_toggle)
                    self._tooltip(self._county_overlay,
                                  "Draw place boundaries over the map.")
                    self._precinct_overlay = dpg.add_checkbox(
                        label="Precincts", default_value=False,
                        callback=self._on_precinct_overlay_toggle)
                    self._tooltip(self._precinct_overlay,
                                  "Draw precinct boundaries over the map.")
                    self._show_labels = dpg.add_checkbox(
                        label="Labels", default_value=False,
                        callback=self._on_labels_toggle)
                    self._tooltip(self._show_labels, "Show district numbers.")
            with dpg.table(header_row=False, width=-1,
                           policy=dpg.mvTable_SizingFixedFit,
                           borders_innerH=False, borders_outerH=False,
                           borders_innerV=False, borders_outerV=False):
                dpg.add_table_column(width_stretch=True,
                                     init_width_or_weight=1.0)
                dpg.add_table_column(width_fixed=True)
                with dpg.table_row():
                    self._splits_view = dpg.add_checkbox(
                        label="Splits", default_value=False, enabled=False,
                        callback=self._on_splits_view_toggle)
                    with dpg.group(horizontal=True, tag="map_save_controls"):
                        self._cam_btn = dpg.add_image_button(
                            "camera_icon_texture", callback=self._on_save_map,
                            width=13, height=13)
                        with dpg.tooltip(self._cam_btn):
                            dpg.add_text("Save a PNG of the full map.")
                        self._more_btn = dpg.add_button(
                            label="Export\u2026",
                            callback=self._on_advanced_save_open)
                        with dpg.tooltip(self._more_btn):
                            dpg.add_text("Save the full map as PNG or PDF.")
                        self._save_spinner = dpg.add_loading_indicator(
                            style=0, radius=2.0, show=False,
                            color=self.theme.color("body"),
                            secondary_color=self.theme.color("muted"))
            self._tooltip(
                self._splits_view,
                "Highlight places assigned to more than one district. "
                "Requires a place column.")

    def _build_map_style_row(self) -> None:
        """Width and colour for each kind of line, and the fill's opacity.

        Two rows under the map. A width of 0 hides that line; a colour swatch
        opens a picker. Everything here applies to the screen and to PNG and
        PDF exports, and is saved in .mosaic/, not in presets.
        """
        self._style_width: dict = {}
        self._style_color: dict = {}
        self._style_label: dict = {}
        defaults = default_colors(self.theme.palette)

        def line(kind: str, label: str, hint: str) -> None:
            self._style_label[kind] = self.theme.text(label, "muted")
            self._style_width[kind] = dpg.add_input_float(
                default_value=self._map_style.width(kind), min_value=0.0,
                max_value=MAX_WIDTH, min_clamped=True, max_clamped=True,
                step=0, format="%.1f", width=48, on_enter=False,
                callback=self._on_map_style)
            self._tooltip(self._style_width[kind],
                          f"{hint} Width in pixels, 0 to 8. 0 hides the line.")
            self._style_color[kind] = dpg.add_color_edit(
                default_value=self._map_style.color(kind, defaults) + (255,),
                no_inputs=True, no_label=True, no_alpha=True, no_tooltip=True,
                callback=self._on_map_style_color, user_data=kind)

        with dpg.group(tag="map_style_row"):
            with dpg.group(horizontal=True):
                self.theme.text("Map style", "subheading")
                line("district", "District borders",
                     "The lines between districts.")
                line("place", line_label(self._place_unit),
                     "The lines between places in the place column.")
            with dpg.group(horizontal=True):
                line("precinct", "Precinct lines",
                     "The lines between precincts.")
                self.theme.text("Fill opacity", "muted")
                self._fill_opacity = dpg.add_slider_int(
                    default_value=int(round(self._fill_opacity_value * 100)),
                    min_value=0, max_value=100, format="%d%%",
                    width=130, clamped=True, callback=self._on_fill_opacity)
                self._tooltip(
                    self._fill_opacity,
                    "How solid the fill is: 45% by default over a basemap, "
                    "100% without one. Lines and labels always stay solid.")
                reset = dpg.add_button(label="Reset",
                                       callback=self._on_map_style_reset)
                dpg.bind_item_theme(reset, self.theme.antinudge_theme)
                self._tooltip(reset, "Put widths, colours and opacity back "
                                     "to their defaults.")

    def _build_map_canvas(self):
        if not self._map_native_input:
            with dpg.drawlist(width=_MAP_DW, height=_MAP_DH, tag="map_canvas"):
                dpg.draw_image("map_texture", (0, 0),
                               (_MAP_DW, _MAP_DH), tag="map_image")
            with dpg.handler_registry():
                dpg.add_mouse_wheel_handler(callback=self._on_map_wheel)
            return

        # macOS only: ImPlot supplies fractional wheel input; DPG's standalone
        # mouse-wheel callback is integer-only. No axes/chrome are shown.
        with dpg.theme() as map_theme:
            with dpg.theme_component(dpg.mvPlot):
                for style in (dpg.mvPlotStyleVar_PlotPadding, dpg.mvPlotStyleVar_FitPadding):
                    dpg.add_theme_style(style, 0, 0, category=dpg.mvThemeCat_Plots)
                dpg.add_theme_style(dpg.mvPlotStyleVar_PlotBorderSize, 0,
                                    category=dpg.mvThemeCat_Plots)
                dpg.add_theme_color(dpg.mvPlotCol_PlotBg, (0, 0, 0, 0),
                                    category=dpg.mvThemeCat_Plots)
        with dpg.plot(width=_MAP_DW, height=_MAP_DH, tag="map_canvas",
                      no_title=True, no_menus=True, no_box_select=True,
                      no_mouse_pos=True, no_frame=True, no_inputs=True,
                      fit_button=dpg.mvMouseButton_Left, zoom_rate=0.025):
            for axis, tag, limits in ((dpg.mvXAxis, "map_x", (0, 1)),
                                      (dpg.mvYAxis, "map_y", (-1, 0))):
                dpg.add_plot_axis(axis, tag=tag, no_label=True, no_gridlines=True,
                                  no_tick_marks=True, no_tick_labels=True,
                                  no_initial_fit=True)
                dpg.set_axis_limits(tag, *limits)
                dpg.set_axis_limits_constraints(tag, *limits)
                dpg.set_axis_zoom_constraints(tag, 1 / MAX_ZOOM, 1)
            dpg.add_image_series("map_texture", (0, -1), (1, 0),
                                 parent="map_y", tag="map_image")
        dpg.bind_item_theme("map_canvas", map_theme)

    def _update_window_layout(self):
        if not self._min_width_set and dpg.get_frame_count() >= 2:
            # The OS frame contributes to viewport width. Correct the minimum
            # after measuring it, without overriding the user's window size.
            frame = dpg.get_viewport_width() - dpg.get_viewport_client_width()
            if dpg.get_viewport_client_width() > 0 and frame >= 0:
                dpg.set_viewport_min_width(_MIN_CLIENT_W + frame)
                if dpg.get_viewport_client_width() < _MIN_CLIENT_W:
                    dpg.set_viewport_width(_MIN_CLIENT_W + frame)
                self._min_width_set = True
        client_w = dpg.get_viewport_client_width()
        client_h = dpg.get_viewport_client_height()
        if client_w > 0 and client_h > 0:
            self._shp_dialog.fit_to_viewport(client_w, client_h)
            self._update_columns(client_w, client_h)
        self._update_map_fill_width()
        self._sync_map_size()

    def _update_columns(self, client_w: int, client_h: int) -> None:
        """Size the three columns and the map box from the window.

        Widths come from mosaic.gui.layout.column_widths, so all three fit the
        window instead of the outer two being constants the window may be too
        narrow for. Heights are set explicitly too: the columns end at the
        window's bottom edge, and the map box takes whatever the toolbar, the
        run strip and (when shown) the charts leave, measured rather than
        assumed, so nothing below the map can be pushed out of the window.
        """
        sp = self.theme.palette.spacing
        setup_w, map_w, scores_w = column_widths(
            client_w, 2 * sp.window_padding[0] + 2 * sp.item_spacing[0])
        menu_h = dpg.get_item_state("main_menu_bar").get("rect_size", (0, 24))[1]
        column_h = max(240, int(client_h - menu_h - 2 * sp.window_padding[1]))
        for tag, width in (("setup_column", setup_w), ("map_column", map_w),
                           ("scores_column", scores_w)):
            cfg = dpg.get_item_configuration(tag)
            if cfg["width"] != width or cfg["height"] != column_h:
                dpg.configure_item(tag, width=width, height=column_h)
        self._columns = (setup_w, map_w, scores_w)

        top = dpg.get_item_state("map_container")["pos"][1]
        if top <= 0 or dpg.get_item_rect_size("run_strip")[1] <= 0:
            return                      # not laid out yet; next frame
        below = [dpg.get_item_rect_size(tag)[1] for tag in self._BELOW_MAP
                 if dpg.does_item_exist(tag) and dpg.is_item_shown(tag)]
        height = map_box_height(column_h, top, below, sp.item_spacing[1])
        if dpg.get_item_configuration("map_container")["height"] != height:
            dpg.configure_item("map_container", height=height)

    # Blocks under the map, top to bottom. Each one that is shown takes its
    # measured height out of the map's.
    _BELOW_MAP = ("basemap_credit", "map_style_row", "run_strip", "main_charts",
                  "visuals_row")

    def _update_map_fill_width(self):
        # Image dimensions exclude frame padding; match the adjacent text button.
        button_height = dpg.get_item_rect_size(self._more_btn)[1]
        if button_height > 0:
            icon_size = max(1, int(button_height - 2 * self.theme.palette.spacing.frame_padding[1]))
            if dpg.get_item_configuration(self._cam_btn)["height"] != icon_size:
                dpg.configure_item(self._cam_btn, width=icon_size, height=icon_size)
        # The Fill combo takes what the overlay checkboxes leave on its row.
        available = dpg.get_item_rect_size("map_container")[0]
        fixed = [dpg.get_item_rect_size(tag)[0] for tag in
                 ("map_fill_label", "map_overlay_controls")]
        if available <= 0 or any(width <= 0 for width in fixed):
            return
        spacing = self.theme.palette.spacing.item_spacing[0]
        width = max(120, min(225, int(available - sum(fixed) - 3 * spacing)))
        if dpg.get_item_configuration(self._fill_combo)["width"] != width:
            dpg.configure_item(self._fill_combo, width=width)
