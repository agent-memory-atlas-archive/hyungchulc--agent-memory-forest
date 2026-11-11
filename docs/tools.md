# Structure tools

The commands that keep a forest in order: `forest move …`, `forest levels …` and `forest collections …`
change where things live; `forest check`, `forest lint`, `forest record …` and `forest numbers …` check
the pages and keep their records and numbers. Each example below runs on an invented forest of a bakery
owner, Ilse Varrow, with a `bakery` tree, a `people` tree and an agent tree `front-desk`. The rules the
tools keep are in the [contract](contract.md); the settings they read are in the
[design](v1-design.md#12-settings).

## An agent's tree, with no command for it

An agent of the forest needs no command. The till-counting bot appends its first line to its raw lane,
and the next upkeep step that settles levels makes its tree:

```sh
ls "06 istm/till-bot"
# 2051-03-02.jsonl
forest levels check
# 06 istm/till-bot/: level_due: till-bot none -> tree: an agent is a tree from its first day (its raw lane holds a file)
forest levels apply
# till-bot: none -> tree (an agent is a tree from its first day (its raw lane holds a file))
```

`02 ltm/till-bot_LTM.md`, `03 mtm/till-bot/till-bot_MTM.md` and the line
`` - `till-bot` — till-bot · (not set) `` under `## Agents` now exist. To give it a name and a role, declare
it in `forest.toml` before its first line, or rename it afterwards:

```toml
[agents.known.till-bot]
name = "Till Bot"
role = "counts the till each evening"
```

With `agents.auto_create = false` a lane makes no tree and every agent is declared this way. The rules
are in the [contract](contract.md), section 3, Agent trees.

## What every writing command does

- It plans the whole change in memory first: every moved page, every rewritten reference, every derived
  list, every `## Update record` line. It then checks the planned forest (registry, homes, up-links,
  references, and that no Timeline line or collection entry was lost) and writes nothing if a check fails.
- It writes under the forest's writer lock. A page that changed on disk since it was read stops the
  write, and a failure while writing puts every page back.
- It rewrites every up-link, list line, registry line, `about:`/`entity:` value and code-span path that
  named the old place. It leaves `## Update record` lines, quoted (`>`) lines and fenced blocks as they are.
- It appends one line per moved path to `.forest/moves.jsonl` (a page, a collection entry or registry line
  as `<page>#<id>`, a folder moved whole), and one `Path changed: was "…", now "…". Why: …` line to the
  moved page's `## Update record`. A page whose lines came from or went to another page gets a line naming
  that page's path (``Lines merged in from `<path>` ``); a page that was only rewritten because another moved, and
  a derived list brought up to the pages, get no record line. A page taken out goes to `.forest/archive/`,
  and references to it are marked ` (archived)`.
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
| `move new-agent <id> [--name NAME] [--role "…"] [--alias A]` | creates an agent's tree and its line under `## Agents` at once (the same operation `levels apply` uses) |
| `move retire-tree <id> [--into PAGE]` | ends an agent (status `ended`, its pages stay), or archives an emptied tree into the page that took its rules |
| `move fold-tree <id> --into <tree> [--as NAME] [--type TYPE]` | folds a tree back into a branch of another tree; its old branches become its leaves' `subtopic:`; an agent takes `--type` (default `agents.folded_type`) and leaves `## Agents` |
| `move set-type <id> <type>` | retypes a subject; writes the change on its index and nothing in the move log |
| `move set <leaf> [--kind K] [--about IDS] [--standing "…"]` | sets one leaf's frontmatter; writes each change on the leaf and nothing in the move log |
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

`levels check [--subjects ID …]` lists every level change that is due, from the pages: an agent declared
in the settings or with a file in its raw lane and no tree yet is due its tree; an entry recorded
on `levels.leaf_days` distinct days (or with a standing line, or with more material than a collection
keeps) is due a leaf; a leaf subject recorded on `levels.branch_days` days is due a branch; a branch
subject of one of `levels.tree_types` with `levels.tree_subtopics` sub-topics that each hold enough leaves
over enough days is due a tree. Days are the dates as written in `## Timeline`; reference lines,
superseded lines and lines that only record the system's own work (an agent's or stage's name followed by
one of `levels.work_verbs`, or a tool name followed by `alerted`) count no day. It exits 1 when something
is due.

`levels apply [--subjects ID …]` carries each change out (an agent's tree through `move.ensure_agent`, an
admission through the collections tool, a promotion through the move tool), each as its own checked plan, with the measured reason in the move log.
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

