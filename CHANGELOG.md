# Changelog

All notable changes to Memory Forest are documented in this file. The project follows [Semantic Versioning](https://semver.org/) once a version is released.

## Unreleased — 1.0.0b1

- The filesystem boundary is checked with import allow-lists and interpreter audit events during both
  fault sweeps. Tests have a process deadline, independent disk witnesses, and separate assertion and
  bound mutation results. An independent watchdog ends a stalled native traceback. Sidecar read probes
  preserve live SQLite locks; a blocked opener is counted even after an index-path swap. Verification in this round ran on macOS.

Version 1, Agent Memory Forest, is a new generation of the project, built in three milestones. The
`forest` commands of milestone 1, the structure tools, are in the tree and tested on made-up forests.
Milestone 2 now has intake, the search index, timeline, reader scopes and the MCP server. Setup
(`forest setup`, `forest doctor` and the guide) is not. Milestone 3's daily record, filing run and nightly
upkeep are carried out by the owner's own agent, following the contract (section 10); the package runs no
stage. Nothing is released. The 0.3 `memory-forest` command stays in the tree until
the version 1 commands replace it.

### Added

- `forest guide`, `forest setup` and `forest doctor`: an agent connects itself. The guide ships in the
  package; setup asks the owner only what the owner decides (`next: ask_owner`), writes the agent into the
  forest and lists what the agent does in its own tool (`todo`); the doctor proves by evidence, a phrase
  brought in the agent's real way included, that its memory works from end to end ([setup](docs/setup.md)).

### Changed

- The licence changed from GPL-3.0-only to MIT. Copies of earlier releases keep the licence they were released under.

### Fixed

- New keyed turns distinguish original input words even when masking gives them identical stored text.
  Their words digest uses the existing private key and survives maintenance; legacy lines retain their stored-text comparison.

The lane commit fixes `stored` and the turn id; ids, caller-key and source records, the tree, the index and connection bookkeeping are derived, with `pending` and a reason when unfinished.

- Caller keys are HMAC-SHA256 digests under the existing forest key, with no lifetime. Missing or damaged
  per-lane lookups rebuild from lane lines. Masking repair migrates legacy raw `turn` fields.
- Joined turns retain ordered arrivals, each with its source label, door, adapter, group, place and time;
  the full join window is searched on both sides of midnight.
- Source records validate the complete entry and carry change stamps and an integrity check; semantic
  refusals are checked against readable lanes. Read-only readers keep completed scans in the process.
- The linear URL scanner computes its classes from the old pattern over all Unicode. The masking version
  stays unchanged, with exhaustive class and generated span comparisons.

- An admitted agent name keeps one key and one admission rule through tree creation, fill and rename.
  Registry and Markdown syntax use reversible numeric character references. Identity is resolved over
  the whole forest, exact stored id before name key, before group membership. Invisible format characters
  remain significant and receive notices. Id allocation shares the tree writer lock and trusts the
  agent-id record first; generated ids avoid Windows device names. Hangul finals use word-final Revised
  Romanization per isolated syllable, with no cross-syllable sound changes; stored ids stay unchanged.
- Member sources validate a list of texts, deduplicate keys with first spelling retained, and share
  concurrent refreshes. The interval starts at completion; cooperative readers receive a deadline and
  cancellation event. An unreturned reader keeps its one source slot and later calls fail immediately,
  with a diagnostic in `forest check`. Names are bounded in characters as well as count.
- Settings recovery edits create missing lists and replace wrong-kind values, retaining a valid single
  text member. Lock setup failures carry the same applicable edits. Parser diagnostics are never owner
  keys. Text arguments are checked before trimming for the contract's Unicode classes, and tests pass
  local clocks instead of replacing a function of the shared `time` module.

- The disk is reached through one module, `system` (the door). Every call that looks, lists, opens,
  reads, writes or locks answers one of eight outcomes (absent, denied, not a file, not a folder, link,
  changed, busy, other), and the code decides on outcomes, not on the system's raw errors. The derived
  folder's state is read as facts in one walk (`derived.read_facts`), decided by one pure function
  (`derived.decide`) into one of 23 states, and told in claims kept as data; a test walks all 2,792
  combinations of the facts and checks every claim of every decision. A forest folder that can be listed
  but not entered, a `.forest` folder that cannot be entered or listed, a pointer this account cannot
  read and an error the system did not name each have their own state and true sentences, the same from
  every command, public function and MCP tool. An outcome no code decided on leaves as a refusal by its
  kind (design, section 15.4), never as a traceback. In a folder this account cannot write, SQLite reads
  the descriptor the door checked, through `/dev/fd/N` where the system names it; an index file that is
  replaced or goes away between the door's look and SQLite's open, or while it is read, is `index_busy`
  with `retry` in every folder, and so is an open that does not finish within 10 seconds.
- The walk over the refusal codes follows a class derived from the refusal type at any depth (the
  archive's two codes are in the table, with `report`), a method, a callback and a partial application,
  and fails on a place it cannot follow; a next move is a literal or a listed set. `fix` and `run` are
  proven by doing. A fault in a file of the owner's (a `forest.toml` that does not parse or is not a file,
  a damaged phrase table, a damaged registry) is a `report` code of its own, apart from the code for the
  caller's own wrong argument. `no_forest` is `fix` and names no folder; the four codes of the older
  index carry `memory-forest index` with the forest the call named, and `not_initialized` is `report`.
  One function reads a page path a caller gives, for the scope check and the read alike, so
  `04 stm/bakery/./ovens//floor.md` is the reader's own page. The "When" column of `docs/refusals.md` is
  generated from the code: every message a code's places can give, and the claims a state's code can
  carry. The MCP server's instructions state the four moves, and the help of every command an agent runs
  names `docs/refusals.md`. A time window that reaches past the year 9999 or before the year 1 is
  `time_phrase_unknown` with `fix`, not `internal_error`.
- The agent is its name, in any script. One function gives a name's key (NFKC, case folding, no white
  space, hyphens, dashes or underscores), and it is the only comparison of agent names: a name finds an
  agent only by being its id exactly or by the key of its stored name, never by a slug of the given text,
  so whether two names are one agent no longer depends on what else holds a slug. A name in Hangul, kana,
  Han characters, Cyrillic, Greek, Arabic or symbols is admitted like any other. A new agent's id is made
  once, letter for letter by one table per script (kept as data in `letters/`), or `agent-` and six digits
  of a hash of the key where no table carries a character, and stored beside the name; a taken id gets
  `-agent` or the next free number, never another agent. The only bad names are empty, longer than 200
  characters, or holding a control character or a line break, with `fix`. A group does not refuse a
  name for being near another, or for already being an agent somewhere else. An open group admits every
  new name; a closed group lists its members. An orchestrator sees the whole forest through a group
  connection without being listed, and a name one mark from its name is another agent.
