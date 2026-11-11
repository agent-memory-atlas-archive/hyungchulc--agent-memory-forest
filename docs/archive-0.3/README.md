# Archive of 0.3

These documents describe Memory Forest 0.3 and its `memory-forest` command. Version 1 replaces them; each
page says on its first line what replaces it. They stay here because the 0.3 code is still in this tree
until the version 1 `forest` commands replace it (see [the roadmap](../roadmap.md)).

| Document | Replaced by |
| --- | --- |
| [architecture.md](architecture.md) | [concepts](../concepts.md), [the v1 design](../v1-design.md) |
| [layers.md](layers.md) | [the contract](../contract.md), section 1 |
| [provenance-and-promotion.md](provenance-and-promotion.md) | [the contract](../contract.md), sections 3 to 5 |
| [retrieval-guide.md](retrieval-guide.md) | the v1 design, section 10 (milestone 2) |
| [integrated-structured-sweep.md](integrated-structured-sweep.md) | the contract, section 10; the v1 design, section 11 |
| [cli.md](cli.md) | the `forest` commands of the v1 design, section 13 |
| [automation.md](automation.md) | the stages and nightly upkeep of the v1 design, section 11 (milestone 3) |
| [oauth-api-integration.md](oauth-api-integration.md) | no equivalent yet; reader scopes and the MCP server arrive with milestone 2 |
| [memory-forest-retrieve.md](memory-forest-retrieve.md) | no equivalent yet; the MCP server arrives with milestone 2 |
| [daily-and-istm-companions.md](daily-and-istm-companions.md) | the lanes of the contract, section 1; the v1 design, section 11 |
| [codex-debug-bridge.md](codex-debug-bridge.md) | no equivalent |
| [roadmap.md](roadmap.md) | [the roadmap](../roadmap.md) |

The 0.3 diagrams under `assets/` are kept beside them. The 0.3 plan and receipt schemas (`daily-plan`,
`structured-sweep-plan`, `promotion-plan`, `query-plan`, `write-receipt`, all `*.schema.json`) stay in
[`docs/`](..), beside the 0.3 code that still reads and writes those shapes. The 0.3
synthetic example forest is in the Git history at the `v0.3.0` tag; the runnable copy that
`memory-forest init --example` creates is built into the 0.3 code.
