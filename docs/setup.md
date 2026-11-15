# Connecting an agent: setup and doctor

The package gives an agent the forest, its doors and the way to connect; the agent connects itself. The
package holds no code that knows a particular agent program and writes nothing into another program's
settings. An agent knows where its own conversation is kept and how its own tool registers an MCP server or
a hook. It does those things itself, and `forest doctor` proves the result.

The agent's instructions travel with the package: `forest guide` prints the setup guide, `forest guide
routine` how the forest grows when that agent keeps it, and `forest guide use` when and how to look things
up. Nothing in them sends an agent to an address on the web.

## What setup asks

`forest setup` is run by the agent for its owner. Each call answers one object with `next`: `ask_owner`
(the questions still unanswered, each with its `choices` and `why_owner`; nothing is written), `done`
(`changed`, each file and block written, and `todo`) or a refusal from [the table](refusals.md). It exits
1 for `ask_owner`, 0 for `done` and 2 for a refusal. Every answer is a flag, and setup never fills one in
by itself:

| Flag | The owner decides |
| --- | --- |
| `--root DIR` | where the forest lives: a new or empty folder (setup creates the forest as `forest init` does) or an existing forest to join |
| `--as NAME` | this agent's name, in any script; the id is made from it by the name contract |
| `--main yes\|no` | whether this agent is the owner's main agent, which reads the whole forest |
| `--conversations all\|asked\|none` | which of this agent's conversations are brought in |
| `--earlier yes\|no` | whether conversations from before today are brought in (not asked for `none`) |
| `--keeper this\|other\|nobody` | who keeps the forest growing: this agent, another agent, or nobody yet |
| `--when`, `--daily-model`, `--filing-model` | when the keeper runs the routine and which of the owner's models it may spend (asked for `--keeper this`; stored as given) |

The time zone of a new forest is shown as a proposal from the system and changed with `--timezone`; it is
never asked. A forest has one keeper: a second agent that answers `--keeper this` is asked again, with the
present keeper named.

What `done` writes stays inside the forest's folder: the forest when it is new; the agent's
`[agents.known.<id>]` table with its name, and its id added to `agents.orchestrators` for the main agent
(taken out again when the answer turns to `no`), each patched into `forest.toml` keeping every other byte; the agent's empty record file
`transcripts/<id>.jsonl` (not for `--conversations none`); and the setup record `.forest/setup/<id>.json` with the answers and when they
were given. `--dry-run` prints the same list and writes nothing. A second run with the same answers
changes nothing; a changed answer says what changed.

`todo` is what the agent then does in its own tool, in order, with paths as the caller gave them:
`register_mcp` (the `forest mcp` command line), `bring_turns` (the record's shape, the file the agent
appends to, and the intake command), `own_turn` (`memory_log` or `forest log`), both left out for
`--conversations none`, `routine` for the keeper
(`forest guide routine`, when, which model for which pass, and that the keeper schedules it itself), and
`verify` (`forest doctor`).

Each agent appends to its own file, named by its id, in one folder, and brings it in with
`forest intake --from jsonl --as <id> --transcript <forest>/transcripts/<id>.jsonl --once --root <forest>`.
Intake keeps a cursor per file name, so two agents' files never share one; the first run reads a file from
its first line and a line appended later is read once. A forest whose settings set `path` under
`[intake.tools.jsonl]` is refused with `setup_jsonl_path_set` (`report`): a sweep of that folder would give
every file in it to one agent.

## What doctor proves

`forest doctor --as NAME` prints `result` (`passed`, `todo` or `problem`) and `checks`, each with its
`state` (`ok`, `todo`, `problem` or `skipped`), its `evidence`, and, when it is not `ok`, its next move and
command. It exits 0 only for `passed`, 1 for `todo` or `problem` and 2 for a refusal, and writes nothing
but its own phrase file.

| Check | `ok` when |
| --- | --- |
| `forest` | the forest opens, no unfinished commit's journal stands, and the structure checks find nothing |
| `setup` | the agent has a setup record |
| `agent` | the name is an agent of the forest; its lane and its tree are shown (a pending tree is `ok`) |
| `turns` | a phrase the doctor made came into the agent's lane its real way (skipped for `--conversations none`) |
| `finding` | the index exists and is current, a search as the agent answers, and the agent was seen on a connection: a call of an MCP tool from its own tool, which `.forest/mcp/seen.json` records (a search run as a command does not count) |
| `routine` | for the keeper: every day of turns before today has its daily page; the lint's counts are shown |

The proof of the turns is a turn that came the agent's real way. `forest doctor --as NAME --phrase` writes
and prints a short new phrase; the agent says it in its conversation and brings that turn in; the next
`forest doctor` finds it in the lane and names the turn's time and source.

Every flag, each `todo` step and each check, with an example run, are in [tools](tools.md#forest-setup).