- A turn said twice is two turns. Every `memory_log` and `forest log` call has an id of its own; a caller's
  `turn_id` (`--turn-id`) makes a repeat one turn, and other words under it are `turn_id_reused`. The same
  words through two different sources within 120 seconds are one line that carries both sources.
- A write that happened says so. Once the lane's line is stored the answer says `wrote` and `stored`, and
  names the tree (`made`, `already there`, `pending`, `none`) and the index (`updated`, `pending`); a
  writer lock held by another process leaves the tree pending for the next write or the level pass instead
  of answering that the call did not run. A refusal after an admission names it in `details.admitted`.
- An admission patches `forest.toml` byte for byte: line endings, a byte-order mark and a missing last line
  ending stay, a one-line list with a comma after its last value, a quoted `members` key and a literal
  string that ends in a backslash are patched,
  and the settings reader accepts a byte-order mark. A group written inline, with dotted keys or under its
  parent table, a file that cannot be written, and a patch that would not parse or would mean anything
  else are refused with the edits for the owner, in words and in `details.edits`.
- A source is a kind and a name (`group:<id>`, `door:<name>`, `adapter:<name>`), on the turn, in
  `forest agents` and in `source`/`--source`. An unknown source is `source_unknown` with the reader's
  sources. A reading narrowed to a source keeps the file's line numbers and is narrowed before its limit.
  `forest agents` reads the sources from a small record the lane writer keeps, not from every lane.
- A source of members has a deadline, a greatest size and a 60-second interval, and its failure is
  `member_source_failed` with `report`, never "not a member"; names on it that are not a name are left out
  and shown by `forest agents`.
- One bad value of the agents' tables never stops the forest: a member written twice, by id or by name, is
  counted once, and a field of the wrong kind or an unknown field is left out; each is reported
  (`agent_setting_invalid`) and everything else loads.
- `memory_log` and `forest log` bound a turn's words by the same 131,072 bytes; the protocol line keeps its
  own larger bound. `forest log` from a terminal has no deadline. Storing a long turn no longer takes time
  quadratic in its length: the number masking finds addresses in one pass.
- A build that meets a page gone between the listing and the read says so (`index_busy`, retry),
  instead of saying the index file is in use. A fifo put in the place of the pointer or the index is
  refused at once rather than waited on.
- Who reads decides what is folded. A command run with `--as`, or with `--as-group` and `--agent`,
  answers as the MCP tools do, through the same function: its refusals, as JSON and as text, carry no
  host, process, user, home folder or absolute path. Without those flags the owner's terminal output
  is unchanged. A read or a walk of an absolute path, or of one that climbs out with `..`, is
  `path_escape`, not a path outside the reader's trees.
- Every refusal code the package can raise is in one table, `errors.REFUSALS`, and in
  `docs/refusals.md`, each with its next move. A test reads every module's syntax tree and proves the
  codes raised, the table and the document are the same set, both ways; a code that is not a literal
  is followed to its callers or named with its values. Every refusal now has `next`: `run`, `retry`,
  `report` or the new `fix` (the call itself is wrong). `tool_failed` is `report` and `out_of_scope`
  is `fix`. A caller's wrong argument to `forest log`, `forest search` or a group connection is `fix`,
  and no `command` holds prose. The command line's own filesystem and internal failures are refusals
  of the same shape, printed as JSON with `--json`.
