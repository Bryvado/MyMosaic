# Map and visuals

Rules for the map, its basemap, the lines a person can restyle, and the charts that sit under it. Everything here is proposed; none of it is built yet.

## Layers, bottom to top

1. Basemap ground (`basemap-ground`)
2. Basemap water, then roads (`basemap-water`, `basemap-road-minor`, `basemap-road-major`)
3. The fill: districts, results, demographics, compactness or population deviation
4. Precinct lines, then city (or county) lines
5. District borders
6. Basemap place names, then district labels

Borders and labels always sit above the fill so a district is readable with or without the basemap.

## Basemap

The basemap is OpenFreeMap. It serves vector tiles only, with no ready-made images, so Mosaic draws a reduced set of layers itself: water, major roads, minor roads when zoomed in, and place names. It does not reproduce a full OpenFreeMap style.

- The basemap is a toggle in the map toolbar, next to the other overlays.
- It is on by default for the Districts fill and off by default for Results, Demographics, Compactness and Population deviation, because a translucent fill makes those colour scales harder to read. A person can turn it on for any fill.
- With the basemap on, the fill draws at the Fill opacity set in Map style (45% by default). With it off, the fill is opaque.
- Tiles are fetched when a map loads and kept on disk, so panning, zooming and later runs do not fetch again. With no connection, the map draws without a basemap and says so in the status line.
- The attribution line "Basemap: OpenFreeMap © OpenMapTiles, data from OpenStreetMap" is always shown under the map and included in exported images when the basemap is on.

## Map style

People can change how the lines draw. Map style sits under the map as one row and applies to the screen and to exports.

| Control | Default | Range |
|---|---|---|
| District borders: width | 2.5px | 0 to 8px |
| District borders: colour | `map-district-line` | any |
| City lines: width | 1.5px | 0 to 8px |
| City lines: colour | `map-place-line` | any |
| Precinct lines: width | 0.5px | 0 to 8px |
| Precinct lines: colour | `map-precinct-line` | any |
| Fill opacity | 45% with basemap, 100% without | 0 to 100% |

A width of 0 hides that line. Choices are saved with the app's settings, not with a score preset. "City lines" takes the name of whatever unit the place column holds.

## Visuals

Visuals are charts built from the plan on screen or from a batch of runs. A person adds one from "Add visual…" and removes it with its close button; each is a card with a title, one line saying what it shows, and the chart.

**Placement.** A new visual docks in a row at the bottom, under the run controls. From there a person can:

- **Move it** by dragging the grip at the left of its title, to another slot in the row or out of the row to float anywhere in the window, including over the map.
- **Resize it** by dragging its bottom-right corner. The chart redraws to fit; the minimum size is 180 by 140px.
- **Pin it** with the pin button. A pinned visual keeps its place and size when the window is resized or other visuals are added, and cannot be dragged until unpinned. An unpinned floating visual returns to the row when dragged back over it.

A docked card uses `window-bg` with `radius-sm`. A floating card uses `popup-bg`, a 1px `border` and `radius-lg`, so it reads as sitting above the screen. The pin is `check-mark` when on and `muted` when off. Positions, sizes and pins are saved with the app's settings and restored on the next launch.

Available from what Mosaic already computes:

| Visual | Source | Shows |
|---|---|---|
| Likely seats | Win Chance probabilities for the current plan | The chance of each Democratic seat count, as a histogram |
| Districts by party share | Partisanship view | Districts ordered by Democratic two-party share, with a 50% line |
| Win chance | Win Chance view | Modeled Democratic win probability per district |
| Score | Score history | The weighted total over the run |
| What is driving the score | Score Contributors | Each goal's share of the total beside its set strength |
| Temperature, Entropy | Run history | Annealing temperature and the rolling acceptance rate |
| Comet plot | Comet Plot view | Two chosen measures traced against each other |
| District table | District Info view | Population, deviation, shares and compactness per district |
| Batch histogram | Batch Runs | Any summary metric across all runs, with the median marked |
| Batch scatterplot | Batch Runs | Two summary metrics across runs |

Rules:

- A visual never changes the search. Showing one does not switch a score on.
- Party visuals use `dem`, `gop` and the `partisan-*` ramp. Every other series uses `slider-grab` on `plot-bg`, with `plot-axis-grid` and `plot-axis-text`.
- A visual that needs data the map lacks is listed in "Add visual…" with what it needs, and cannot be added.
- Charts redraw on the map's render interval, not every iteration.
