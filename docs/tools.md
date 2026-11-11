# Structure tools

The commands that change where things live in a forest: `forest move …`, `forest levels …`,
`forest collections …` and `forest check`. Each example below runs on an invented forest of a bakery
owner, Ilse Varrow, with a `bakery` tree, a `people` tree and an agent tree `front-desk`. The rules the
tools keep are in the [contract](contract.md); the settings they read are in the
[design](v1-design.md#12-settings).

## What every writing command does

- It plans the whole change in memory first: every moved page, every rewritten reference, every derived
  list, every `## Update record` line. It then checks the planned forest (registry, homes, up-links,
  references, and that no Timeline line or collection entry was lost) and writes nothing if a check fails.
- It writes under the forest's writer lock. A page that changed on disk since it was read stops the
  write, and a failure while writing puts every page back.
- It rewrites every up-link, list line, registry line, `about:`/`entity:` value and code-span path that
  named the old place. It leaves `## Update record` lines, quoted (`>`) lines and fenced blocks as they are.
- It appends one line per moved path to `.forest/moves.jsonl`, and one `Path changed: was "…", now "…".
  Why: …` line to the moved page's `## Update record`. A page taken out goes to `.forest/archive/`, and
  references to it are marked ` (archived)`.
- Common options: `--dry-run` prints the plan as JSON and writes nothing; `--why "…"` gives the reason the
  move log and the record lines carry; `--lock-token` lets a caller that already holds the writer lock run
  it; `--json` prints the result as JSON; `--root` names the forest (by default the one above the current
  folder).

A refused command exits 2 and prints its error code (for example `target_exists` or
`branch_holds_other_subjects`).

## `forest move`

| Command | What it does |
| --- | --- |
| `move leaf <source> <target> [--entity ID]` | moves one leaf to another name or into another branch folder |
| `move rename <id> [<new id>] [--name NAME]` | gives a subject a new id (its board, folder, leaves and every `entity:`/`about:` follow) or a new name (the old one becomes an alias) |
| `move rename <tree>/<branch> <new name>` | renames a branch: its board and its folder |
| `move promote <id>` | a leaf subject becomes a branch; a branch subject becomes a tree |
| `move merge <keep> <drop>` | two subjects that are one: the dropped one's leaves, lines, names and ids go to the kept one |
| `move archive <path> --reason "…" [--successor PATH]` | takes a page out into the archive |
| `move move-branch <tree>/<branch> <tree> [--as NAME]` | moves a branch with its board, folder and registry lines to another tree |
| `move new-tree <id> --description "…" [--name NAME]` | creates an area tree and its line under `## Trees` |
| `move new-agent <id> --name NAME --role "…" [--alias A]` | creates an agent's tree and its line under `## Agents` |
| `move retire-tree <id> [--into PAGE]` | marks an agent's tree ended, or archives an emptied tree into the page that took its rules |
| `move fold-tree <id> --into <tree> [--as NAME] [--type TYPE]` | folds a tree back into a branch of another tree; its old branches become its leaves' `subtopic:` |
| `move set-type <id> <type>` | retypes a subject |
| `move set <leaf> [--kind K] [--about IDS] [--standing "…"]` | sets one leaf's frontmatter |
| `move recut-stream <id> <leaf> …` | cuts a stream of event pages of one subject again by period |
| `move plan <file.json>` | runs many operations as one plan: all are written, or none |
| `move resolve <old path>` | follows the move log from an old path to the present page or the archive |

Examples:

```sh
# The oven repairs page gets a clearer name.
forest move leaf "04 stm/bakery/deck-oven/repairs.md" "04 stm/bakery/deck-oven/fixes.md"
# leaf 04 stm/bakery/deck-oven/repairs.md -> 04 stm/bakery/deck-oven/fixes.md
#   created: 04 stm/bakery/deck-oven/fixes.md
#   changed: 03 mtm/bakery/deck-oven.md
#   removed: 04 stm/bakery/deck-oven/repairs.md

# The old deck oven is now called the hearth oven; "Deck Oven" stays as an alias.
forest move rename deck-oven hearth-oven --name "Hearth Oven"

# The flour mill has come up on many days: it gets a board of its own.
forest move promote millstone-flour --why "weekly deliveries since January"

# The same mill was filed twice under two ids.
forest move merge millstone-flour millstone-mill

# A page about an oven that was sold.
forest move archive "04 stm/bakery/deck-oven/fixes.md" --reason "the oven was sold"

# The oven branch belongs with the kitchen tree.
forest move move-branch bakery/deck-oven kitchen

# A new area and a new agent.
forest move new-tree garden --description "the herb garden behind the bakery."
forest move new-agent till-bot --name "Till Bot" --role "counts the till each evening" --alias "the till"

# The front-desk agent was switched off; its tree stays as history.
forest move retire-tree front-desk

# The front-desk agent's notes become one branch of the bakery tree.
forest move fold-tree front-desk --into bakery --type component

# Retype, and set one leaf's fields.
forest move set-type hearth-oven component
forest move set "04 stm/bakery/deck-oven/fixes.md" --kind plan --about millstone-flour

# Two month pages of repairs join into one year page while it stays under the cap.
forest move recut-stream deck-oven "04 stm/bakery/deck-oven/repairs-2051-01.md" \
    "04 stm/bakery/deck-oven/repairs-2051-02.md"

# Where did an old path go?
forest move resolve "04 stm/bakery/deck-oven/repairs.md"
```

A plan file is a JSON list of operations (or an object with an `operations` list). Each item names its
operation in `op` and gives the same arguments the command takes, by their long names:

```json
[
  {"op": "rename", "target": "bakery/suppliers", "new": "mills"},
  {"op": "promote", "subject": "millstone-flour"},
  {"op": "admit", "entry": "tamsin-orrow"}
]
```

```sh
forest move plan reshape.json --dry-run   # see the whole plan first
forest move plan reshape.json --why "spring tidy-up"
```

Plan items: `leaf` (`source`, `target`, `entity`), `rename` (`target`, `new`, `name`), `promote`
(`subject`), `merge` (`keep`, `drop`), `archive` (`path`, `reason`, `successor`), `move-branch` (`branch`,
`tree`, `name`), `new-tree` (`tree`, `description`, `name`), `new-agent` (`id`, `name`, `role`,
`aliases`), `retire-tree` (`tree`, `into`), `fold-tree` (`tree`, `into`, `name`, `type`), `set-type`
(`subject`, `type`), `set` (`path`, `kind`, `about`, `standing`), `recut-stream` (`subject`, `leaves`),
`demote` (`subject`), `admit` (`entry`), `recut` (`page`).

## `forest levels`

`levels check [--subjects ID …]` lists every level change that is due, from the pages: an entry recorded
on `levels.leaf_days` distinct days (or with a standing line, or with more material than a collection
keeps) is due a leaf; a leaf subject recorded on `levels.branch_days` days is due a branch; a branch
subject of one of `levels.tree_types` with `levels.tree_subtopics` sub-topics that each hold enough leaves
over enough days is due a tree. Days are the dates as written in `## Timeline`; reference lines,
superseded lines and lines that only record the system's own work (an agent's or stage's name followed by
one of `levels.work_verbs`, or a tool name followed by `alerted`) count no day. It exits 1 when something
is due.

