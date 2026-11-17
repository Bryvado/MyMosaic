"""The Basemap toggle: OpenFreeMap context painted under the fill.

All the work is in ``mosaic.basemap`` and happens on a worker thread. This
mixin decides when a basemap is wanted, asks for one whenever the map's raster
changes (load, resize, zoom, pan, theme), and installs the result. The frame
loop only compares a few tuples.
"""
import time
from concurrent.futures import ThreadPoolExecutor

from mosaic import basemap as B
from mosaic.paths import cache_dir

from ._common import _FILL_NO_OUTLINE, _FILL_NONE, _FILL_OPTIONS, dpg, log

# Fills the basemap is on for unless the user says otherwise. A translucent
# fill makes the colour scales of the other fills harder to read (map.md).
_BASEMAP_DEFAULT_ON = {"districts"}
BASEMAP_OPACITY = 0.45          # default fill opacity while the basemap is on


class BasemapMixin:
    """The Basemap toggle and the worker that paints it."""

    def _init_basemap(self) -> None:
        self._basemap_store = B.TileStore(cache_dir() / "basemap")
        self._basemap_executor = None
        self._basemap_job = None          # (future, key)
        self._basemap_key = None          # key the installed basemap answers
        self._basemap_want = None         # key the screen asks for, and since
        self._basemap_want_at = 0.0
        self._basemap_choice: dict = {}   # fill key -> on/off, where chosen
        self._fill_opacity_basemap = BASEMAP_OPACITY

    # ── Which fill, and whether it wants a basemap ────────────────────────────

    def _fill_key(self) -> str:
        """A stable name for the current fill, for per-fill basemap choices."""
        choice = dpg.get_value(self._fill_combo)
        if choice in (_FILL_NONE, _FILL_NO_OUTLINE):
            return "districts"
        for label, attr, _need in _FILL_OPTIONS:
            if choice.startswith(label):
                return attr
        return "districts"

    def _basemap_wanted(self) -> bool:
        key = self._fill_key()
        return self._basemap_choice.get(key, key in _BASEMAP_DEFAULT_ON)

    def _sync_basemap_to_fill(self) -> None:
        """After the fill changes: the checkbox and opacity follow the fill."""
        on = self._basemap_wanted()
        if bool(dpg.get_value(self._basemap_chk)) != on:
            dpg.set_value(self._basemap_chk, on)
        self._apply_fill_opacity()

    def _on_basemap_toggle(self) -> None:
        """The checkbox: remember the choice for this fill."""
        self._basemap_choice[self._fill_key()] = bool(
            dpg.get_value(self._basemap_chk))
        self._save_map_style()
        self._apply_fill_opacity()

    def _active_fill_opacity(self) -> float:
        """45% over a basemap by default, opaque without one; each is its own
        setting, so turning the basemap off does not leave a washed-out map."""
        return (self._fill_opacity_basemap if dpg.get_value(self._basemap_chk)
                else self._fill_opacity_value)

    def _apply_fill_opacity(self) -> None:
        value = self._active_fill_opacity()
        dpg.set_value(self._fill_opacity, int(round(value * 100)))
        if self.map_view is not None:
            self.map_view.fill_opacity = value

    # ── Colours ───────────────────────────────────────────────────────────────

    @staticmethod
    def _basemap_colors(palette) -> dict:
        def rgb(token):
            return tuple(int(v) for v in getattr(palette, token)[:3])
        return {"ground": rgb("basemap_ground"), "water": rgb("basemap_water"),
                "road_major": rgb("basemap_road_major"),
                "road_minor": rgb("basemap_road_minor"),
                "label": rgb("body"), "halo": rgb("basemap_ground")}

    # ── Per-frame sync ────────────────────────────────────────────────────────

    def _redraw_map_now(self) -> None:
        if self.state.current_assignment is not None:
            self._rerender_map()
        elif self.map_view is not None and self.map_view._loaded:
            self.map_view.draw_blank()

    def _sync_basemap(self) -> None:
        """Ask for a basemap when the raster changes; install it when ready.

        Cheap when nothing changed: one tuple comparison. The painting itself
        (tile fetch, decode, draw) is on the worker and never blocks a frame.
        """
        mv = self.map_view
        gdf = self.runner.gdf if self.runner is not None else None
        on = (bool(dpg.get_value(self._basemap_chk)) and mv is not None
              and mv._loaded and gdf is not None and not self._ensemble_active)
        if dpg.is_item_shown(self._basemap_credit) != on:
            dpg.configure_item(self._basemap_credit, show=on)
        if mv is None:
            return
        if mv.basemap_on != on:
            mv.basemap_on = on
            self._redraw_map_now()
        if not on:
            self._basemap_want = None
            return

        key = (mv._w, mv._h, tuple(mv._view_bounds), id(gdf),
               self.theme.palette.name)
        job = self._basemap_job
        if job is not None and job[0].done():
            self._basemap_job = None
            self._install_basemap(job, key)
        if key == self._basemap_key or self._basemap_job is not None:
            return
        now = time.monotonic()
        if key != self._basemap_want:
            self._basemap_want, self._basemap_want_at = key, now
            return
        if now - self._basemap_want_at < 0.25:      # let a drag or resize settle
            return
        projection = mv.projection()
        if projection is None:
            return
        colors = self._basemap_colors(self.theme.palette)
        crs = gdf.crs
        store = self._basemap_store

        def paint():
            return B.render(store, crs, projection, colors,
                            cancelled=lambda: self._basemap_want != key)

        if self._basemap_executor is None:
            self._basemap_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="mosaic-basemap")
        self._basemap_job = (self._basemap_executor.submit(paint), key)

    def _install_basemap(self, job, current_key) -> None:
        future, key = job
        try:
            raster = future.result()
        except Exception as exc:
            log.exception("Basemap render failed")
            raster = None
            self.state.update(
                status_message=f"Basemap unavailable ({exc}); drawing without it.")
            self._basemap_key = key       # do not retry the same view in a loop
            return
        if raster is None or key != current_key:
            return                        # cancelled or superseded
        self._basemap_key = key
        mv = self.map_view
        if raster.error and not raster.drawn:
            # Offline, or a map that cannot be placed on the earth: draw the
            # map as if the basemap were off, and say why.
            mv.basemap = None
            self.state.update(
                status_message=f"Basemap unavailable: {raster.error}. "
                               "Drawing without it.")
        else:
            mv.basemap = raster
            if raster.error:          # some tiles missing; the rest is drawn
                self.state.update(status_message=f"Basemap: {raster.error}.")
        self._redraw_map_now()

    def _export_basemap(self, offscreen) -> bool:
        """Paint a basemap for an export's own raster. Worker thread.

        Exports draw on a white page whatever the screen's theme, so the
        basemap uses the light palette there. Returns whether one was drawn.
        """
        from mosaic.gui.theme import PALETTES

        gdf = self.runner.gdf if self.runner is not None else None
        projection = offscreen.projection()
        if gdf is None or projection is None:
            return False
        try:
            raster = B.render(self._basemap_store, gdf.crs, projection,
                              self._basemap_colors(PALETTES["light"]))
        except Exception:
            log.exception("Export basemap failed")
            return False
        if raster is None or (raster.error and not raster.drawn):
            return False
        offscreen.basemap = raster
        offscreen.basemap_on = True
        return True

    def _shutdown_basemap(self) -> None:
        self._basemap_want = None
        if self._basemap_executor is not None:
            self._basemap_executor.shutdown(wait=False, cancel_futures=True)
