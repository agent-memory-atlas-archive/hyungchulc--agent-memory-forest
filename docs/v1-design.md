# Agent Memory Forest v1: design

This page is the design of version 1. It says what the forest is, what each part must do, and where each
part lives in the package. The rules a forest must keep are in [`contract.md`](contract.md); this page is
about the software around them.

## 1. What it is for

A long-running agent, or a team of them, needs a memory that

- people can read and correct: plain Markdown pages, no opaque store;
- stays findable as it grows: every fact has one home, and the place of a page is decided by rules, never
  by which chat, tool or agent happened to bring the fact in;
- keeps its history: a changed fact keeps what it was before, a moved page is found under its old path,
  and nothing is deleted;
- is kept in order by code: a registry, level rules, a move tool with a log and a linter with codes, so a
  model does the reading and writing and the code decides shape and place;
- serves many agents at once: shared trees for what everyone needs, one tree per agent for what is the
  agent's own, and an orchestrator that reads the whole forest.

It is a local library and a set of command-line tools. It is not a hosted service and it holds no
credentials. An embedding provider is optional; everything except vector search works with no network.

## 2. What changed since 0.3

| 0.3 | 1.0 |
| --- | --- |
| pages filed by layer and by date | the **subject** is the unit; a page's place follows its subject |
| free-form branch names | a **registry** line per subject, with aliases, compiled to JSON |
| promotion by a plan a model writes | **levels set by code** from the number of distinct days a subject has |
| a page per sighting | **collections**: subjects seen on one day share a page until they return |
| leaves of any size | a **leaf cap** (8 KB of content); a leaf over it splits by sub-topic |
| summaries anywhere | summaries only above the leaves; **what goes up** needs repeated evidence |
| links between leaves | one **home** per fact, a reference line everywhere else, `about:` pointers |
| an audit | a **lint** with codes, a baseline, and due work for a nightly upkeep run |
| renames by hand | a **move tool**: every move rewrites its references and is logged; old paths resolve |
| one writer assumed | a writer lock, per-agent raw lanes, **agent trees** and reader scopes |

## 3. On disk

```
<root>/
  forest.toml                    settings (section 12)
  01 xltm/XLTM.md                the root index: identity, the list of trees, the list of agents
  02 ltm/<tree>_LTM.md           one page per tree
  03 mtm/<tree>/<tree>_MTM.md    the tree's index: its registry (## Subjects) and its branches
  03 mtm/<tree>/<branch>.md      one board per branch
  04 stm/<tree>/<branch>/*.md    the leaves, and a category's collection pages
  05 daily/YYYY-MM-DD.md         the readable record of each day (a lane, not forest)
  06 istm/                       the raw record, append-only (a lane, not forest)
  .forest/                       derived and kept state: registry.json, moves.jsonl, archive/,
                                 the search index, the time index, lint baselines, locks
```

The four numbered layers above the lanes are the forest. Everything under `.forest/` except `moves.jsonl`
and `archive/` can be deleted and rebuilt.

## 4. Subjects, the registry and levels

- A **subject** is a person, a group, an organisation, a project, a product, an app, a device, a place,
  an event, a title, a topic, an admin item, a component of the system, or an **agent**. The list of types
  is a setting; `agent` and `collection` are always in it.
- Every subject has one **registry line** in the `## Subjects` section of its home tree's index:
  ``- `<id>` — <name> · <type> · <level> · `<home>` · aliases: <a>; <b> · <status>``.
  `forest registry build` compiles all of them into `.forest/registry.json`, which search and the lint read.
- The **level** of a subject is set by code from one measure, its **distinct days**: the number of local
  dates on which its own material records something.
  - under 2 days: an **entry** of its category's collection page;
  - from 2 days, or at once with a `standing:` line: one **leaf** in a category branch;
  - from 5 days: its own **branch** (a board and a folder of leaves, one per sub-topic);
  - a **tree** when 3 of its sub-topics each span 5 days and hold 3 leaves, for the types allowed to be
    trees. A sub-topic made only of `received`, `notice` or `brief` leaves does not count: what a subject
    was sent is not a part of it. An **agent** is a tree from its first day (section 6).
  The thresholds are settings. A level never goes down by count.
