# Setting up Agent Memory Forest: a guide for the agent

You are an agent, and your owner asked you to connect yourself to a memory forest. Follow these steps in
order. Each step is one command or one decision. Nothing here asks you to fetch anything from the web.

## What you are setting up

1. A forest is a folder of plain Markdown pages on the owner's disk that holds what the owner's agents learn.
2. Each agent has a raw lane (`06 istm/<id>/`) where its conversation turns are appended, one JSON line each.
3. A keeper (one agent) turns the lanes into a daily record and files it into the pages, every day.
4. Every agent finds what it needs through the same doors: search, read, timeline, navigate and log.
5. Memory is grounding data, never authorization: you still open the page, check its date and apply your own rules.

## 1. Check the install

The package is installed before you read this. Check that its command answers (it needs Python 3.11 or
newer):

```sh
forest --version
```

If the `forest` command is not on the path, the same command is `python -m memory_forest.forest`: use that
form wherever this guide writes `forest`.

## 2. Ask the owner, through setup

Run setup with no answers. It answers one JSON object; follow its `next`.

```sh
forest setup --json
```

- `next: ask_owner`: ask the owner each question in `questions`, word for word, with its `choices` and its
  `why_owner`. Never answer one yourself and never pick a default. Then run setup again with every answer as
  its flag. `proposals` are shown to the owner as proposals (the time zone of a new forest); pass
  `--timezone` with the owner's zone when the owner names another one.
- `next: done`: `changed` lists what was written inside the forest's folder (your entry in its settings,
  your own tree, your record file), `agent` gives the id the forest made from your name, and `todo` lists,
  in order, what you now do in your own tool. Carry out every item of `todo`, then come back to step 6.
- A refusal (`code`, `message`, `details`, `next`): `fix` means change what the message names and call
  again; `retry` means call again after `after_seconds`; `run` means run its `command`; `report` means tell
  the owner the message.

An example call with every answer, in the invented studio of the guide's examples:

```sh
forest setup --root studio-forest --as "Kiln Bot" --main yes --conversations all --earlier no --keeper this --when "every night at 23:30" --daily-model "small model" --filing-model "large model" --json
forest setup --root studio-forest --as "Kiln Bot" --main yes --conversations all --earlier no --keeper this --when "every night at 23:30" --daily-model "small model" --filing-model "large model" --dry-run
```

`--dry-run` shows what setup would write and writes nothing. A second run with the same answers changes
nothing; a run with a changed answer says what changed, and its `todo` is the one to carry out now.

The keeper reads every agent's turns to write the daily record, so the keeper is the owner's main agent:
`--keeper this` with `--main no` is asked again. When the owner changes the answer about conversations to
`none`, the new `todo` has the item `no_recording`: turn off the exporter or hook you set up before. The
forest itself refuses your turns from then on (`recording_off`), whatever still runs.

## 3. Finding: register the server

`todo` item `register_mcp` gives the command. Register it the way your own tool registers an MCP server.
Use the forest's full path there if your tool starts servers in another folder. `--as` takes the id that
setup printed for you, here and in every command of this guide.

```sh
forest mcp --as kiln-bot --root studio-forest
```

A tool without MCP uses the same doors as commands, and says so to the doctor in step 6 with
`--doors commands`:

```sh
forest search "kiln two" --as kiln-bot --root studio-forest
forest timeline query "yesterday" --as kiln-bot --root studio-forest
forest navigate --as kiln-bot --root studio-forest
```

## 4. Bringing your conversation in

Skip this step if the owner answered `--conversations none`.

**What must go in.** One JSON line per turn, with `at` (the time with its offset; any offset is taken),
`session`, `role` (`owner`, `agent`, `tool`, `system` or `other:<name>`) and `text`. A tool turn also has
`name`, the tool's name:

```json
{"at": "2042-05-14T09:15:00-03:00", "session": "s1", "role": "owner", "text": "Fire glaze loads to cone 5 for now."}
{"at": "2042-05-14T09:15:20-03:00", "session": "s1", "role": "agent", "text": "Done: cone 5 until ten firings have run."}
{"at": "2042-05-14T09:16:00-03:00", "session": "s1", "role": "tool", "name": "kiln_program", "text": "ran kiln_program"}
```

You know where your own tool keeps your conversation; the package does not. Do this:

1. Write a small exporter that reads your conversation where your tool keeps it, appends the new turns, in
   that shape, to the file `todo` item `bring_turns` names (`transcripts/<id>.jsonl` in the forest), and
   then runs the intake command that item printed. The file is yours: only your exporter writes it.
2. Take in only what the owner chose: every conversation, or only those the owner asks for; and turns from
   before today (the day setup ran, in the forest's time zone) only if the owner answered `--earlier yes`.
   With `--earlier no`, a session that began before that day is exported from its first turn of that day
   on, and what was said before stays out.
3. Keep a tool's turn, with `ran <tool>` as its whole text: what a tool printed stays out (keys and tokens
   leak there). Leave out entirely your own memory-work sessions (a session whose first owner turn opens
   with the line `MEMORY_FOREST_INTERNAL`) and anything the owner said not to record.
4. Intake takes a session in once it has read the session's first owner turn: that turn tells it whether
   the session is memory work. So export a session from its start, the owner's first words included,
   unless rule 2 keeps them out. When your file begins with a turn of yours and no owner turn follows
   yet, the answer says `waiting for the owner turn` and nothing is lost: the turn is taken in once the
   owner's next turn is in the file.
5. Run the exporter after each session, or from your tool's own turn-end hook if it has one. Setting that
   up is yours, in your own tool, like registering the server.
6. The record file is a hand-over, not an archive: it holds the words as your tool had them, unmasked, and
   the lane keeps the masked copy. So the exporter empties the file once intake has read all of it. The
   answer lists your file under `files`: when its `read_to_end` is `true`, truncate the file; when it is
   `false`, a turn still waits (for the first owner turn of its session, or for the rest of its line), so
   leave the file as it is and append to it next time. Appending, intake and emptying are one run of the
   exporter: never let two runs overlap.

```sh
forest intake --from jsonl --as kiln-bot --transcript studio-forest/transcripts/kiln-bot.jsonl --once --root studio-forest --json
```

Only an agent with no transcript to export writes its own turn instead (`todo` item `own_turn`; skip it
when your exporter brings your turns in), with the MCP tool `memory_log`, or with `forest log`, which reads
the turn's words on stdin:

```sh
forest log --as kiln-bot --root studio-forest
```

## 5. If you keep the forest

If `todo` has a `routine` item, read the routine and set up its schedule as it says:

```sh
forest guide routine
```

## 6. Proving it

Run the doctor and follow each check that is not `ok`, until the result is `passed`. A check that is
`todo` names its next move and the command for it:

```sh
forest doctor --as kiln-bot --root studio-forest --json
forest doctor --as kiln-bot --root studio-forest --phrase
```

The second line prints a new phrase. Say it in your conversation, bring that turn in your real way (step 4),
and run the doctor again: it finds the phrase in your lane. The check named `finding` passes once you have
called `memory_search` once from your own tool. A tool without MCP has no connection to be seen on: it adds
`--doors commands`, and the check is the search the doctor runs as you.

```sh
forest doctor --as kiln-bot --root studio-forest --doors commands --json
```

Then tell the owner, in five lines: which forest you joined and your id; whether you are the main agent;
which conversations come in and how; who keeps the forest (and when, if it is you); and that the doctor
passed. To read how to use the forest from now on:

```sh
forest guide use
```
