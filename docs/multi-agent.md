# Agents

This guide is about one forest serving several agents. The rules are in
[the contract](contract.md) (section 3, "Agent trees"); the software is in [the design](v1-design.md)
(section 6). The examples use an invented team that works for Juno, the owner of a small pottery studio:

| Agent | Id | Does |
| --- | --- | --- |
| Studio Hub | `studio-hub` | the orchestrator: talks with Juno, hands work to the bots, checks what comes back |
| Kiln Bot | `kiln-bot` | runs and watches the kiln firings |
| Ledger Bot | `ledger-bot` | keeps the books and sends invoices after Juno approves them |
| Post Bot | `post-bot` | packs and ships orders, and answers customers' delivery questions |

[`examples/synthetic-forest`](../examples/synthetic-forest/README.md) has the first three as files.

## What an agent is

An agent is a worker that a model runs under its own instructions, over many runs, and that acts. An
orchestrator is an agent; so is a bot that owns one kind of work, a scheduled stage, or the sessions of a
coding assistant. What it runs on (a model, an engine, a provider) is a tag on the lines it touches, not
its identity: when Kiln Bot moves to another model, it is still Kiln Bot and keeps its tree. A helper
started for a single task is not an agent of its own; it works as the agent that started it.

## How an agent's tree appears

An agent is a subject of type `agent`, and it is a **tree from its first day**. Nothing has to be run to
make one: the forest knows an agent from either of two facts.

- **Its raw lane.** The moment Post Bot appends its first line to `06 istm/post-bot/`, the place every
  agent writes to, it is an agent of the forest.
- **Its settings entry.** The people running the forest can declare it, with a name in any script and a
  role, both optional:

  ```toml
  [agents.known.post-bot]
  name = "Post Bot"
  role = "packs and ships orders"
  ```

The upkeep step that settles levels makes the tree:

```sh
forest levels check
# 06 istm/post-bot/: level_due: post-bot none -> tree: an agent is a tree from its first day (its raw lane holds a file)
forest levels apply
# post-bot: none -> tree (an agent is a tree from its first day (its raw lane holds a file))
```

With the settings entry above, that writes:

- the registry line, in `03 mtm/post-bot/post-bot_MTM.md`:
  ``- `post-bot` — Post Bot · agent · tree · `02 ltm/post-bot_LTM.md` ``
- the tree page `02 ltm/post-bot_LTM.md`, which is what the agent reads first, with the sections
  `## Role`, `## Standing instructions`, `## Lessons`, `## Current state` and `## Branches`;
- its index `03 mtm/post-bot/post-bot_MTM.md`;
- its line under `## Agents` on the root index: `` - `post-bot` — Post Bot · packs and ships orders ``.

A name not given is the id (`post-bot`); a role not given leaves `## Role` with the line `(not set)`.
`forest move new-agent post-bot --name "Post Bot" --role "packs and ships orders"` makes the same tree at
once, through the same operation. A tree once made is left as it is: if the settings later give another
name, `forest move rename post-bot --name …` carries it out, and until then the lint reports the
difference (`agent_settings_differ`).

A forest that wants every agent declared sets `agents.auto_create = false`: a lane then makes no tree,
and a lane whose agent is not declared is the lint's upkeep hit `agent_lane_without_tree`. A lane folder
whose name is not a slug, is the id of a shared tree, or is a reserved name (the owner's id, the system's
id, `none`) never becomes a tree; `forest check` reports it as `agent_id_invalid`.

Inside its tree the ordinary rules hold: its sub-topics are branches (`shipping-runs`, `methods`), its
leaves follow the leaf rules, and they grow by distinct days like anything else.

If something was first filed as an ordinary subject and turns out to be an agent (a scheduled job that grew
into a worker with its own instructions), it is retyped and promoted with its leaves:

```sh
forest move set-type nightly-backup agent    # milestone 1
forest levels apply                           # milestone 1
```

## Two filing policies

The question an agent forest has to answer is: when an agent learns a fact, where does it go? The
`agents.policy` setting chooses between two answers.

### `subject-first` (the default)

For one owner served by several agents. A fact about a subject lives in that subject's home, whichever
agent learned it. The agent is a tag on the line. The agent's own tree holds only what is the agent's own:
its role, the instructions given to it, its methods and lessons, its runs and outcomes, its open work and
hand-offs.

**Worked example.** Post Bot ships six plates to Wren Castellane, a customer. On the way, Wren writes back
that her wedding has moved and the plates should arrive a week later.

- The new date is a fact about Wren. It goes to Wren's home, the leaf
  `04 stm/people/acquaintances/wren-castellane.md`, as a Timeline line:
  `- 2042-08-02 10:40 America/Halifax — wrote that her wedding moved to 2042-08-30; the plates are now due by 2042-08-23; by mail, noted by post-bot.`
