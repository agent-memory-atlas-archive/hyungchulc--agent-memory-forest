# Structure tools

The commands that keep a forest in order: `forest move …`, `forest levels …` and `forest collections …`
change where things live; `forest check`, `forest lint`, `forest record …`, `forest numbers …` and
`forest credentials …` check the pages and keep their records, numbers and sign-in values.
`forest intake` is the writer of a raw lane. Each example below runs on an invented forest of a bakery
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
# 06 istm/till-bot/: level_due: till-bot none -> tree: agent; its raw lane holds a file
forest levels apply
# till-bot: none -> tree (agent; its raw lane holds a file)
```

`02 ltm/till-bot_LTM.md`, `03 mtm/till-bot/till-bot_MTM.md` and the line
`` - `till-bot` — till-bot · (not set) `` under `## Agents` now exist. To give it a name and a role, declare
it in `forest.toml`, before its first line or afterwards: the next `forest levels apply` fills the name
and the role the tree still leaves unset, each with a change line on its tree page
(`till-bot: tree -> tree (agent; its settings entry gives its unset name and role)`):

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
  write, and a failure while writing puts every page back. Before its first write it keeps an undo
  journal, `.forest/journal/commit.json`; if its process is killed, the next command that takes the writer
  lock rolls the half-written change back and says so in one line, and `forest recover` does the same by
  hand.
- It rewrites every up-link, list line, registry line, `about:`/`entity:` value and code-span path that
  named the old place. It leaves `## Update record` lines, quoted (`>`) lines and fenced blocks as they are.
- It appends one line per moved path to `.forest/moves.jsonl` (a page, a collection entry or registry line
  as `<page>#<id>`, a folder moved whole), and one `Path changed: was "…", now "…". Why: …` line to the
  moved page's `## Update record`. A page whose lines came from or went to another page gets a line naming
  that page's path (``Lines merged in from `<path>` ``); a page that was only rewritten because another moved, and
  a derived list brought up to the pages, get no record line. A record line names a person's number only
  starred, also when a merged subject was saved under its number. A page taken out goes to `.forest/archive/`,
  and references to it are marked ` (archived)`.
- Common options: `--dry-run` prints the plan as JSON and writes nothing; `--why "…"` gives the reason the
  move log and the record lines carry; `--lock-token` lets a caller that already holds the writer lock run
  it; `--json` prints the result as JSON; `--root` names the forest. When `--root` is omitted, the forest
  is the nearest folder at or above the current one that holds `forest.toml`. `forest init` is the only
  command that creates a forest. Any other command, when there is no such folder, stops with a message
  and writes nothing.

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
| `move recut-stream <id> <leaf> …` | cuts a stream of event pages of one subject again by period: a year, its quarters, a quarter's months, and a month still past the cap into runs of whole days |
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

# A month of repairs still past the cap: runs of whole days, the first keeping the month's name.
forest move recut-stream deck-oven "04 stm/bakery/deck-oven/repairs-2051-02.md"
#   created: 04 stm/bakery/deck-oven/repairs-2051-02-11.md
#   changed: 04 stm/bakery/deck-oven/repairs-2051-02.md

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
| `collections cite-entries [--dry-run]` | once, for a forest written before entry sources: makes each current-state line that cites a bare collection page cite the entries it names |

A collection page holds one category's entries for one period (`seen-once-2051-q1.md`). An entry's address
is `<page>#<entry id>`; each command logs it, so `move resolve` follows an entry onto a page, across a
recut and out into its leaf, and rewrites every current-state source and path that cited the old address.

A current-state line about one entry cites that entry, so that another entry's news does not make it read
as stale (contract, section 5):

