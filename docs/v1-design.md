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
                                 repeats-judged.jsonl, the search index, the time index, lint
                                 baselines, locks, journal/
```

The four numbered layers above the lanes are the forest. Everything under `.forest/` except `moves.jsonl`,
`archive/` and `repeats-judged.jsonl` (the lines a reading judged rightly held by more than one leaf) can be
deleted and rebuilt.

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
  split by period (year, quarter, month, and a month still over the cap into runs of whole days). A file
  name carries no date except that period.
- A **board** (`03 mtm/<tree>/<branch>.md`) says what its subject is, its current state by kind, its
  rules, its leaves (`## Leaves`, derived from the folder) and its pointers (`## Pointers`, derived from
  the `about:` of leaves elsewhere); `forest check --sync` brings the derived lists up to the pages
  (contract, section 9).
- A **tree page** (`02 ltm/<tree>_LTM.md`) holds the tree's scope, its durable patterns and rules, its
  current state, and one line per branch.
- Above the leaves a line is carried up only when it is established: repeated evidence, or the owner's own
  statement. Current-state lines carry `(as of DATE; source PATH)` and are stale when their source changed.
- An `## Update record` holds only the page's own history, in three kinds of line: what a statement or a
  frontmatter value of the page was before it changed (`was "…", now "…"`), what was taken out or corrected at the owner's word
  about the page, and where the page's own lines came from or went. It is bounded on every page, a leaf
  or above: the newest lines that fit a small budget stay with the owner's removals and corrections, and
  older lines move verbatim to the page's record history file (one per page for its whole life, only
  appended to, one block per trim), named by the one pointer line the page keeps, where the subject
  timeline still reads the changes. A trim's cost follows what moves, not the page. A run's note about its own work (how many lines it touched, that it rechecked and changed
  nothing, that it rewrote a page's form) is never written on a page: it goes with the run's receipts,
  and a run that changes no statement writes no record line.

## 6. Agents

An **agent** is a worker that a model runs under its own instructions over many runs and that acts: an
orchestrator, a bot that owns a kind of work, a scheduled stage, the sessions of a coding assistant. The
engine or model it runs on is a tag on the lines it touches; when the engine changes, the agent stays.
A helper started for one task works as the agent that started it.

- **A tree per agent, made by code, with no command to run.** An agent exists for the forest from its
  entry in the settings (`[agents.known.<id>]`, `name` and `role` optional) or from the first file in its
  raw lane `06 istm/<id>/` (unless `agents.auto_create` is false). A turn written with `memory_log` or
  `forest log` makes that one agent's tree, or leaves it pending while another writer holds the lock (the
  next write or the level pass makes it). `forest levels check` reports every
  other such agent without a tree as due one from its first day, and `forest levels apply` makes it through
  `move.ensure_agent`, the operation `forest move new-agent <id> [--name …] [--role …]` also uses: the
  registry line (type `agent`, level `tree`), the tree page, the index and the line under `## Agents`. A
  name not given is the id and a role not given is the line `(not set)`; a value the tree left unset is filled
  from the settings by the next `forest levels apply` (`move.fill_agent`, a change line on the tree page),
  and a value set in both places is never changed by code (contract, section 3). A lane folder that is
  not a slug, a shared tree's id or a reserved name never becomes a tree (`agent_id_invalid`). A subject
  that was filed as a branch and turns out to be an agent is retyped (`set-type <id> agent`) and promoted
  by `forest levels apply` with its leaves. Inside its tree the same rules hold as anywhere: sub-topics
  are branches, leaves follow the leaf rules, levels follow days.
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
  agent; a reader that is no agent reads the shared trees. `agents.readable_trees(config, registry,
  reader_id)` is that rule as one function. Each tool call records which trees answered.