`forest check [NAME …]` runs every check, or the ones named, and prints one line per problem with its
path and its code; it exits 1 when there is one.

| Check | What it finds |
| --- | --- |
| `structure` | page roles, frontmatter keys, sections and their order, up-links, leaves outside their subject's home, boards without folders, trees and agents without their pages or root lines |
| `lists` | derived lists that differ from the pages: `board_leaves_stale`, `branch_list_stale` (also `## Trees` and `## Agents`), `board_pointers_stale` |
| `pointers` | `board_pointer_stale`: a pointer to the board's own leaf, or a bare name instead of a path |
| `agents` | `agent_id_invalid`: a lane folder or declared agent whose name cannot be an agent's id; with `agents.auto_create` false, `agent_lane_without_tree` |
| `levels` | `level_due`: what `forest levels apply` would carry out |
| `categories` | `category_too_large`: a category branch over `limits.category_max_leaves` |

The derived lists are the `## Leaves` and `## Pointers` of each board, the `## Branches` of each tree page
and tree index, and the `## Trees` and `## Agents` of the root index. `forest check --sync` first brings
them up to the pages under the writer lock, then runs the same checks. A line's hand-written description
is kept, its `(updated …)` date follows the page, a line whose page is gone is dropped, a second line for
the same page is dropped, and a missing line is added with a description taken from the page.

```sh
forest check
# 02 ltm/bakery_LTM.md: branch_list_stale: `## Branches` misses `ovens`
# 03 mtm/bakery/ovens.md: board_leaves_stale: `## Leaves` misses `deck-oven.md`
forest check --sync
# 02 ltm/bakery_LTM.md: ## Branches brought up to the pages
# 03 mtm/bakery/ovens.md: ## Leaves brought up to the pages
forest check lists pointers
```

## `forest lint`

`forest lint` checks every rule of the contract that has a shape (section 8) and prints a summary, each
violation, the upkeep counted by code with an example, and the due work. It exits 1 when there is a
violation, or, with `--against`, when the run added a violation or an upkeep hit a run may not add.
`--baseline FILE` writes the present hits, `--codes CODE …` runs only those codes and `--json` prints all.

```sh
forest lint --baseline .forest/baselines/before.json
# forest lint: 1 violations, 2 upkeep, 0 due
# violation account_number_in_full  04 stm/bakery/ovens/deck-oven.md:12 — a phone number in full
# upkeep record_over_budget 1: a record over `limits.record_keep_bytes` is trimmed (`forest record trim`); e.g. …
# upkeep record_run_note 1: a record holds only the three kinds of line; …
# not checked: daily_section_unfiled
forest lint --against .forest/baselines/before.json
```

## `forest record`

`record size PATH …` prints a page's content and record bytes, its record lines by kind and what is due;
`record kinds [--examples N]` counts the forest's record lines by kind and page; `record trim PATH … | --all
[--dry-run]` moves what a record holds over `limits.record_keep_bytes` out of the page (contract, section
4, Record trim).

```sh
forest record size "04 stm/bakery/ovens/deck-oven.md"
# 04 stm/bakery/ovens/deck-oven.md: content 346 B, record 2619 B (change 19, owner-words 0, provenance 0, run-note 1); due: trim
forest record kinds --examples 1
# 04 stm/bakery/ovens/deck-oven.md: change 19, run-note 1
# total: change 19, owner-words 0, provenance 0, run-note 1
#   change  04 stm/bakery/ovens/deck-oven.md:24  - 2051-01-01 — Baking temperature: was "201 C", now "202 C". Why: …
#   run-note  04 stm/bakery/ovens/deck-oven.md:43  - 2051-03-02 — rechecked the page, nothing changed.
forest record trim "04 stm/bakery/ovens/deck-oven.md"
# 04 stm/bakery/ovens/deck-oven.md: moved 5 lines, kept 15 (the record holds 2619 bytes, over 2048) -> …
# 1 of 1 pages trimmed
```

## `forest numbers`

`numbers check PATH … | --all` names the pages that write an account, card, identity or person's phone
number in full, by line and kind and never by digits, and exits 1 when there is one; `numbers star PATH …
| --all [--dry-run]` stars them, keeping a whole-page copy in the archive first (contract, section 4,
Account and identity numbers).

```sh
forest numbers check --all
# 04 stm/bakery/ovens/deck-oven.md: 1 phone in full (lines 12)
# 1 pages
forest numbers star "04 stm/bakery/ovens/deck-oven.md"
# the line now reads: Bram Eskell, who fixes it: +1 5** *** 0199.
```
