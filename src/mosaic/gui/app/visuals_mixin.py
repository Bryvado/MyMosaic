"""Visuals: chart cards that dock in a row under the run strip or float.

Each card is the panel window that already held the chart, so the chart code
and its "only update while shown" gating are untouched. This mixin gives the
window a card header (grip, title, pin, close, one line), decides where it
sits from mosaic.gui.visuals, and saves that in .mosaic/visuals.json.

A docked card is positioned and sized by the app into a slot of the row; a
floating card keeps the place and size the person gave it.
"""
from __future__ import annotations

import time

from mosaic import likely_seats as LS
from mosaic.gui import visuals as V

from ._common import _SETTINGS_DIR, dpg, log, np

_VISUALS_FILE = _SETTINGS_DIR / "visuals.json"

# Views menu items that show the same windows as a visual.
_MENU_ITEMS = {
    "temperature": "_panel_temp_item",
    "party_share": "_panel_partisan_item",
    "win_chance": "_panel_win_chance_item",
    "contrib": "_panel_contrib_item",
    "district_table": "_panel_district_item",
    "comet": "_panel_phase_item",
}
# Panels that already open with their own one-line description.
_OWN_LINE = {"panel_district_info", "panel_score_contrib"}
# Panels whose width is fixed by their content: they float, never dock.
_FIXED_WIDTH = {"panel_score_contrib"}


