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
# 06 istm/post-bot/: level_due: post-bot none -> tree: agent; its raw lane holds a file
forest levels apply
# post-bot: none -> tree (agent; its raw lane holds a file)
```

A turn an agent writes with `forest log` or `memory_log` makes that one agent's tree before the call
returns, or answers `pending` with a reason when tree work cannot finish. The next write retries it and
makes the tree once the cause clears. The level pass still makes the tree that a settings entry or an intake
lane left due. For readability,
the following lines show decoded display names; generated names on disk use marked numeric references.
With the settings entry above, that pass writes:

- the registry line, in `03 mtm/post-bot/post-bot_MTM.md`:
  ``- `post-bot` — Post Bot · agent · tree · `02 ltm/post-bot_LTM.md` ``
- the tree page `02 ltm/post-bot_LTM.md`, which is what the agent reads first, with the sections
  `## Role`, `## Standing instructions`, `## Lessons`, `## Current state` and `## Branches`;
- its index `03 mtm/post-bot/post-bot_MTM.md`;
- its line under `## Agents` on the root index: `` - `post-bot` — Post Bot · packs and ships orders ``.

A name not given is the id (`post-bot`); a role not given leaves `## Role` with the line `(not set)`.
`forest move new-agent post-bot --name "Post Bot" --role "packs and ships orders"` makes the same tree at
once, through the same operation. What the settings give later fills only what the tree left unset:
a name that is still the id and a role that is still `(not set)` are filled by the next `forest levels
apply`, with a change line on the tree page. A name or a role set in both places and set differently is
never changed by code; the lint reports it (`agent_settings_differ`) until `forest move rename post-bot
--name …` or an edit makes them one.

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

Every reader has an id. It comes from the reader's connection (the MCP session, the command's `--as`),
never from the text of the question, so an agent cannot widen its view by asking for another's. When
several agents share one connection, the caller also names itself. Identity is resolved across the
whole forest first, the exact stored id before the name key, and membership is applied afterwards.

- An **orchestrator**, listed in `agents.orchestrators`, reads the whole forest. Studio Hub needs that to
  check the bots' work and to answer Juno about anything.