- One function reads the state of the derived folder (`sound`, `masking_changed`, `missing`,
  `damaged`, `pointer_damaged`, `wal`, and whether the folder can be written), and every command
  answers from it: `index status`, `build`, `reset`, `search`, the timeline and the MCP tools agree,
  and a refusal names the state in `details.state`. In a folder this account cannot write, the next
  move is `report` and the text names what an account that can write runs; a read-only forest with
  no `.forest/` is one of them. A damaged pointer there, or a masking change, no longer says that
  reading still answers. `memory_log` there is `log_read_only`, and nothing is written.
- A reset publishes the pointer in mode 0600, so a pointer this account could not read is replaced by
  one it can. Its answer's next move is `forest index build`.
- `index status` says sound only when a query works: the probe runs what a query runs (a match on each
  word index, a read of each table for the columns the code selects), every object of the schema is
  there, and every table that held rows at the last commit still holds one. The schema is now 4.
- An internal protocol code never leaves a public function. The index file, the pointer, a page and a
  lane file are opened first, without following a link and without blocking, and then checked; an
  index file that goes away or is replaced between the door's look and SQLite's open is `index_busy`
  with `retry`, in a folder this account can write and in one it cannot.
- Two processes that take their first lock at once both get the lock folder.
- A test no longer stops a real process with an environment variable. A child that must stand at
  a point is started from a launcher under `tests/` that replaces the function, and every wait on a
  child or a fifo has a deadline and the child's stderr.
- A `.forest/` folder this process cannot write can still be searched when no write-ahead log stands
  beside the index. The answer says `read_only: true`. A log there, or no index at all, is
  `index_read_only`, and the cure is one `forest index build` by an account that can write.
- A refusal names the current index file, not the first name, and says what is actually wrong: a
  read-only file can still be read, and a held file is in use.
- One intake in a server waits as long as the file lock waits, and no longer. A caller that does
  not get its turn receives `lock_busy` with `next: retry` and the `waited_seconds` it actually waited.
  The turn and the file lock share one budget.
- A tool returns a page's lines exactly, even when a line is a path. A refusal, and only a refusal,
  carries no host name, process id, user name, home folder or absolute path: the forest root in the
  server's own text becomes a forest path, the home folder becomes `~`, and `real_path`, `root` and an
  absolute `source` are dropped at the door. The command line still prints them. The settings object's
  own `source` stays the absolute path, because the sign-in ledger finds the forest from it.
- A pointer is absent, one index file name, or damaged. Damaged bytes (including text that is not
  UTF-8) are `index_unreadable` with `forest index reset` as the next move, and reset replaces the
  pointer. A longer pointer is not read whole.
- Masking reads a text in time proportional to its length. An assignment is found from its operator back
  (it finds what the earlier pattern found), each run is read for its end once, and a value longer than
  1024 characters after a secret name, a label or a scheme word is masked whole instead of being checked
  again for each label inside it. A run of `key-` or `token:os.x(` 40 kB long took seconds; it now takes
  milliseconds, and the tests count the characters read.
- A turn's id escapes its parts before they are joined, so two different raw ids never become one, and
  the day's id file is read by `\n` alone. No release shipped the earlier form, so nothing is migrated.
  Any failure after a line is in the lane reports how many turns it stored (`N turns, then failed`). A
  Grok tool id of a short form is composed with the session. `forest intake reread` goes back to where
  intake first placed a cursor and no further, leaves an excluded cursor, and clears what a read carried
  over; a reread with no fault clears the fault count. A dry run writes nothing when the tool's state
  cannot be read.
- Intake state is kept by tool, and each cursor names the agent whose lane its turns went to. A
  transcript whose cursor names another agent is not read, and `status` counts those for each pair of
  names; a second agent needs no rename. `rename-agent` sets the name in each cursor and completes after
  an interruption at any step. A hook that finds the lock held leaves one marker for its transcript.
  Earlier intake state under `.forest/intake/cursors/` is not read: no release shipped it.
- A hook never turns intake on: the first sweep does, and hook calls before it are counted. The hook's
  stdin is read as it arrives under one deadline, so an open pipe does not hold it. A stage session is
  one small file found by one `stat`. Only the first owner turn's own text marks a session. A line
  longer than a hook reads is the sweep's. A plugin's `discover` is consumed inside the guard and
  bounded. Codex `tool` provenance and the `subagent_notification` and `turn_aborted` blocks are not
  the owner.