```text
- Tamsin Orrow wants rye on Fridays (as of 2051-03-02; source `04 stm/people/regulars/seen-once-2051-q1.md#tamsin-orrow`)
```

`cite-entries` finds the entries a line names by their names, the name in another script and their
aliases, compared as the registry compares names; a line that names none keeps the bare page. It brings
an `as of` that is later than the cited entries' evidence back to that evidence and leaves an earlier one
for the lint to report. Each changed page is archived first; only the tag changes and no record line is
written. With `--dry-run` it prints the counts and writes nothing; a second run changes nothing.

```sh
forest collections demote bram-eskell
forest collections admit tamsin-orrow
forest collections recut "04 stm/people/regulars/seen-once-2051-q1.md"
forest collections list
forest collections cite-entries --dry-run
# 04 stm/people/regulars/seen-once-2051-q1.md (647 B, regulars-seen-once)
#   tamsin-orrow — Tamsin Orrow: 2051-03-02, 2051-03-04
#   bram-eskell — Bram Eskell: no counted day
```

## `forest check`

`forest check [NAME …]` runs every check, or the ones named, and prints one line per problem with its
path and its code; it exits 1 when there is one.

| Check | What it finds |
| --- | --- |
| `journal` | `commit_journal_standing`: a commit a killed process left half-written (`forest recover`) |
| `structure` | page roles, frontmatter keys, sections and their order, up-links, leaves outside their subject's home, boards without folders, trees and agents without their pages or root lines |
| `lists` | derived lists that differ from the pages: `board_leaves_stale`, `branch_list_stale` (also `## Trees` and `## Agents`), `board_pointers_stale` |
| `pointers` | `board_pointer_stale`: a pointer to the board's own leaf, or a bare name instead of a path |
| `agents` | `agent_id_invalid`: a lane folder or declared agent whose name cannot be an agent's id; with `agents.auto_create` false, `agent_lane_without_tree` |
| `levels` | `level_due`: what `forest levels apply` would carry out |
| `categories` | `category_too_large`: a category branch over `limits.category_max_leaves` |

