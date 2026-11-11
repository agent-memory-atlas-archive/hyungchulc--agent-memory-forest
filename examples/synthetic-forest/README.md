# Synthetic forest

An invented forest that follows the [version 1 contract](../../docs/contract.md). Juno Hale, Brindlemoor
Clayworks, the town of Saltmere, Kiln Two, Cinderwell Supply, every person and every agent below are made
up. Nothing here describes a real person, company or product.

The forest is the four numbered layers `01 xltm` to `04 stm`; `05 daily` and `06 istm` are its lanes;
`forest.toml` holds its settings. This file is not part of the forest.

## What it shows

| Rule | Where |
| --- | --- |
| three shared trees listed under `## Trees`, with placement rules | `01 xltm/XLTM.md` |
| an orchestrator and two bots, each with its own tree (agent shape, registry line, two branches) | `02 ltm/studio-hub_LTM.md`, `02 ltm/kiln-bot_LTM.md`, `02 ltm/ledger-bot_LTM.md` |
| a collection page with three entries | `04 stm/people/acquaintances/seen-once-2042-q2.md` |
| a leaf subject in a category branch | `04 stm/people/acquaintances/odile-brask.md` |
| a branch subject with four sub-topics | `03 mtm/studio/kiln-two.md` and `04 stm/studio/kiln-two/` |
| an event list split by period | `04 stm/studio/kiln-two/firings-2042-q1.md`, `firings-2042-q2.md` |
| an episode | `04 stm/studio/kiln-two/repairs.md` |
| a changed fact with its record line and a superseded Timeline line | `04 stm/studio/kiln-two/settings.md` |
| a leaf that carries `about:`, and the board whose `## Pointers` lists it | `04 stm/admin/shops/cinderwell-supply.md`, `03 mtm/studio/kiln-two.md` |
| a masked card number | `04 stm/admin/shops/cinderwell-supply.md` |
| `subject-first` filing: facts in the subject's home, `noted by <agent>` tags, the agents' trees holding their runs and lessons | `04 stm/studio/kiln-two/`, `04 stm/kiln-bot/` |
| two days of the daily record and a raw lane per agent | `05 daily/`, `06 istm/` |

The code that checks these shapes (`forest lint`) arrives with milestone 1 of the
[design](../../docs/v1-design.md); until then this tree is a reference to read, not a fixture the 0.3
`memory-forest` command can validate.