- Masking finds where a value ends by one rule (white space, a tag, a closing quote, bracket, comma or
  semicolon, or the partner of an opening quote), so a value before a closing tag is masked and a part's
  tags stay whole. `[REDACTED]` is one token and is never masked again. A name is a secret name when its
  last part is the secret word, on any line of the text; a value that reads as code stays, and after a
  name that ends in `key` only a literal secret is masked. `basic`, `digest` and `bearer` are a scheme only
  in a header, or `Bearer` before a mixed token; after `Authorization: Digest` the whole parameter list is
  masked. The home folder is folded before the parts are mapped, and only where it is a whole path. The
  masking version is 4, so an index built under an earlier one is rebuilt. `forest credentials check`
  counts the lane lines the new rules would change and `forest credentials fix --all` rewrites them in
  place.
- `forest index build` that does not build exits non-zero with `index_busy` and next `retry`. The
  message says whether the index lock is held or another program holds the file. A retry says how long
  the call already waited and the total after which the caller reports. A lock conflict inside one
  connection is `index_failed`. A protocol error is retried, then `index_io_error`. A schema mark that
  matches a missing table or column is `index_unreadable`. A word search no longer hides a busy or I/O
  error. `indexed: false` carries `reason`. A read-only `.forest/` folder cannot be searched.
- A tool answer never carries a host name, a process id, a user name, or an absolute path. `lock_busy`
  and `lock_already_held` leave a tool as one busy refusal, next `retry`. Two `memory_log` calls in one
  server process wait their turn. The command line still shows a lock's owner.
- Intake masks a sign-in value by its shape before a label, including a token after `Bearer`, `Basic`
  or `Digest`, a value after a label that looks like a hash or a UUID, and the published key prefixes
  of the model providers, code hosts, cloud consoles, payment and chat services and package registries,
  plus a JWT. An assignment whose name contains KEY, TOKEN, SECRET, PASSWORD, PASSWD or CREDENTIAL is
  a labelled value. The masking fingerprint moves with that change, so an index built under the old
  rules is rebuilt.
- A turn is stored once. The id check reads the day's id file and the lane bytes after its end mark.
  A session or turn id that cannot be written as it is becomes a hash of its exact code points. One
  turn that raises does not stop the transcript; `forest intake reread` reads it again. A copied turn
  whose id is unique by itself is the same turn in a forked session. A dry run writes nothing.
- The stage marker is read once, on the first owner turn. A hook before the first placement does not
  import history. Compaction, local-command output and instruction blocks are not the owner. A hook
  reads at most 256 KiB per call, counts a held lock, and cannot be ended by a plugin. `forest intake
  rename-agent` moves a lane with its cursors and ids.

### Added

- One connection can serve a group of the owner's agents. `forest mcp --as-group <group>` (stdio or
  `--http`, the same token rule as `--as`) requires `agent` on every tool. The agent is its name.
  With `--as` a tool argument cannot set the reader. A closed group refuses a name that is not
  a member and lists the members. An open group admits every new name, keeps the given name, and a
  second admission changes no byte. `forest log` is the command-line twin of `memory_log` (the text is
  stdin). `forest search`, `forest read`, `forest timeline` and `forest navigate` take the same
  `--as-group` and `--agent`. `forest agents` lists each group, its lanes, its sources, and when a
  member was last seen.

- `forest index build | status` writes `.forest/index.sqlite`, one row per page section, collection
  entry, daily block and raw-lane turn. The stored text is what a read prints. The build holds the
  `index` lock and writes the file in place in WAL mode, in one transaction, so a reader never waits and
  no journal file is unlinked under one. A lane that only grew has the bytes already indexed read and
  hashed again, to prove the file was only appended to (8 MiB in about 4 ms from the page cache on a
  development machine); only the new complete lines are parsed. The
  index keeps a fingerprint of the masking; when settings, the ledger or the masking rules change, or the
  stored fingerprint cannot be read, every
  file is indexed again before a search answers; a busy file is tried again every tenth of a second until
  the budget is spent, then the search refuses with `index_busy`. When the fingerprint is the same, a busy
  open answers from the rows already on file. Only a file that is corrupt or is not a database is
  `index_unreadable`, whose next move is `forest index reset`: that command publishes a new generation
  under `.forest/index.current` (with no pointer, `.forest/index.sqlite` stays current) and only then
  removes the old file and its `-wal` and `-shm` files. A call that already has a file open finishes on
  it. A file that cannot be removed is an orphan, not a failure. The command exits 2 with
  `index_busy` when the index lock is held. A busy or locked file is the busy path; a read-only file, a
  full disk, an I/O error and a path that cannot be opened have codes of their own whose next move is
  `report`. A file whose first build has not committed is no index yet. A file is skipped when its
  size and time are unchanged, and hashed when either changed, when `build --verify` is given, or when
  the stored hash is more than a day old. A CJK word of one or two characters is found inside a longer
  word. A page that is not valid UTF-8 is read with replacement characters and named in the status.
  FTS5 uses the trigram tokenizer where this SQLite has it, beside unicode61 for spaced words. Deleting
  the file and building again makes the same index. A search brings changed lane and daily files up to
  date first
- `forest search` matches registry names and aliases first, then words, then vectors only when
  `[embeddings] provider` is not `none` and a provider is registered in the process. No provider ships
  with the package, and the package makes no network call. One result per page, the section as written,
  with why it matched. `forest read` prints a page or `#address` with the same masking as every other read