- Any **other agent** reads the shared trees and its own tree. It reads another agent's tree only when
  `agents.default_scope` is `all`, or when its task names that agent ("Ledger Bot, check what Post Bot
  charged for shipping"). The default is `own+shared`.
- A reader that is no agent of the forest reads the shared trees only.
- The daily record is shared with every reader. A block on it comes from an agent's raw lane, and once
  filed it shows that agent, the time and the text. The lane file stays with that agent and with
  orchestrators.
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
the lint baseline stay exact. The package supplies the lock and the move tool. Writing the daily record and
filing it are the work of the owner's own agent, which carries them out following the contract (section 10)
and the guide; the package runs no stage of its own, and `forest check`, `forest lint` and `forest levels`
judge what the agent wrote.

## Intake

The raw lane is not edited by the agent. `forest intake` is the only writer. It reads through an adapter,
which knows where a tool keeps transcripts and how a turn is shaped. The package ships the generic one,
`jsonl`, for records already in the lane's shape; a reader of a particular program is written by whoever
knows that program and plugged in through the entry point group `memory_forest.adapters`. An agent can
also write its own turn with `forest log` or `memory_log`. A hook runs
`forest intake --from <tool> --hook` and reads only the transcript it was called for. A missed hook on
that same transcript is caught by the next hook of it. Any other transcript is caught by a sweep,
`forest intake --from <tool>`. The first sweep of a tool turns intake on for it; a hook never does.
`forest intake status` names a source whose file is ahead of its cursor, the internal sessions, the hooks
that found the intake lock held, and the hook calls that came before intake was turned on. A tool's
cursors are kept by tool and each names the agent whose lane its turns went to, so two agents can read
different transcripts of one tool. A transcript whose cursor names another agent than the one now set
is not read, and `forest intake rename-agent OLD NEW` moves that agent's lane and ids and the name in its
cursors when the configured name was changed.

A hook exits 0 and prints nothing: a client can treat a hook's exit 2 as a block, and can show its
stderr or add its stdout to the model's context.

Which conversations are taken in is set per tool, and intake stays off until that tool is enabled. The
folders a session ran in can be included or excluded. While a source is excluded its cursor follows the
file's end and nothing is written; lifting the exclude does not import that past. Turns from before the
adapter was first enabled are left where they are unless earlier import is turned on. Only a value the
adapter knows to be the owner's own client is `interactive`. An unrecognised value is `unknown`, kept
raw in `source`, and a later stage treats it like `headless`. A session another program started is
`headless`. A parent session id alone does not make a session a sub-agent's. A sub-agent's work is a
tool turn of the agent that started it, so a later stage does not read it as the owner speaking.

What a transcript marks is the adapter's to read. Text a program inserts into a user turn, where the
program marks it, is `system` or a `harness` span; text inserted without a mark cannot be told from the
owner. The record and
the writer's rules are in the contract, section 1.

Two rules hold for whoever files with a model. A line the model writes to state what quoted turns say is
filed only after a second reading of the same turns has kept, corrected or dropped it: code can check that
its names and numbers stand in the source, never what it means. And a subject is named from its own
material but never merged into another on what that material claims: a sender that writes in Cinderwell
Supply's name may be the shop or someone posing as it. When several subjects would take one name, each
carries a distinguishing word and none takes the bare name, and the merge waits for a reader who has read
both sides (contract, sections 2 and 4).

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

## An app that runs several agents

Kiln Bot and Ledger Bot can run inside one app Juno uses, with one tool connection for both of them, no
hooks, and no transcript on Juno's machine. The connection is a group. Inside a group a caller is whoever
it says it is, so any member can read what another member's own tree and lane hold by calling under that
member's name. A group tells one owner's agents apart for memory's sake and is no wall between them.

```toml
[agents.groups.studio-counter]
members = ["kiln-bot", "ledger-bot"]
open = false

[agents.known.kiln-bot]
name = "Kiln Bot"
```

```sh
forest mcp --as-group studio-counter
```

Every tool on that connection takes `agent`, the name the app uses for the caller.

### The agent is its name

Every agent comes in under the name its owner gave it, in any script, and gets its own lane and tree with
no step by Juno. The same name is the same agent wherever it is used: a Kiln Bot in two apps is one Kiln
Bot, with one lane and one tree. Nothing else makes two names one agent.

**A name's key** decides. The key is the name in Unicode NFKC, case-folded, with white space, hyphens and
dashes of every kind and underscores taken out. Every other character counts: a letter with an accent is
another letter, and a dot, a plus sign or an exclamation mark is part of the name.

| Names | One agent or two |
| --- | --- |
| `Kiln Bot`, `kiln_bot`, `KILN-BOT`, `Ｋｉｌｎ Ｂｏｔ` (full-width) | one: the key is `kilnbot` |
| `Kiln Bot`, `Kiln.Bot` | two: the dot counts |
| `Café Bot`, `Cafe Bot` | two: the accent counts |
| `스카우트 봇`, `스카우트봇` | one: the key is `스카우트봇` |
| `スカウト`, `ｽｶｳﾄ` (half-width) | one |
| `Разведчик`, `РАЗВЕДЧИК` | one |

A name finds an agent in two ways only: the text **is** the agent's id, character for character
(`kiln-bot`), or its key is the key of the agent's stored name (an agent that has no name is named by its
id). The exact stored id wins before the key, over all agents, before group membership. A name whose
key no agent has is a new agent. The only names refused are empty text, text longer
than 200 characters (counted in characters, not bytes), and text that holds a control character or a line
break; the refusal is `agent_name_invalid` or `agent_unset`, with `fix`. The argument rule and its
Unicode classes are in the contract, section 2. Names that need escaping in a registry line or page
use reversible numeric character references, with the same displayed title. Subject alias rules and
masked-value tests never refuse an admitted agent name. Invisible format characters keep their key
and receive a notice in `forest check`.

New names use the marker `<!--amf-name-v1-->` and a numeric reference for every character, including
letters. This keeps a credential-looking display name intact through masking upkeep. Unmarked
legacy references stay literal and untouched legacy lines keep their representation.

### The id is only a handle

A new agent's id is made once, when it is admitted, and stored beside its name in `forest.toml`
(`[agents.known.<id>]`, `name = "…"`). Every later call finds the agent by its key among the stored
names; the id is never made again, so a later change of a letter table moves nobody. The id is ASCII, made
letter for letter, one table per script: not a language's spelling, and no sound changes.

