# Concepts

This page explains the forest for someone meeting it for the first time, in the order you need the ideas.
The rules themselves are in [the contract](contract.md); the software is in [the design](v1-design.md).
The examples follow one invented owner, Juno, who runs a small pottery studio, and the agents that work for
her. The same world is written out as files in [`examples/synthetic-forest`](../examples/synthetic-forest/README.md).

## The layers

A forest is a folder of plain Markdown pages in numbered layers. Two lanes feed it and four layers hold it.

```text
06 istm    the raw record: append-only, one lane per agent           (lane)
05 daily   one readable page per day, written from the lanes          (lane)
04 stm     leaves: one sub-topic of one subject, in full detail       (forest)
03 mtm     a tree's index, and one board per branch                   (forest)
02 ltm     one page per tree                                          (forest)
01 xltm    the root index: identity, the trees, the agents            (forest)
```

Things arrive at the bottom and are carried upward: a message lands in `06 istm`, the day's page in
`05 daily` records it readably, and a filing run puts each fact in the leaf where it belongs. Placement
works the other way, from the top: the root index says which tree a kind of subject belongs to, the tree's
index says which branch, the branch holds the leaf.

The lanes are not forest. They are chronology and provenance: you go there to see exactly what was said
and when. The forest is where you go to find out what is known.

A **tree** is an area the owner works or lives in. Juno's forest has three shared trees: `studio` (the
pottery business), `people` (the people she knows for themselves) and `admin` (her own accounts, money and
shopping). Each agent also has a tree of its own (below, and in [the agents guide](agents.md)).

## Subjects

The unit of the forest is the **subject**: a person, a group, a project, a device, a shop, a place, an
event, an agent, and so on. Every fact in a leaf is about one subject, and every subject has one line in
the **registry**, the `## Subjects` section of its home tree's index:

```text
- `kiln-two` — Kiln Two · device · branch · `03 mtm/studio/kiln-two.md` · aliases: K2; the big kiln
```

The line says what the subject is called, what type it is, how big it has grown (its **level**), where it
lives (its **home**), and every other name it goes by. `forest registry build` compiles all the lines into
one JSON file, so that a question that says "the big kiln" finds Kiln Two.

Where a fact arrived from (a chat, a mail, a call, the agent that heard it) never decides where it lives.
A chat with a friend belongs to the friend.

## A subject's life

A subject's place in the forest follows one measure: its **distinct days**, the number of different local
dates on which its own material records something. The code counts them from the `## Timeline` lines of
the subject's leaves, and moves the subject when a threshold is crossed. Follow one invented subject, the
"Tidepool" glaze line Juno starts developing.

**1. An entry.** On its first day Tidepool is mentioned once: Juno mixes a test batch of a sea-green glaze
and calls it Tidepool. A subject seen on one day does not get a page of its own. It becomes an **entry** on
its category's collection page, `04 stm/studio/projects/seen-once-2042-q2.md`: a section headed
`## Tidepool`, a line ``- entry: `tidepool-glazes` · project · aliases: Tidepool``, what was said, and a
line in the page's Timeline. Most names that pass through a life stay here, and that is the point: a
hundred one-day mentions cost one page, not a hundred.

**2. A leaf.** A week later Juno fires a test tile in Tidepool. That is the subject's second distinct day,
and `forest levels apply` **admits** it: its section becomes the leaf
`04 stm/studio/projects/tidepool-glazes.md`, it gets its own registry line, and the collection page keeps
one reference line saying where it went. If Juno had said "Tidepool is going to be our main line this
year", a `standing` line recording her words would have made it a leaf at once.

**3. A branch.** Over the next weeks Tidepool appears on five distinct days: recipes, test firings, a
customer's reaction. At five days it is **promoted** to its own branch: a board
`03 mtm/studio/tidepool-glazes.md` and a folder `04 stm/studio/tidepool-glazes/` with one leaf per
sub-topic, `recipes.md`, `test-firings.md`, `feedback.md`. The board says what Tidepool is, its current
state, and lists its leaves.