- `forest levels check` lists each subject whose level is due; `forest levels apply` carries every change
  out through the move tool and logs it. No model judges a level and nothing waits for a later run.

## 5. Pages

- A **leaf** is one sub-topic of one subject, in full detail, never a summary. Frontmatter: `entity`,
  `subtopic`, `kind` (one of a fixed list: `conversation`, `event`, `visit`, `trip`, `plan`, `application`,
  `purchase`, `received`, `notice`, `brief`, `watched`, `read`, `link`, `rule`, `profile`, `work`,
  `collection`), `about` (the other subjects it holds something about, or `none`), `timezone`,
  `updated_at`, and where they apply `standing` (the owner's standing statement that keeps a subject a
  leaf from its first day), `date` (a page about one dated event) and `status: superseded` with
  `superseded_by`. Body: free sections, then `## Timeline` (one line per dated activity; the time index
  reads them), `## Links` (one `up:` link) and `## Update record`.
- The **cap** is 8 KB of a leaf's content. Over it the leaf splits by sub-topic; a pure event list may
  split by period (year, quarter, month). A file name carries no date except that period.
- A **board** (`03 mtm/<tree>/<branch>.md`) says what its subject is, its current state by kind, its
  rules, its leaves (`## Leaves`, derived from the folder) and its pointers (`## Pointers`, derived from
  the `about:` of leaves elsewhere).
- A **tree page** (`02 ltm/<tree>_LTM.md`) holds the tree's scope, its durable patterns and rules, its
  current state, and one line per branch.
- Above the leaves a line is carried up only when it is established: repeated evidence, or the owner's own
  statement. Current-state lines carry `(as of DATE; source PATH)` and are stale when their source changed.
- An `## Update record` holds only the page's own history, in three kinds of line: what a statement of
  the page was before it changed (`was "…", now "…"`), what was taken out or corrected at the owner's word
  about the page, and where the page's own lines came from or went. It is bounded on every page, a leaf
  or above: the newest lines that fit a small budget stay with the owner's removals and corrections, and
  older lines move verbatim to the page's archive copy, where the subject timeline still reads the
  changes. A run's note about its own work (how many lines it touched, that it rechecked and changed
  nothing, that it rewrote a page's form) is never written on a page: it goes with the run's receipts,
  and a run that changes no statement writes no record line.

## 6. Agents

An **agent** is a worker that a model runs under its own instructions over many runs and that acts: an
orchestrator, a bot that owns a kind of work, a scheduled stage, the sessions of a coding assistant. The
engine or model it runs on is a tag on the lines it touches; when the engine changes, the agent stays.
A helper started for one task works as the agent that started it.

- **A tree per agent, made by code.** `forest move new-agent <id> --name … --role …` creates the agent's
  registry line (type `agent`, level `tree`), its tree page, its index and its line under `## Agents` on
  the root index. A subject that was filed as a branch and turns out to be an agent is retyped
  (`set-type <id> agent`) and promoted by `forest levels apply` with its leaves. Inside its tree the same
  rules hold as anywhere: sub-topics are branches, leaves follow the leaf rules, levels follow days.
- **Its tree page is what the agent reads first**: `## Role`, `## Standing instructions`, `## Lessons`,
  `## Current state`, `## Branches`.
