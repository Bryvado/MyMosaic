# Audit

A review of the interface as it runs today (dark theme, Tarrant County map, a run in progress) against the tokens above. Ratings are judgments, not measurements.

## Summary

| Area | Rating | In one line |
|---|---|---|
| Colour tokens | 8/10 | 63 semantic tokens with full dark and light values; a few redundant, a few failing contrast. |
| Type | 5/10 | Three sizes. No small text and no large-number style, so hierarchy leans on colour. |
| Spacing | 4/10 | Four values, all between 4px and 10px. Nothing for separating groups or sections. |
| Components | 6/10 | Toolkit primitives are themed consistently. States exist but disabled is hard to see. |
| Layout and patterns | 2/10 | The screen is one flat surface with every control visible at once. |

The theme is sound. The problems people feel are layout, hierarchy and density, which the tokens do not yet cover.

## Token coverage

| Category | Defined | Gaps |
|---|---|---|
| Colour | 63 tokens × 2 themes | About 50 lines outside `theme.py` carry literal colour tuples, in 11 files. 22 of them are the Score Contributors bar colours. District (50), demographic and chart-series colours are not tokens. |
| Type | 3 styles, 1 family | No 12px style for secondary text, no style for big readouts. |
| Spacing | 4 values | No scale above 10px. Group and section gaps are whatever the toolkit default gives. |
| Radius | 4 values | Complete for the toolkit. |
| Border | 2 values | Complete. |
| Focus | none | No focus colour is set, so keyboard focus uses the toolkit default. |
| Elevation, motion | none | Not available in Dear PyGui. Not a gap. |

## Contrast

Measured on `child-bg` unless noted. Text needs 4.5:1; control boundaries need 3:1.

| Pair | Dark | Light | Verdict |
|---|---|---|---|
| `body` | 12.5 | 16.7 | Pass |
| `heading` | 8.9 | 5.6 | Pass |
| `muted` | 5.4 | 4.7 | Pass |
| `accent-green` | 8.7 | 4.9 | Pass |
| `error` | 4.4 | 6.1 | Dark just fails |
| `warning` | 7.3 | 4.1 | Light fails |
| `disabled` | 3.2 | 4.1 | Fails both |
| `disabled-subtle` | 3.9 | 3.6 | Fails both |
| `disabled-deep` | 2.2 | 3.1 | Fails both |
| `dialog-muted` on `popup-bg` | 4.7 | 4.2 | Light fails |
| `slider-grab` on `frame-bg` | 3.1 | 2.9 | Light fails |
| `frame-bg` against `child-bg` | 1.3 | 1.1 | Fails both |

The last row matters most. Inputs have no border and their fill is almost the panel colour, so fields, slider tracks and unchecked boxes are hard to pick out, and a disabled button is close to an enabled one.

## Redundant tokens

| Tokens | Issue | Proposal |
|---|---|---|
| `disabled`, `disabled-deep`, `disabled-subtle` | Three greys within a few steps of each other, none passing. | One `disabled` that passes 4.5:1, plus a non-colour cue. |
| `accent-green`, `success-soft`, `success-pale`, `ok` | Four greens. `accent-green` means "on" and is also used for the loaded-file line. | `on` for enabled scores, `ok` for confirmations. Drop the other two. |
| `muted`, `secondary`, `subheading`, `dialog-muted` | Four secondary text greys. | Two: `subheading` for labels, `muted` for help. |

## What is wrong on screen

1. **Everything is visible at once.** Setup, run controls, nine live statistics, the map, two charts and twenty-odd score rows share one screen with equal weight. Nothing says what to do first.
2. **The map is the subject and gets the least room.** It draws about 350px square inside a container four times as wide.
3. **Scores are squeezed into the bottom third.** Three columns scroll separately and the top row is clipped.
4. **The score row is oversized and repetitive.** Each row is a checkbox, a name, two unlabelled icon buttons, a slider about 500px wide for a 0 to 100 value, and the word "Weight". Some rows add a Fair / D / R line, so heights vary.
5. **Statistics are loose text.** Score, Best, Temperature, Entropy, Accepted steps, Flip rate, Time, Iter/sec and Est. left sit in ad hoc columns with label and value in the same style.
6. **Button states blur.** Start (unavailable while running), Reset (available), and three disabled buttons look alike. Only Pause stands out.
7. **Green is overloaded.** It marks the loaded file and every enabled score, so a screen with many scores on is mostly green text.
8. **The new Goals feature enters through two small buttons** in a hint line above the score rows, though it is meant to be the main way to set priorities.

## Proposed direction

These are proposals for discussion. None is in the tokens yet.

- **Lay the screen out by task:** Set up → Goals → Run → Results. Setup collapses once a map is loaded.
- **Make Goals the main surface** and move the detailed score rows behind "Advanced".
- **Give the map the centre and most of the width,** with run status as one strip beneath it.
- **Replace loose statistics with stat tiles:** a `muted` label over a larger value.
- **Shrink the score row to one line:** switch, name, short slider, number field, one overflow menu.
- **Add the missing tokens:** a spacing scale (4, 8, 12, 16, 24), a 12px text style, a large number style, a focus colour, chart-series and district colours, and either a visible input border or a lighter `frame-bg`.

## Priority actions

1. Decide the screen layout by task. Every other change depends on it.
2. Fix input and disabled contrast (`frame-bg`, the three `disabled` tokens).
3. Add the spacing scale and the two type styles, then define the stat tile and the compact score row with them.
