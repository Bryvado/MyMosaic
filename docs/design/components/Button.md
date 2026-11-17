Buttons start an action; there are three weights and one disabled look.

| Variant | Tokens | Use when |
|---|---|---|
| Primary | `nudge-button`, `nudge-text` | The one recommended action in a view: Start, Pause, Load. At most one per view. |
| Default | `button`, `button-text` | Every other action. |
| Quiet | `antinudge-button`, `antinudge-text` | Available, but not the next step. |

**States.** Hover and pressed step the fill lighter (`*-hovered`, `*-active`). Disabled uses the quiet tokens with `disabled` text. Because quiet and disabled look alike, never show both in the same group.

**Shape.** 8px by 5px padding, `radius-md`, no border, `body` text. Dialog footer buttons are 90px wide and right-aligned, primary last.

**Wording.** A verb in sentence case. Add "…" when the button opens a dialog.

The consumer provides the label and the action. There is no icon slot.