- **What is filed there** follows the forest's `agents.policy`:
  - `subject-first` (the default for one owner served by several agents): a fact about a subject lives in
    that subject's home whichever agent learned it; the agent is a tag on the line (`noted by <agent>`).
    The agent's tree holds what is the agent's own: its role and the instructions given to it, its methods
    and the lessons of doing its work, its runs and their outcomes, its open work and hand-offs.
  - `agent-first` (for a team whose agents work on separate matters): what an agent learns is filed in
    its own tree, each leaf naming in `about:` the shared subjects it concerns, so a shared subject's board
    points to it. A fact two agents hold, or one the owner states for everyone, is **shared**: the upkeep
    run moves it to the shared subject's home and each agent's leaf keeps a reference line.
- **Who reads what.** Every reader has an id, taken from its connection and never from the question. An
  orchestrator (`agents.orchestrators`) reads the whole forest. Any other agent reads the shared trees and
  its own tree, and another agent's tree only when `agents.default_scope` allows it or its task names that
  agent. Each tool call records which trees answered.
- **Who writes.** Agents do not edit forest pages at the same time. Each appends to its own raw lane
  (`06 istm/<agent>/`), the daily record is written from the lanes, and one filing run at a time, holding
  the forest writer lock, files into the forest. Direct edits go through the same lock.
- **Ending.** A retired agent keeps its tree with status `ended`. `fold-tree` turns an agent's tree back
  into a branch of another tree; nothing is lost and old paths resolve.

## 7. Moves, archive, records

- `forest move` moves a leaf, promotes or merges subjects, renames, creates and folds trees, re-cuts a
  stream of event pages by period, and sets frontmatter. Each operation rewrites every link, list line,
  registry line and code-span path that named the old place and appends one line per path to
  `.forest/moves.jsonl`. `forest move resolve <old path>` follows the log to the present place.
- `.forest/archive/` holds one compressed copy per archived version of a page, under the date it was
  archived, with a manifest line (old path, hash, size, reason, the leaf it was folded into). Nothing is
  deleted. `forest archive show <old path>` prints the latest copy and `forest archive list` every copy.
- Machine output (hash tables, run receipts, recorder rows, derived views) never becomes a forest page.

## 8. The lint

`forest lint` checks every rule that has a shape and reports each hit with a code and a path: violations
(a leaf outside its subject's home, an unknown entity, a dated or numbered name, a registry line out of
shape, a stale reference, a leaf over the hard limit), upkeep (rules the forest still breaks on many
pages, counted with examples), and due work (a level that is due, a category that grew too large). A run
that writes pages is checked against a baseline taken when it started: it must fix every violation and
every upkeep hit its own pages added, and it is never blocked by what was already there.

## 9. Time

`## Timeline` lines are indexed by instant and by the time zone the line was written in, so "yesterday
evening" matches each entry's own local date and "the last three hours" is a window of instants.
`forest timeline query "<when>"` and `forest timeline subject <id>` read it.

## 10. Retrieval

A question is first matched to subjects through the registry's aliases, then to the likely tree and
branch, then to leaves: coarse to fine, stopping at the level that answers. A global word search and, when
an embedding provider is set, a vector search run beside it. A subject named in the question also brings
the leaves that name it in `about:`. Results are page sections as written, never generated summaries.
The MCP server (`forest mcp`) offers search, read, navigate and timeline with the reader's scope.

## 11. The pipeline

`06 istm` (raw, append-only) → `05 daily` (one readable page per day, appended in marked blocks) →
the filing run (reads the blocks it has not filed, at most a bounded amount per run, newest days first)
→ the forest. A nightly upkeep run applies levels, works the lint's upkeep list down, trims records and
re-checks stale current-state lines. The stages are plain programs around a model of your choice; the
package supplies their input and commit steps, their cursors and receipts, and the prompts as files.

## 12. Settings (`forest.toml`)