| Name | Id |
| --- | --- |
| `Kiln Bot` | `kiln-bot` |
| `Crème Bot` | `creme-bot` (a letter with an accent becomes its base letter) |
| `스카우트` | `seukauteu` (Revised Romanization of each syllable's initial, vowel and final) |
| `スカウト` | `sukauto` (Hepburn) |
| `Разведчик` | `razvedchik` |
| `Θάλεια Ρόδου` | `thaleia-rodou` |
| `侦察兵`, `★`, `Tea+Bot` | `agent-` and six hexadecimal digits of the SHA-256 of the key |

Han characters, a script with no table, and symbols cannot be read letter for letter, and they are not
guessed: when any letter, digit or symbol of the name is carried by no table, the id is `agent-` and the
first six hexadecimal digits of the SHA-256 of the key, and the rest of the name is not used. An id longer
than 200 characters is cut and ends in the same six digits.

An id somebody already holds is never shared. Held by something that is not an agent (a subject, the
owner's id or name or alias, a group's id, the system id, `none`, a collection id, a shared tree), the new
id is the id and `-agent` (`bakery-agent`), then `-agent-2`. Held by an agent with another key (`Crate.Bot`
was admitted as `crate-bot`, then `Crate Bot` comes), the new id is the id and the smallest free number from
2 (`crate-bot-2`).

Generated ids also avoid the Windows device basenames, case-insensitively and before an extension,
and never end in a dot or space. A declared reserved id still works where the system holds it;
`forest check` gives its rename. The id record is read first under the lock shared with tree creation,
so a half-created tree cannot give another agent the non-agent suffix.

To give an agent the id you want, declare it before its first turn: an agent declared with an id and a name
is found by that name, whatever the tables would have made.

```toml
[agents.known.scout]
name = "스카우트"
```

The agent that carries out setup can propose such a romanization for a name no table carries. After an
agent's first turn its tree carries its id, and the package has no move that changes it: `forest move
rename` refuses a tree's id (`rename_tree_unsupported`) and `forest intake rename-agent` moves the lane
only.

### Open, closed, and a source of members

An open group (`open = true`) admits every new name a caller gives, at once. There is no greatest number
of agents unless Juno sets `max_members`. A name some agent already has is that agent, whichever group or
tool it first came from, and on an open group it becomes a member here as well. A closed group
(`open = false`) refuses a name that is not a member, lists the members' ids and names, and the next move is
to call again under the caller's own name as it is listed. It never says to call as some particular member.

A group may name a `source` of members, a reader of the app's own list registered in the process. The
source is then the list: a name on it is admitted, a name not on it is refused as a closed group refuses
it, and `open` does not widen it. A read of the source has a deadline (5 seconds) and a greatest size
(50,000 texts of at most 200 Unicode characters each), and for 60 seconds after completion the last list is used again, so a caller cannot make Juno
pay for a read on every call. A source that fails, does not answer in time, lists too many names or has no
reader in the process is `member_source_failed` with `report`, never "not a member". Names on the list
that are not a name by the rule above are left out, and `forest agents` shows them. A single text,
mapping, number or a list with a non-text entry is a malformed result, never a list of letters.
Duplicate name keys keep the first spelling and are reported. Concurrent misses share one refresh.

`register_member_source(name, reader, cooperative=True)` calls
`reader(group_id, *, deadline, cancelled)`, with an absolute monotonic deadline and a cancellation event;
the reader must return by that deadline or cancellation. Legacy one-argument readers remain supported.
An abandoned reader keeps its one source slot in the process. Later calls fail immediately, stating
when it started, and `forest check` reports it. A late result is discarded. No second worker starts
until the first returns, or the answering process is restarted.

On a group connection a caller that names the main agent sees what the main agent sees. An orchestrator
needs no entry in `members`, on an open group, on a closed one, or on a group fed by a source. A name that
differs from the main agent's by a dot, a mark or an accent is another agent, with none of its reach.

### What an admission writes

A second identical admission changes no byte of `forest.toml`. Two calls that would both admit the same
new name end as one member and one settings write. An admission adds the id to the group's `members` list
and, for a new agent, one `[agents.known.<id>]` table at the end; every other byte of the file stays as it
was, line endings, a byte-order mark and a missing last line ending included. A group written inline, with
dotted keys, or under its parent table is not patched: the refusal `settings_unpatched` gives Juno the
edits, as sentences and in `details.edits` (`{"add_to_list": "members", "table": "agents.groups.<id>",
"value": "<id>"}` and `{"new_table": "agents.known.<id>", "values": {"name": "…"}}`), and so does
`settings_unwritable` when the file cannot be written. A patch whose result would not parse, or would
mean anything else, is caught before anything is written and answered the same way.

`add_to_list` requires an existing list. `set_value` creates a missing key or replaces a wrong-kind
value, creating missing parent tables; a valid single-text member is retained when repaired into a
list, and the message shows the rejected value. `new_table` creates a missing table. Failure to create
or take an admission lock is `settings_unpatched` with the same edits, even without `.forest`.
Apply these operations to the parsed TOML table, serialize it, parse it again, and retry the call.
Parser-filled diagnostic fields are not settings; `forest check` reports them as unknown entries.

### Where a turn came from

A turn's source is a kind and a name: `group:<id>` for a turn through a group connection,
`door:mcp` for `memory_log` on `forest mcp --as`, `door:cli` for `forest log --as`, and `adapter:<name>`
for a turn `forest intake` read. A group called `mcp` is `group:mcp` and never the door. The turn carries
its sources in `sources`; `forest agents` shows the sources each agent has written through, and search,
read and timeline take `source` (a tool argument) or `--source` (a flag) in that form. A source no lane
the reader may read has is `source_unknown` with `fix`, and the answer lists the reader's sources. A
reading narrowed to a source keeps the file's own line numbers (a read names them in `kept_lines`), and it
is narrowed before its limit. `forest agents` starts with a small record the lane writer keeps,
`.forest/intake/sources.json`; a verified record avoids another lane scan. It holds each agent's sources and each lane file's device, inode, size, modification and change
nanoseconds. A same-length rewrite changes that stamp. The whole record, every element and its integrity
check must agree; a malformed record is rebuilt in full. Before `source_unknown`, the readable lanes are
verified, and the correction choices come from them.

For unchanged lane and record stamps, a reader scans an agent's lanes at most once per process, and persists that scan when it can.

### A call is a turn

Every `memory_log` or `forest log` call is a turn of its own, with an id of its own: Kiln Bot saying
"yes" twice in a session has said it twice. A caller that may send the same turn twice gives it its own
id, `turn_id` (the tool's argument) or `--turn-id` (the command's flag): the same id in the same lane and
session is the same turn, stored once, and the second call answers `stored: "already stored"` with the
stored id. The same id with other words is `turn_id_reused` with `fix`. There is no lifetime. The lane carries only the HMAC-SHA256 digest of the key, never its raw text;
the small per-lane lookup is rebuilt from all lane days when missing, torn or stale.
New keyed turns compare a keyed digest of the original input words. Different input words are refused even
when masking makes their stored text equal. Legacy turns without that digest retain their stored-text comparison.

Only one thing joins two writes without an id: the same words, by the same role, in the same lane and
session, through **two different sources**, within 120 seconds of each other's own time. That is one
turn that came in two ways, and its line keeps each arrival in `arrivals`, with its source label,
door, adapter, group, place and time. Ordered source labels are derived from those arrivals
and the search window includes every local day on both sides of the given time. Its sources remain in arrival order
(`"sources": ["group:studio-counter", "door:cli"]`); the second call answers `stored: "joined"`. Through
one source, the same words again are another turn.

### What a write answers

The lane commit fixes `stored` and the turn id; ids, caller-key and source records, the tree, the index and connection bookkeeping are derived, with `pending` and a reason when unfinished.

If a crash loses the answer, the caller cannot know whether the turn was stored; repeating the call with its key is safe.

Once the lane's line is stored, the call says so (`wrote`, and `stored`: `written`, `already stored` or
`joined`), whatever comes after. The agent's tree and the index follow, and the answer names each:
`tree` is `made`, `already there`, `pending` or `none` (the settings give this agent no tree), and
`index` is `updated` or `pending`, with `reason`. Neither turns the answer into a refusal. The first write
of an agent makes its tree in one step that accepts another process having made it; it waits up to 2
seconds for the writer lock, and a lock held longer leaves the tree `pending`: the next write of that
agent, or the level pass, makes it without anyone running a command. A pending index brings the lane in
on the next read. A call on a group connection that admitted its caller and was then refused says so in
`details.admitted`, since that write took effect.

### Three questions for a host of this kind

- **Who is calling.** The group, and the name on the call.
- **How the forest is reached from a computer that is not Juno's.** `forest mcp --http` on Juno's
  machine, behind an HTTPS front inside Juno's own private network, the token sent as a header. It is
  not put on the public internet. Where the host can only run a command on Juno's machine, the same
  doors are `forest search`, `forest read`, `forest timeline`, `forest navigate` and `forest log`, with
  `--as-group studio-counter --agent "Kiln Bot"`.
- **How turns get in.** Where a reader of the host's own records exists, that reader is the net.
  Otherwise the agent writes its own turn.

A command door gives an agent its owner lets run commands the same scope and the same folded answers as
the tools: every flag of those commands is either plain or refused, and `--named` is refused. It is not a
wall: an agent that can run commands can read files.

## An agent you wrote yourself

An agent Juno wrote, under a name Juno chose, uses the same doors.

- Its own write: `forest log --as kiln-bot`, with the turn on stdin, never as an argument, or
  `memory_log` on `forest mcp --as kiln-bot`. The door sets the role and the origin. The same masking
  applies, and the same rules of a turn: a call is a turn, and `--turn-id` makes a repeat one turn. The
  words are at most 131,072 bytes through either door. This depends on the agent calling it, so where a
  reader of the host's records exists, the reader is the net and this write is the addition.
- An export folder the `jsonl` adapter reads.
- An adapter module for that agent's own records, outside the core.

A turn written with `forest log` or `memory_log` makes that agent's tree, or leaves it pending as above.
A lane that arrives by intake is made by `forest levels apply`.
