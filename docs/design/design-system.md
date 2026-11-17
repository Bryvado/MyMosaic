# Mosaic

Redistricting for Python.

Mosaic is a desktop tool that draws district maps by running an optimizer over precincts and scoring every candidate plan. This system records the visual language the app already has, taken from its theme code, so new screens can be designed against one set of rules. It describes the foundation as it exists today. Proposed changes live in the Audit section and are marked as proposals.

Source: `src/mosaic/gui/theme.py` and `src/mosaic/gui/map_view.py` in `Bryvado/MyMosaic`, commit `8b8aead`. The app is built in Dear PyGui, so every rule here has to be something that toolkit can draw: flat fills, 1px borders, one font family, no shadows, no gradients, no animation.

## What the product is like

- **An instrument.** People watch a long run, read numbers and adjust weights. The interface should read like a control panel: dense, stable, nothing moving that isn't data.
- **Dark first.** The dark theme is the reference. Light exists and uses the same tokens, and every pairing has to hold in both.
- **The map is the subject.** Chrome stays neutral grey so the map's colours are the only strong colour on screen.
- **Colour carries meaning, so it is rationed.** Amber is the brand and section headings. Indigo is the one recommended action. Green is "this score is on". Red and blue belong to the parties and to nothing else.

## Content fundamentals

- Sentence case everywhere: "Open shapefile…", "Run parameters". The current app mixes Title Case headings with sentence-case labels; sentence case is the rule going forward.
- Name scores the way the app does and never rename them per screen: County Congruence, Electoral Opportunity, Plan Continuity, Community Dispersion, Neighborhood Severance.
- Numbers get thousands separators and a unit or a denominator: `563,890 / 1,000,000`, `2,930 iter/s`, `52.4%`. Keep decimals to what a person can act on.
- Status lines say what is happening in plain words: "Running ReCom", "Paused at 563,890". No ellipsis-only states.
- Buttons are verbs: Start, Pause, Reset, Load, Export. A menu item that opens a dialog ends with "…".
- Party labels are "Democratic" and "Republican" in full, or D and R where space is tight. Use the same form within one view.

## Colour

All colours are tokens with a dark and a light value. Use the token, never the hex.

**Surfaces** step up in lightness: `menubar-bg` → `window-bg` → `popup-bg` → `child-bg` → `frame-bg`. Panels sit on `child-bg` with a 1px `border`. Inputs are a `frame-bg` fill with no border.

**Text** has one default, `body`, and a small set of roles:

| Token | Use |
|---|---|
| `title` | The app title. Amber, from the logo. |
| `heading` | Section headings. |
| `subheading` | Labels above inputs. |
| `muted` | Helper and status text. |
| `accent-green` | The name of a score that is switched on. |
| `disabled` | The name of a score that is switched off. |
| `error`, `warning`, `ok` | Messages only. |
| `dem`, `gop` | Party names and party chart series. |

**Actions** come in three weights. `nudge-button` (indigo) marks the single recommended action in a view. `button` is every ordinary action. `antinudge-button` is for actions that are available but not the next step. One nudge per view.

**Party colour.** `gop` and `error` are both red. Never place them where they can be confused: errors are text messages near the control that caused them, and party red appears only next to a party name, in a party chart series or on the map.

**The map ramp.** `partisan-00` to `partisan-13` are the fourteen fills for Democratic two-party share, wine red through blue-violet, with the split at 50%. They are identical in both themes because the map always draws on its own white page. Use them for party vote share only. District identity colours (50 of them) and the demographic ramps are defined in `map_view.py` and are not tokens yet.

## Type

One family, Inter, in two weights. Three sizes.

| Style | Size | Weight | Use |
|---|---|---|---|
| `title` | 20px | 600 | App title |
| `heading` | 15px | 600 | Section headings |
| `body` | 14px | 400 | Everything else |

Hierarchy below the heading comes from colour (`subheading`, `muted`), not from size. Dear PyGui draws one line height per font, so there are no line-height tokens.

## Space and shape

- Inputs and buttons pad 8px horizontally and 5px vertically (`space-x`, `space-y`).
- Items sit 8px apart in a row and 5px apart in a stack.
- Windows, panels and dialogs pad 10px (`space-window`).
- Radii are small and close together: 4px panels and tabs (`radius-sm`), 5px inputs and buttons (`radius-md`), 6px dialogs and menus (`radius-lg`). The main window is square.
- Panels and popups have a 1px border. Inputs and buttons have none.
- No shadows. Depth is the surface step and the border.

## Iconography

The app uses almost no icons: a camera glyph on the map toolbar, and text glyphs (`»`, `…`, a north-east arrow) on buttons that open panels. There is no icon set. Until one is chosen, label buttons with words.

The logo is in Assets → Logos. It is navy and amber; `nudge-button` and `title` are its nearest tokens.

## Rules that name tokens

- Text on `window-bg`, `child-bg` or `popup-bg` is `body` unless it has one of the roles above.
- A view has at most one `nudge-button`.
- `accent-green` means "on". Do not use it for success messages (`ok`) or for a loaded file.
- `dem` and `gop` never signal state.
- Chart frames use `plot-border`, gridlines `plot-axis-grid`, tick labels `plot-axis-text`.
- A disabled control uses the `antinudge-*` tokens and `disabled` text, and must also look different in shape or position, because the contrast step alone is small.
