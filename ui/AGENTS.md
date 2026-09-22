# OpenFlight UI — agent notes

The production UI is a **1024×600 Raspberry Pi kiosk**. Also ship **800×400**
and **800×480**. Assume a finger, not a mouse. `*` is
`touch-action: manipulation`, so native overflow scrolling often does not
work; Pi displays also report finger motion as mouse drags.

## Type (rem, scales with the screen)

Shot numbers, labels, and UI chrome must use **`rem`**, not `px`, so type
follows `html` font-size.

- `html` font-size is a **fluid clamp** (`index.css`): about **112.5%** on
  800-wide kiosks and **150%** at 1024-wide. Do not pin `html` to a fixed
  `150%`.
- Live metric values: size the **row** with `clamp(min-rem, min(vw, vh, cqi, cqb), max-rem)`.
  The unit is `em` so it tracks the number. `useSharedFitFontSize` then applies
  **one** size to every Live tile so 181 and 2,328 match; it shrinks the whole
  grid until the widest value + unit fits (`10,000`). Do not shrink tiles
  independently. No `px` font-size in CSS for those numbers, and no `min-width`
  breakpoint that jumps the size in one step.
- Keep `10,000` + unit inside the tile on 800×400, 800×480, and 1024×600
  (see `tests/e2e/live-metrics-layout.spec.ts`).

## Overflow + tap (always consider)

Any list, chip row, or other region that can overflow **must**:

1. Scroll by dragging on the content (not only via a scrollbar).
2. Still allow selecting a **single** item with a tap.

Do not put unbounded chip rows in `PanelHeader` actions. They clip the title.
Put them in the panel body (above tiles/lists) with a single-line horizontal
scroller. Keep a pinned control like **All** outside that scroller so it never
scrolls away.

### How

Reuse `useDragScroll` / `createDragScrollController` (`src/hooks/useDragScroll.ts`,
`src/utils/dragScroll.ts`):

- Vertical lists: `useDragScroll()` (axis `y`). See `ShotsPanel`.
- Horizontal chip rows: `useDragScroll('x')`. See `StatsPanel`.
- Capture the pointer **only after** `DRAG_SCROLL_THRESHOLD_PX` (16px) so a
  tap still clicks. After a real drag, `onClickCapture` swallows the click so
  the item under the finger is not selected.
- Set `touch-action: none` on the scroller **and** its tappable children
  (`button`, row mains, chips).

### Tests

- Unit-test drag vs tap on the controller (threshold, axis, click suppress).
- E2E: mouse-drag must scroll without activating the item; `hasTouch: true`
  must still tap-select one item.
