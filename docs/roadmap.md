# Roadmap

Agent Memory Forest is beta software: 1.0.0b1 is the first public release of version 1. This page
describes direction, not delivery commitments. Version 1 is designed in [`v1-design.md`](v1-design.md) and
its rules are in [`contract.md`](contract.md); its code came in three milestones, all in this release.

## Version 1 milestones

1. **Skeleton.** The contract, the design, a synthetic forest with shared trees and agent trees that passes
   the lint, and the structure tools: `forest init`, `forest registry`, `forest move`, `forest levels`,
   `forest collections`, `forest record`, `forest archive`, `forest check` and `forest lint`, with tests on
   made-up forests.
2. **Connecting and finding.** Intake (`forest intake` with the generic `jsonl` adapter and the entry
   point group `memory_forest.adapters` for adapters written elsewhere, `forest log` and `memory_log`),
   the search index (`forest index`), time phrases (`forest timeline`), search with reader scopes
   (`forest search`, `forest read`) and the MCP server (`forest mcp`), and setup that the owner's agent
   carries out itself (`forest guide`, `forest setup`, `forest doctor`). Setup writes nothing into another
   program's settings; it tells the agent what to register in its own tool.
3. **Growing.** The daily record from the raw lanes, the bounded filing run, and the nightly upkeep that
   applies levels, works the lint's upkeep list down, trims records and re-checks stale current-state
   lines. In the first release the owner's own agent carries these out, following the contract
   (section 10) and `forest guide routine`.

The documents, the contract and the synthetic forest are written, and the `forest` commands of the three
milestones are in 1.0.0b1. The 0.3 `memory-forest` command stays in the tree beside them.

## After the first release

Not part of the first release, on purpose:

- readers of particular agent programs' own transcripts. The package cannot keep up with every program's
  format; an agent writes a small exporter of its own turns, and whoever knows a program can write its
  reader as an adapter behind `memory_forest.adapters` ([adapters](adapters.md));
- an engine that runs the daily, filing and upkeep stages itself.

Either may come later, and nobody promises either. Other candidate directions, not promises:

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
