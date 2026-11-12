# Agent Memory Forest

**A readable, rule-kept local memory for long-running AI agents and teams of agents.**

[한국어 README](README.ko.md)

[![CI](https://github.com/hyungchulc/memory-forest/actions/workflows/ci.yml/badge.svg)](https://github.com/hyungchulc/memory-forest/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-2EA44F.svg)](LICENSE)
[![Local-first](https://img.shields.io/badge/runtime-local--first%20%7C%20no%20network-555555.svg)](docs/privacy-and-trust.md)

Agent Memory Forest is a Python package and a `forest` command that keep what agents learn as plain
Markdown pages in a few numbered folders on your own disk. Every fact has one home, decided by rules rather
than by the chat or the agent that brought it in, and code, not a model, decides where a page belongs and
when a subject has grown enough to get pages of its own. Several agents share one forest: shared trees for
what everyone needs, a tree per agent for what is its own, and an orchestrator that reads all of it. An
agent's tree comes by itself: when an agent writes the first line to its raw lane, the next upkeep step
makes its tree.

```sh
ls "my-forest/06 istm/post-bot"            # the agent wrote its first line here
# 2042-05-15.jsonl
forest levels apply --root my-forest       # one command, the same upkeep step that settles every level
# post-bot: none -> tree (agent; its raw lane holds a file)
ls my-forest/"02 ltm"
# post-bot_LTM.md   studio_LTM.md          # its tree page: role, instructions, lessons, current state, branches
```

Version 1 is a new generation of the 0.3 Memory Forest. Its rules are written down in full; its code is
being ported in three milestones (see [Status](#status)).

> [!IMPORTANT]
> Agent Memory Forest is not a hosted service, an authorization system, or a promise that an agent will
> remember correctly. Memory is grounding data: a caller still opens the page it was routed to, checks how
> fresh it is, and applies its own authority and safety rules. The forest holds no credentials, and
> everything except optional vector search works with no network.

## Why this exists

A long-running agent tends to fail at memory in one of two ways: it keeps too little, or it keeps a pile
that grows until nothing in it can be found and nobody can correct it. Version 1 is built around a few
properties that address that directly.

- **The subject is the unit.** A person, a project, a device, an agent: each has one registry line with its
  aliases, and its material lives in its own home.
- **Places are decided by code.** A subject's level (an entry on a shared page, a leaf, a branch, a tree)
  follows the number of distinct days its material covers. Every move goes through one tool that rewrites
  references and logs the move.
- **One home for each fact.** Other pages carry a dated reference line, not a copy, so facts cannot drift
  apart.
- **History is kept.** A changed fact moves to the page's update record; long records are archived
  verbatim; nothing is deleted.
- **Upper pages hold what is established.** A preference reaches a summary page only on repeated evidence
  or the owner's own words, never because it is the newest thing said.
- **A linter with codes.** Every rule that has a shape is checked, and a run must clean up what it added
  without being blocked by what was already there.
- **Built for several agents.** Raw lanes per agent, one writer at a time, reader scopes, and two filing
  policies.

## The forest in one picture

```mermaid
flowchart TB
    owner(["Owner"]) <--> hub["Orchestrator agent"]
    hub <--> botA["Bot A"]
    hub <--> botB["Bot B"]

    subgraph lanes ["Lanes (not forest)"]
        istm["06 istm<br/>one raw lane per agent, append-only"]
        daily["05 daily<br/>one readable page per day"]
        istm --> daily
    end

    hub -- append --> istm
    botA -- append --> istm
    botB -- append --> istm

    daily -- "filing run<br/>one writer at a time" --> stm

    subgraph forest ["Forest"]
        xltm["01 xltm<br/>identity · trees · agents"]
        subgraph shared ["Shared trees"]
            ltm["02 ltm tree pages"] --> mtm["03 mtm indexes and boards"] --> stm["04 stm leaves"]
        end
        subgraph own ["Agent trees, one per agent"]
            altm["role · instructions · lessons"] --> amtm["boards"] --> astm["runs · methods · hand-offs"]
        end
        xltm --> ltm
        xltm --> altm
    end

    hub -. reads everything .-> forest
    botA -. reads shared + own .-> shared
    botB -. reads shared + own .-> shared
```

Material moves up from the lanes into the leaves, and only what the leaves establish is carried up to the
boards and tree pages. Placement goes the other way: the root index says which tree a kind of subject
belongs to, the tree says which branch, the branch holds the leaf. [Concepts](docs/concepts.md) walks
through one subject's life from a one-day mention to a tree of its own.

| Folder | Holds |
| --- | --- |
| `01 xltm/XLTM.md` | the root index: who the owner is, the list of trees with placement rules, the list of agents |
| `02 ltm/<tree>_LTM.md` | one page per tree: scope, durable patterns and rules, current state, branches |
| `03 mtm/<tree>/` | the tree's index (its registry of subjects) and one board per branch |
| `04 stm/<tree>/<branch>/` | the leaves: one sub-topic of one subject each, in full detail, at most 8 KB of content |
| `05 daily/` | the readable record of each day (a lane) |
| `06 istm/` | the raw record, append-only, one lane per agent (a lane) |
| `.forest/` | the compiled registry, the move log, the archive, indexes and locks |

## Quick start

Agent Memory Forest needs Python 3.11 or newer, a POSIX filesystem, and no runtime dependencies outside the
standard library. On Windows, use WSL2 and keep the forest on the Linux filesystem; read the
[WSL2 guide](docs/wsl2.md) first.

```sh
git clone https://github.com/hyungchulc/memory-forest.git
cd memory-forest
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

The version 1 commands are the `forest …` commands of the [design's package map](docs/v1-design.md#13-package-map).
**Version 1.0.0a1 is this tree's alpha, not a stable release.** The milestone 1 commands below are in this tree and installed with it.
Milestone 2 has intake, the search index, timeline, reader scopes and the MCP server in this tree. Setup
is not, and the milestone 3 commands are not either. The 0.3 `memory-forest` command (documented in
[`docs/archive-0.3/`](docs/archive-0.3/cli.md)) stays installed beside them.

`forest init` is the only command that creates a forest. The others use `--root` when you pass it, and
otherwise the nearest folder at or above the current one that holds `forest.toml`. The block below creates
a forest and enters it, so each following line finds that file.

```sh
# Milestone 1: skeleton and structure tools
forest init my-forest                          # settings, layers, root index
cd my-forest                                   # this folder is the forest the commands below use
forest move new-agent studio-hub --name "Studio Hub" --role "orchestrator"   # or let its lane make it
forest registry build                          # compile every ## Subjects section
forest levels check                            # which subjects and agents are due a new level
forest levels apply                            # carry them out through the move tool, logged
forest check --sync                            # bring derived lists up to the pages, then every check
forest lint                                    # every rule with a shape, by code
forest repeats check                           # fact lines two leaves of one subject share
forest record kinds                            # the update records' lines by kind
forest numbers check --all                     # account, card, identity and phone numbers in full

# Milestone 2: intake, the index, search, timeline and the MCP server are in this tree; setup is not
forest intake --from jsonl --once              # new turns of that tool's transcript into its raw lane
forest intake status                           # lines an adapter did not understand
forest index build                             # .forest/index.sqlite, rebuildable
forest search "the old oven" --as till-bot     # aliases first, then words, inside the reader's scope
forest timeline query "yesterday evening" --as till-bot
forest mcp --as till-bot                       # search, read, timeline, navigate, memory_log
forest setup                                   # not built yet: run by your agent
forest doctor                                  # not built yet: proves a turn arrives and the MCP server answers

# Milestone 3: growing (not built yet)
forest stage …                                 # the daily, filing and upkeep stages around your model
```

To see what a forest looks like before any code runs, read the invented
[synthetic forest](examples/synthetic-forest/README.md): an owner, three shared trees, an orchestrator and
two bots, two days of daily pages and a raw lane, about forty small files.

## Agents in five lines

1. An agent (an orchestrator, a bot, a scheduled stage) is a subject that is a tree from its first day:
   its settings entry or the first file in its raw lane is enough, and `forest levels apply` makes the tree.
2. Its tree page holds its role, standing instructions, lessons, current state and branches.
3. `subject-first` (default) files facts in the subject's home tagged `noted by <agent>`; `agent-first`
   files them in the agent's tree with `about:` pointers, and shares a fact once two agents hold it.
4. An orchestrator reads the whole forest; other agents read shared trees and their own.
5. Each agent appends to its own raw lane, and one filing run at a time writes the forest under a lock.

Details: [Agents](docs/multi-agent.md).

## Documentation

| Document | What it covers |
| --- | --- |
| [Concepts](docs/concepts.md) | the forest for a new reader: layers, a subject's life, one home, what goes up, records, moves, the lint |
| [Contract](docs/contract.md) | every rule a forest keeps, stated once |
| [Agents](docs/multi-agent.md) | agent trees, the two filing policies, reader scopes, the single writer |
| [Design](docs/v1-design.md) | the package: on-disk layout, settings, package map, milestones |
| [Tools](docs/tools.md) | `forest move`, `levels`, `collections`, `check`, `lint`, `record`, `numbers`, `intake`, `index`, `search`, `timeline` and `mcp` |
| [Comparison](docs/comparison.md) | how this differs from Mem0, GBrain, the LLM Wiki idea and plain vector RAG |
| [Privacy and trust](docs/privacy-and-trust.md) | what stays local, what a reader can see, memory as untrusted data |
| [Roadmap](docs/roadmap.md) | the three milestones and what comes after |
| [WSL2](docs/wsl2.md) | running on Windows through WSL2 |
| [Archive of 0.3](docs/archive-0.3/) | the 0.3 documents that version 1 replaces |

## Status

Agent Memory Forest is alpha software. The version in this tree is 1.0.0a1.

- **Written:** the contract, the design, the concepts and agents guides, and an invented synthetic forest
  that follows the contract.
- **Milestone 1, skeleton (in this tree, unreleased):** the `forest` structure commands (registry, move,
  levels, collections, record with its history files, archive, `check`, `lint`, number starring,
  `recover`), agents' trees made from their lanes or settings, and an undo journal for every commit, with
  tests on made-up forests and the synthetic forest passing every check.
- **Milestone 2, connecting and finding (intake, the index, search and the MCP server are in this tree):**
  `forest intake` reads each tool's transcripts into that agent's raw lane. `forest index` builds
  `.forest/index.sqlite`. `forest search`, `forest read` and `forest timeline` answer from it, inside
  `agents.readable_trees`, and `forest mcp` serves search, read, timeline, navigate and `memory_log`.
  Setup carried out by the owner's agent (`forest setup`, `forest doctor`, the guide, the plugins) is
  not built yet ([design, section 15](docs/v1-design.md#15-connecting-agents)).
- **Milestone 3, growing (not built yet):** the stage programs and their prompts, the nightly upkeep.

The 0.3 code (`memory-forest`) stays in this tree until the version 1 commands replace it. It is suitable
for evaluation and adaptation, not for unattended high-stakes decisions.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). Keep fixtures invented, keep real forests outside Git, and run the
full local check before opening a pull request.

```sh
make check
```

Security issues follow [SECURITY.md](SECURITY.md). Community participation follows
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

Agent Memory Forest is released under the [GNU General Public License v3.0](LICENSE).
