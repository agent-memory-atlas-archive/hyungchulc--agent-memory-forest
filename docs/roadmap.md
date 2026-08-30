# Roadmap

Memory Forest is an alpha reference implementation. The roadmap describes direction, not delivery commitments.

## v0.3 scope

- portable 00-06 filesystem contracts
- deterministic local initializer
- structural validation and audit commands
- local SQLite derived index
- route-first and metadata-first retrieval
- explicit body inclusion boundary
- public-release privacy audit
- synthetic example forest
- deterministic XLTM-to-STM root-first retrieval
- strict query-only expansion protocol
- caller-owned OAuth and API gateway boundary
- English and Korean usage documentation
- standalone documentation and companion skill
- strict provenance-bound Daily Plan, Structured Sweep Plan, and compatibility
  Promotion Plan v1 protocols
- local network-free `apply-daily` and `apply-structured` transaction writers
- integrated XLTM/LTM/MTM/STM materialization with adjacent parent-child links
- private write receipts, exact retry idempotency, and handled-failure rollback
- Windows-facing WSL2 filesystem, permission, and backup guidance

## Issue #3 proposal tracks

The following tracks record how the early-adopter proposals in [issue
#3](https://github.com/hyungchulc/memory-forest/issues/3) fit the public
architecture. They are candidate directions, not implemented behavior or
delivery commitments.

### Accept as roadmap direction

- design bounded migration and attachment helpers around explicit source and
  target identities, dry-run inspection, backup manifests, rehearsed restore,
  and fail-closed cutover; never dual-write one live forest
- define bounded read-only health diagnostics that can report structural,
  provenance, index, permission, and freshness signals without repairing
  canonical content or widening filesystem discovery
- specify a distinct Life Archive plan, provenance, and receipt contract before
  automating archive selection; current archive prose is not an enforceable
  writer contract

### Modify at the integration boundary

- express human review as a caller-owned strict review envelope around closed,
  hash-bound plans and deterministic receipts; the core may validate and apply
  a reviewed plan, but it does not become an approval, identity, or
  authorization service
- explore deterministic, opt-in per-probe retrieval diagnostics that expose
  which accepted QueryPlan probe contributed lexical evidence and ranking
  weight; the core does not generate translations, infer semantic intent, or
  claim universal multilingual coverage
- evaluate a future strict review-envelope commitment that binds the caller's
  reviewed source set and one explicit disposition per source into durable
  receipt evidence; v0.3 receipts prove deterministic writes and retries, not
  semantic review completeness or intentional `source-only` coverage

### Defer until the safety contracts exist

- canonical writes to attached legacy forests and in-place migration before
  identity assignment, rehearsal, restore verification, and a single-writer
  cutover contract are specified and tested
- approval workflow orchestration inside the core, unbounded diagnostic scans,
  model-generated QueryPlans inside the local CLI, and native Windows ACL
  equivalence

## Candidate next steps

### Retrieval evaluation

- keep the core retrieval contracts versioned and compatible with the separate [Memory Retrieval Lab](https://github.com/hyungchulc/memory-retrieval-lab)
- publish synthetic routing fixtures and repeatable quality metrics in the Lab
- expand measured multilingual and mixed-language evaluation beyond the current multiscript regression fixture
- measure root-first routing separately from body ranking
- add conflict and stale-source evaluation cases
- keep the public retrieval guide aligned with the deterministic core and the
  separate Lab evaluation contract

### Derived graph views

- generate read-only graph views from canonical paths and adjacent-layer links beyond the current static fictional diagram
- visualize domains, branches, leaves, and promotion provenance
- keep lateral similarity and community edges outside canonical Markdown
- make graph generation optional and rebuildable

### Extensible ranking

- define a provider-neutral ranker interface
- retain deterministic lexical routing as the baseline
- allow optional local or remote ranking adapters without changing canonical storage
- record ranker provenance and evaluation results

### Maintenance and migration

- incremental integrity checks
- bounded migration helpers for earlier starter layouts
- export and restore manifests
- richer conflict-aware plan review helpers outside the deterministic writer

### Ecosystem adapters

- documented helper adapters for agent runtimes
- editor and knowledge-tool integrations that preserve the local source boundary
- machine-readable route result schema with versioned compatibility tests
- keep macOS Codex-only and Apple-context ISTM collection in their source-specific companion repositories

## Non-goals

The roadmap does not include a requirement to become a hosted memory service, a silent background surveillance system, an authorization engine, or an automatic truth oracle. Integrations that need those capabilities must provide their own explicit governance and user consent.