- **Several agents, one connection.** A group (`[agents.groups.<id>]`) is one app the owner runs, with
  several named agents and one connection: `forest mcp --as-group <id>`, or a command with `--as-group`
  and `--agent`. Exactly one of `--as` and `--as-group` is given. On a group connection every tool takes
  `agent`, the caller's own name. With `--as` the tools are unchanged, and no argument can set the reader.
  - **The name alone decides, in any script** (`names`). A name's key is the name in NFKC, case-folded,
    with white space, hyphens and dashes of every kind and underscores taken out; every other character
    counts (`Kiln Bot`, `kiln_bot` and `ＫＩＬＮ-ＢＯＴ` are one key; `Kiln.Bot` and `Kíln Bot` are others;
    `스카우트 봇` and `스카우트봇` are one; `Разведчик` and `РАЗВЕДЧИК` are one). The key is the only
    comparison of agent names: the group connection, a command's `--agent`, the settings parser, a source
    of members and `forest agents` use it. A name finds an agent in two ways only: it is the agent's id
    character for character, or its key is the key of the agent's stored name (an agent with no name is
    named by its id). A name whose key no agent has is a new agent. The only bad names are empty text,
    more than 200 characters, and text with a control character or a line break (`fix`).
  - **The id is a handle, made once.** A new agent's id is made from its name when it is admitted and
    stored beside the name (`[agents.known.<id>]`); no call makes it again. It is ASCII, letter for letter,
    one table per script, kept as data in `letters/` (Latin letters lose their accents; Hangul by the
    Revised Romanization of each syllable's three parts; kana by Hepburn; Greek and Cyrillic by one table
    each): not a language's spelling, and no sound changes. A name with any letter, digit or symbol no
    table carries (Han characters, a script with no table, a symbol) gets `agent-` and six hexadecimal
    digits of the SHA-256 of its key. An id held by something that is not an agent is followed by
    `-agent`; one held by an agent with another key by the smallest free number from 2. An agent the
    owner declares with an id and a name is found by that name: that is how a person chooses an id, before
    the agent's first turn. After it, a tree carries the id and no move changes it.
  - A name some agent already has is that agent, whichever group or tool it first came from, and on an
    open group it becomes a member here as well. One name in two apps is one agent, one lane and one tree.
    Inside a group a caller is whoever it says it is, so any member can read what another member's own
    tree and lane hold by calling under that member's name. A group tells one owner's agents apart for
    memory and is no wall between them. A closed group (`open = false`) refuses a name that is not a
    member, lists the members' ids and names, and the caller retries under their own name as listed. An
    open group (`open = true`) admits every new name at once. There is no greatest number of members
    unless the owner sets `max_members`. `forest agents` lists them. A group may name a `source` of
    members: then the source is the list. Its read has a deadline and a greatest size, the last list is
    used for 60 seconds, and a source that fails is `member_source_failed` (`report`), never "not a
    member". On a group connection a caller that names the main agent sees what the main agent sees. An
    orchestrator needs no entry in `members`.
  - **An admission patches bytes.** A second identical admission changes no byte of the settings. Two
    calls that would both admit the same new name end as one member and one settings write. The patch
    keeps every byte of `forest.toml` and adds one run inside the group's `members` and one table at the
    end; a group it does not patch safely, or a file it cannot write, is refused with the edits for the
    owner, in words and in `details.edits`. A value of the agents' tables that is wrong for one entry (a
    member written twice, a field of the wrong kind, an unknown field) is reported (`agent_setting_invalid`
    or `agent_id_invalid`) and that entry is left out; everything else loads.
  - **Where a turn came from** is on the turn: `sources`, each `group:<id>`, `door:<name>` or
    `adapter:<name>`, beside the session. Search, read and timeline take a source in that form; a reading
    narrowed to it keeps the file's line numbers and is narrowed before its limit.
  - **A call is a turn.** Each `memory_log` or `forest log` call is a turn with an id of its own. The
    caller's own `turn_id` makes a repeat one turn; the same words through two different sources within
    120 seconds are one line that carries both sources. The first lane write makes that agent's tree
    through the level tool for that agent only, waiting up to 2 seconds for the writer lock; the answer
    says the turn was written and names the tree and the index as `made`, `already there`, `pending` or
    `updated`. A pending tree is made by the next write of that agent or by the level pass. A lane that
    arrives another way is made by `forest levels apply`. One id the settings cannot place is reported
    and the pass goes on with the rest.
- **Who writes.** Agents do not edit forest pages at the same time. Each appends to its own raw lane
  (`06 istm/<agent>/`), the daily record is written from the lanes, and one filing run at a time, holding
  the forest writer lock, files into the forest. Direct edits go through the same lock.
- **Ending.** `retire-tree` gives a retired agent's registry line the status `ended` and its line under
  `## Agents` `· ended`; its pages stay and the orchestrator still reads them. `fold-tree` turns an
  agent's tree into a branch of another tree, of the type `--type` names (by default
  `agents.folded_type`), and the agent leaves `## Agents`; nothing is lost and old paths resolve.

## 7. Moves, archive, records

- `forest move` moves a leaf, promotes or merges subjects, renames, creates and folds trees, re-cuts a
  stream of event pages by period, and sets frontmatter. Each operation rewrites every link, list line,
  registry line and code-span path that named the old place and appends one line per path that moves
  (a page, a collection entry or registry line as `<page>#<id>`, a folder moved whole) to
  `.forest/moves.jsonl`. `set-type` and `set` move nothing and log nothing; they write the change line on
  the page they change. A page rewritten only because another moved gets no record line.