- `forest timeline query` reads phrases from a language table. English is built in. Korean ships as
  `memory_forest/phrases/ko.json`. A forest adds a table under `.forest/phrases/`. A day-part phrase is
  judged in each entry's own time zone, and a line with no zone uses the page's. A duration is a window
  of instants. A raw-lane turn is found before any filing. `forest timeline subject` lists one subject's
  indexed sections in time order
- reader scope on search, read, timeline and MCP: results come from `agents.readable_trees`. An agent
  reads its own raw lane. An orchestrator reads every lane. The daily record is shared. Each answer names
  the trees that answered. `--named` is not an MCP argument
- `forest mcp --as <agent> [--http HOST:PORT]` serves `memory_search`, `memory_read`, `memory_timeline`,
  `memory_navigate` and `memory_log` over newline-delimited stdio and streamable HTTP, for protocol
  versions 2024-11-05, 2025-03-26, 2025-06-18 and 2025-11-25. On stdio a JSON-RPC batch is accepted for
  2025-03-26 only, after `initialize`, and a batch that holds `initialize` is refused. HTTP keeps no
  session, so a 2025-03-26 batch that does not contain `initialize` is answered from the protocol header.
  An HTTP error status may omit `id` where the revision allows it; a 200 payload keeps `id: null`. A tool's input validation error is `isError: true` under
  2025-11-25 and `-32602` before it. Every refusal of a tool is one JSON object (`code`, `message`,
  `details`, and `next` with `command` or `after_seconds` when it has a next move), the shape `--json`
  prints. A stdio line is bounded while it is read. The server's instructions stay under 2,048 characters. `memory_log` appends through the intake
  writer and reports whether that line was indexed; the server sets the role and the origin. Every HTTP
  bind, loopback included, uses `.forest/mcp/token` in mode 0600. The first start creates it and the
  token is never printed. A non-loopback `Origin`, a non-loopback `Host` on a loopback bind, and a body
  that is not JSON are refused. One bad request does not stop the server. The first tool call of each
  agent is recorded in `.forest/mcp/seen.json`
- `forest intake` writes one turn per line to an agent's raw lane (`06 istm/<agent>/<YYYY-MM-DD>.jsonl`),
  from the adapters `claude-code`, `codex`, `grok` and `jsonl`. A second run adds nothing. The cursor
  moves only after the line is synced. A hook that cannot take the short lock exits 0 and leaves its
  cursor. Numbers and sign-in values are masked at the door. Tool output is left out unless
  `intake.tool_output` is `keep`. Intake stays off until `[intake.tools.<adapter>] enabled` is true,
  unless `--once` is given. `forest intake status` counts lines the adapter did not understand
- `docs/v1-design.md` section 15, connecting agents: the turn record and `forest intake` with per-tool
  adapters and cursors, the MCP server and skill an agent uses, and setup that the owner's agent drives
  through `forest setup` and `forest doctor` (intake, search and the MCP server are built; setup is
  not); milestone 2 is widened to hold it

- the `forest` command (milestone 1): `init`; `registry build | check | show`; `move` with `leaf`,
  `rename`, `promote`, `merge`, `archive`, `move-branch`, `new-tree`, `new-agent`, `retire-tree`,
  `fold-tree`, `set-type`, `set`, `recut-stream`, `plan` and `resolve`; `levels check | apply`;
  `collections demote | admit | recut | list | cite-entries`; `record size | kinds | trim | history`;
  `archive put | show | list | check`; `numbers check | star`; `credentials check | fix | candidates |
  judge`; `check`; `lint`; `recover`
- `forest check [NAME …] [--sync]` is the one check command: the journal, the structure, the derived
  lists, the pointers, the agents, the levels and the categories, one line per problem
- the lint registers `board_leaves_stale`, `branch_list_stale`, `board_pointers_stale`,
  `board_pointer_stale`, `level_due`, `category_too_large`, `agent_lane_without_tree` and
  `agent_settings_differ`; `daily_section_unfiled` is supplied later, with the daily record
- every record line a tool writes is a change, an owner's word or a provenance line, on the page it is
  about; the tools write no run notes
- an agent gets its tree from its settings entry or from the first file in its raw lane
  (`agents.auto_create`); `forest levels apply` makes it, `forest move new-agent` takes an optional name
  and role, and a name or role the tree left unset is filled later from the settings
- new settings: `[agents.known.<id>]`, `agents.auto_create`, `agents.folded_type`,
  `levels.shared_branch` and `levels.default_subtopic`
- readers see trees by scope: an orchestrator every tree, an agent the shared trees, its own and those its
  task names, any other reader the shared trees
- every commit keeps an undo journal (`.forest/journal/commit.json`); the commit of a killed writer is
  rolled back by the next writer or by `forest recover`
- trimmed record lines go to one append-only record history file per page, with one pointer line for
  life; `forest record history` prints them, `forest archive list` shows each block with its kind, and
  `forest archive check` verifies copies and blocks against the manifest