`levels apply [--subjects ID …]` carries each change out (an admission through the collections tool, a
promotion through the move tool), each as its own checked plan, with the measured reason in the move log.
A change that is refused is listed with its error and the others go on. No level is lowered by count.

```sh
forest levels check
# 04 stm/people/regulars/seen-once-2051-q1.md#tamsin-orrow: level_due: tamsin-orrow entry -> leaf:
#   2 distinct days (2051-03-02 to 2051-03-04) (a leaf from 2)
forest levels apply --subjects tamsin-orrow
# tamsin-orrow: entry -> leaf (2 distinct days (2051-03-02 to 2051-03-04) (a leaf from 2))
```

## `forest collections`

| Command | What it does |
| --- | --- |
| `collections demote <id>` | folds a leaf subject recorded on too few days into its category's collection page |
| `collections admit <entry>` | moves a collection entry out into a leaf of its own and registers it |
| `collections recut <page>` | cuts a collection page past the cap by its entries' first days |
| `collections list` | names every collection page with its entries and their counted days |

A collection page holds one category's entries for one period (`seen-once-2051-q1.md`). An entry's address
is `<page>#<entry id>`; each command logs it, so `move resolve` follows an entry onto a page, across a
recut and out into its leaf.

```sh
forest collections demote bram-eskell
forest collections admit tamsin-orrow
forest collections recut "04 stm/people/regulars/seen-once-2051-q1.md"
forest collections list
# 04 stm/people/regulars/seen-once-2051-q1.md (647 B, regulars-seen-once)
#   tamsin-orrow — Tamsin Orrow: 2051-03-02, 2051-03-04
#   bram-eskell — Bram Eskell: no counted day
```

## `forest check`

The derived lists are the `## Leaves` and `## Pointers` of each board, the `## Branches` of each tree page
and tree index, and the `## Trees` and `## Agents` of the root index. `forest check` lists every one that
differs from the pages and exits 1; `forest check --sync` brings them up to the pages. A line's hand-written
description is kept, its `(updated …)` date follows the page, a line whose page is gone is dropped, a
second line for the same page is dropped, and a missing line is added with a description taken from the
page.

```sh
forest check
# 03 mtm/bakery/deck-oven.md: board_leaves_stale: ## Leaves
#   + - `fixes.md` — deck-oven (updated 2051-03-05)
#   - - `repairs.md` — deck-oven (updated 2051-03-02)
forest check --sync
# 03 mtm/bakery/deck-oven.md: ## Leaves brought up to the pages
```
