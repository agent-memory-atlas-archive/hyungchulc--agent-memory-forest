---
entity: kiln-bot
---

# Kiln Bot

## Role

Runs Kiln Two's firings on the program Juno picks, watches the controller during a firing, and reports
faults to Studio Hub. Under the forest's `subject-first` policy, what it learns about the kiln is filed in
the kiln's own home with `noted by kiln-bot`; this tree holds its runs and its methods.

## Standing instructions

- Fire glaze loads to the limit in `04 stm/studio/kiln-two/settings.md`, never above it.
- Stop a firing and report when the temperature has not risen for twenty minutes.

## Lessons

- After new elements are fitted, a firing climbs faster; watch the last hour closely (as of 2042-05-13; source `04 stm/kiln-bot/methods/cooling-curves.md`).

## Current state

- Last run: the glaze firing of 2042-05-13, ended normally (as of 2042-05-13; source `04 stm/kiln-bot/firing-runs/glaze-firing-runs.md`).

## Branches

- `firing-runs` — each firing Kiln Bot ran, with its program and outcome.
- `methods` — how Kiln Bot runs and watches a firing, and what it learned doing it.

## Links

- up: [[01 xltm/XLTM|XLTM]]

## Update record
