# Writing a reader

This page is for someone who knows one agent program well and wants its conversations brought into a
forest's raw lane without exporting records by hand. The package ships one reader, `jsonl`. It ships no
reader of a particular program's own transcripts: it cannot keep up with every program's format. Whoever
knows a program writes its reader as a small module of their own and plugs it in. The record a reader
feeds and the writer's rules are in the [contract](contract.md), section 1; the command that runs it is
[`forest intake`](tools.md#forest-intake).

## A reader, or records for `jsonl`

`forest intake` reads a transcript as a file that grows at its end: a cursor is a byte offset into that
file, and the next read starts there. A reader is worth writing when all of these hold:

- the program keeps each conversation on the owner's machine as such a file, one line per entry, which it
  only appends to;
- you want every conversation taken in as it happens, without anyone exporting it, and the program has
  hooks (the end of a turn, the end of a session) that can run a command;
- you know the format well enough to tell the owner's own words from text the program inserted, and you
  will keep the reader up when the format changes.

Records for the `jsonl` reader are enough, and simpler, when the program can export or write its
conversations itself, when a small script you run converts them now and then, or when the program keeps
them somewhere that is not a growing file (a database, a hosted service). Write one JSON object per line
into a folder, with `at` (an instant with its offset), `session`, `role` and `text`, and optionally `id`,
`origin`, `name` and `arguments` for a tool turn, and `parts`; the first object of a file may state the
session's `cwd`. Set `path` under `[intake.tools.jsonl]` to that folder. An agent that writes its own turns
needs neither: it uses `forest log` or the MCP tool `memory_log` ([tools](tools.md#forest-log)).

## The interface

A reader is one object. `forest intake` uses the members below; *required* means intake calls it without
a fallback, *optional* means intake looks for it and has a default. The types are in
[`memory_forest.adapters.common`](../src/memory_forest/adapters/common.py): `Transcript` (one file and its
session), `Turn` (one turn before the writer masks it and gives it an id), `Part` (a marked span of a
turn's text) and `ReadBatch` (what one read returns).

| Member | Mark | What intake does with it |
| --- | --- | --- |
| `name` | required | The adapter's name: `--from <name>`, the table `[intake.tools.<name>]`, the folder of its cursors, `adapter:<name>` in a turn's `source`. It is the same as the entry point's name. |
| `discover(settings)` | required | A sweep: every transcript the program keeps, as `Transcript`s. Intake keeps at most 100,000 of them. |
| `read(transcript, offset, carry=None, max_bytes=None)` | required | The turns after `offset` as a `ReadBatch`. Intake always passes both keywords. |
| `open_path(path, settings)` | required | `forest intake --transcript <path>`: the one transcript a path names, confined to the program's folder. |
| `hook(payload, settings)` | required | `forest intake --hook`: the transcript the hook's JSON object names, without reading its body. |
| `describe(transcript, settings)` | optional | The transcript with what its own head states (session, `cwd`, origin, version, parent). Intake calls it when it first reads a transcript, and again after a file was rewritten. Without it, the `Transcript` that `discover`, `open_path` or `hook` returned stands. |
| `transcript_root(settings)` | optional | The folder the transcripts' keys are relative to, so `forest intake status` can tell a file that is ahead of its cursor. Without it, `path` from the settings. |
| `id_stands_alone(turn_id)` | optional | Whether this one id is unique without its session. Asked per id, before `ids_stand_alone`. |
| `ids_stand_alone` | optional | Whether every id of this program is unique without its session. Default false: the stored id keeps the session. |

`ReadBatch` carries `turns`, `offset` (where the next read starts), `unknown` (lines not understood),
`internal`, `dropped` (`(type, count)` for lines that were not turns) and `faults` (`(class, place)` for a
line that could not be taken at all). Two of its parts and one error are optional:

- `ReadBatch.carry` (optional): a small JSON-safe dict for something the read began and could not finish,
  such as a tool call whose output is on a line not written yet. Intake keeps it on the cursor and hands it
  back as `carry=` on the next read of that transcript; it is dropped when the file is read again from its
  start.
- `max_bytes` (a keyword `read` takes): a hook's read is bounded to 256 KiB. Read no further than that; a
  line longer than the bound gives no turn and no progress, and the sweep, which reads unbounded, takes it.
  `complete_lines(path, offset, max_bytes=max_bytes)` does this and leaves a last line with no newline for
  the next read.
- `transcript_deferred` (optional, a code `hook` may raise): when the hook cannot find its transcript
  cheaply, raise `MemoryForestError("transcript_deferred", …)`. Intake counts it, writes nothing, and the
  next sweep takes the session.

What `discover`, `open_path` or `hook` raises stops the command; a `MemoryForestError` is its refusal.
What `describe` or `read` raises ends only that transcript's read: intake logs it by its code or class and
reads the other transcripts. A member that ends the process (`SystemExit`) is reported as `plugin_failed`.
Inside `read`, catch one line's own failure and report it in `faults`, so the rest of the file is read.

## Registering a reader

A reader is an object behind the entry point group `memory_forest.adapters`. In the reader's own package:

```toml
[project.entry-points."memory_forest.adapters"]
kiln-log = "kiln_log_reader:adapter"
```

where `adapter` is the module's instance of the reader class. Once that package is installed beside this
one, `--from kiln-log` finds it. A reader that fails to load is off, and `forest intake status` names it
with its error class; the others still load. A plugin that claims a built-in name (`jsonl`) is off, and
the built-in stays. Intake for the reader stays off until the owner sets `enabled = true` under
`[intake.tools.kiln-log]`; the first sweep turns it on and places its cursors (contract, section 1).

## What a hook call hands the reader

The program's hook runs `forest intake --from kiln-log --hook` and writes one JSON object, the program's
own hook payload, on stdin. Intake reads at most 8 MiB of it and waits at most 2 seconds, then calls
`hook(payload, settings)` with that object as a dict and the reader's `[intake.tools.<name>]` settings
(`path`, `include`, `exclude`, `agent`, `import_earlier`). `hook` returns the `Transcript` the payload
names. Take the path from the payload and confine it to the program's folder (`confine_file`), take the
session and the working folder where the payload states them. Intake calls `describe` when it first reads
the file, so `hook` need not open it. A hook that runs with `MEMORY_FOREST_INTERNAL` in its environment
marks the transcript internal; the reader does not need to look. Intake then reads that transcript only,
from its cursor, at most 256 KiB. In hook mode the process exits 0 and prints nothing, whatever happens.

## Rules a reader keeps

- **Ids.** `Turn.turn_id` is the program's own id for that turn, the same each time the line is read, or
  `None` where the program gives none (intake then makes one from the turn's content and time). Never make
  an id from a line's position. Say truthfully whether an id is unique without its session
  (`ids_stand_alone`, or `id_stands_alone` when it depends on the id); when in doubt, it is not.
- **Roles and origin.** A role is `owner`, `agent`, `tool`, `system` or `other:<name>`. A turn's origin
  is `interactive`, `headless`, `subagent` or `unknown`, and only a value you know to be the owner's own
  client at the keyboard is `interactive`. A value you do not recognise is `unknown`, with the program's
  own word kept in `Transcript.origin_raw`. A session another program started is `headless`. A sub-agent's
  work is a `tool` turn of the agent that started it, one id per message; a parent session id alone does
  not make a session a sub-agent's (contract, section 1).
- **Tool output left out.** Give a tool call as a `tool` turn with its `name`, its output as `text` and,
  where the program records them, its `arguments`. Never fold tool output into an agent's or the owner's
  turn: intake keeps only that the tool ran unless the owner set `intake.tool_output = "keep"`, and tool
  output is where keys and tokens leak.
- **Spans the client inserted.** Keep a turn's text whole. Where the program marks text it inserted into a
  user turn, give it as a `system` turn or name it as a `harness` span in `Turn.parts`; spans the owner
  pasted, attached or quoted are `paste`, `file` and `quote`, by their offsets in `text`
  (`find_marks` reads the common tags). Text the program inserted without a mark cannot be told from the
  owner; say so where you document the reader.
- **The marker of a memory-work session.** A session opened to carry out a forest stage starts with the
  line `MEMORY_FOREST_INTERNAL` in its first owner turn. Hand that turn's text over unchanged, keep
  `Transcript.session` the program's own session id (the id a stage may have recorded), and when
  `transcript.internal` is set, return `ReadBatch((), offset, unknown, True, ...)`: no turns of that
  session reach the lane.
- **Paths, bounds and masking.** Read only inside the program's folder and refuse a symlink
  (`confine_file`, `require_file`); make `Transcript.key` relative to that folder (`relative_key`), never an
  absolute home path. Open no network connection. Skip and count a line you do not understand rather than
  raise. Do not mask: intake masks numbers and sign-in values at the door, in every string of the line.

## A worked example: the kiln-log reader

[`tests/plugin_adapter.py`](../tests/plugin_adapter.py) is a complete reader for an invented format, the
kiln log of a studio's kiln controller, written the way a third party would write one and plugged in by
the tests through the entry point group (`plugged()` registers it without installing anything). In
under 170 lines it shows each member above:

- `discover` lists the `*.kiln` files in `path`, skipping symlinks; `open_path` confines a named file to
  that folder;
- `describe` and `_open` read the head line (`{"head": {...}}`) for the session, the working folder, the
  version and the parent session, and map the program's raw origin (`desk` is `interactive`, `batch` is
  `headless`, anything else `unknown`, kept raw);
- `id_stands_alone` decides per id: a short `call_` id repeats across sessions, any other id is unique by
  itself;
- `read` uses `complete_lines` with `max_bytes`, keeps a tool call marked `"pending": true` in `carry`
  until its `output_for` line arrives, counts lines it does not understand, and returns no turns for an
  internal transcript;
- `hook` takes the path from its own payload key (`kiln_log`), marks a helper's session `subagent`, and
  raises `transcript_deferred` when the payload asks it to leave the session for a sweep.

The tests that drive it, in `tests/test_v1_intake*.py`, are the reader's proof: write tests like them for
yours, on invented transcripts.