`forest check --sync` first brings the derived lists (each board's `## Leaves` and `## Pointers`, the
`## Branches` of tree pages and indexes, the root index's `## Trees` and `## Agents`) up to the pages, then
runs the same checks; what a sync keeps, drops and adds is in the [contract](contract.md), section 9.

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

## `forest repeats`

Two leaves of one subject that share fact lines (`leaf_lines_repeated`) hold a copy; `forest repeats` takes
each line to its one home (contract, section 3, One home for each fact).

| Command | What it does |
| --- | --- |
| `repeats plan [--decisions FILE]` | prints what `apply` would do, and each shared line that still waits for a reading, by subject, digest and line; writes nothing |
| `repeats apply [--decisions FILE]` | removes reference lines that stand in the subject's index leaf, moves a period page's reference to a sibling period into it, takes each decided line to its home, archives the pages left empty with the index leaf as successor, and appends each `both` to the judged ledger |
| `repeats check` | the count left; exits 1 while a run would change something or a line waits for a reading |

A decisions file is a JSON list; `home` is the leaf that keeps the line, or `both` when the line is rightly
in more than one leaf:

```json
[
  {"subject": "deck-oven", "digest": "3f0c9a51d2e7b864", "home": "04 stm/bakery/deck-oven/history.md",
   "reason": "the profile page is where the oven's size is kept"},
  {"subject": "deck-oven", "digest": "8b21e07a4c6f9d13", "home": "both",
   "reason": "Ilse wrote the safety rule on both pages on purpose"}
]
```

```sh
forest repeats plan
# repeats: lines taken to their homes in 2 leaves
#   04 stm/bakery/deck-oven/repairs-2051-01.md: 3 lines would be taken out, kept in 04 stm/bakery/deck-oven/history.md
#   04 stm/bakery/deck-oven/fan.md: would be archived, every line standing in 04 stm/bakery/deck-oven/history.md
#   judged: 0; waiting for a reading: 2; hits after: 2
#   deck-oven 3f0c9a51d2e7b864: 04 stm/bakery/deck-oven/history.md:24, 04 stm/bakery/deck-oven/repairs.md:13
forest repeats apply --decisions decisions.json
forest repeats check
# leaf_lines_repeated: 0 hits; 0 lines a run takes out; 0 pages it folds; 0 lines wait for a reading
```

## `forest record`

`record size PATH …` prints a page's content and record bytes, its record lines by kind and what is due;
`record kinds [--examples N]` counts the forest's record lines by kind and page; `record trim PATH … |
--all [--dry-run]` moves what a record holds over `limits.record_keep_bytes` out of the page (contract,
section 4, Record trim); `record history PATH` prints the lines trims moved out.

```sh
forest record size "04 stm/bakery/ovens/deck-oven.md"
# 04 stm/bakery/ovens/deck-oven.md: content 346 B, record 2619 B (change 19, owner-words 0, provenance 0, run-note 1); due: trim
forest record kinds --examples 1
# 04 stm/bakery/ovens/deck-oven.md: change 19, run-note 1
# total: change 19, owner-words 0, provenance 0, run-note 1
#   change  04 stm/bakery/ovens/deck-oven.md:24  - 2051-01-01 — Baking temperature: was "201 C", now "202 C". Why: …
#   run-note  04 stm/bakery/ovens/deck-oven.md:43  - 2051-03-02 — rechecked the page, nothing changed.
forest record trim "04 stm/bakery/ovens/deck-oven.md"
# 04 stm/bakery/ovens/deck-oven.md: moved 5 lines, kept 15 (the record holds 2619 bytes, over 2048) -> .forest/archive/record-history/04 stm/bakery/ovens/deck-oven.md
# 1 of 1 pages trimmed
forest archive check                # every archive copy and record history block against the manifest
```

The moved lines go, as one block, to the page's record history file, and the page keeps one pointer line to
it for life; a later trim appends another block and adds nothing to the page. `record history PATH` prints
the lines a page's history file and archive copies keep, oldest first, and `forest archive list` shows each
block beside the whole-page copies, with its kind:

```sh
forest record history "04 stm/bakery/ovens/deck-oven.md"
# - 2051-01-01 — Baking temperature: was "201 C", now "202 C". Why: …
# …
forest archive list --path "04 stm/bakery/ovens/deck-oven.md"
# 2051-03-02T07:40:12Z  record-history  04 stm/bakery/ovens/deck-oven.md  5 lines 2051-01-01 to 2051-01-05  .forest/archive/record-history/04 stm/bakery/ovens/deck-oven.md
```

## `forest numbers`

What a tool shows is starred the same way: every `forest` command prints a person's number, an account, a
card or an identity number only starred and a sign-in value only as `[REDACTED]` (`archive show`, `record
history`, `record kinds --examples`, `registry show`, `registry check`, `check`, `lint`, `collections list`
and the rest), and so do the 0.3 reads `search`, `route`, `retrieve` and `structured-context`, while the
archive copy and the history file keep their bytes.

`numbers check PATH … | --all` names the pages that write an account, card, identity or person's phone
number in full, by line and kind and never by digits, and exits 1 when there is one; `numbers star PATH …
| --all [--dry-run]` stars them, keeping a whole-page copy in the archive first (contract, section 4,
Account and identity numbers). `numbers star` changes only the pages it is given: name them, or pass
`--all` for every page; with neither it refuses and writes nothing.

```sh
forest numbers check --all
# 04 stm/bakery/ovens/deck-oven.md: 1 phone in full (lines 12)
# 1 pages
forest numbers star "04 stm/bakery/ovens/deck-oven.md"
# the line now reads: Bram Eskell, who fixes it: +1 5** *** 0199.
```

## `forest credentials`

A value sent to sign in or to pay is removed rather than starred: the line keeps who sent it, to whom and
for which account, and `[REDACTED]` in the value's place (contract, section 4, Sign-in values). It is found
by the word before it, from `credentials.label_words`; a forest that records other languages adds their
words in `forest.toml`:

```toml
[credentials]
label_words = ["password", "passcode", "pin", "one-time code", "door code", "mot de passe", "code porte"]
joining_words = ["is", "was", "est"]
```

Every record line a tool writes is masked as it is written, and everything a command prints is masked
(see `forest numbers` above for the reads).
`credentials check PATH … | --all` names the pages that hold such a value, by line and never by value, and
exits 1 when there is one; `credentials fix PATH … | --all [--dry-run]` masks them, keeping a whole-page copy
in the archive first and writing the page only after that copy reads back as the page's bytes. Like
`numbers star`, `credentials fix` takes the pages it names or `--all` for every page, and refuses with neither.
With no page named, or with `--all`, `check` also counts for each lane the lines that today's rules would
mask (written under an earlier rule); `fix --all` rewrites them in place, the file replaced whole with the
same number of lines and only the masked spans changed.

```sh
forest credentials check --all
# 04 stm/bakery/suppliers/millstone-flour.md: a sign-in value in full (lines 14)
# 1 pages
# 06 istm/front-desk: 2 lines that today's rules would mask
forest credentials fix --all
# the line now reads: Ilse sent the supplier portal password: [REDACTED].
```

A code with no label word (`"it is 739154, see you at the side door"`) is for a reader to judge, once.
`credentials candidates` lists each code-shaped token in a quoted turn that nobody has judged, as
`PATH:LINE:PLACE` and never by value (the lint counts them as `credential_candidate_unjudged`);
`credentials judge PATH:LINE:PLACE … --sign-in` keeps an HMAC-SHA256 digest of each value in
`.forest/credentials/sign-in.jsonl`, under a key made on first use in `.forest/credentials/key` (mode
0600), and from then on that value is masked wherever it appears; `--not-sign-in` keeps its digest in
`judged.jsonl` so it is not counted again. Neither the key nor a digest is printed.

```sh
forest credentials candidates
# 04 stm/bakery/suppliers/millstone-flour.md:14:1
# 1 candidates wait for a reading
forest credentials judge "04 stm/bakery/suppliers/millstone-flour.md:14:1" --sign-in
forest credentials fix --all
```

## `forest intake`

`forest intake` is the only writer of a raw lane. It reads a tool's transcripts through an adapter and
appends one JSON line per new turn to `06 istm/<agent>/<YYYY-MM-DD>.jsonl`, the day in the forest's time
zone. The record and the writer's rules are in the [contract](contract.md), section 1.

The package ships four adapters: `claude-code`, `codex`, `grok` and `jsonl`. `jsonl` reads a folder of
files that are already in the record's shape. Another tool is another module in the entry point group
`memory_forest.adapters`.

Intake for a tool stays off until `[intake.tools.<adapter>] enabled` is true. Until then the command says
so and takes nothing, unless `--once` is given. The agent is `--as`, or `agent` in that table. Without
either, the command stops and says how to set it. `include` and `exclude` are folders the session ran in.
The first sweep of a tool, `forest intake --from <adapter>`, turns intake on for it: it places a cursor
at the end of every transcript that exists (unless `import_earlier` is true, when it takes them from their
start) and records that it did. A hook never turns intake on. Before that sweep a hook reads nothing and
adds one line to a counter that needs no lock, which `status` shows with the command to run; setting a
tool up includes that first sweep (today by hand, later by `forest setup`). After it, a transcript with
no cursor is a new one and is read from its start. `--dry-run` prints a count per transcript and writes
nothing under the forest, also when the tool's state cannot be read. `forest intake reread --from
<adapter>` sets that adapter's cursors back to where intake first placed them, never further, and leaves
an excluded cursor alone. A tool's cursors are kept by tool and each names the agent whose lane its
turns went to; a transcript whose cursor names another agent than the one now set is not read, and
`status` counts those transcripts for each pair of names. `forest intake rename-agent OLD NEW` moves the
lane and the id index and sets the name in each cursor, in an order that a second run completes after
an interruption.

A hook runs the same command with `--hook` and the tool's JSON on stdin, and reads only that transcript.
A missed hook on the same transcript is caught by its next hook. Other transcripts are caught by a sweep.
In hook mode the process exits 0 and prints nothing, on every path, because a client can treat exit 2
as a block and can add stdout to the model's context. A failure is one line under `.forest/intake/`
(its class and its place, and for a run that failed after it stored turns, how many: `N turns, then
failed`). If the intake lock is already held, the command stops at once and leaves the cursor; a hook
that finds it held leaves one marker for its transcript, which the run that reads that transcript
removes. A hook reads at most 256 KiB; a line longer than that is the sweep's, and the hook notes once
that the transcript waits for a sweep. A line the adapter does not understand is skipped and counted
by its type. One turn that raises is counted and does not stop the transcript. `forest intake status`
shows that count, the turn faults, the internal sessions, the tool version where the transcript states
it, sources whose file is ahead of their cursor, the transcripts waiting for a sweep, the busy markers,
the hook calls that came before intake was turned on, and the last few hook failures. A cursor file that is not valid is renamed aside and the source
is read again. A plugin that claims a built-in name is off and named here; the built-in stays.

```sh
# Turn intake on for the till bot's exported records, and take what is already there.
# In forest.toml:
# [intake.tools.jsonl]
# enabled = true
# agent = "till-bot"
# path = "transcripts"
# import_earlier = true

forest intake --from jsonl --dry-run
# till-bot: 2 turns would be taken in
#   2051-03-02.jsonl: 2 turns
forest intake --from jsonl
# till-bot: 2 turns taken in
forest intake status
# jsonl: on, agent till-bot, unknown 0
forest levels check
# 06 istm/till-bot/: level_due: till-bot none -> tree: agent; its raw lane holds a file
```

A tool turn records that the tool ran (`ran <name>`), not what it printed, unless `intake.tool_output` is
`keep`. A forest stage's own session is not taken in: its first owner turn opens with the line
`MEMORY_FOREST_INTERNAL`, the stage records it, and a hook sees the name in its environment. Setup is not part of this command. Search, read, timeline and the MCP server are the next
sections.

## `forest index`

`forest index build` writes the search index. With no pointer the file is `.forest/index.sqlite`.
The file is derived: `forest index reset` points at a new file, and the next build makes the same rows. One row is one page section, one collection entry, one marked block of a daily
page, or one raw-lane turn. The text in the row is the text a read would print, with sign-in values
masked and numbers starred. The build holds the `index` lock and writes the file in place, in one
transaction in WAL mode, so a search that is already running keeps reading the last committed index and
does not wait. A build or an update never replaces or unlinks the file or its journal files; only
`forest index reset`, below, removes them. A second build that finds the
lock held says so and returns. A file whose size and time are unchanged is skipped. A raw lane that only
grew has the bytes already indexed read and hashed, in 1 MiB pieces, to prove they are unchanged
(8 MiB in about 4 ms on a development machine with the file in the page cache, which is the cost of the
hash; a read from disk adds the disk's own time). Only the new complete lines are
parsed, and a last line without its newline waits for it. `forest index build --verify`, and a build
whose stored hash is more than a day old, hashes those files too and rewrites a file only when the bytes
changed. A page that is not valid UTF-8 is read with replacement characters, named in the status, and
does not stop the build.

When the masking changes (a `[credentials]` or `[numbers]` setting, a value judged into the ledger, or
the package's masking rules), the next build, update or search indexes every file again before it
answers, so a search cannot find a value a read now hides. The fingerprint check and the answer are two
snapshots: a masking change that lands between them is answered under the old rules, the same as a query
that arrived a moment earlier. A query whose masking changed while the index is held waits for the
other build, trying a busy file again every tenth of a second, for up to 60 seconds, and then refuses
with `index_busy` rather than answer from rows masked under the old rules.

What SQLite refuses is sorted by its result code:

| Code | When | Next move |
| --- | --- | --- |
| `index_unreadable` | the file or the pointer is damaged: the file is corrupt or is not a database, a table, an index or the rows of a table that held rows are missing, or the pointer does not name an index file | `run` `forest index reset` |
| `index_busy` | the index lock is held, or the index file is in use | `retry` after `after_seconds`, stop at `stop_after_seconds` |
| `index_read_only` | the index file cannot be written; or the folder the index lives in (`.forest/`, or the forest root while there is no `.forest/`) cannot be written, and the call would build or reset, or the index is missing, damaged, its pointer damaged, its masking changed since the build, or a `-wal` file stands beside it. The text says whether reading still answers and names what an account that can write would run | `report` |
| `index_disk_full` | the disk is full | `report` |
| `index_io_error` | the disk returned an I/O error, or the locking protocol failed and this call's budget is spent | `report` |
| `index_cannot_open` | the path cannot be opened or created (a folder in its place) | `report` |
| `index_missing` | there is no index and none could be built yet | `run` `forest index build` |
| `index_failed` | SQLite refused for a reason this package does not classify, or a lock conflict inside this connection | `report` |
| `index_reset_refused` | the index pointer is not a file, so a reset cannot publish a new name | `report` |
| `symlink_forbidden` | a symlink stands where the index or its pointer would be | `report` |

A build that does not build is a refusal: `index_busy`, a non-zero exit, next move `retry`. The message
says which cause it can tell apart, the index lock or the index file being in use. It does not
guess which program holds the file. On `retry`,
`waited_seconds` is how long this call already waited and `stop_after_seconds` is the total after which
the caller reports instead of calling again. A refusal that has already waited that whole time is
reported at once and not tried again. A protocol error is tried again only inside a query's reads and
inside a write. `status`, the timeline and opening the file turn it into an I/O refusal at once.
A search with the same masking answers `stale`. `report`
means no command helps: the message says what a person has to do. Resetting a working index on a full
disk would lose it and the rebuild would fail again. A file whose first build has not committed yet is
not refused: it is no index yet.

Every command reads one state of the derived folder: `sound`, `masking_changed`, `missing`, `damaged`,
`pointer_damaged` or `wal` (design, section 10). A refusal names it in `details.state`, and `index
status` in `state`. When there is something to do, `index status` also carries `next`, `command` and
`message`, as a refusal does: `run` `forest index build` for a missing index, a masking change or
files changed since the build, in a folder this account can write. A `.forest/` folder this process
cannot write (or a forest root it cannot write, with no `.forest/` yet) can still be searched when the
index is sound: the answer says `read_only: true`, and `stale` is the count of lane files and daily
pages changed since the build. Every other state there is `index_read_only`, for a read and for a
write: the text says nothing is answered and names what an account that can write runs, `forest index
build`, or `forest index reset` and then `forest index build` for a damaged index or pointer. A build
or a reset of a sound index there is `index_read_only` too, and its text says reading still answers.
Nothing on disk changes in such a folder. A search never counts a page of a tree changed since the
build: only `index status` lists those pages, with `next`, and a build brings them in.

`forest index reset` is for an index that is corrupt or damaged. The index file's name carries a
generation. `.forest/index.current` names the current file, and with no pointer `.forest/index.sqlite`
is the current file, so a forest made before this still opens. Under the index lock the command chooses
a name that has not been used, writes the pointer in mode 0600 and syncs it, then renames it into place.
Only then does it remove the old database and its `-wal`, `-shm` and `-journal` files, the sidecars
first, each when it is present, and a temporary pointer file (`.index.current.<8 characters>.tmp`) a
killed reset left. Its answer says `next: run` and `command: forest index build`. A file that cannot be removed is named in the output and is not a failure: it is an orphan
nothing reads. The next reset or build removes orphans, only files of the index's own naming pattern in
`.forest/`, never a symlink, never a folder, and never the current file. A call that already has a file
open finishes on that file, so a forest server may be running. The lock keeps a build or an update out.
When the lock is held the command removes nothing and exits 2 with `index_busy`, next move `retry`.
The next query or `forest index build` makes the index again, at the new path. `forest index reset
--dry-run` says what it would do and writes nothing.

`forest index status` prints the tokenizer, the counts, whether the masking is current, how many files
have changed since the build, and any page that was not valid UTF-8. Where this machine's SQLite has the trigram tokenizer, the line says
`trigram+unicode61`: a run of three or more characters is found inside unspaced text, and spaced words of
any length are found too. A CJK word of one or two characters is found inside a longer word either way.
Where trigram is absent, the line says `unicode61`.

```sh
forest index build
# index: .forest/index.sqlite
# tokenizer: trigram+unicode61
# files: 40  units: 180  updated: 40
forest index status
# stale: 0
```

## `forest search` and `forest read`

A search brings changed lane files and daily pages into the index first. When a build already holds the
lock, or the folder cannot be written, the JSON answer includes `stale` and the count of those files,
and the search reads the index that is already published. A changed page of a tree is not counted:
a search does not walk the trees, `index status` lists the page, and a build brings it in.

`forest search` tries registry names and aliases first. A subject named in the question brings its home
and every leaf that names it in `about:`. Word matches come after that. A vector match is added only
when `[embeddings] provider` is not `none` and a provider of that name has been registered in the
process. This package registers none and never opens a network connection.

Results are one per page. Each matching section is printed as written, with why it matched and the lines
to read. `--as` is the reader. Omit it and the reader is not an agent: shared trees only, and no raw
lane. `--as-group` with `--agent` is the same read for one member of a group (design, section 6). Pass
one of `--as` and `--as-group`, not both. `--agent` without a group cannot change the reader.
`--named` is an agent the task names. It is how one agent is allowed into another's tree. It does
not open that agent's raw lane.

`forest read` prints a page, or the section an address names. `--lines A-B` keeps that range. The text
is masked the same way as every other read. A path outside the reader's trees or lane is refused, and
the refusal does not quote the page. A path that is absolute or climbs out with `..` is `path_escape`:
a forest path is written relative to the forest root.

```sh
forest search "the old oven" --as till-bot
# 04 stm/bakery/ovens/deck-oven.md
#   why: alias: "the old oven" names deck-oven
#   ...
# trees: bakery
forest read "04 stm/bakery/ovens/deck-oven.md#head" --as till-bot --lines 1-12
forest search "the old oven" --as-group bakery-bots --agent till-bot
```

The answer ends with the trees that produced a result.

## `forest timeline`

`forest timeline query` takes a phrase or a range. English phrases are built in (`yesterday evening`,
`the last 3 hours`, `yesterday`, a bare date). Korean phrases ship as an example table,
`memory_forest/phrases/ko.json` (`어제 저녁`, `지난 3시간`). A forest adds or replaces a language with
JSON files in `.forest/phrases/`. A day-part phrase is judged in each entry's own time zone, and a line
with no zone of its own uses the page's `timezone:`. A duration, and a range written
`2051-03-01..2051-03-02`, is one window of instants. Hours that are still only in a raw lane are included,
including a lane written since the last manual build.

`forest timeline subject <id>` lists the indexed sections of that subject, in time order, inside the
same scope.

```sh
forest timeline query "yesterday evening" --as front-desk
forest timeline query "지난 3시간" --as front-desk
forest timeline subject deck-oven --as till-bot
forest navigate --as till-bot
forest navigate "03 mtm/bakery/bakery_MTM.md" --as-group bakery-bots --agent till-bot
```

`forest navigate` is the command for `memory_navigate`: one level down from the root index, or from a
path the previous call showed. The scope is the reader's.

## `forest log`

`forest log` is the command-line twin of `memory_log`. The turn is read from stdin. An argument is
refused, because arguments show in process lists, and the refusal says to pass the text on stdin. The
command sets the role to `agent` and the origin to `interactive`, the same as `memory_log` on stdio. A
caller-supplied role or origin is refused. `--session` names the session. The default session is `cli`. The turn goes through the one
writer of the lane, with the same masking, and a turn the lane already holds is not written again.

```sh
forest log --as till-bot <<'EOF'
the till counted the trays
EOF
forest log --as-group bakery-bots --agent "Till Bot"
```

## `forest agents`

`forest agents` lists each group, whether it is open, and each member: its id, its name, whether it has
written a lane, and when it was last seen on a connection. Agents that are not in a group are listed
after the groups.

## `forest mcp`

`forest mcp --as <agent>` speaks MCP on stdio. The tools are `memory_search`, `memory_read`,
`memory_timeline`, `memory_navigate` and `memory_log`. Each description says when to use it. The
server's own instructions stay under 2,048 characters. The reader is `--as`. A tool argument cannot
set it, and cannot set the role or origin of `memory_log`. `forest mcp --as-group <group>` is the same
server for several agents on one connection. Exactly one of `--as` and `--as-group` is given. On a
group connection every tool requires `agent`, the caller's own name, resolved only to a member of the
group. Stdio and `--http` use the same token rule.

`memory_navigate` starts at the root index and walks one level down. `memory_log` appends the caller's
own turn through the intake writer. The server sets the role to `agent` and the origin from the
connection: `interactive` on stdio, `headless` on HTTP.

Stdio is one JSON-RPC message per line. Nothing but those messages is written to stdout.

`--http 8765` binds to loopback. Every HTTP bind, loopback included, uses `.forest/mcp/token` in mode
0600. The first start creates the file and never prints it. The client sends that token as a bearer
token. A request with an `Origin` that is not loopback is refused, and on a loopback bind so is a `Host`
that is not a loopback name with this server's port. The body must be `application/json`. Reaching a
non-loopback port from outside the machine is the owner's own network decision; the server does not open
that path itself.

`memory_log` reports `indexed: true` or `indexed: false`. False means the line is in the lane and the
index was not updated. `reason` names which refusal that was: the index lock was busy, or the index was
read-only, damaged, or full, or another refusal from the table above.

Every refusal of a tool is a result with `isError: true` whose text is one JSON object, the same object
`--json` prints under `error`:

```json
{"code": "index_busy", "details": {"path": ".forest/index.sqlite"},
 "message": "...", "next": "retry", "after_seconds": 2.0}
```

`code`, `message`, `details` and `next` are always there. `next` is `run` with `command`, `retry` with
`after_seconds`, `waited_seconds` and `stop_after_seconds` (call again after `after_seconds` while the
sum of each answer's `waited_seconds` and `after_seconds` is below `stop_after_seconds`, and report the
message once that sum would reach it), `report` (tell the owner the message; no command helps), or
`fix` (the call itself is wrong: change what the message names, such as empty words to `search_empty`,
and call again). Every code a tool or a command can send, with its next move, is in
[refusals.md](refusals.md); a test proves that table and the codes the package raises are the same.
`tool_failed`, a tool that could not answer for a reason the package does not name, is `report`, and
`out_of_scope`, a path outside the reader's trees and lane, is `fix`: read a path a search or a
navigation showed. An argument that fails a tool's schema under 2025-11-25 is `arguments_invalid`
(`fix`) in the same shape. The strings are masked like every answer. What the forest holds, and what the caller sent
(a page's lines, a snippet, the question), is returned exactly, even when a line is a path. What the
server says itself carries no host name, process id, user name, home folder or absolute path. A
refusal's `details` name a path as the caller gave it, or relative to the forest; `real_path`, `root`
and an absolute `source` are for the terminal only, and the door drops them. In a refusal's own text
the forest root becomes a forest path (the root alone becomes `.`) and the home folder becomes `~`.
Who reads decides what is folded: an answer an agent reads, from an MCP tool or from a command run
with `--as`, or with `--as-group` and `--agent`, carries no host, process, user, home folder or absolute
path, as JSON and as text, refusals included, because both doors fold through one function. Without
those flags a command answers the owner at the terminal, and its paths and a lock's owner stay.
`lock_busy` and `lock_already_held` leave a tool as one busy refusal, next `retry`. `waited_seconds`
is how long that call waited. The command line still shows the lock's owner, and the absolute path,
to the person at the terminal. Two `memory_log` calls in one server process share one budget, the
same two seconds as the intake file lock: the second waits its turn, and a first call that does not
end answers the second with `lock_busy` once that budget is spent, not again afterwards.

The clients checked here, `claude mcp`, `codex mcp` and `grok mcp`, speak protocol versions
2024-11-05, 2025-03-26, 2025-06-18 and 2025-11-25, over stdio and streamable HTTP. Claude and Grok also
offer the older SSE transport. This server does not. It answers with the newest of those versions that
the client names. On stdio a JSON-RPC batch is accepted for 2025-03-26 only after `initialize`, and a
batch that holds `initialize` is refused. HTTP keeps no session, so a 2025-03-26 batch that does not
contain `initialize` is answered from the `MCP-Protocol-Version` header. The other three revisions
refuse a batch on both transports. An argument a tool does not accept is a tool result with `isError: true`
under 2025-11-25 and a `-32602` error under the earlier versions; an unknown tool is `-32602` under all.
A stdio line over 1,000,000 bytes gets one error and the next line is read. The HTTP
`MCP-Protocol-Version` response header is the version negotiated with that client.

The first tool call of each agent is recorded in `.forest/mcp/seen.json`, with the agent, the time, the
tool and the transport. Each later call records when the agent was last seen. The question is not stored.
`forest doctor` can later use that file to prove the server was called. `forest doctor` itself is not built yet.

```sh
forest mcp --as till-bot
forest mcp --as till-bot --http 8765
forest mcp --as-group bakery-bots --http 8765
```