class VisualsMixin:

    # ── State ────────────────────────────────────────────────────────────────

    def _init_visuals(self) -> None:
        self._visuals = V.VisualsState()
        self._visual_cards: dict[str, dict] = {}
        self._visual_placed: dict[str, tuple] = {}
        self._visual_drag: tuple | None = None     # (key, window pos at start)
        self._visual_themes: dict[str, int] = {}
        self._visual_needs_sig = None
        self._visuals_blocked = False
        self._likely_sig = None
        self._likely_at = 0.0
        self._likely_series: list = []
        self._likely_themes: list = []
        self._visuals_hint = 0
        self._load_visuals()

    def _load_visuals(self) -> None:
        import json
        try:
            data = json.loads(_VISUALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return                      # missing or unreadable: default row
        self._visuals = V.VisualsState.from_json(data)

    def _save_visuals(self) -> None:
        import json
        try:
            _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            _VISUALS_FILE.write_text(
                json.dumps(self._visuals.to_json(), indent=2), encoding="utf-8")
        except Exception:
            log.debug("could not save visuals", exc_info=True)

    # ── Build ────────────────────────────────────────────────────────────────

    def _build_visual_panels(self) -> None:
        """The charts that had no window of their own: Score, Entropy and the
        new Likely seats."""
        with dpg.window(tag="panel_score", show=False, width=300, height=240):
            with dpg.plot(height=-1, width=-1, tag="score_half_plot",
                          no_menus=True):
                dpg.add_plot_axis(dpg.mvXAxis, label="Iteration", tag="score_x")
                with dpg.plot_axis(dpg.mvYAxis, label="Score", tag="score_y"):
                    dpg.add_line_series([], [], label="Score",
                                        tag="score_series")
        with dpg.window(tag="panel_entropy", show=False, width=300, height=240):
            with dpg.plot(height=-1, width=-1, tag="entropy_half_plot",
                          no_menus=True):
                dpg.add_plot_axis(dpg.mvXAxis, label="Iteration", tag="acc_x")
                with dpg.plot_axis(dpg.mvYAxis, label="Rate (%)", tag="acc_y"):
                    dpg.add_line_series([], [], label="Accepted",
                                        tag="acc_series")
        with dpg.window(tag="panel_likely_seats", show=False, width=300,
                        height=240):
            self._likely_note = self.theme.text("", "muted")
            with dpg.plot(height=-1, width=-1, tag="likely_plot", no_menus=True):
                # The card's own line names both axes; labels would take a
                # third of a docked card's chart.
                dpg.add_plot_axis(dpg.mvXAxis, tag="likely_x", no_label=True)
                with dpg.plot_axis(dpg.mvYAxis, tag="likely_y", no_label=True):
                    # Republican majority, tie, Democratic majority.
                    self._likely_series = [
                        dpg.add_bar_series([], [], weight=0.85)
                        for _ in range(3)]

    def _build_visuals_row(self) -> None:
        with dpg.group(tag="visuals_row"):
            with dpg.group(horizontal=True):
                self.theme.text("Visuals", "heading")
                self._add_visual_btn = dpg.add_button(
                    label="Add visual...", callback=self._open_visual_picker)
                self._visuals_hint = self.theme.text("", "muted")
            # Reserves the row; the docked cards are windows placed over it.
            dpg.add_child_window(tag="visuals_slots", height=V.ROW_H, width=-1,
                                 border=False, no_scrollbar=True,
                                 no_scroll_with_mouse=True)
        with dpg.window(tag="visual_picker", popup=True, show=False,
                        no_title_bar=True, autosize=True, no_move=True):
            pass

    def _wrap_visual_cards(self) -> None:
        """Give each visual's window the card header and card behaviour."""
        self._rebuild_visual_themes()
        for v in V.CATALOG:
            if not v.dockable and v.panel not in _FIXED_WIDTH:
                continue                # batch windows keep their own frame
            children = dpg.get_item_children(v.panel, 1) or []
            kwargs = {"before": children[0]} if children else {}
            with dpg.group(parent=v.panel, **kwargs):
                with dpg.group(horizontal=True) as head:
                    grip = dpg.add_button(label="::")
                    self._tooltip(grip, "Drag to move this visual. Drop it on "
                                        "the Visuals row to dock it.")
                    self.theme.text(v.title, "heading")
                    pin = dpg.add_button(label="Pin", user_data=v.key,
                                         callback=self._on_visual_pin)
                    self._tooltip(pin, "Pinned: keeps its place and size, and "
                                       "cannot be dragged.")
                    close = dpg.add_button(label="x", user_data=v.key,
                                           callback=self._on_visual_close)
                    self._tooltip(close, "Remove this visual.")
                line = (0 if v.panel in _OWN_LINE
                        else self.theme.text(v.shows, "muted", wrap=0))
            cfg = {"no_title_bar": True, "no_move": True, "no_collapse": True,
                   "no_focus_on_appearing": True}
            if v.panel not in _FIXED_WIDTH:
                cfg["min_size"] = [V.MIN_W, V.MIN_H]
            if v.panel not in _OWN_LINE:
                # A chart fills its card; a table or a list scrolls in it.
                cfg.update(no_scrollbar=True, no_scroll_with_mouse=True)
            dpg.configure_item(v.panel, **cfg)
            self._visual_cards[v.key] = {"grip": grip, "pin": pin, "line": line,
                                         "head": head}
            dpg.bind_item_theme(head, self._visual_themes["head"])

    def _rebuild_visual_themes(self) -> None:
        """Docked: window-bg, radius-sm. Floating: popup-bg, a 1px border and
        radius-lg, so it reads as sitting above the screen."""
        p = self.theme.palette
        old = list(self._visual_themes.values()) + list(self._likely_themes)

        def window(bg, rounding, border):
            with dpg.theme() as t:
                with dpg.theme_component(dpg.mvAll):
                    dpg.add_theme_color(dpg.mvThemeCol_WindowBg, bg)
                    dpg.add_theme_color(dpg.mvThemeCol_Border, p.border)
                    dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, rounding)
                    dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, border)
                    # Dear PyGui's plots refuse to go under 200 x 150, which
                    # is more than a card at the minimum size has to give.
                    dpg.add_theme_style(dpg.mvPlotStyleVar_PlotMinSize, 60, 40,
                                        category=dpg.mvThemeCat_Plots)
            return t

        def text(color):
            with dpg.theme() as t:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Text, color)
            return t

        def bar(color):
            with dpg.theme() as t:
                with dpg.theme_component(dpg.mvBarSeries):
                    dpg.add_theme_color(dpg.mvPlotCol_Fill, color,
                                        category=dpg.mvThemeCat_Plots)
            return t

        self._visual_themes = {
            "docked": window(p.window_bg, p.spacing.child_rounding, 0),
            "floating": window(p.popup_bg, p.spacing.popup_rounding, 1),
            "pin_on": text(p.check_mark),
            "pin_off": text(p.muted),
        }
        # Tighter than the app's spacing, so the longest title and its
        # buttons fit a card in a three-card row at 1440 px.
        with dpg.theme() as head:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 4, 4)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 3, 3)
        self._visual_themes["head"] = head
        for card in self._visual_cards.values():
            dpg.bind_item_theme(card["head"], head)
        self._likely_themes = [bar(p.gop), bar(p.muted), bar(p.dem)]
        for series, theme in zip(self._likely_series, self._likely_themes,
                                 strict=True):
            dpg.bind_item_theme(series, theme)
        self._visual_placed.clear()     # rebind card themes on the next frame
        for theme in old:
            if dpg.does_item_exist(theme):
                dpg.delete_item(theme)

    # ── What the map has ─────────────────────────────────────────────────────

    def _visual_has(self) -> dict:
        has = {"batch": getattr(self, "_ens_writer", None) is not None}
        runner = self.runner
        if runner is not None and runner.gdf is not None:
            has["elections"] = bool(runner.election_arrays)
        return has

    # ── Picker ───────────────────────────────────────────────────────────────

    def _open_visual_picker(self) -> None:
        dpg.delete_item("visual_picker", children_only=True)
        has = self._visual_has()
        for v in V.CATALOG:
            need = V.missing(v, has)
            with dpg.group(horizontal=True, parent="visual_picker"):
                dpg.add_selectable(
                    label=v.title, width=190, user_data=v.key,
                    default_value=self._visual_shown(v.key),
                    enabled=need is None, callback=self._on_visual_pick)
                self.theme.text(need or v.shows,
                                "warning" if need else "muted")
        x, y = dpg.get_item_rect_min(self._add_visual_btn)
        rows = len(V.CATALOG)
        line = dpg.get_item_rect_size(self._add_visual_btn)[1] or 20
        height = rows * (line + 2) + 20
        dpg.configure_item("visual_picker", show=True,
                           pos=[int(x), max(8, int(y - height - 4))])

    def _on_visual_pick(self, sender, _value, key) -> None:
        dpg.configure_item("visual_picker", show=False)
        self._set_visual(key, not self._visual_shown(key))

    def _visual_shown(self, key: str) -> bool:
        v = V.BY_KEY[key]
        if key in self._visual_cards:
            return self._visuals.is_shown(key)
        return dpg.does_item_exist(v.panel) and dpg.is_item_shown(v.panel)

    def _set_visual(self, key: str, show: bool) -> None:
        """Add or remove a visual. Adding one the map cannot feed does
        nothing; the picker says what it needs."""
        v = V.BY_KEY[key]
        if show and V.missing(v, self._visual_has()):
            self._sync_visual_menu()
            return
        if key not in self._visual_cards:          # batch windows
            if show:
                (self._on_open_ens_hist if key == "batch_hist"
                 else self._on_open_ens_scatter)()
            elif dpg.does_item_exist(v.panel):
                dpg.configure_item(v.panel, show=False)
            return
        if show:
            row = self._visuals_row_rect()
            cap = V.capacity(row[2]) if row and v.panel not in _FIXED_WIDTH else 0
            n = len(self._visuals.floating)
            self._visuals.add(key, cap, float_at=(140 + 24 * n, 120 + 24 * n))
            if key in self._visuals.floating and v.panel in _FIXED_WIDTH:
                self._visuals.floating[key]["size"] = [
                    dpg.get_item_width(v.panel), dpg.get_item_height(v.panel)]
        else:
            self._visuals.remove(key)
        if key == "district_table":
            self._dist_info_last_iter = -1         # refresh on next show
        self._save_visuals()
        self._sync_visuals()

    def _on_visual_close(self, _sender, _value, key) -> None:
        self._set_visual(key, False)

    def _on_visual_pin(self, _sender, _value, key) -> None:
        if key in self._visuals.pinned:
            self._visuals.pinned.discard(key)
        else:
            self._visuals.pinned.add(key)
            self._adopt_floating_rect(key)
        self._save_visuals()
        self._sync_visuals()

    def _visual_menu_toggle(self, key: str) -> None:
        """A Views menu item for a window that is now a visual."""
        self._set_visual(key, bool(dpg.get_value(getattr(self, _MENU_ITEMS[key]))))

    def _sync_visual_menu(self) -> None:
        for key, attr in _MENU_ITEMS.items():
            item = getattr(self, attr, 0)
            if item and dpg.does_item_exist(item):
                shown = self._visuals.is_shown(key)
                if bool(dpg.get_value(item)) != shown:
                    dpg.set_value(item, shown)

    # ── Placement, once per frame ────────────────────────────────────────────

    def _visuals_row_rect(self):
        if not dpg.does_item_exist("visuals_slots"):
            return None
        w, h = dpg.get_item_rect_size("visuals_slots")
        if w <= 0 or h <= 0:
            return None
        # A child window reports no absolute position; the group around it
        # does, and the slots start under the group's heading line.
        x, top = dpg.get_item_rect_min("visuals_row")
        y = top + dpg.get_item_rect_size("visuals_row")[1] - h
        return (int(x), int(y), int(w), int(h))

    def _adopt_floating_rect(self, key: str) -> None:
        """Record where a floating card is now (after a corner resize)."""
        item = self._visuals.floating.get(key)
        if item is None or self._visual_placed.get(key, ("",))[0] != "floating":
            return False                # not yet placed at its floating size
        panel = V.BY_KEY[key].panel
        size = [dpg.get_item_width(panel), dpg.get_item_height(panel)]
        if size[0] and size[1] and size != item["size"]:
            item["size"] = size
            self._visual_placed[key] = ("floating", tuple(item["pos"]),
                                        tuple(size),
                                        key in self._visuals.pinned)
            return True
        return False

    def _sync_visuals(self) -> None:
        """Show the cards the state lists, where it lists them. Does work
        only when something moved: a drag, a resize, the row's rectangle."""
        if not self._visual_cards:
            return
        state = self._visuals
        blocked = bool(getattr(self, "_workflow_blocks_main", False))
        row = self._visuals_row_rect()
        if blocked or row is None:
            # A workflow window owns the screen (or nothing is laid out yet).
            if not self._visuals_blocked:
                for key in self._visual_cards:
                    dpg.configure_item(V.BY_KEY[key].panel, show=False)
                self._visual_placed.clear()
                self._visuals_blocked = True
            return
        self._visuals_blocked = False

        if state.overflow(V.capacity(row[2]), origin=(row[0] + 20, 120)):
            self._save_visuals()
        self._drag_visuals(row)

        # The row gives its height back to the map when nothing is docked.
        want_h = V.ROW_H if state.row else 4
        if dpg.get_item_configuration("visuals_slots")["height"] != want_h:
            dpg.configure_item("visuals_slots", height=want_h)
        hint = ("" if state.row else
                "Nothing docked. Add a visual, or drop a floating one here.")
        if dpg.get_value(self._visuals_hint) != hint:
            dpg.set_value(self._visuals_hint, hint)

        slots = dict(zip(state.row, V.slot_rects(row, len(state.row)),
                         strict=True))
        bounds = (dpg.get_viewport_client_width(),
                  dpg.get_viewport_client_height())
        dragging = self._visual_drag[0] if self._visual_drag else None
        mouse_down = dpg.is_mouse_button_down(dpg.mvMouseButton_Left)
        changed = False
        for key, card in self._visual_cards.items():
            panel = V.BY_KEY[key].panel
            if not state.is_shown(key):
                if self._visual_placed.pop(key, None) is not None \
                        or dpg.is_item_shown(panel):
                    dpg.configure_item(panel, show=False)
                continue
            if key == dragging:
                continue
            pinned = key in state.pinned
            if key in slots:
                x, y, w, h = slots[key]
                placed = ("docked", (x, y), (w, h), pinned)
            else:
                item = state.floating[key]
                if not mouse_down and not pinned:
                    changed |= bool(self._adopt_floating_rect(key))
                pos, size = V.clamp_rect(item["pos"], item["size"], bounds)
                if panel in _FIXED_WIDTH:
                    size[0] = item["size"][0]
                placed = ("floating", tuple(pos), tuple(size), pinned)
            if self._visual_placed.get(key) == placed:
                continue
            kind, pos, size, _ = placed
            dpg.configure_item(panel, show=True, pos=list(pos), width=size[0],
                               height=size[1],
                               no_resize=(kind == "docked" or pinned))
            dpg.bind_item_theme(panel, self._visual_themes[kind])
            dpg.configure_item(card["pin"], label="Pinned" if pinned else "Pin")
            dpg.bind_item_theme(
                card["pin"], self._visual_themes["pin_on" if pinned else "pin_off"])
            self._visual_placed[key] = placed
        if changed:
            self._save_visuals()
        self._sync_visual_menu()
        self._sync_visual_needs()

    def _drag_visuals(self, row) -> None:
        """Move a card with its grip; on release, dock or float it."""
        state = self._visuals
        if self._visual_drag is None:
            for key, card in self._visual_cards.items():
                if (state.is_shown(key) and key not in state.pinned
                        and dpg.is_item_active(card["grip"])):
                    panel = V.BY_KEY[key].panel
                    self._visual_drag = (key, tuple(dpg.get_item_pos(panel)))
                    dpg.focus_item(panel)
                    break
            return
        key, start = self._visual_drag
        panel = V.BY_KEY[key].panel
        if dpg.is_item_active(self._visual_cards[key]["grip"]):
            dx, dy = dpg.get_mouse_drag_delta()
            dpg.configure_item(panel, pos=[int(start[0] + dx),
                                           int(start[1] + dy)])
            return
        self._visual_drag = None
        self._drop_visual(key, dpg.get_mouse_pos(local=False), row)

    def _drop_visual(self, key: str, point, row) -> None:
        """A card was let go with the pointer at ``point``."""
        panel = V.BY_KEY[key].panel
        # Dropping on the heading line counts as the row too.
        top = dpg.get_item_rect_min("visuals_row")[1]
        target = (row[0], int(top), row[2], row[1] + row[3] - int(top))
        if panel in _FIXED_WIDTH:
            target = (0, 0, 0, 0)       # never docks
        self._visuals.drop(
            key, target, point, dpg.get_item_pos(panel),
            (dpg.get_item_width(panel), dpg.get_item_height(panel)))
        self._visual_placed.pop(key, None)
        self._save_visuals()

    def _sync_visual_needs(self) -> None:
        """A card whose data the map lacks says what it needs in place of its
        one line. It stays on screen; nothing is switched on to feed it."""
        has = self._visual_has()
        sig = tuple(sorted(has.items()))
        if sig == self._visual_needs_sig:
            return
        self._visual_needs_sig = sig
        for key, card in self._visual_cards.items():
            if not card["line"]:
                continue
            v = V.BY_KEY[key]
            need = V.missing(v, has)
            dpg.set_value(card["line"], need or v.shows)
            self.theme.retoken(card["line"], "warning" if need else "muted")

    # ── Likely seats ─────────────────────────────────────────────────────────

    def _update_likely_seats(self) -> None:
        """Redrawn on the map's render interval, and only for a new plan."""
        if not dpg.is_item_shown("panel_likely_seats"):
            return
        now = time.monotonic()
        if now - self._likely_at < max(0.1, dpg.get_value(self._map_interval)):
            return
        self._likely_at = now
        with self.state._lock:
            a = self.state.current_assignment
            a = a.copy() if a is not None else None
            n = self.state.num_districts
        arrays = self.runner.election_arrays if self.runner else None
        if a is None or not arrays or len(arrays[0][0]) != len(a):
            if self._likely_sig is not None:
                self._likely_sig = None
                for series in self._likely_series:
                    dpg.set_value(series, [[], []])
                dpg.set_value(self._likely_note, "")
            return
        win, swing = dpg.get_value(self._win_prob), dpg.get_value(self._swing_sigma)
        sig = (hash(a.tobytes()), n, win, swing, id(arrays[0][0]))
        if sig == self._likely_sig:
            return
        self._likely_sig = sig
        dem, gop = arrays[0]
        dist = LS.seat_distribution(LS.district_shares(a, dem, gop, n), win, swing)
        seats = np.arange(n + 1)
        groups = (seats * 2 < n, seats * 2 == n, seats * 2 > n)
        for series, mask in zip(self._likely_series, groups, strict=True):
            dpg.set_value(series, [seats[mask].astype(float).tolist(),
                                   (dist[mask] * 100.0).tolist()])
        best, p, mean = LS.summary(dist)
        dpg.set_value(self._likely_note,
                      f"Most likely: {best} of {n} ({p * 100:.0f}%). "
                      f"Average: {mean:.1f}.")
        dpg.set_axis_limits("likely_x", -0.6, n + 0.6)
        dpg.set_axis_limits("likely_y", 0.0, max(5.0, float(dist.max()) * 110.0))
