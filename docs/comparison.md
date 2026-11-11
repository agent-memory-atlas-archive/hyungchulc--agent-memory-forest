# Comparison with other open memory systems

This page compares Agent Memory Forest version 1 with four other ways to give an agent memory. For the
other systems it states only what their own public pages say, with the page and the date it was read
beside each claim. Where a page did not say something, the table says "not stated" rather than guessing.
Projects change quickly; check the linked pages before relying on a row.

For Agent Memory Forest the row describes [the v1 design](v1-design.md). The code is being ported in
milestones (design, section 14), so some of what the row describes is designed and not yet released. No
benchmark results of Agent Memory Forest are published, and this page claims none.

All pages below were read on 2026-11-11.

## At a glance

| | Agent Memory Forest v1 | Mem0 (open source) | GBrain | LLM Wiki (idea file) | Plain vector-store RAG |
| --- | --- | --- | --- | --- | --- |
| **Storage** | Markdown pages in numbered folders; derived state (registry JSON, indexes) under `.forest/` [d§3] | a vector store (local Qdrant by default) plus a SQLite history database; the self-hosted server defaults to Postgres with pgvector [m1][m2] | a Markdown repository is canonical; PGLite or Postgres with pgvector as a rebuildable index, though some ledgers live only in the database [g1][g2] | "a git repo of markdown files", with raw sources kept apart [k1] | a vector store: in memory, local or hosted [r1] |
| **Unit of memory** | a subject (person, project, device, agent…) with one registry line; its leaves hold one sub-topic each [d§4–5] | one extracted fact with metadata and an embedding; raw text when extraction is off [m3][m4] | a page with frontmatter, a "compiled truth" section and a timeline; facts as rows in a page's table [g3][g4] | a wiki page: a source summary, an entity or concept page, a comparison [k1] | a text chunk with its embedding [r1] |
| **Update semantics** | the page states the current fact; the old statement moves to the page's update record; levels and moves are done by code [d§4–5, d§7] | the current version adds only; an explicit update call overwrites a stored value [m5][m6] | the agent rewrites the compiled truth and appends to the timeline, whose entries are not rewritten [g3] | the model rewrites pages and notes where new sources contradict old claims [k1] | the page describes re-indexing on a schedule; no incremental update is described [r1] |
| **Multi-agent scoping** | shared trees plus one tree per agent; an orchestrator reads all, other agents read shared and own; reader id from the connection [d§6] | `user_id`, `agent_id`, `app_id`, `run_id` filters on records [m7] | sources inside a brain, with source grants and private visibility for remote callers; durable facts shared, task state local [g2][g5] | not stated; framed for one person [k1] | not stated [r1] |
| **History** | update records with an archive, a move log that resolves old paths, nothing deleted [d§5, d§7] | per-memory history in open source; a history endpoint with old and new values on the hosted platform [m8][m9] | git commits, page timelines, and version and belief ledgers in the database [g2] | git and an append-only log page [k1] | not stated [r1] |
| **What enforces structure** | a registry, level rules, a move tool and a linter with codes and baselines; a model reads and writes, code decides shape and place [d§1, d§8] | LLM extraction (on by default) and hash-based de-duplication [m4][m5] | schema packs with a human review step, pattern-based auto-linking, LLM passes, agent instruction files [g5] | a schema file of conventions, the writing model, a periodic model "lint", human review [k1] | a text splitter and embeddings [r1] |
| **What needs a network** | nothing except optional vector search through an embedding provider; the stages call a model of your choice [d§1, d§11] | an LLM is required; the defaults call a hosted provider, local models are supported [m5][m1][m9] | nothing in a keyless keyword mode; cloud providers or local runtimes when configured; paid hosting offered [g5] | not stated beyond the writing agent [k1] | embeddings and the LLM, hosted or local [r1] |

## Each system

### Mem0

Mem0 extracts short facts from conversations with a model and stores them in a vector store, with a
history database beside it [m2][m3]. Its documentation describes a change in its current version: the
earlier versions asked a second model call to choose between add, update and delete, and the current one
only adds, ranking the most recent fact first at retrieval [m5]. Records can be filtered by user, agent,
app and run ids, and a filter that names only some ids also returns records scoped by the others [m7]. It
runs as a library, a self-hosted server or a hosted platform [m1].

What it does better: it is working software, offered as a library, a self-hosted server and a hosted
platform [m1], and it needs no decisions about structure from the user; facts are extracted and retrieved automatically, with hybrid retrieval that
combines semantic, keyword and entity signals [m5]. Agent Memory Forest asks more of its writer (a home for
every fact, a level for every subject) in exchange for pages a person can read and correct.

### GBrain

