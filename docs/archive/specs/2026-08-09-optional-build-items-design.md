# Optional Battery and Camera Build Items

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

## Goal

Document two optional OpenFlight build components in both the canonical parts
list and the Raspberry Pi setup prerequisites:

- Geekworm X1202 four-cell UPS HAT for Raspberry Pi 5
- InnoMaker OV9281 global-shutter camera module

## Documentation Changes

Add both products to the existing `Optional` table in `docs/PARTS.md`, using
the supplied Amazon links. List the UPS HAT at approximately $48 and note that
it requires four compatible 18650 Li-ion cells, sold separately for roughly
$25 total. List the camera at approximately $30.

Add matching bullets under a current `Optional` heading in
`docs/raspberry-pi-setup.md`. Keep this heading separate from the existing
deprecated K-LD7 hardware so builders do not mistake the new items for
deprecated components.

## Scope

This is a purchase-list and prerequisite-checklist update only. It does not add
installation instructions, change the core cost summary, or claim that either
item is required for OpenFlight operation.

## Verification

Review the Markdown tables and lists for valid formatting, confirm both Amazon
links exactly match the supplied URLs, and inspect the final Git diff for
unrelated changes.