**4. A tree.** Months later Tidepool has become a product line with its own recipes, firings and orders,
each of those sub-topics spanning many days and holding several leaves. When three sub-topics each span
five days and hold three leaves, Tidepool becomes a **tree** of its own, `02 ltm/tidepool-glazes_LTM.md`,
and its sub-topics become its branches. Only some types can become trees (a project can; a person never
does: a friend stays a branch of `people`).

No model decides any of this. The thresholds are settings, the counting is code, every move goes through
the move tool and is logged, and a level never goes down because a subject went quiet.

## One home, and reference lines

Each fact lives in exactly one leaf: the leaf of its subject and sub-topic. When the fact changes, only that
leaf changes. Every other page that touches the fact carries a **reference line** instead of a copy:

```text
- 2042-05-08 — two replacement elements ordered: recorded in `04 stm/admin/shops/cinderwell-supply.md`.
```

That line sits in the kiln's repair record; the order itself lives with the shop, because a shop and what
was bought there are kept together. Copies drift apart; a reference line cannot.

When a leaf holds something about another subject, its frontmatter says so: `about: kiln-two`. The board of
the subject it names lists it under `## Pointers`, generated by code, so the kiln's board shows that the
shop's leaf holds something about the kiln without copying it. `about:` never names the owner (every leaf
is the owner's) and never names a channel that only carried the message.

## What goes up

Leaves hold everything in full detail. The pages above them hold only what the leaves **establish**, and
newer is not the same as more certain.

- A board's `## Current state` gathers what its leaves hold, by kind, each line with the date of its
  evidence and its source:
  ``- State: back in service after the May element fault (as of 2042-05-09; source `04 stm/studio/kiln-two/repairs.md`)``.
- A preference or a habit is carried up only on repeated evidence (several distinct days) or on the
  owner's own statement. One evening at a concert does not make Juno someone who loves that band.
- A one-off event stays in its leaf.
- When a source leaf changes after the date a line cites, the line is stale, and the nightly upkeep run
  checks it again.

## When a fact changes: the update record and the archive

A page states what is true now. When Juno lowers Kiln Two's glaze firing from cone 6 to cone 5, the leaf's
text changes, and the old statement moves, whole, into the leaf's `## Update record`, which is always the
page's last section:

```text
- 2042-05-14 — glaze firing limit: was "Glaze loads: cone 6 (1,222 °C).", now "Glaze loads: cone 5 (1,196 °C) until the new elements have run ten firings.". Why: Juno decided it after the 2042-05-13 load came out slightly over-fired. Source: `05 daily/2042-05-14.md`.
```

The old Timeline line stays too, marked `[superseded 2042-05-14]`.

Records grow. A leaf's size cap (8 KB by default) counts its content only, not its record, so history never
forces a split. When the record makes the page too long, `forest record trim` keeps the newest lines and
every line that quotes the owner, and moves the rest, verbatim, to the page's copy in `.forest/archive/`,
leaving a pointer line. Nothing is deleted: archived pages and trimmed lines are compressed copies listed in
a manifest, and `forest archive show` prints them.

## The move log

Pages move: a leaf splits, a subject is promoted, two ids turn out to be one person and merge. Every move is
done by `forest move`, which rewrites every link, list line, registry line and code-span path that named the
old place, and appends one line per path to `.forest/moves.jsonl`. `forest move resolve <old path>` follows
the log to wherever the page is now, so an old note, an evaluation set or an archived page that names a path
from last year still finds its page.

## The lint

The rules that have a shape are checked by code. `forest lint` reports every hit with a code and a path, in
three groups:

- **violations**: a leaf outside its subject's home, an unknown entity, a dated or numbered file name, a
  registry line out of shape, a path that leads nowhere, an account number written in full;
- **upkeep**: rules the forest still breaks on many pages (leaves over the cap, leaves without `kind:` or
  `about:`, lines copied between leaves), counted with examples for the nightly run to work down;
- **due work**: a subject whose level is due, a category grown too large.

A run that writes pages takes a baseline when it starts. It must clean up whatever its own pages added, and
it is never blocked by what was already there. So a forest can adopt a stricter rule without being frozen
until every old page complies.

## Where to go next

- [The contract](contract.md): every rule, stated once.
- [Agents](agents.md): how several agents share one forest.
- [The design](v1-design.md): the package, its settings and its milestones.
- [Comparison](comparison.md): how this differs from other open memory systems.
