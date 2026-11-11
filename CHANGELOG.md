# Changelog

All notable changes to Memory Forest are documented in this file. The project follows [Semantic Versioning](https://semver.org/) once a version is released.

## Unreleased — 1.0.0 (alpha)

Version 1, Agent Memory Forest, is a new generation of the project, built in three milestones. The
`forest` commands of milestone 1, the structure tools, are in the tree and tested on made-up forests;
milestone 2 (finding: the time index, search, the MCP server) and milestone 3 (growing: the stage programs
and the nightly upkeep) are not built yet. Nothing is released. The 0.3 `memory-forest` command stays in
the tree until the version 1 commands replace it.

### Added

- the `forest` command (milestone 1): `init`; `registry build | check | show`; `move` with `leaf`,
  `rename`, `promote`, `merge`, `archive`, `move-branch`, `new-tree`, `new-agent`, `retire-tree`,
  `fold-tree`, `set-type`, `set`, `recut-stream`, `plan` and `resolve`; `levels check | apply`;
  `collections demote | admit | recut | list | cite-entries`; `record size | kinds | trim | history`;
  `archive put | show | list | check`; `numbers check | star`; `check`; `lint`; `recover`
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
- `docs/comparison.md`: how the design differs from Mem0, GBrain, the LLM Wiki idea and plain vector-store
  RAG, citing only those projects' own pages with the date they were read
- `docs/v1-design.md`: the design of version 1, its settings, package map and milestones
- `examples/synthetic-forest/`: a new invented forest that follows the version 1 contract, with three
  shared trees, an orchestrator and two bots with trees of their own, `forest.toml`, two days of daily
  pages and a raw lane per agent

### Changed

- the project is presented as "Agent Memory Forest"; `README.md` and `README.ko.md` are rewritten for
  version 1, with a quick start that marks which milestone brings each command
- `docs/privacy-and-trust.md` and `docs/roadmap.md` are rewritten for version 1
- the 0.3 documents and diagrams that version 1 replaces moved to `docs/archive-0.3/`, each with a first
  line naming what replaces it; the five 0.3 plan and receipt schemas stay in `docs/`, beside the 0.3 code
  that still uses them

### Removed

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
