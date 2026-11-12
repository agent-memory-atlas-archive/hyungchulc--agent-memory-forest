# Roadmap

Agent Memory Forest is alpha software. This page describes direction, not delivery commitments. Version 1
is designed in [`v1-design.md`](v1-design.md) and its rules are in [`contract.md`](contract.md); the code
is being ported in three milestones.

## Version 1 milestones

1. **Skeleton.** The contract, the design, a synthetic forest with shared trees and agent trees that passes
   the lint, and the structure tools: `forest init`, `forest registry`, `forest move`, `forest levels`,
   `forest collections`, `forest record`, `forest archive`, `forest check` and `forest lint`, with tests on
   made-up forests.
2. **Connecting and finding.** Intake (`forest intake` and the adapters), the search index
   (`forest index`), time phrases (`forest timeline`), search with reader scopes (`forest search`,
   `forest read`) and the MCP server (`forest mcp`) are in this tree. Still to build: setup that the
   owner's agent carries out itself (`forest setup`, `forest doctor`, the guide for agents, one plugin
   per tool).
3. **Growing.** The stage programs and their prompts (`forest stage`): the daily record from the raw lanes,
   the bounded filing run, and the nightly upkeep that applies levels, works the lint's upkeep list down,
   trims records and re-checks stale current-state lines.

The documents, the contract and the synthetic forest are written. None of the `forest` commands is released
yet; the 0.3 `memory-forest` command stays in the tree until they replace it.

## After version 1

Candidate directions, not promises:

- a migration path from a 0.3 forest to the version 1 layout, with a dry run, a backup manifest and the move
  log recording every old path;
- retrieval evaluation on synthetic forests, measuring registry, word and vector search separately;
- read-only graph views generated from `about:` pointers and the registry, kept out of the pages;
- adapters for agent runtimes beyond MCP.

## Non-goals

Agent Memory Forest will not become a hosted memory service, a background surveillance system, an
authorization engine or an automatic judge of truth. Integrations that need those must provide their own
governance and the owner's consent.

The 0.3 roadmap is kept in the [archive](archive-0.3/README.md) together with the other 0.3 documents.