- The shipment is Post Bot's run. Its tree gets one line in `04 stm/post-bot/shipping-runs/parcels.md`:
  ``- 2042-08-02 — held the plates for Wren Castellane a week, at her request: recorded in `04 stm/people/acquaintances/wren-castellane.md`.``
- If the episode taught Post Bot something ("ask for the event date before shipping a wedding order"), that
  goes to its `## Lessons`.

When Juno later asks Studio Hub "when is Wren's wedding?", the answer is in one place, and it does not
matter which bot heard it.

### `agent-first`

For a team whose agents work on separate matters, where each agent's knowledge is mostly its own. What an
agent learns is filed in its own tree. Each such leaf names, in `about:`, the shared subjects it concerns,
so that the shared subject's board points to it.

**Worked example.** Juno's studio grows and the bots now run almost separate businesses: Post Bot handles
all shipping and Ledger Bot all money. Post Bot learns from the courier that the Saltmere depot closes at
14:00 on Fridays.

- The fact is filed in Post Bot's tree, `04 stm/post-bot/shipping/depot-hours.md`, with
  `about: saltmere-depot`.
- The depot's board, `03 mtm/admin/saltmere-depot.md`, lists that leaf under `## Pointers` (generated from
  `about:`), so anyone reading about the depot finds it.

A week later Ledger Bot reads the same closing time on a courier's invoice. Now **two agents hold the same
fact**, which makes it **shared**. The nightly upkeep run moves it to the shared subject's home, the depot's
leaf, and leaves a reference line in each agent's leaf:

```text
- 2042-09-12 — the depot's Friday closing time: recorded in `04 stm/admin/saltmere-depot/hours.md`.
```

The same happens at once when the owner states a fact for everyone ("the depot is closed on the 14th").

## Who reads what

Every reader has an id. It comes from the reader's connection (the MCP session, the command's settings),
never from the text of the question, so an agent cannot widen its view by asking for another's.

- An **orchestrator**, listed in `agents.orchestrators`, reads the whole forest. Studio Hub needs that to
  check the bots' work and to answer Juno about anything.
- Any **other agent** reads the shared trees and its own tree. It reads another agent's tree only when
  `agents.default_scope` is `all`, or when its task names that agent ("Ledger Bot, check what Post Bot
  charged for shipping"). The default is `own+shared`.
- A reader that is no agent of the forest reads the shared trees only.
- Each tool call records which trees answered it, so a wrong answer can be traced to the page that gave it.

The rule is one function, `memory_forest.agents.readable_trees(config, registry, reader_id)` (with
`named=` for the agents a task names), which the search tools and the MCP server of milestone 2 call.

## One writer at a time, and raw lanes

Agents never edit forest pages at the same time. Instead:

1. Each agent appends what it saw and did to its own raw lane, `06 istm/<agent>/`. Appending to separate
   files needs no coordination.
2. The daily record, `05 daily/YYYY-MM-DD.md`, is written from the lanes in marked blocks.
3. One filing run at a time, holding the forest **writer lock**, files the day into the forest.
4. A direct edit of a forest page (a person fixing a line, an agent applying the owner's correction) takes
   the same lock.

This keeps every page change in one place in time, which is what lets the move log, the update records and
the lint baseline stay exact. The stage programs that do this arrive in milestone 3; the lock and the move
tool in milestone 1.

## Sharing a lesson

A lesson starts in the agent that learned it, under `## Lessons` on its tree page, with its date and the leaf
it came from:

```text
- Ask for the event date before shipping a wedding order (as of 2042-08-02; source `04 stm/post-bot/shipping-runs/parcels.md`).
```

When a lesson turns out to apply to more than one agent (Ledger Bot also needs event dates, to time its
invoices), it is no longer one agent's own. It becomes a line in the shared subject it concerns, here the
`## Rules` of the board for the studio's customers, and each agent's `## Lessons` keeps a reference line to
it. An instruction the owner gives to every agent is filed the same way at once. An instruction given to
one agent stays in that agent's `## Standing instructions`.

## Retiring an agent

When Juno stops shipping by courier and Post Bot is retired (`forest move retire-tree post-bot`):

- Its registry line gets the status `ended`, and its line under `## Agents` gets `· ended`. Its pages stay
  where they are and Studio Hub still reads them, so every past shipment and lesson can still be found and
  cited. An agent's tree is never emptied by retiring.
- If the tree is better kept as part of another, `forest move fold-tree post-bot --into studio-hub` turns it
  into a branch of Studio Hub's tree. That ends it as an agent: the subject takes the type `--type` names
  (by default `agents.folded_type`, `component`) and leaves `## Agents`. Its old branches become the
  `subtopic:` of its leaves, nothing is lost, and `forest move resolve` sends every old path to the new
  place. Its raw lane makes no new tree: the id now names the folded subject.