```toml
[forest]
timezone = "UTC"
language = "en"                 # memory text; quoted words always stay in their own language
[owner]
id = "owner"
name = "the owner"
aliases = []
[limits]
leaf_cap_bytes = 8192
leaf_max_bytes = 20480
record_keep_bytes = 2048
category_max_leaves = 100
about_max_ids = 8               # the most subjects one leaf names in `about:`
entry_admit_margin_bytes = 1024 # room a collection page keeps when it admits a large entry
repeat_min_lines = 3            # the same lines on two leaves of one subject: counted from this many
repeat_min_chars = 40           # ... each at least this long
[numbers]
identity_formats = []           # national identity number shapes to mask, "<shape>:<hide>": N a digit,
                                # A a letter; hide = after-hyphen | last-<n>, e.g. ["NNNNNN-NNNNNNN:after-hyphen"]
account_words = ["account", "acct", "a/c"]  # words (and bank names) before a number that make it an account
reference_words = [...]         # words before a number that keep it whole: order, parcel, invoice, booking ...
identity_words = [...]          # words before an identity shape that make it an identity number
phone_patterns = []             # regular expressions for national phone forms; the international form
                                # (+ and 8 to 15 digits) is always recognised
keep_whole = []                 # regular expressions for a company's or a service's line, kept whole
[subjects]
types = [...]                   # the subject types (section 4); `agent` and `collection` are always in it
system_id = ""                  # the id of the system's own subject, when the forest keeps one
[names]
date_words = [...]              # month and season words a file name must not carry
[levels]
leaf_days = 2
branch_days = 5
tree_subtopics = 3
tree_subtopic_days = 5
tree_subtopic_leaves = 3
tree_types = ["project", "topic", "product", "device", "title", "agent"]
stream_kinds = ["received", "notice", "brief"]  # sub-topics made only of these do not count toward a tree
stream_subtopic_words = [...]   # sub-topic names that mark such a stream
stage_names = [...]             # a Timeline line that only records the system's own work
work_verbs = [...]              #   (a stage or tool name followed by one of these verbs)
tool_names = [...]              #   does not count as a day of its subject
[lint]
channel_words = [...]           # channel words a leaf's file name must not carry
remark_phrases = [...]          # phrases that mark a writer's remark left on a page
[record]
owner_word_names = []           # name forms that mark the owner's words in a record line; empty: the
                                # owner's name and aliases. A name form, then in the same clause a quotation
quote_marks = ['""', "“”", "«»", "„“"]  # the quote-mark pairs (opening, closing) such a quotation uses
[agents]
policy = "subject-first"        # or "agent-first"
orchestrators = []
default_scope = "own+shared"    # or "all"
[embeddings]
provider = "none"               # none | openai-compatible
```

## 13. Package map

| Module | Does | Command |
| --- | --- | --- |
| `config`, `layout` | settings, layer paths, slugs | `forest init` |
| `model` | parses pages: frontmatter, sections, Timeline and record lines | |
| `locking` | the forest writer lock and stage locks | |
| `registry` | registry lines, the compiled JSON, `agents`, `tree_kinds` | `forest registry` |
| `movelog`, `move` | operations, reference rewriting, the move log | `forest move` |
| `levels` | distinct days, due levels, apply | `forest levels` |
| `collections` | collection pages: demote, admit, recut | `forest collections` |
| `record`, `archive` | record trim, the compressed archive | `forest record`, `forest archive` |
| `maps`, `validate`, `audit` | derived lists (Leaves, Pointers, Branches), structure checks | `forest check` |
| `lint` | the codes, baselines | `forest lint` |
| `timeline` | the time index and time phrases | `forest timeline` |
| `search`, `scope` | the search index, coarse-to-fine retrieval, reader scopes | `forest search`, `forest read` |
| `mcp` | the MCP server | `forest mcp` |
| `stage` | daily store, bounded filing interval, cursors, receipts | `forest stage` |

## 14. Milestones

1. **Skeleton.** The contract, this design, a synthetic forest with shared trees and agent trees that
   passes the lint, and the structure tools: registry, move, levels, collections, record, archive, maps,
   validate, lint, with tests on made-up forests.
2. **Finding.** The time index, search with scopes, the MCP server.
3. **Growing.** The stage programs and their prompts, the nightly upkeep.