GBrain is the closest in spirit. It keeps a Markdown repository as the source of truth and treats its
database as an index, though its pages say that some ledgers (belief and page versions) live only in the
database [g1][g2]. A page has a "compiled truth" section that is rewritten and a timeline that is appended
to [g3], which resembles a leaf's current text and `## Timeline` here. Facts are rows scoped to an entity
[g4]. It has typed schema packs with a human review gate and pattern-based linking without a model [g5].

What it does better: it is working software with typed pages, a graph of links between entities, cited
synthesis that says what is not yet known, and a keyless mode that runs without any model API [g5].
Agent Memory Forest differs in deciding placement and levels by counted rules (distinct days, a size cap)
rather than by schema types, and in giving each agent its own tree with reader scopes. Its README does not
mention a linter; whether GBrain checks structure by other means than the review gate and its auto-linking
was not verified.

### LLM Wiki

The "LLM Wiki" is an idea file, not software [k1]. It proposes that a model maintain a wiki of Markdown pages
in a git repository, compiled once from raw sources that stay separate and read-only, with an index page, an
append-only log, a schema file of conventions, and a periodic model pass that looks for stale or
contradicted claims [k1]. Agent Memory Forest shares that starting point (plain pages, raw sources kept
apart, the model as the writer) and differs mainly in moving the conventions from a prompt into code: a
registry, counted levels, a move log and a linter with codes.

What it does better: it is simple: one page of ideas that any capable agent can follow, with no software to
install, and it argues that a compiled wiki compounds where per-query retrieval starts over each time [k1]. It does
not state how several agents would share one wiki, and nothing in it enforces its conventions beyond the
model and a human reviewer.

### Plain vector-store RAG

The common pattern, as a widely used tutorial describes it: load documents, split them into overlapping
chunks, embed the chunks, store them in a vector store, and at question time retrieve the nearest chunks for
the model to answer from [r1]. The tutorial re-indexes on a schedule and does not describe updating or
deleting a single fact, scoping by user or agent, or keeping history [r1].

What it does better: it needs no structure at all, works on any pile of documents at once, and finds
passages by meaning across wording the question does not share. Agent Memory Forest keeps vector search as
an optional addition beside registry and word search (design, section 10), not as its organising idea.

## Sources

Agent Memory Forest:

- [d] [`docs/v1-design.md`](v1-design.md), this repository; section numbers as `d§n`.

Mem0 (read on 2026-11-11):

- [m1] README: <https://github.com/mem0ai/mem0>
- [m2] How it works: <https://docs.mem0.ai/core-concepts/how-it-works> and open-source overview:
  <https://docs.mem0.ai/open-source/overview>
- [m3] Python quickstart: <https://docs.mem0.ai/open-source/python-quickstart>
- [m4] Add memory: <https://docs.mem0.ai/core-concepts/memory-operations/add>
- [m5] Migration from v2 to v3: <https://docs.mem0.ai/migration/oss-v2-to-v3>
- [m6] Update memory: <https://docs.mem0.ai/core-concepts/memory-operations/update>
- [m7] Entity-scoped memory: <https://docs.mem0.ai/platform/features/entity-scoped-memory>
- [m8] Memory history API: <https://docs.mem0.ai/api-reference/memory/history-memory>
- [m9] Platform and open source compared: <https://docs.mem0.ai/platform/platform-vs-oss>

GBrain (read on 2026-11-11):

- [g1] README: <https://github.com/garrytan/gbrain>
- [g2] System of record: <https://github.com/garrytan/gbrain/blob/master/docs/architecture/system-of-record.md>
  and brains and sources: <https://github.com/garrytan/gbrain/blob/master/docs/architecture/brains-and-sources.md>
- [g3] Page anatomy: <https://github.com/garrytan/gbrain/blob/master/docs/guides/page-anatomy.md>
- [g4] Entity-scoped facts: <https://github.com/garrytan/gbrain/blob/master/docs/guides/entity-scoped-facts.md>
- [g5] README sections on schema packs, linking, network use and hosting: <https://github.com/garrytan/gbrain>

LLM Wiki (read on 2026-11-11):

- [k1] <https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f> (the gist's comment thread was
  not read)

Plain vector-store RAG (read on 2026-11-11):

- [r1] LangChain RAG tutorial: <https://docs.langchain.com/oss/python/langchain/rag>

## Not verified

- Mem0: no page read documents a history method in the open-source library itself; the history endpoint
  page documents the hosted API [m8]. The pages disagree on how many vector stores are supported ([m5] and
  [m9] give different numbers). The graph memory page was not read; that graph memory is platform-only comes
  from [m5] and [m9]. How newer facts outrank older ones at retrieval is not stated beyond a time signal [m2].
- GBrain: how a fact is corrected or withdrawn is not described on the pages read [g3][g4]; the MCP tool
  reference was not read. Whether it checks structure with a linter was not found.
- LLM Wiki: licence and multi-agent use are not stated; the comments under the gist were not read.
- Plain vector-store RAG: the row describes one tutorial's pattern, not every RAG system; many add update,
  scoping and history on top.