- A commit is all or nothing even when its process dies: `plan.commit` writes an undo journal
  (`.forest/journal/commit.json`: each changed page's text before, the folders made and removed, the move
  log's length) durably before its first write and removes it after the move-log append; the registry is
  rebuilt after that. A standing journal is a dead writer's: the next taker of the writer lock, or
  `forest recover`, rolls it back, and `forest check` reports it until then. `forest move resolve <old path>` follows the log to the present place.
- `.forest/archive/` holds one compressed copy per version of a page that an operation took out or
  rewrote whole, under the date it was archived, with a manifest line (old path, hash, size, reason, the
  leaf it was folded into), and `record-history/`, one append-only file per page whose record was trimmed,
  each block with its manifest row (page, file, offset, length, hash, counts, dates). Nothing is deleted;
  a copy a rolled-back commit stored stays. `forest archive show <old path>` prints the latest copy,
  `forest archive list` every copy, and `forest archive check` verifies copies and blocks.
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
`forest timeline query "<when>"` and `forest timeline subject <id>` read that index. A relative phrase
comes from a language table (English is built in; `memory_forest/phrases/ko.json` is a second table
shipped as an example), not from a sentence written into the matcher. A forest adds or replaces a
language with a JSON file in `.forest/phrases/`; the package tables stay the default. A raw-lane turn
is included whether or not a daily block or a leaf has filed it yet. A line that carries no zone of
its own is read in the page's `timezone:`.

## 10. Retrieval

Finding is `forest index`, `forest search`, `forest read` and `forest timeline`, over one rebuildable
index. With no pointer the file is `.forest/index.sqlite`. The lanes themselves still hold no index.
The derived file does, so an hour that has not been filed can be found, and `forest index reset`
followed by a build makes the same index.

`forest index build` holds the `index` lock. A second build that finds the lock held, or finds the index
file in use (SQLite says it is busy; the package does not tell which program holds it), does not build:
it refuses with `index_busy`, a non-zero exit, and next move `retry`.
A file whose size and time are unchanged is left, unless the build is `forest index build --verify` or
the stored hash is more than a day old: then the file is hashed, and rewritten only when the hash has
changed. That catches a restore that kept the size and the time.

**One way of writing the file.** The published file is only ever written in place, in WAL mode: a build,
an update after an append and a `memory_log` each change it inside one transaction. A reader reads the
last committed snapshot and never waits for a writer. The read opens the index, runs what it was given,
compares the file's identity and closes the connection before it returns, so no connection or cursor
leaves that function. A query asked while a build has not committed answers from the last committed
snapshot, or refuses when there is nothing committed yet. A build that must index everything again (a new schema,
tokenizer or masking) empties the tables and fills them inside that same transaction. A build, an update
and a `memory_log` never replace, rename or unlink the database or its `-wal` and `-shm` files: SQLite's
notes on how to corrupt a database (sections 1.3 and 2.5) warn that a process holding the old file can
then share a journal with a new one. A file that is still empty because the first build has not committed
is no index yet, for a reader as before any build.

**What SQLite refuses.** One function turns a SQLite error into a refusal, by its result code. Only a file
that is corrupt or is not a database, or whose schema mark matches while a table, an index or the rows
of a table that held rows at the last commit are missing, is `index_unreadable`, and it is refused rather
than replaced: its
next move is `run` with the command `forest index reset`. The index file's name carries a generation.
`.forest/index.current` names the current file; with no pointer, `.forest/index.sqlite` is current, so a
forest made before this still opens. Reset holds the index lock, chooses a name that has not been used,
and replaces the pointer by writing it and syncing it before the rename; the new pointer has mode 0600,
whatever the file it replaces had. Only then does it remove the old database and its `-wal`, `-shm` and
`-journal` files, sidecars first, each if it is present, and a temporary pointer file a killed reset
left behind (`.index.current.` and eight letters, digits or underscores, then `.tmp`). Its answer's next
move is `run` `forest index build`. A file that cannot be
removed is an orphan nothing reads, named in the output, and not a failure. The next reset or build
removes orphans of the index's own naming pattern in `.forest/`, never a symlink, a folder, or the
current file. A call that already opened a file finishes on that file, so a server may be running. A
held lock is the refusal `index_busy` with a non-zero exit. The next query or `forest index build`
makes the index again at the new path. A busy file is the busy path below. A lock conflict inside the
caller's own connection is `index_failed`, next `report`, because waiting does not cure it. A protocol
error is retried inside the call like a busy file, and when that call's budget is spent it is
`index_io_error`, next `report`. A read-only
file or folder, a full disk, an I/O error and a path that cannot be opened have codes of their own
(`index_read_only`, `index_disk_full`, `index_io_error`, `index_cannot_open`) whose next move is
`report`: no command helps, and resetting a working index on a full disk would only lose it. A
protocol error is tried again only inside a query's reads and inside a write. `status`, the timeline
and opening the file report it as I/O at once. A refusal that has already waited the whole stop time
is reported at once and not tried again.

**One state of the derived folder.** One function reads what is true on disk and gives one state:
`sound`, `masking_changed`, `missing`, `damaged`, `pointer_damaged` or `wal`, and whether this process
can write the folder the index lives in (`.forest/`, or the forest root while there is no `.forest/`,
because a build would make it there). The state gives the refusal of a read and of a write, their text
and their next move, and `index status`, `index build`, `index reset`, `search`, the timeline and every
MCP tool that reads or writes the index or a lane ask it, so no command meets a different check first.
A refusal's `details.state` and the `state` of `index status` name it. Its text is made of fixed
sentences, each a claim about the disk or about what a call does, and a test checks each by doing it.
In a folder this process can write, a missing index or a masking change is built before a read
answers, and a damaged index or pointer is `index_unreadable`, next `run` `forest index reset`. In a
folder it cannot write, no command of this account cures anything: every state but `sound` is
`index_read_only` for a read and for a write, next `report`, and the text names what an account that
can write would run (`forest index build`, or `forest index reset` and then `forest index build` for a
damaged index or pointer). A `sound` index there can still be searched: the open is SQLite's immutable
mode, the answer says `read_only: true`, and a build or a reset is `index_read_only` whose text says
reading still answers. Nothing on disk changes in such a folder. In every folder the index file is
opened first, through the door, without following a link and without blocking, and checked to be a
regular file. In immutable mode SQLite reads that same descriptor, through `/dev/fd/N` where the system
names the open file there, and otherwise opens the path. After the read, in every mode, the device and
inode the path names are compared with the descriptor's; in immutable mode the descriptor's size and
time, and the absence of a write-ahead log, are compared too. If any of them changed, nothing read is
answered: the answer is `index_busy` with `retry`. That compare is inside the one read, after the last
statement and before the answer is given. A failed read compares again before its refusal is chosen.
No public function returns the connection or a cursor. A file that went away or was replaced between the
door's look and SQLite's open is `index_busy` too, and so is an open that does not finish within
`OPEN_DEADLINE_SECONDS` (10). Immutable mode is never used on a folder this process can write.
`memory_log` and `forest log` in a forest this account cannot write are `log_read_only`, next `report`:
nothing is written, and the text names `forest log` for an account that can. Publishing a
fresh copy by an atomic rename was the other model; it was not chosen because it copies the whole
database for every appended line.

**Appends.** A raw lane only grows. For each lane file the index keeps `indexed_bytes`, the end of its
last complete line, and the hash of the bytes before it. When the file has only grown, those bytes are
read and hashed again, in 1 MiB pieces, to prove they are unchanged. Measured on a development machine
with the file in the page cache (read just after it was written), 8 MiB took about 4 ms: that is the cost
of the hash. A read from disk adds the disk's own time, which was not measured. Only the new complete lines are parsed
and inserted; anything else indexes the file again whole. A last line with no newline yet is not indexed;
the next update takes it when it is complete. The masking settings and the ledger are loaded once per
build or update, not once per row.

`forest index status` reports the
tokenizer, the counts, which files have changed, and any page that was not valid UTF-8. Such a page is
read with replacement characters and the rest of the build is published. A raw-lane line that is not
JSON is named the same way; the lines that are JSON are indexed.

One row is one page section (a leaf, a board, a tree page or the root index, including the lines before
the first heading), one collection entry (`<page>#<entry id>`), one marked block of a daily page
(`<!-- block: <agent> <time> -->`), or one raw-lane turn. The row keeps the path and address, the layer,
the tree, the subjects it is about, the times and the text.

SQLite FTS5 indexes the text a read would print: sign-in values masked, numbers starred. A search cannot
confirm a guess at a value the read would hide. What a read hides can change after a row was written: a
new `credentials.label_words` entry, another number setting, a value judged into the ledger, or a new
masking rule in the code. So the index keeps one fingerprint of everything that masking reads (the
`[credentials]` and `[numbers]` settings, the set of judged sign-in digests, and the masking code's
`MASKING_VERSION`). When the forest's fingerprint differs from the stored one, or the stored one cannot
be read, at a build, at an update or at a query, every file is indexed again before anything is answered.
A query that cannot rebuild waits for the other build's lock, and tries a busy file again every tenth of
a second, until its budget (60 seconds) is spent; then it refuses (`index_busy`, next move `retry`) rather than answer from text masked under the old rules. The refusal says how long this call already waited. On `retry`, the caller adds `waited_seconds` and `after_seconds` and reports once the sum reaches `stop_after_seconds`.
When the fingerprint is the same, a busy open answers from the rows already on file and says `stale`. The fingerprint check and the answer are two snapshots: a masking
change that lands in the milliseconds between them is answered under the old rules, the same as a query
that arrived a moment earlier. `forest index status` says `masking: current` or `changed`.

Where the linked SQLite has the trigram tokenizer, a run
of three or more characters is findable inside unspaced text, and a unicode61 index beside it finds
spaced words of any length, which is how Korean is usually written. A CJK word of one or two characters
is stored as grams, so it is found inside a longer word whether or not a space was written, and whether
or not trigram is available. `forest index status` says which tokenizer was built.

A search, a timeline query and a subject listing bring changed lane files and daily pages into the index
first, by size and time, from one listing of the daily and lane folders per query. When another build
holds the lock, or the folder cannot be written, the answer says `stale:` and how many of those files
were left, and it reads the index that is already published. A page of `02 ltm`, `03 mtm` or `04 stm`
changed since the build is not counted in a query's `stale`: `forest index status` lists it, with its
next move, and `forest index build` brings it in. A query does walk `04 stm`, because it compiles the
registry from the pages on every call: it reads every leaf (`docs/tools.md` states what a query costs).

`forest search "<words>" [--as <reader>] [--limit N] [--json]` matches registry names and aliases first.
A subject named in the question brings its home and the leaves that name it in `about:`. Word matches
follow. Vector matches run only when `[embeddings] provider` is not `none` and a provider of that name
is registered in the process. The package ships no provider and contains no network client;
`openai-compatible` records the intention and does nothing until something registers a provider. (The
design's `[embeddings]` table is the switch; there is no separate `[search.embeddings]` table.) Results
are one per page. Each matching section is included as written, with the lines to read and why it
matched.

`forest read <path>[#address] [--lines A-B] [--as <reader>]` prints that page or section. Numbers are
starred and sign-in values are masked, as on every other read. A path or address outside the reader's
trees or lane is refused, and the refusal does not include the text.

Every search, read, timeline and MCP call takes its reader from `--as` or from the connection, never
from the question. On a group connection the caller also passes `agent`, and that name is the agent
(section 6). On a group connection a caller that names the main agent sees what the main agent sees.
Results come only from `agents.readable_trees`. An agent also reads its own raw lane;
an orchestrator reads every lane. The daily record and the root index are shared. `--source` keeps one
group or tool of an agent's turns. `--named <agent>` is the task naming another agent when the owner
runs the command. On an agent's door (`--as`, or `--as-group` with `--agent`) it is refused, as the
tools take no such argument. The answer names the trees that answered.

`forest timeline query "<when>"` and `forest timeline subject <id>` read the same index. A day-part
phrase is evaluated in each entry's own time zone. A duration, and a range written
`YYYY-MM-DD..YYYY-MM-DD`, is one window of instants in the forest's zone.

`forest mcp --as <agent> [--http HOST:PORT]` serves `memory_search`, `memory_read`, `memory_timeline`,
`memory_navigate` and `memory_log`. `forest mcp --as-group <group> [--http HOST:PORT]` is the same
server for one group: every tool then requires `agent`. Exactly one of `--as` and `--as-group` is
given. The token rule is the same for both. `memory_navigate` walks one level down from the root index and
returns the lines the reader may follow, each line as written. `memory_log` appends the caller's own
turn through the intake writer; the server sets the role to `agent` and the origin from the transport
(`interactive` on stdio, `headless` on HTTP). A caller-supplied role or origin is refused. The server's
instructions are under 2,048 characters.

`memory_log` answers `indexed: true` when the new line is in the index and `indexed: false` when it is
not, with `reason` naming the refusal (the lock was busy, or the index was read-only, damaged, or full).
The line is in the lane either way. The update reads and hashes the bytes already indexed, parses only the new line, and writes
it into the published index; it does not copy the database.

The clients checked here (`claude mcp`, `codex mcp`, `grok mcp`) speak protocol versions 2024-11-05,
2025-03-26, 2025-06-18 and 2025-11-25, over stdio and streamable HTTP. Claude and Grok also offer the
older SSE transport; this server does not. Stdio is one JSON-RPC message per line. A batch is accepted
for 2025-03-26, the revision that added batches, and refused for the others. The server answers with
the newest of those versions that the client names. On stdio, a batch before `initialize`, or one that
holds `initialize`, is refused. HTTP keeps no session: the revision is the `MCP-Protocol-Version`
header, and a 2025-03-26 batch that does not contain `initialize` is answered. A batch that contains
`initialize` is refused on both transports. The other revisions have no batches, so a batch is refused
on both. An argument that fails a tool's input schema is a tool result with
`isError: true` under 2025-11-25 and JSON-RPC `-32602` under the earlier versions, as each version's
tools page says; an unknown tool is `-32602` under all of them. An error for a message whose id could
not be read has no `id` on stdio under 2025-11-25, and on an HTTP error status from 2025-03-26. A 200
payload keeps `id: null`, and so does every other case, as JSON-RPC 2.0 says. A request id is a string
or an integer. A stdio line longer than 1,000,000 bytes is
read in bounded pieces, answered with one error and skipped. `--http PORT` binds to loopback. Every bind,
loopback included, uses `.forest/mcp/token` in mode 0600. The first start creates the file and never
prints it. The client sends it as a bearer token. A request whose `Origin` is present and is not a
loopback origin is refused. On a loopback bind, a `Host` that is not a loopback name with this server's
port is refused; the name is not resolved. A bind the owner set to another address also accepts that
address with the same port, and a bind on every interface accepts the host the client used, because
that port was opened on purpose. The token is still required. The body must be `application/json`.
Reaching a non-loopback port from outside the machine is the owner's own network decision. The first tool call of each agent is recorded in `.forest/mcp/seen.json` (the
agent, the time, the tool and the transport, not the question), and each later call records when that
agent was last seen, so `forest doctor` can later prove use. `forest agents` lists each group, its
members, whether a member has written a lane, and that last-seen time.

## 11. The pipeline

`06 istm` (raw, append-only) → `05 daily` (one readable page per day, appended in marked blocks) →
the filing run (reads the blocks it has not filed, at most a bounded amount per run, newest days first)
→ the forest. A nightly upkeep run applies levels, works the lint's upkeep list down, trims records and
re-checks stale current-state lines. The stages are plain programs around a model of your choice; the
package supplies their input and commit steps, their cursors and receipts, and the prompts as files.
`stage.prepare` is the place a stage starts a model: it records the session id and marks the prompt and
the environment so intake does not take that session in. The rest of the stage programs are milestone 3.

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
[credentials]
label_words = [...]             # words before a value sent to sign in or to pay: password, passcode, pin,
                                # one-time code, verification code, security code, door code ... (English);
                                # add your other languages' words, e.g. ["password", "mot de passe", "code porte"]
joining_words = ["is", "was"]   # words between a label and its value without a colon ("the PIN is …")
count_words = [...]             # words after a number that make it a count: characters, digits, times ...
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
shared_branch = "shared"        # the branch that takes a category's other leaves when a promoted
                                #   subject takes over the category's board
default_subtopic = "profile"    # the sub-topic of a leaf subject's only leaf when it names none
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
auto_create = true              # the first file in a raw lane 06 istm/<id>/ makes that agent's tree
folded_type = "component"       # the type an agent's subject takes when fold-tree makes it a branch
[agents.known.<id>]             # an agent declared by the people running the forest (any slug as id)
name = "…"                      # optional, free text in any script; default: the id
role = "…"                      # optional; default: `## Role` holds `(not set)`
[agents.groups.<group-id>]      # one app that runs several of the owner's agents, one connection
members = ["kiln-bot", "ledger-bot"]
open = false                    # true: every new name a caller gives becomes an agent. There is no
                                # greatest number unless max_members is set. `forest agents` lists them.
# max_members = 40              # unset unless the owner wants a bound
# source = "the-app"            # optional. A reader of an app's own member list. A miss reads it once.
[intake]
tool_output = "omit"            # omit | keep. omit: a tool turn records that the tool ran
[intake.tools.<adapter>]        # claude-code, codex, grok, jsonl, or an adapter added later
enabled = false                 # automatic intake is off until this is true
agent = ""                      # the agent these turns belong to; else `forest intake --as`
include = []                    # folders the session ran in; empty means every folder
exclude = []
import_earlier = false          # the first run places cursors at the end of what already exists
path = ""                       # jsonl: the folder of record files; others: look here instead of the tool's own place
[embeddings]
provider = "none"               # none | openai-compatible
```

## 13. Package map

| Module | Does | Command |
| --- | --- | --- |
| `config`, `layout` | settings, layer paths, slugs | `forest init` |
| `model` | parses pages: frontmatter, sections, Timeline and record lines | |
| `locking`, `journal` | the forest writer lock and stage locks; the undo journal of a commit | `forest recover` |
| `registry` | registry lines, the compiled JSON, `agents`, `tree_kinds` | `forest registry` |
| `movelog`, `move` | operations, reference rewriting, the move log | `forest move` |
| `levels` | distinct days, due levels, apply | `forest levels` |
| `collections` | collection pages: demote, admit, recut | `forest collections` |
| `record`, `archive` | record trim, the compressed archive | `forest record`, `forest archive` |
| `maps`, `validate`, `audit` | derived lists (Leaves, Pointers, Branches), structure checks | `forest check` |
| `checks` | the one table of `forest check`'s checks | `forest check` |
| `agents` | agents from the settings and the raw lanes, their problems, `readable_trees` | `forest levels` |
| `groups` | one connection's members, admitting a new member, the agent list | `forest agents`, `forest mcp --as-group` |
| `lint` | the codes, baselines | `forest lint` |
| `repeats` | fact lines, the index leaf, the judged ledger; repeated lines to their one home | `forest repeats` |
| `view` | a read-only view of one forest that the checks share: settings, registry, pages read once | |
| `cli_upkeep` | the record, numbers, check and lint commands | `forest record`, `forest numbers`, `forest check`, `forest lint` |
| `intake`, `adapters` | the turn record, the one writer of a raw lane, per-tool transcript readers and their cursors | `forest intake` |
| `timeline` | the time index and time phrases | `forest timeline` |
| `search`, `scope` | the search index, coarse-to-fine retrieval, reader scopes | `forest search`, `forest read` |
| `mcp` | the MCP server, one agent or one group | `forest mcp` |
| `stage` | daily store, bounded filing interval, cursors, receipts | `forest stage` |
| `setup`, `doctor` | setup driven by the agent, the connection proof, the guide for agents | `forest setup`, `forest doctor`, `forest guide` |

## 14. Milestones

1. **Skeleton.** The contract, this design, a synthetic forest with shared trees and agent trees that
   passes the lint, and the structure tools: registry, move, levels, collections, record, archive, maps,
   validate, lint, with tests on made-up forests.
2. **Connecting and finding.** Intake (the turn record, `forest intake`, the adapters), the time index,
   search with scopes, the MCP server, and setup (`forest setup`, `forest doctor`, the guide, the plugins):
   section 15.
3. **Growing.** The stage programs and their prompts, the nightly upkeep.

## 15. Connecting agents

A forest is used by agents that already exist: a coding agent in a terminal, a desktop assistant, a bot in
a chat app. Every connected agent gets the same three doors (intake, use, upkeep), and setting them up is
that agent's own job.

### 15.1 Intake: how a conversation reaches the raw lane

- **The record.** One line per turn in `06 istm/<agent>/<YYYY-MM-DD>.jsonl`: `id` (stable for the turn:
  the turn's own id where that id is unique by itself, the session and that id where it is not, or a
  hash of the turn's content and time where it has none; an id of its own for every call of `memory_log`
  or `forest log`, which also keeps the caller's `turn_id` as `turn` and the turn's `sources`; the lane,
  not the id, names the agent), `at`
  (with its offset), `session`, `role` (`owner`, `agent`,
  `tool`, `system`, or `other:<name>`), `text`, `origin` (`interactive`, `headless`, `subagent` or
  `unknown`), `source` (the adapter, the transcript relative to the tool's own root, and the place in
  it) and, for a tool turn, the tool's `name`. Where the tool marks a span the owner pasted, attached or
  quoted, or a span the client inserted, `text` stays whole and `parts` names the span and its kind
  (`paste`, `file`, `quote` or `harness`). A turn is the owner speaking only when its role is `owner`
  and its origin is `interactive`. `unknown` is treated like `headless`. A sub-agent's work is a tool
  turn of the agent that started it, one id per message. A parent session id alone does not make one.
- **One writer.** `forest intake` is the only program that appends to a lane. It takes turns from an
  adapter, skips a turn whose `id` the lane already holds, and masks numbers and sign-in values at the door
  by the contract's rules for them. A tool turn records that the tool ran, not what it printed, unless
  `intake.tool_output = "keep"`: tool output is where keys and tokens leak.
- **Adapters.** An adapter knows one thing: how to read one tool's own transcript (where the files are, how
  a turn is shaped, how to go on from a cursor). The package ships one for each coding agent it has a plugin
  for and a generic one, `jsonl`, that reads a folder of files already in the record's shape, for any tool
  that can export or write them. An adapter is a small module behind the entry point group
  `memory_forest.adapters`, so one more tool is one more module and no change to the core.
- **A hook signals, a sweep catches up.** Where a tool has hooks (the end of a turn, the end of a
  session, before its context is compacted), the hook runs `forest intake --from <tool> --hook`. The hook
  reads only the transcript it was called for, from its cursor to the end of that file. A missed hook on
  that same transcript is caught by the next hook of it. Other transcripts are caught by a sweep,
  `forest intake --from <tool>`, not by a different session's hook. No hook is ever the only path. A hook
  reads at most 256 KiB of that transcript per call (about 50 ms to read and mask that much on the
  machine this was measured on) and leaves the rest waiting, which `status` shows as ahead; a line longer
  than that is the sweep's. A hook never turns intake on: the first sweep of a tool does, and a hook
  before it reads nothing and is counted. A hook reads only its own transcript's cursor. In hook mode
  the process exits 0 and prints nothing, on every path: clients treat a hook's exit 2 as a block and may
  show its stderr to the model, and may add its stdout to the model's context. A failure is one line in
  `.forest/intake/`, class and place only, and `forest intake status` shows the count and the last few.
  A hook that finds the intake lock held leaves one marker for its transcript, which needs no lock and
  which the run that reads that transcript removes.
- **Consent.** Which conversations are taken in is the owner's choice, asked at setup and never defaulted.
  Per tool, `[intake.tools.<adapter>]` holds `include` and `exclude` (folders the session ran in) and
  `import_earlier` (false until the owner says otherwise: the first run places cursors at the end of what
  already exists). While a source is excluded, its cursor follows the file's end and nothing from it is
  written; lifting the exclude does not import that past. Automatic intake stays off until `enabled` is
  true. `forest intake --from <adapter>` then says so and takes nothing, unless `--once` is given. A
  session a forest stage started is not taken in. The stage records the session id (one small file
  named by its hash), opens the prompt with the line `MEMORY_FOREST_INTERNAL`, and sets that name in
  the environment of the model process. Only the first owner turn's own text marks a session; a hook
  still reads the environment. A hosted assistant has no transcript to read. Its
  `memory_log` path belongs to the MCP server, not to intake.
- **An agent with no transcript and no hooks** has three ways in, strongest last: its own write, either
  the MCP tool `memory_log` or the command `forest log` (the text comes on stdin, never as an argument;
  it depends on the agent calling it, so where a reader of the host's own records exists, the reader is
  the net and this write is the addition); an export folder read by the `jsonl` adapter; an adapter
  module for that host, kept outside the core.
- **An agent that runs somewhere else** (a hosted assistant) reaches the forest over `forest mcp --http` on
  the owner's machine, inside the owner's own private network, and writes its lane with `memory_log` or
  through an export the owner's machine reads. A lane belongs to one agent and is append-only, so two
  machines never write the same file.

### 15.2 Use: how an agent finds what it needs

- `forest mcp --as <agent>` (section 10): search, read, navigate, timeline. The reader's id comes from the
  connection, so the scope rule of section 6 holds without the agent saying who it is. `forest mcp
  --as-group <group>` is the same server when several agents share that one connection: each call passes
  `agent`, and the name resolves only to a member (section 6). `forest search`, `forest read`,
  `forest timeline`, `forest navigate` and `forest log` take the same `--as` or `--as-group` and `--agent`.
- A skill says when to look: when the owner names something, when a question reaches into the past, and
  before asking the owner something the forest may already hold.
- A session may open with a short card (who the owner is, what is in progress, the newest daily lines);
  `connect.session_card` turns it off.

### 15.3 Upkeep

`forest nightly` runs the stage programs of section 11 with the model command the owner chose
(`stages.runner`). It is asked at setup, because it spends the owner's model budget.

### 15.4 Setup is the agent's job

- The owner says one sentence to their agent. The agent runs `forest setup`, which is written to be driven
  by an agent: every output is one JSON object whose `next` is `ask_owner` (the question, the choices, and
  why only the owner can answer), `done`, or one of the four moves of a refusal: `fix` (the call itself is
  wrong, and a corrected call succeeds on the forest as it is: change what the message names and call
  again), `retry` (the same call succeeds later without anyone doing anything: call again after
  `after_seconds`), `run` (a command this caller may run now, which changes the state: the answer carries
  it in `command`) or `report` (only the owner or another account can change what is wrong (a file, a
  setting, an access right): tell the owner the message). On `retry`, add this answer's `waited_seconds` and the
  `after_seconds` it asks the caller to wait onto the time already spent, call again after `after_seconds`
  while that sum is below `stop_after_seconds`, and report the message once the sum would reach it.
A protocol error is tried again only inside a query's reads and a write, and is an I/O refusal at
once in `status`, the timeline and while the file opens. A refusal that has already waited the whole
stop time is reported at once and not tried again. A refusal of any command or tool has the same form:
  `code`, `message`, `details` and `next`, with its `command` or `after_seconds`. Every code the package
  raises, with its next move, is in `docs/refusals.md`, and a test proves that list is the code's. The agent
  asks, runs the command again with `--answer key=value`, and is finished when `forest doctor` passes. An
  agent that never read a guide, or lost its place, still ends in the right state by following the output.
- **Asked** (only what the owner alone can decide): where the forest lives, or which forest to join; this
  agent's name; which conversations are taken in and whether earlier ones are imported; which model command
  the nightly upkeep may spend; whether the machine's other agents are connected too. **Detected and shown
  as a proposal**: the timezone, the language, the installed tools, the paths.
- Every write is previewed (`--dry-run` prints each file and block it would write), repeatable (a second
  run changes nothing), bounded (one marked block in an instruction file, one hook entry, one MCP entry, one
  plugin folder) and removable (`forest setup --remove <tool>`).
- `forest doctor` proves the connection instead of describing it: it sends a test turn through the tool's
  real path and finds it in the lane, calls the MCP server as that agent and gets an answer, and shows when
  the nightly upkeep will run.
- The guide travels with the package: `forest guide` prints the setup guide for agents, and each plugin
  carries it as its setup skill. A page on the web is for people to read; an agent is never asked to fetch
  instructions from an address and follow them.
- The guide is tested like code: for each supported tool, a new agent in an empty sandbox is given the one
  sentence, and the run passes only when `forest doctor` does.

### 15.5 Plugins

One plugin folder with a manifest for each supported tool, and shared `skills/`, `hooks/hooks.json` and
`.mcp.json`: the hooks call `forest intake`, the MCP entry starts `forest mcp --as <agent>`, and the setup
skill is the guide. Installing the plugin and running `forest setup` are one path: with the plugin installed
and no forest set up, the first session says so and names the command.

### 15.6 An app that runs several agents

Some hosts are not a coding tool on the owner's machine. One app runs several named agents (Kiln Bot and
Ledger Bot, in Juno's studio) and offers one tool connection for all of them, no hooks, and no transcript
the owner's machine can read. Three questions, and the answers this package gives:

- **Who is calling.** The connection is the group (`forest mcp --as-group studio-counter`). Each call
  passes `agent`, the name the app uses for that agent. The agent is its name. Inside a group a caller is
  whoever it says it is, so any member can read what another member's own tree and lane hold by calling
  under that member's name. A group tells one owner's agents apart for memory and is no wall between them.
  An open group lets every new name become an agent. A closed group allows the names it lists. On a group
  connection a caller that names the main agent sees what the main agent sees. Studio Hub does not need to
  be a member.
- **How the forest is reached from a computer that is not the owner's.** `forest mcp --http` on the
  owner's machine, behind an HTTPS front that stays inside the owner's own private network. The client
  sends the token as a header. The server is not put on the public internet. Where the host can run a
  command on the owner's machine and nothing else, the commands are the same doors: `forest search`,
  `forest read`, `forest timeline`, `forest navigate` and `forest log`, with `--as` or with
  `--as-group` and `--agent`.
- **How turns get in.** Where a reader of the host's own records exists, that reader is the net. Otherwise
  the agent writes its own turn, with `memory_log` or with `forest log`. The write depends on the agent
  calling it.

### 15.7 An agent you wrote yourself

An agent the owner wrote, rather than an app's, has the same doors under the name the owner gave it.

- Its own write: `forest log --as kiln-bot`, the turn on stdin, or `memory_log` on `forest mcp --as kiln-bot`.
  The role and the origin are set by that door. Each call is a turn; the caller's `turn_id` makes a repeat
  one turn. Numbers and sign-in values are masked at the door.
- An export folder of records in the lane's shape, read by the `jsonl` adapter.
- An adapter module for that agent's own records, kept outside the core (`memory_forest.adapters`).

A turn written with `forest log` or `memory_log` makes that agent's tree, or leaves it pending (section 6).
The line names where it came from in `sources` (`door:cli`, `door:mcp`, or `group:<id>`) and when it was
written in `at`. The same words through both doors within 120 seconds are one line that names both
sources; through one door, twice, they are two lines. The words are at most 131,072 bytes through either
door; the protocol line has its own larger bound (1,000,000 bytes), so a turn at that bound fits when every
byte is escaped. `forest log` refuses bytes that are not UTF-8, and refuses a piped stdin that has not
ended after 30 seconds; a terminal has no deadline, since a person types there.
