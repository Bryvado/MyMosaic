# Build brief: the redesigned main screen

This folder is the design for a rebuild of Mosaic's main window. Read it in this order:

1. `screen.png` – the target screen. `screen.html` is the same mockup as static HTML, with exact colours and spacing in its inline styles.
2. `design-system.md` – what each colour, type size and spacing value means and when to use it.
3. `tokens.json` – every token with dark and light values. The first 63 colour tokens are the existing fields of `Palette` in `src/mosaic/gui/theme.py`, renamed with hyphens. Tokens whose usage starts with "Proposed" do not exist in the code yet.
4. `map.md` – rules for the basemap, Map style and Visuals.
5. `components/ScoreRow.md` – the one-line collapsible score row.
6. `audit.md` – what is wrong with the current screen and why.

## Ground rules

- The app is Dear PyGui. The mockup is drawn with web layout and cannot be matched pixel for pixel. Match the structure, the tokens and the behaviour. Where the toolkit cannot do something, say so in the pull request and propose the nearest thing it can do.
- Do not change scoring math, the optimizer, preset files or `src/mosaic/goals.py`'s calibration.
- Every colour comes from `ThemeManager` tokens. Add the proposed tokens to `Palette` for both `DARK` and `LIGHT`; do not hard-code colour tuples in the mixins.
- No new dependencies in steps 1 to 3. Step 4 may add one small pure-Python package for decoding vector tiles if needed; name it in the pull request first.
- The app must stay usable on a 2-core Linux server over remote desktop with software rendering. Nothing may redraw every frame that does not have to.
- Wording is sentence case. Keep existing score names exactly.
- One pull request per step, each branched from the previous one, each runnable on its own. Add tests for any logic that does not need a display.

## What the screen is

Three columns under the menu bar, in the order a person works:

| Column | Holds | Drawn today by |
|---|---|---|
| Left, about 300px | Map (file, start plan, districts, iterations), Goals, Results | `setup_mixin.py` (left column, status box), `goals_mixin.py` (floating Goals panel) |
| Centre, takes all spare width | Map toolbar, the map, Map style, run strip, Visuals row | `setup_mixin.py` (`_build_map_canvas`, controls), `map_view.py` |
| Right, about 440px | Scores: all 20, grouped, one line each | `setup_mixin.py` (`score_row_*` groups in three columns) |

Layout constants live in `src/mosaic/gui/app/_common.py` (`_VP_W`, `_LEFT_W`, `_SCORE_COL_W`, `_MAP_DW`). Extra window width goes to the map; extra height goes to the map first, then the Visuals row.

## Step 1: layout, scores, goals, run strip

- Rebuild the main window as the three columns above.
- **Scores.** Replace the three scrolling columns of tall rows with one list grouped under "Geography and population", "Elections" and "Demographics". A closed row is one line: checkbox, name (`accent_green` when on, `body` when off), a short phrase in `muted`, and the weight or "off" right-aligned. Clicking the row opens it in place to show the weight slider (about 200px) with a number field, the Fair / D / R choice where the score has one, and an "Open chart" button that shows the score's existing panel. Rows open and close independently. Use the phrases in `screen.html`. The existing widgets (`_w_*`, `_*_enabled`, `_*_dir`) stay the source of truth so presets, Goals and the runner keep working unchanged; the "Advanced: show all scores" filter from the Goals work is no longer needed because every score is always listed.
- A score whose data is missing stays listed, unchecked and disabled, and its phrase says what it needs. Reuse `_PRESET_NEEDS` and `_preset_chk_usable`.
- **Goals.** Move Goals from a floating panel into the left column. Each goal is one line: name and a strength dropdown (Off, Light, Moderate, Strong, Very strong, Dominant, plus "Custom" when the weights are off-ladder). Keep "Starting points…". The fine-tune slider moves into a small popup opened from the row.
- **Results.** Four stat tiles (Score, Best with its iteration, Time, Speed): a `muted` label over the value in the title size. Temperature, Entropy, Accepted steps and Flip rate move into a "Run details…" popup.
- **Run strip** under the map: one primary button (Start, Pause or Resume, using the nudge theme), Reset, Revert to best, a progress bar drawn as 20 tiles, the iteration count, and the estimated time left (`_eta_txt` today) in semibold.
- Inputs get a 1px border so they stand out from panels (see the contrast table in `audit.md`).

## Step 2: Map style

`MapView` has one `border_thickness` multiplier and draws borders black. Replace that with separate width and colour for district borders, city (county) lines and precinct lines, plus fill opacity, set from a "Map style" row under the map. Defaults and ranges are in `map.md`. A width of 0 hides the line. Settings apply to the screen and to PNG/PDF exports, and are saved in `.mosaic/`, not in presets. The label "City lines" follows the place-unit name from the Goals work.

## Step 3: Visuals

- A "Visuals" row under the run strip with an "Add visual…" picker. Each visual is a card with a grip, a title, a pin button, a close button, one line saying what it shows, and the chart.
- A new visual docks in the row. Dragging its grip moves it within the row or out of it to float anywhere in the window, including over the map. Dragging its corner resizes it. Pinning fixes its position and size until unpinned. Positions, sizes and pins are saved in `.mosaic/` and restored on launch. Full rules: `map.md`, "Visuals".
- Wrap the existing panels as visuals rather than rewriting their charts: Partisanship, Win Chance, Score Contributors, Temperature, Entropy, Score, Comet Plot, District Info, and the Batch Runs histogram and scatterplot (`panels_mixin.py`, `phase_mixin.py`, `ensemble_views_mixin.py`).
- One new chart, **Likely seats**: the probability of each Democratic seat count for the plan on screen, as a histogram from 0 to the number of districts. Compute it from the per-district win probabilities Mosaic already models (`build_p_wins_matrix` and the Win Chance panel) as a Poisson-binomial distribution, in a function with no Dear PyGui import, with tests.
- A visual never changes the search. One that needs data the map lacks is listed in the picker with what it needs and cannot be added.

## Step 4: Basemap

A "Basemap" toggle in the map toolbar that draws OpenFreeMap context under the fill. OpenFreeMap serves vector tiles only, so fetch the tiles covering the loaded map's bounds, decode them, and paint a reduced set of layers into the map texture: ground, water, major roads, minor roads when zoomed in, and place names. Do not try to reproduce a full OpenFreeMap style. Layer order, default-on rules per fill, opacity, caching, offline behaviour and the required attribution line are in `map.md`. Fetch on a worker thread, cache tiles on disk under `cache/`, and never block the frame loop.

## In each pull request, report

- What matches the design and what does not, with the reason.
- Anything Dear PyGui could not do and what was done in its place.
- What was verified headless and what still needs a person to look at on a real display.