- a current-state line about one collection entry cites `<page>#<entry id>` and is dated by that
  entry's own Timeline lines; a line about several cites each (``sources `<a>`, `<b>` ``). The lint reports an
  entry source that moved with its place now, and one found nowhere as gone. `move resolve` follows an
  entry address through cuts, category moves, admissions, the admitted leaf's later moves and merges and a
  demotion; every operation that moves an entry rewrites the sources that cited it, in the same write.
  `forest collections cite-entries [--dry-run]` gives the lines of an older forest their entry sources
  once, archiving each page first and changing only the tags
- a value sent to sign in or to pay (a password, a PIN, a one-time, verification or door code) after a
  label word is removed, never starred: record lines the tools write, the text a model hands the 0.3
  writer, and what `archive show`, `record history`, `record kinds --examples`, `registry show`, `search
  --include-body` and the structured context show carry `[REDACTED]` in its place. The lint reports
  `credential_in_full`; `forest credentials check | fix` finds and masks such lines, archiving each page
  first. New settings: `credentials.label_words` (English by default; a forest adds its other languages),
  `credentials.joining_words` and `credentials.count_words`
- a code in a quoted turn with no label word is judged once by a reader: `forest credentials candidates`
  lists the unjudged ones by page, line and place (the lint counts `credential_candidate_unjudged`), and
  `forest credentials judge … --sign-in | --not-sign-in` keeps an HMAC-SHA256 digest of the value under a
  key of mode 0600 in `.forest/credentials/`; a value judged a sign-in value is masked wherever it appears
  (the key's mode is checked on every read of the ledger, also after a change of mode alone)
- every `forest` command prints page text with numbers starred and sign-in values masked (registry and
  lint messages that quote a line and `collections list` printed them whole), and the 0.3 `search
  --include-body`, `route`, `retrieve` and `structured-context` star numbers as well as mask sign-in values
- a code-span address `<page>#<id>` on a page that is archived is marked ` (archived)` like a bare path
- `numbers star` and `credentials fix` say in their help that they take named pages or `--all`
- a redaction mark in doubled brackets (`[[REDACTED]]`, what masking leaves of a value that stood in square
  brackets) is quoted text and no link: the structure check and the route audit read it as none, where a
  masked `[4821]` made its page fail `wikilink_not_up`
- a name says who or what a subject is: an id, name or alias of a subject or a collection entry that reads
  as a sign-in value (`Lowtide verification code 4821`) or holds `[REDACTED]` is the violation
  `registry_name_masked`, and a plan that would add one is refused; before, every line carrying such a name
  was a `credential_in_full` line and `credentials fix` took the name off the subject's own pages
- after a label word and a colon, a dash or a row of dots (`PIN: -`) is no value and stays
- the contract states two rules for writing with a model: a line that states what quoted turns say is
  filed only after a second reading of the same turns judged it; a subject is never merged into another on
  what its own material claims, and subjects that would share a name each carry a distinguishing word
  until a reader who read both sides merges them (no tool merges on a name by itself)
- `forest move set-type` and `forest move set` write change lines; `retire-tree`, `fold-tree` and
  `recut-stream` write provenance lines
- a record trim killed inside its append leaves a torn tail that the next trim cuts off before appending
  its block whole (a torn first append is the page's own file, not a reason to take the next name)
- the read path stars what it shows: `forest archive show`, the record lines `forest record history` reads
  back from a history file, `forest record kinds --examples` and `forest registry show`
- every record line a writer adds goes through the number starring, and a merge of a subject saved under
  its phone number writes that number starred in its record lines and in the alias it adds; one test runs
  every writer on the synthetic forest and classifies each record line it adds
- the upkeep code `board_state_empty`: a board with no line under `## Current state` while its folder
  holds a leaf that is not a collection page; the synthetic forest's agent boards now carry theirs
- `forest repeats plan | apply | check`: `leaf_lines_repeated` counts fact lines only (a line in a fenced
  block or a bare reference is none); within a subject a reference line lives in the subject's index leaf;
  each other shared line goes to the home a decisions file names, or is judged once into
  `.forest/repeats-judged.jsonl`, which the lint reads; a leaf left empty is archived into the index leaf
  through the move tool; all of it as one checked plan under the undo journal, which now also cuts back the
  appended ledger
- `forest move recut-stream` cuts a month that still passes the cap by time, into runs of whole days (the
  first keeping the month's name, each later run named by its first day, a day past the cap into numbered
  pages of that day), each line on exactly one page and each title giving the days it holds; a
  `-YYYY-MM-DD[-N]` suffix is a period only beside its stream's other period pages, and the lint, the level
  rule and the tools read that one definition, which collection pages share

- `docs/contract.md`: the version 1 forest contract, every rule stated once: the lanes and the four forest
  layers; the subject as the unit with one registry line per subject; levels set by code from distinct
  days (collection entry, leaf, branch, tree); collections for subjects seen on one day, with the
  `standing` line; home trees with general placement rules; recurring events; one home for each fact with
  reference lines; `about:` pointers and generated `## Pointers`; notices; decisions; shops and purchases;
  agent trees; the leaf shape, the 8 KB content cap and splits by sub-topic or period; episodes; open
  points instead of disclaimers; `kind:` from a fixed list; the update record, its trim and the archive;
  "do not record"; account and identity number masking; what goes up; links; machine records; the lint
  codes with baselines; the tools; who does what
- `docs/concepts.md`: the forest explained for a new reader, following one invented subject from a one-day
  entry to a tree of its own
- `docs/multi-agent.md`: agent trees, the `subject-first` and `agent-first` filing policies with worked
  examples, reader scopes, raw lanes and the single writer, sharing a lesson, retiring an agent
- `docs/adapters.md`: how to write a reader of one program's own transcripts, for someone who knows that
  program: when a reader is worth writing and when records for `jsonl` are enough, every member intake
  uses with its mark (required or optional), the entry point group, what a hook call hands a reader, the
  rules a reader keeps, and the kiln-log reader of the tests as the worked example. The `Adapter`
  Protocol's text names the same members with the same marks, and a test holds the two together
- `docs/comparison.md`: how the design differs from Mem0, GBrain, the LLM Wiki idea and plain vector-store
  RAG, citing only those projects' own pages with the date they were read
- `docs/v1-design.md`: the design of version 1, its settings, package map and milestones
- `examples/synthetic-forest/`: a new invented forest that follows the version 1 contract, with three
  shared trees, an orchestrator and two bots with trees of their own, `forest.toml`, two days of daily
  pages and a raw lane per agent

### Changed

- the repository is `https://github.com/hyungchulc/agent-memory-forest` and the distribution is
  `agent-memory-forest`, the name the project is presented under; the import name `memory_forest` and the
  commands `forest` and `memory-forest` stay
- no document promises an engine: the design (sections 11, 13, 14 and 15.3 to 15.4), the contract
  (sections 9 and 10), the agents, privacy, comparison, concepts and tools pages say that the owner's own
  agent carries out the daily record, the filing run and the upkeep run, and that the package runs no stage
  and starts no model. The contract's filing run finds a subject with `forest registry show`, the command
  this tree has
- CI also runs on pushes to `v1`. The comparison states 2026-11-11 once, as the day the other projects'
  documents were read
- the package version is `1.0.0b1`, a beta, defined once as `memory_forest.__version__`. The `forest` command and
  the package metadata read it, and a test checks the citation, this heading and the CI smoke assertion
  against it. The `memory-forest` command reports `0.3.0`, the release that command is
- the project is presented as "Agent Memory Forest"; `README.md` and `README.ko.md` are rewritten for
  version 1, with a quick start that marks which milestone brings each command
- `docs/privacy-and-trust.md` and `docs/roadmap.md` are rewritten for version 1
- the two 0.3 agent skills, `memory-forest` and `memory-forest-retrieve`, moved from `skills/` to
  `docs/archive-0.3/skills/`, each with a first line saying it is for the 0.3 `memory-forest` command and
  that a version 1 agent runs `forest guide`: an agent that found them at the top of a version 1
  repository would follow the wrong commands
- the 0.3 documents and diagrams that version 1 replaces moved to `docs/archive-0.3/`, each with a first
  line naming what replaces it; the five 0.3 plan and receipt schemas stay in `docs/`, beside the 0.3 code
  that still uses them

### Fixed

- a `forest` command other than `init` takes the forest at `--root`, or the nearest folder at or above the
  current one that holds `forest.toml`. With no such folder it stops, names `forest init` or `--root`, and
  writes nothing. The quick start enters the new forest before the commands that run inside it

### Removed

- the readers of three particular agent programs (`claude-code`, `codex` and `grok`), their entry points
  and what only they used, before the first release. The package cannot keep up with every program's own
  transcript format, and shipping readers for three of them promised what it cannot keep. An agent brings
  its own conversation in through doors that know no program: its own turn through `forest log` or
  `memory_log`, whole conversations as records in the lane's shape read by the `jsonl` adapter, or an
  adapter that someone who knows the program writes and plugs in through the entry point group
  `memory_forest.adapters`. The package gives the way; whoever knows a program builds its reader. The
  plugins planned for setup went with them: setup writes nothing into another program's settings and
  tells the agent what to register in its own tool. Likewise the daily record, the filing run and the
  upkeep run are carried out by the owner's own agent, following the contract (section 10) and the
  guide; an engine that runs those stages is not part of the first release
- the 0.3 synthetic example forest under `examples/synthetic-forest/` (kept in the history at `v0.3.0`;
  the runnable copy built into the 0.3 code is unchanged)

## 0.3.0 - 2026-08-31

### Added

- end-to-end retrieval guide covering untrusted query intake, strict
  QueryPlan probes, deterministic root-first trails, explicit body access,
  freshness/conflict/no-evidence handling, Daily/ISTM fallback, and external
  ranker boundaries
- regression checks that require the public retrieval guide and resolve its
  repository-local documentation links
- scheduler-neutral automation guide with explicit deterministic-maintenance and semantic-promotion boundaries
- runnable lock-protected maintenance wrapper plus POSIX cron, macOS launchd, and Codex Scheduled Task examples
- automation diagram separating caller-owned semantic review from the
  implemented lock-protected writer and index lane
- regression checks for automation templates, wrapper behavior, and self-contained SVG assets
- Memory Forest Retrieve integration profile with a metadata-only per-turn gate, advisory system prompt, companion skill, and regression tests
- companion-project guide separating Codex conversation ingestion, Apple context collection, and retrieval evaluation from the canonical core
- bounded Daily contract covering stable source identities, compact provenance, cursor commits, verified archival, and fail-closed partial states
- strict Daily Plan v1, Structured Sweep Plan v1, and compatibility Promotion
  Plan v1 protocols with closed objects, bounded identifiers and hashes,
  explicit empty no-op batches, and semantic targets that cannot carry raw
  paths or arbitrary operations
- bounded `structured-context` snapshots with exact current-body hashes for one
  integrated XLTM/LTM/MTM/STM review and a whole-forest preimage hash that the
  writer rechecks under the maintenance lock
- local standard-library `apply-daily` and `apply-structured` writers with a
  shared sibling maintenance lock, one-transaction structured replacement,
  inert model text, canonical provenance, and compatibility `promote` support
- deterministic Write Receipt v1 files under private derived state, exact
  retry idempotency, and rollback of canonical files and the prior index on
  handled validation, audit, indexing, or receipt-publication failure
- focused writer tests for integrated materialization order, multi-layer
  replacement, stale preimages, idempotency, path safety, locking, rollback,
  receipts, no-op plans, inert text, and CLI output

### Changed

- retrieval diagram now uses ASCII-only visible labels and an explicit cross-platform font stack to avoid missing-glyph rendering
- retrieval evaluation roadmap now points to a dedicated companion Lab while keeping the core route and retrieve contracts authoritative
- package and CLI version advanced to `0.3.0`
- project license changed from MIT to GPL-3.0-only
- English and Korean documentation now describe the integrated Structured
  sweep as the reference write path and `promote` as a compatibility command
- automation documentation now distinguishes semantic plan generation from the
  implemented deterministic canonical writer

### Fixed

- aligned the published Write Receipt v1 operation enum and writer
  documentation with the implemented `apply-structured` transaction path
- added a regression that keeps the runtime receipt-operation vocabulary,
  machine-readable schema, and persisted Structured receipts in sync

## 0.2.0 - 2026-07-22

### Added

- deterministic `retrieve` operation that materializes validated `XLTM -> LTM -> MTM -> STM` trails and explicit canonical parent-child relationships without returning memory bodies
- strict versioned QueryPlan protocol for caller-supplied query expansion, with query-only probes and closed rejection of extra fields
- original-query-first ranking so untrusted expansion probes cannot outrank a direct user-query match
- provider-neutral OAuth and API gateway contract that keeps tokens, network access, forest-root mapping, and authorization outside the local core
- optional bounded QueryPlan configuration while preserving v0.1 forest configuration compatibility
- strict raw forest-configuration parsing with duplicate-key rejection and exact integer schema-version checks
- English and Korean documentation, a fictional multi-domain fixture, multiscript retrieval tests, and a static derived retrieval diagram

### Changed

- local index schema advanced to version 2 with explicit canonical parent paths; existing indexes fail with `index_schema_mismatch` until rebuilt with `memory-forest index ROOT`
- the synthetic runnable example now contains two fictional structured domains
- package metadata advanced to `0.2.0`

### Compatibility

- the canonical forest schema remains version 1
- `route` and `search` inputs, body boundary, and JSON result shapes remain compatible with v0.1 after rebuilding the derived index
- language bridging depends on caller-provided probes and the indexed content; this release does not claim universal semantic or cross-language retrieval

## 0.1.0 - 2026-07-22

Initial public alpha release.

### Added

- portable seven-layer Memory Forest contracts from `00 life_archive` through `06 istm`
- parent-first domain, branch, and leaf ownership model with adjacent-layer wikilinks
- exact-case canonical paths and required immediate-parent links for structured records
- bounded file, directory, depth, byte, link, query, and result traversal
- current-source hash verification for explicit indexed body retrieval
- public release auditing for sensitive path names and nested private runtime data
- local standard-library Python CLI for initialization, diagnostics, validation, audit, indexing, routing, and search
- route-first retrieval with explicit body inclusion
- local rebuildable SQLite search index
- filesystem escape, symlink, size, count, and private-permission guards
- wholly synthetic example forest
- public-release privacy and secret audit
- companion agent skill, architecture documentation, security policy, and contribution guide

### Boundary

This release does not include any real forest, private prompts, production ranking or promotion automation, operational logs, user identifiers, or private adapters.
