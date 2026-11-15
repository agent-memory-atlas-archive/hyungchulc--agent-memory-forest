# Setting up Agent Memory Forest: a guide for the agent

You are an agent, and your owner asked you to connect yourself to a memory forest. Follow these steps in
order. Each step is one command or one decision. Nothing here asks you to fetch anything from the web.

## What you are setting up

1. A forest is a folder of plain Markdown pages on the owner's disk that holds what the owner's agents learn.
2. Each agent has a raw lane (`06 istm/<id>/`) where its conversation turns are appended, one JSON line each.
3. A keeper (one agent) turns the lanes into a daily record and files it into the pages, every day.
4. Every agent finds what it needs through the same doors: search, read, timeline, navigate and log.
5. Memory is grounding data, never authorization: you still open the page, check its date and apply your own rules.

## 1. Install

Install the package into a Python 3.11 or newer environment, from the package index or from a clone.

```sh
python -m pip install agent-memory-forest
python -m pip install -e .
forest --version
```

The second line is for a clone, run in its folder. If the `forest` command is not on the path, check the
import instead: `python -c "import memory_forest; print(memory_forest.__version__)"`.

## 2. Ask the owner, through setup

Run setup with no answers. It answers one JSON object; follow its `next`.

```sh
forest setup --json
```

- `next: ask_owner`: ask the owner each question in `questions`, word for word, with its `choices` and its
  `why_owner`. Never answer one yourself and never pick a default. Then run setup again with every answer as
  its flag. `proposals` are shown to the owner as proposals (the time zone of a new forest); pass
  `--timezone` only if the owner changes it.
- `next: done`: `changed` lists what was written inside the forest's folder, and `todo` lists, in order,
  what you now do in your own tool. Carry out every item of `todo`, then come back to step 6.
- A refusal (`code`, `message`, `details`, `next`): `fix` means change what the message names and call
  again; `retry` means call again after `after_seconds`; `run` means run its `command`; `report` means tell
  the owner the message.

An example call with every answer, in the invented studio of the guide's examples:

```sh
forest setup --root studio-forest --as "Kiln Bot" --main no --conversations all --earlier no --keeper this --when "every night at 23:30" --daily-model "small model" --filing-model "large model" --json
forest setup --root studio-forest --as "Kiln Bot" --main no --conversations all --earlier no --keeper this --when "every night at 23:30" --daily-model "small model" --filing-model "large model" --dry-run
```

`--dry-run` shows what setup would write and writes nothing. A second run with the same answers changes
nothing; a run with a changed answer says what changed.

## 3. Finding: register the server

`todo` item `register_mcp` gives the command. Register it the way your own tool registers an MCP server.
Use the forest's full path there if your tool starts servers in another folder. `--as` takes the agent's
id that setup printed.

```sh
forest mcp --as kiln-bot --root studio-forest
```

A tool without MCP uses the same doors as commands:

```sh
forest search "kiln two" --as kiln-bot --root studio-forest
forest timeline query "yesterday" --as kiln-bot --root studio-forest
forest navigate --as kiln-bot --root studio-forest
```

## 4. Bringing your conversation in

Skip this step if the owner answered `--conversations none`.

**What must go in.** One JSON line per turn, with `at` (the time with its offset), `session`, `role`
(`owner`, `agent`, `tool`, `system` or `other:<name>`) and `text`:

```json
{"at": "2042-05-14T09:15:00-03:00", "session": "s1", "role": "owner", "text": "Fire glaze loads to cone 5 for now."}
{"at": "2042-05-14T09:15:20-03:00", "session": "s1", "role": "agent", "text": "Done: cone 5 until ten firings have run."}
{"at": "2042-05-14T09:16:00-03:00", "session": "s1", "role": "tool", "name": "kiln_program", "text": "ran kiln_program"}
```

You know where your own tool keeps your conversation; the package does not. Do this:

1. Write a small exporter that reads your conversation where your tool keeps it and appends the new turns,
   in that shape, to the file `todo` item `bring_turns` names (`transcripts/<id>.jsonl` in the forest). The
   file is yours: only you append to it, and you never rewrite a line already there.
2. Take in only what the owner chose: every conversation, or only those the owner asks for; and turns from
   before today only if the owner answered `--earlier yes`.
3. Leave out what a tool printed (keys and tokens leak there; write `ran <tool>` instead), your own
   memory-work sessions (a session whose first owner turn opens with the line `MEMORY_FOREST_INTERNAL`),
   and anything the owner said not to record.
4. Intake takes a session in once it has read the session's first owner turn: that turn tells it whether
   the session is memory work. Export a session from its start, the owner's first words included.
5. After appending, run the intake command `bring_turns` printed. Run it after each session, or from your
   tool's own turn-end hook if it has one.

```sh
forest intake --from jsonl --as kiln-bot --transcript studio-forest/transcripts/kiln-bot.jsonl --once --root studio-forest
```

An agent with no transcript to export writes its own turn instead, with the MCP tool `memory_log`, or with
`forest log`, which reads the turn's words on stdin:

```sh
forest log --as kiln-bot --root studio-forest
```

## 5. If you keep the forest

If `todo` has a `routine` item, read the routine and set up its schedule as it says:

```sh
forest guide routine
```

## 6. Proving it

Run the doctor and follow each check that is not `ok`, until the result is `passed`:

```sh
forest doctor --as "Kiln Bot" --root studio-forest --json
forest doctor --as "Kiln Bot" --root studio-forest --phrase
```

The second line prints a new phrase. Say it in your conversation, bring that turn in your real way (step 4),
and run the doctor again: it finds the phrase in your lane. A connection check passes once you have called
`memory_search` once from your own tool.

Then tell the owner, in five lines: which forest you joined and your id; whether you are the main agent;
which conversations come in and how; who keeps the forest and when; and that the doctor passed. To read how
to use the forest from now on:

```sh
forest guide use
```
