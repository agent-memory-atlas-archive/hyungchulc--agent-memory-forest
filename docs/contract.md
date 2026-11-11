# The forest contract (version 1)

This page states the rules every Agent Memory Forest keeps. Each rule is stated here once; the design
([`v1-design.md`](v1-design.md)) describes the software around the rules, and the stage prompts, the tools
and the other documents point to a section of this page instead of repeating it. The words used in the
examples (an owner called Juno who runs a pottery studio, a kiln, a supply shop, a few agents) belong to an
invented world; nothing in them describes a real person, company or product.

Two words are kept apart throughout:

- **Promotion** is a subject changing level: from a collection entry to a leaf, to a branch, to a tree
  (section 3).
- **Carrying up** is a fact, pattern or state being gathered into the page above the leaf that holds it
  (section 5).

Settings named on this page live in the forest's `forest.toml` (design, section 12). Where a rule names a
number, the setting that holds it is given with its default.

## 1. Layers

- Two **lanes** sit below the forest:
  - `06 istm` is the raw record: append-only, in the order things arrived, with where each came from. It
    is where provenance lives, not where meaning is decided. Full tool input, output and patch bodies stay
    here and are never copied upward.
  - `05 daily` is the readable record of each day: one page per local date. It keeps what the owner, the
    agents and other people wrote close to the original, and turns work, tool use and results into short
    causal episodes per request or run, keeping every failure and every change and closing routine
    repeats with exact pointers into `06 istm`. The only changes it makes to quoted text are removing
    secrets, leaving out binary data and making control characters safe.
  - The lanes are not forest. They create no derived state: no search index, no embeddings, no audit. A
    lane has its own retention, outside this contract.
- Material moves up from the bottom, `06 istm → 05 daily → 04 stm → 03 mtm → 02 ltm → 01 xltm`, and is
  placed from the top, `01 xltm → 02 ltm → 03 mtm → 04 stm`: the root index says which tree a subject
  belongs to, the tree's index says which branch, and the branch says which leaf.
- The **forest** is four layers under the forest root:
  - `01 xltm/XLTM.md`, the root index: `## Identity`, `## Trees` and `## Agents` (section 5);
  - `02 ltm/<tree>_LTM.md`, one page per tree;
  - `03 mtm/<tree>/`, the tree's index `<tree>_MTM.md` and one board per branch, `<branch>.md`;
  - `04 stm/<tree>/<branch>/`, the leaves, and in a category branch its collection pages (section 3).
- Each layer up gathers what the layer below holds. A leaf keeps the full detail as it was said. A board
  gathers its subject, its current state, its recurring patterns and what its leaves and pointers hold by
  kind, so that one page answers a question about all things of one kind. A tree page holds the tree's
  durable patterns and the core of each of its branches. What may be carried up is in section 5.
- There are no summary leaves. A board's `## Leaves` is the list of its folder.
- Only `.md` files live in the four layers, and no symlinks. Every file and folder name is a slug,
  `[a-z0-9][a-z0-9-]*`, apart from the fixed names `XLTM.md`, `<tree>_LTM.md` and `<tree>_MTM.md`.
- `.forest/` holds derived and kept state (design, section 3). Everything in it except the move log, the
  archive and the judged ledger of repeated lines (section 3, One home for each fact) can be deleted and
  rebuilt.

## 2. Subjects and the registry

- The unit of the forest is the **subject**: a person, a group, an organisation, a project, a product, an
  app, a device, a place, an event, a title (a book, a show, a film), a topic, an admin item, a component
  of the system, an agent, or a category's collection (section 3). The list of types is a setting;
  `agent` and `collection` are always in it.
- **The channel never decides the home.** A channel is how a fact arrived: a message app, mail, a call,
  the screen, a notification, a browser, or the agent that heard it.
  1. A channel is a tag on each line it touches ("by mail", "in the studio chat", "noted by kiln-bot") and
     never decides where the line lives. A chat with a friend belongs to that friend's subject.
  2. A page that gathers several people's turns by channel and period (a year of text messages, an inbox
     of headlines) is re-filed by subject: each person's turns go to that person's leaf, and each
     subject's lines to that subject's leaf.
  3. An app or channel is the subject only of a leaf about the app itself: how it works, its settings,
     the owner's account with it.
- Every subject has exactly one **registry line**, in the `## Subjects` section of its home tree's index
  `03 mtm/<tree>/<tree>_MTM.md`:

  ```text
  - `<id>` — <name> · <type> · <level> · `<home>` · aliases: <a>; <b> · <status>
  ```

  - `<id>` is a stable slug made from the subject's name, never a sentence.
  - `<type>` is one of the forest's types.
  - `<level>` is `leaf`, `branch` or `tree` (section 3). `<home>` is the leaf path, the branch board or the
    tree page.
  - `aliases` lists every other name and spelling seen: forms in other scripts, chat display names,
    handles. It is left out when there is none.
  - `<status>` is `active` or `ended`, and is left out when active.
- **Ids from names.**
  1. A person's id is the Latin-letter form of the name the person uses. For a name written only in
     another script, the forest uses one fixed romanization standard per script and keeps the name order
     the person uses (an invented example: "Θάλεια Ρόδου" → `thaleia-rodou`).
  2. The aliases keep every script and every display-name spelling seen.
  3. A contact with no name gets a descriptive id (`unnamed-kiln-repair-contact`), never a phone number.
- **The owner.** The owner of the forest is a subject like any other, with the id `owner.id`. The setting
  `owner.name` is how memory text names the owner, and `owner.aliases` are the owner's other names.
  1. In memory text, the bare `owner.name` always means the owner.
  2. Anyone else who shares that name always carries a surname or another distinguishing word: if the
     owner is "Juno", a customer also called Juno is written "Juno Ashby", never "Juno".
- `forest registry build` compiles every `## Subjects` section into `.forest/registry.json`, which search
  and the lint read. It also records each collection entry's name and spellings (they point to the entry's
  collection page), each subject's `standing` line (section 3) and the leaves that name the subject in
  `about:`. The pages are the source; the JSON is derived.
- One id names one subject, and an alias belongs to one subject. When two ids turn out to be one subject,
  the move tool merges them (section 9).
- Names and aliases are compared after NFKC normalisation, case folding and collapsing white space: two
  that are equal after that are one name. Two that differ only in punctuation or spacing are reported by
  `forest registry check` as likely the same and are never merged by a tool on that ground alone.

## 3. Where a subject and its material live

### Levels

The one measure is **distinct days**: the number of local dates (in the `forest.timezone` setting, unless a
line names its own zone) on which the subject's own material records something. Days are read from the
`## Timeline` lines of the leaves whose `entity:` is the subject, from the `date` of such a leaf when it
has no Timeline, and, for a collection entry, from the lines of its page's `## Timeline` that open with its
name. Three kinds of line do not count:

1. a reference line (section 3, One home for each fact);
2. a line about an agent's or a tool's own work, because it repeats an event that the subject's own lines
   already date: a line that opens with an agent's id or name, or with the name of a stage (`Daily`, the
   filing run, the upkeep run), followed by `notified`, `reminded`, `reported`, `alerted`, `checked`,
   `re-checked`, `ran`, `sent`, `logged`, `recorded` or `noted`, and a line that opens with a tool's name
   and `alerted` ("Calendar alerted …");
3. a superseded line or page.

The levels, with the defaults of the `[levels]` settings:

- **Collection entry.** A subject seen on fewer than `levels.leaf_days` (2) days has no leaf of its own:
  it is an entry of its category's collection (Collections, below).
- **Leaf.** From `levels.leaf_days` days, or at once when a `standing` line records it, a subject is one
  leaf, `04 stm/<tree>/<category>/<id>.md`, in a category branch of its home tree. A category is a branch
  that names a kind of subject (`acquaintances`, `shops`, `suppliers`) or the branch `shared`. When the
  leaf passes the size cap it splits into `<id>-<subtopic>.md` leaves in the same branch.
- **Branch.** From `levels.branch_days` (5) days a subject has its own branch: the board
  `03 mtm/<tree>/<id>.md` and the folder `04 stm/<tree>/<id>/`. All its leaves move there, one per
  sub-topic, as `04 stm/<tree>/<id>/<subtopic>.md`.
- **Tree.** A subject becomes its own tree when `levels.tree_subtopics` (3) of its sub-topics each span
  `levels.tree_subtopic_days` (5) days and hold `levels.tree_subtopic_leaves` (3) leaves. A sub-topic is
  the set of leaves that share one `subtopic:`. Two limits belong to the rule:
  1. Only a subject whose type is in `levels.tree_types` can become a tree (by default `project`, `topic`,
     `product`, `device`, `title` and `agent`): something that can be an area the owner works or lives in.
     A person, a group or an organisation stays a branch.
  2. A sub-topic counts only when its leaves are not all of kind `received`, `notice` or `brief`
     (section 4): a sender's stream and the system's notices are not the owner's activity. A leaf with no
     `kind:` is taken for one of those when its sub-topic names an offer, an alert, a newsletter, mail, a
     notice, a digest or run evidence.

  The new tree's sub-topics become its branches. A channel is never a sub-topic.
- An **agent** is a tree from its first day (Agent trees, below).
- **Set by code.** `forest levels apply` computes every subject's level from the pages by these measures
  and carries out each change through the move tool: an entry admitted to a leaf, a leaf subject promoted
  to a branch, a branch to a tree. Each change is logged with the subject, its old and new level and home,
  and the days and leaves that set it. No model judges a level, and a level that is due waits for no later
  run (section 10 says which run applies it).
- **A level never goes down by count.** A subject keeps the highest level the rule has given it. A subject
  that has ended keeps its level and gets the status `ended`.
- **A promoted subject whose id is already the name of a category** takes over that category's board, and
  the category's other leaves move to the tree's `shared` branch.
- A category branch with more than `limits.category_max_leaves` (100) leaves splits once more by kind of
  subject, never by date. Its collection pages count as one leaf.
- **Sub-topics, whatever the size.** A leaf is one sub-topic. Two leaves that hold one sub-topic merge,
  and a leaf that holds two sub-topics splits, whatever their size; the size cap (section 4) adds splits
  but is not the only reason for one.
- **Boards that something outside the forest names.** When a prompt, a runbook or a tool opens a board by
  its path, the board keeps that path: if its branch splits or regroups, the board stays where it is and
  its `## Pointers` names where the leaves went.

### Collections

- A category branch keeps the subjects seen on one day in its **collection**: one subject of type
  `collection`, with the id `<category>-seen-once`, level `leaf`, the name `<Category words> seen once`,
  its first page as its home, and a registry line in its tree's `## Subjects` like any other subject. When
  another tree already has a category of that name, the new collection's id is
  `<tree>-<category>-seen-once`. An id is fixed when its collection is created and does not change when
  another tree later gains the same category.
- Its pages are `04 stm/<tree>/<category>/seen-once-<period>.md`. An entry goes on the page of the quarter
  of its first day (`seen-once-2042-q2.md`), or of its month (`seen-once-2042-05.md`) when the quarter's
  page would pass the leaf cap. An entry with no dated line goes by the `updated_at` of the page it came
  from.
- **Cutting a full quarter.** A quarter page that passes the cap is cut into month pages, each entry going
  to the month of its first day; the quarter page goes to the archive and the move log sends its old
  addresses to the month pages.
- **Cutting a full month.** A month page that passes the cap is cut by its entries' first days into runs
  of consecutive days, each run filled up to the cap.
  1. The first run keeps the month page's name, so references to it stay valid. Each later run is a page
     named by its first day (`seen-once-2042-05-14.md`).
  2. A day whose entries alone pass the cap takes numbered pages after its first
     (`seen-once-2042-05-14-2.md`, `-3`, …), each holding whole entries. An entry is never divided.
  3. A new entry of a month cut this way goes on the latest page of the month whose start is on or before
     its first day, or, when that day has numbered pages, on the first of them with room, else on a new
     numbered page. A page it pushes over the cap is cut again the same way.
  4. Each entry that moves takes its Timeline and record lines with it, and the move log sends its old
     addresses (`<page>#<id>`) to its new page. `forest collections recut` does the cut.
- **Page shape.** Frontmatter: `entity: <the collection's id>`, `subtopic: seen-once`, `kind: collection`,
  `timezone` and `updated_at`. Body: the title `# <Category words> seen once (<period>)`, or on a page of a
  month cut by days `# <Category words> seen once (<first day> to <last day>)` with the span of first days
  it holds, a one-line description, then one section per entry:
  1. the heading `## <Name>`, or `## <Name> (<the name in another script>)` when one is known;
  2. the line ``- entry: `<id>` · <type> · aliases: <a>; <b>``, with the id and type a registry line would
     give the subject;
  3. `- standing: …` when one applies (below), and `- kind: <kind>` when the entry's material is all of one
     kind (section 4);
  4. the entry's material as a leaf would hold it, its own headings one level down.

  After the entries come `## Timeline`, with every entry's dated lines in date order, each naming its entry
  first (`- 2042-05-14 11:20 America/Halifax — Wren Castellane: …`, the name as the heading gives it before
  any ` (`), then `## Links` with the `up:` link to the category's board, then `## Update record`.
- The registry points each entry's name and spellings to its collection page, so a search by any of them
  reaches the entry. An entry name that another subject also holds is a registry error.
- **Too large for a collection.** An entry whose own material (its section, its Timeline lines and its
  record lines) is larger than the leaf cap less 1,024 bytes (7,168 bytes with the default
  `limits.leaf_cap_bytes`) is admitted at once, and a leaf subject whose leaves together hold more never
  folds into a collection. Being a leaf because of size says nothing about weight: what goes up still
  follows section 5.
- **Admission.** An entry that reaches `levels.leaf_days` days, gets a `standing` line, or is too large as
  above, is admitted: its section becomes the leaf `04 stm/<tree>/<category>/<id>.md` with its Timeline
  lines, it gets its own registry line, and the collection page keeps one reference line for it, in the
  list directly under the page's description and before its first entry.
- **Standing.** A `standing` line, in a leaf's frontmatter or on an entry, makes its subject a leaf subject
  at once, whatever its days. It records one of two things, with the words that support it:
  1. the owner's own statement of a lasting tie: `standing: said — "<the owner's words>" (YYYY-MM-DD)`;
  2. something of the owner's own that lasts (an account, a card, a membership or subscription, a device,
     a document, a home, an employer, a project the owner runs, a family member, a component of the
     system): `standing: own — "<the words of the page that say so>"`.

  `forest levels` reads the line and makes no judgment of its own.

### Home trees

- A **tree** is an area the owner works or lives in. The root index's `## Trees` section is the one list
  of trees and of what lives in each; a subject's home tree is the tree whose line covers it. This contract
  does not list trees.
- A subject becomes a tree of its own only by the level rule (or, for an agent, by being created as one),
  and then names the area it grew from. What kind of material a page holds is its `kind:` (section 4),
  never a tree. A channel is never a tree.
- **Placement follows general rules, never a rule for one subject.** The rules are written on the root
  index under the `## Trees` list, and each one names a kind of subject and the tree that kind belongs to.
  Three invented examples for an owner who runs a pottery studio:
  1. A place where the owner buys things is a shop, in `admin` under `shops`, whatever it sold.
  2. Equipment the studio uses (a kiln, a wheel, a pug mill) belongs to `studio`; the same kind of device
     at home belongs to `home`.
  3. A person the owner knows for themself belongs to `people`; a company the owner deals with only as a
     customer belongs to `studio` under `customers`.
- A subject promoted to its own tree takes its leaves with it, and the tree it left points to it.

### Recurring events

- A recurring event (a weekly class, a market day, a shift pattern) is one subject: the series.
- The series has one sessions leaf (what happened at each session, dated) and one plan leaf (the schedule,
  the place, how it repeats and how that changed).
- A single session gets a leaf of its own only when it has material beyond a session line: a talk with its
  own notes, a trip of its own.
- When a sessions leaf splits by period (section 4), every page of the series is named alike, so that the
  series reads as one set: `sessions-2042-04.md` and `sessions-2042-05.md`, never `sessions.md` beside
  `sessions-2042-05.md`.

### One home for each fact

1. A fact lives in one leaf: the leaf of its subject and sub-topic. When the fact changes, only that leaf
   changes.
2. Every other page that touches the fact (another leaf, a period page, a contact log, a board) carries one
   **reference line** instead of a restated summary:

   ```text
   - YYYY-MM-DD — <the event in a few plain words>: recorded in `04 stm/<tree>/<branch>/<leaf>.md`.
   ```

3. A period page (`-2041`, `-2042-q2`, `-2042-05`) holds only its own period. A fact of another period
   lives on that period's page or in its subject's leaf.
   Within one subject, a fact recorded elsewhere has one reference line, in the subject's **index leaf**:
   the leaf whose reference lines name the most distinct pages (ties: a page without a period suffix
   first, then a `kind: profile` page, then the shorter path). The subject's other leaves carry no copy of
   it, and a period page carries none to a sibling period of its stream. A reference line never has its
   home in the page it names.
4. Two separate events of the same kind are kept apart, each named by its date: two kiln repairs in
   different months are "the March repair" and "the May repair", each with its own lines, never one
   merged account.
5. **Quoted once.** A turn, quote, reply or event is quoted in the one leaf of the subject it is mainly
   about. When several subjects take part, it is mainly about the one that did or said the most in the
   material. An event that becomes a subject of its own takes its turns and its account with it.
6. Each fact that quoted material carries also has a plain-words line in the leaf of the subject the fact
   is about: beside the quote when that is the same leaf, otherwise with the quoting leaf's path and the
   occasion.
7. **Repeated lines.** A **fact line** of a leaf is a line of its head or free sections (its `## Links`
   and `## Update record` aside) of `limits.repeat_min_chars` (40) characters or more that is neither inside
   a fenced block (a document kept as written) nor a bare reference (a list item that is nothing but code
   spans, links or URLs). Two leaves of one subject that share fact lines hold a copy (`leaf_lines_repeated`,
   section 8): each line goes to its one home, by code for reference lines and by a reading for the rest. A
   line that a reading finds rightly held by more than one leaf is judged once, in the judged ledger
   `.forest/repeats-judged.jsonl` (one row per subject and line digest, kept state like the move log),
   and no longer counts. `forest repeats` does this (section 9).
8. Something that only describes a subject (a dog's breed, a van's colour) is a line in that subject's
   leaf. The owner's own past told in passing (a town they grew up in, a team they played on) is a line
   where that part of their life is kept, not only inside the topic it came up in. What one person calls
   another, or says the other is like, is a line in the described person's leaf, with who said it.

### Pointers

1. A leaf has exactly one home.
2. A leaf's frontmatter `about:` names the other subjects it holds something about: what each did, has,
   made, felt or said of itself. It lists registry ids, at most 8. It never names the leaf's own
   `entity:`, never `owner.id` (every leaf is the owner's, so the owner's id says nothing about where a
   leaf belongs and would bring every leaf to every question that names the owner), and never the id of
   the system as a whole when the forest registers one, for the same reason. A component of the system,
   or an agent, is a subject of its own and is named under the carrier test below.
3. **The carrier test.** The channel, app, agent or component that only carried, delivered, recorded or
   announced the material is not what the leaf is about. It is named in `about:` only when the leaf says
   something about it itself: how it behaved, its settings, a fault, a change, the owner's account with
   it, or a decision about it.
4. Someone who only asks, listens, admires or answers about another's matter is not named.
5. Each id in `about:` has its name, an alias or another spelling in the leaf's text.
6. `about: none` says that a leaf was read and holds nothing about another subject. A leaf with no
   `about:` has not been read for it yet. A change that drops every id writes `none`; it never removes the
   field. `none` is never a registry id.
7. A board's `## Pointers` is generated from `about:`: each leaf of another branch or tree that names the
   board's subject is one line, its path in a code span followed by what the leaf holds (the description
   its own board's `## Leaves` gives it). A pointer to a board or a tree page is written by hand. The
   registry lists each subject's `about:` leaves, and search brings them in when a question names the
   subject.

### The assistant's own messages

A message an agent sends on its own, not as an answer to the owner, is a **notice**: a reminder, a brief, a
poll, an alert, or a departure or route notice.

1. **Kinds.** Each notice has one kind, which names the sub-topic of its leaf:
   - `departure-notices`: when to leave, routes and transfers, leave-by and on-the-way notices;
   - `reminders`: something due: a task, a deadline, a form, what to prepare or bring;
   - `briefs`: a brief or digest: an event brief, the day's schedule, a news digest;
   - `polls`: a question the agent put to the owner on its own (which calendar, did it go out, is the owner
     going);
   - `alerts`: any other notice the agent raised on its own about the subject (an account or security
     notice, a price, a delivery or a stock alert).
2. **Home.** A notice belongs to the subject it was for, in that subject's notices leaf of its kind:
   `04 stm/<tree>/<id>/<kind>.md` for a branch subject, `04 stm/<tree>/<category>/<id>-<kind>.md` for a
   leaf subject, with `subtopic: <kind>` and `kind: notice` (`kind: brief` for `briefs`). The leaf is
   created beside the subject's other leaves when none exists; a leaf that already serves as that notices
   leaf under another name is used as it is.
3. **Wording.** A notice is written as what the agent said, `<agent> notified: …`, with the times, routes
   and steps it gave, and its Timeline line. The owner's reply to a notice goes with the notice.
4. **The owner's doings.** The leaf that records what the owner did keeps the doings: how and when they
   went, how long they stayed, what they did there. It mentions a notice only as far as the owner acted on
   it, in one line with the notices leaf's path ("left at 16:20, after the advised 15:50").
5. **Recurring events.** A notice for one session of a recurring event belongs to the series; the series'
   notices leaf holds every session's notices, each dated.
6. **Several subjects.** A brief or poll that covers several subjects is one leaf under the subject that
   groups them (the day's schedule briefs under a day-plans subject, a news digest under its topic). Each
   item also gets a line on its own subject's leaf.
7. **The sending agent.** The agent or component that sent a notice is its subject only for how it
   worked, failed or was changed: a delivery fault, a rule about when to send, an alert that fired wrongly.
8. **Other tools.** An alert that a calendar or reminders app raised by itself, and anything another tool
   made for a subject (a calendar entry, a reminder), lives with that subject, attributed ("Calendar
   alerted: …"); an alert goes in the subject's notices leaf of its kind. The tool's own leaves hold facts
   about the tool.

Example: a 15:50 departure notice for the weekly glaze class is a line in the class's
`departure-notices` leaf; the class's sessions leaf says when Juno left, how she went and how long she
stayed.

### Decisions and instructions

1. A decision is a line in the subject it governs, in the words of whoever decided, with its date.
2. An instruction about how the system behaves goes to the tree that owns that behaviour, in the branch of
   the component it concerns. An instruction given to one agent goes to that agent's tree, under
   `## Standing instructions` (Agent trees, below).
3. A decision that also changes a subject's state is carried up like any other state (section 5).

### Shops and purchases

1. A shop is one subject in one tree, whatever it sold.
2. A purchase is filed where the owner's purchases are kept (the shop's leaf, or a purchases sub-topic of
   the owner's admin tree), with a line on the subject it was for: the device, the trip, the person a gift
   was for.

### Agent trees

An **agent** is a worker that a model runs under its own instructions over many runs and that acts: an
orchestrator, a bot that owns one kind of work, a scheduled stage, the sessions of a coding assistant. The
engine or model it runs on is a tag on the lines it touches; when the engine changes, the agent stays. A
helper started for one task works as the agent that started it.

- **A tree per agent, made by code.** An agent exists for the forest from either of two facts, and no
  command has to be run for it:
  1. its entry in the settings, `[agents.known.<id>]`, with a `name` and a `role`, each optional; the id is
     any slug the people running the forest choose, and the name is free text in any script;
  2. the first file in its raw lane, `06 istm/<id>/`, unless `agents.auto_create` is `false`.

  An agent is a tree from its first day and for as long as it is an agent. `forest levels check` reports
  each agent without a tree as due one, and `forest levels apply` makes it: the registry line (type
  `agent`, level `tree`), the tree page, its index and its line under `## Agents` on the root index.
  `forest move new-agent <id> [--name …] [--role …]` makes the same tree at once. A name not given is the
  id; a role not given leaves `## Role` with the one line `(not set)`. An unset value is filled and a set one is
  never changed: when the settings later give a name or a role that the tree still leaves unset (a name
  that is the id, a `## Role` of `(not set)`), `forest levels check` reports it as due and `forest levels
  apply` fills it, with its change line on the tree page; a value set in both places and set differently
  stays as it is, and the lint reports it (`agent_settings_differ`) until `forest move rename` or an edit
  makes them one. With `agents.auto_create` set to `false`, a lane whose agent is neither
  declared nor registered is the upkeep hit `agent_lane_without_tree`. A lane folder or a declared id that
  is not a slug, that is a shared tree's id, or that is a reserved name (`owner.id`, `subjects.system_id`,
  `none`, a collection's id) never becomes a tree: `forest check` reports it as `agent_id_invalid`.

  A subject that was filed as a branch and turns out to be an agent is retyped
  (`forest move set-type <id> agent`) and promoted with its leaves by `forest levels apply`. Inside its
  tree the same rules hold as anywhere: sub-topics are branches, leaves follow section 4, levels follow
  days.
- **Its tree page is what the agent reads first.** It has the sections `## Role`, `## Standing
  instructions`, `## Lessons`, `## Current state` and `## Branches` (section 5).
- **What is filed there** follows the `agents.policy` setting:
  - `subject-first` (the default, for one owner served by several agents): a fact about a subject lives in
    that subject's home whichever agent learned it; the agent is a tag on the line (`noted by <agent>`).
    The agent's tree holds what is the agent's own: its role and the instructions given to it, its methods
    and the lessons of doing its work, its runs and their outcomes, its open work and its hand-offs.
  - `agent-first` (for a team whose agents work on separate matters): what an agent learns is filed in its
    own tree, each leaf naming in `about:` the shared subjects it concerns, so that a shared subject's
    board points to it. A fact that two agents hold, or that the owner states for everyone, is
    **shared**: the upkeep run moves it to the shared subject's home, and each agent's leaf keeps a
    reference line.
- **Who reads what.** Every reader has an id, taken from its connection and never from the question.
  - An orchestrator (listed in `agents.orchestrators`) reads the whole forest.
  - Any other agent reads the shared trees and its own tree, and reads another agent's tree only when
    `agents.default_scope` is `all` or its task names that agent. The default scope is `own+shared`.
  - A reader that is no agent of the forest reads the shared trees only.
  - Each tool call records which trees answered it.
- **Who writes.** Agents never edit forest pages at the same time.
  1. Each agent appends to its own raw lane, `06 istm/<agent>/`.
  2. The daily record is written from the lanes.
  3. One filing run at a time, holding the forest writer lock, files into the forest.
  4. A direct edit of a forest page goes through the same lock.
- **Ending.** `forest move retire-tree <id>` ends an agent: its registry line gets the status `ended`, its
  line under `## Agents` says `· ended`, its pages stay where they are and the orchestrator still reads
  them. `forest move fold-tree <id> --into <tree>` ends the tree itself: the subject becomes a branch of
  the other tree, of the type named with `--type` (by default `agents.folded_type`, `component`), its old
  branches become its leaves' `subtopic:`, and it leaves `## Agents`; nothing is lost, and old paths
  resolve.

## 4. Leaves

### What a leaf holds

1. A leaf is one sub-topic of one subject. It keeps full detail and is never a summary: the facts as the
   source gave them, times with their zone, places, people, exact titles, URLs, typed text, outcomes and
   the wording of results.
2. What an agent's replies to the owner concretely said is a fact too, marked as such (`studio-hub
   replied: …`). What an agent sent on its own is a notice (section 3). What an agent did for the owner,
   and with which tool, shortcut or app, is a fact too, in the source's words. Call ids, raw tool output,
   one-time codes and clock times to the second stay in the lanes.
3. A fact that a quoted turn carries also has its own plain-words line, which keeps who did, made or said
   what, and where.
4. A question and its answer are one line, the answer's. A question has a line of its own only when
   nothing answers it.
5. What one person calls another is a line about the one called and an alias on their registry line.
6. A back-reference ("the one from last week") with one candidate on record is filed as that one, its
   words kept; with several candidates the line names them.
7. A leaf states each fact once, and its title and opening description say what it holds now.
8. A statement is attributed to whoever made it; the attribution is the whole mark of whose statement it
   is. A figure or a reason that no source states appears only as a labelled inference.
9. Writer notes, reading positions ("chat 3", "turn 6"), remarks about the forest or its owner, and
   restated conventions stay in the run's own directory.
10. A leaf is never shortened to tidy it.
11. Only what the owner opened is filed: a page they visited, a video they played, an item their history
    or a player records. A recommendation, a card or a link only shown to them stays in the daily record,
    with no leaf, Timeline line or summary section of its own.

### Episodes

An **episode** is a run of state changes of one thing from its start to its end: an order, a repair, an
application, a stay, a trip.

1. An episode is one section of its sub-topic's leaf, headed `## <start date> <what>` with the start as
   `YYYY-MM-DD`. Its dated lines sit in that leaf's `## Timeline`, each naming the episode.
2. Episodes of one kind share one leaf, newest first (`repairs.md`): never one leaf per episode, and never
   one leaf per state change.
3. The board's current state carries the episode's latest state with its date (section 5).
4. An episode list over the cap splits by period (Names, size and shape, below), and its board names the
   current period's page.

### Open points, not disclaimers

1. A page writes what the source said, attributed.
2. Something still awaited (an outcome, a confirmation, a step still to do) is an **open point**, written
   once on the line it concerns, in the source's words: "the refund still to come", "said she still has
   to send it back".
3. A plan or an announced step stays as it was said. A detail the source did not give goes unmentioned.
   Statements that pull apart stay as said, each with its words, and are not called unclear.
4. A general remark on whether something was verified, established or followed to the end is not written:
   the attribution already says whose word it is.
5. Where the owner was, whether they went, who opened a door, when they paid: these are written as the
   answer that the sources which place the owner give together (location, calendar, messages, receipts),
   with that basis. A location record is the strongest of them unless it lagged. What none of them answers
   is one open point, and a few that matter become questions to the owner.

### No machine output

1. Typed text is written as the final text that was typed, never as keystrokes or as a field captured
   again and again while it grew.
2. A video, a page or a match is written as what was watched or read (section 7), never as positions
   captured one by one or as URL groups with lists of clock times.
3. Ids of messages and runs, hashes and run receipts stay in the run directory or the lanes.

### Names, size and shape

- A leaf is a living page: it is updated in place and split by sub-topic (section 3).
- **File names carry no date or period** in any form: no `2042-05-14`, no year or month, no month or
  season words (`-may`, `-spring`). The exceptions are the period suffix of an event list, the day of a
  page of a month cut by days among them (below), and the period of a collection page (section 3).
- **A leaf is named by its sub-topic**: `<id>-<subtopic>.md` beside a leaf subject's leaf, `<subtopic>.md`
  in a subject's own folder, and, in a branch of a tree that grew from a subject or of an agent's tree,
  by what it holds within the branch. Never a name that ends in a counter (`notes-7`, `plan-2`); a number
  that belongs to what the page is about stays (`cone-6-limits`). Its board's `## Leaves` line says what
  it holds.
- **The cap.** `limits.leaf_cap_bytes` (8,192) bytes of a leaf's **content**: the page without its
  `## Update record`. The cap keeps every leaf readable whole and lets several leaves fit one read; a read
  names the record instead of showing it, and `forest record size PATH` prints a page's content size, its
  record size and what is due.
  1. Over the cap a leaf splits by sub-topic, by what its entries are about (a contact log into gifts,
     work talk and family news), each sub-topic's text moving whole. One long episode splits by sub-topic
     within the episode.
  2. A pure event list with no sub-topic left (firings, a series' sessions, an episode list) may split by
     period, the period being the last part of the name: a year `<stem>-2041.md`, a quarter
     `<stem>-2042-q2.md`, or a month `<stem>-2042-05.md` when a quarter passes the cap. A month that
     still passes the cap is cut by time, never left for a reading of its lines: into runs of whole days,
     each filled up to the cap, the first run keeping the month's name and each later run named by its
     first day (`<stem>-2042-05-14.md`); a day whose lines alone pass the cap takes further pages of that
     day in line order (`<stem>-2042-05-14-2.md`, `-3`, …). The lines stay as written and in time order,
     each line, with the lines indented under it, on exactly one page, and each page of a month cut this
     way has a title that gives the days it holds (`(2042-05-01 to 2042-05-13)`). Every page of a series
     split this way carries a period of this form. When such a page later splits by sub-topic, each new
     leaf keeps the page's period as the last part of its name.
     A day is a period only on a page of a month cut by days: a `-YYYY-MM-DD` or `-YYYY-MM-DD-N` suffix
     counts as one where a page of the same stream with a year, quarter or month period stands in the
     folder. A lone day-named leaf carries a date in its name (`leaf_dated_name`). The lint, the level
     rule and the tools read one definition of the period suffix.
  3. A collection page splits as section 3 says, and counts as the whole page.
  4. A run that pushes a leaf's content over the cap, or adds to a leaf that is already over it, splits it
     in the same run. A record never makes a leaf split.
  5. A leaf page over `limits.leaf_max_bytes` (20,480) bytes, record included, is a violation (section 8).
- **What a record line is.** A line stands in a page's `## Update record` only when it is one of three
  kinds, each about that page:
  1. a change: what a statement or a frontmatter value of the page was and what it became
     (`was "…", now "…"`), quoting the statement or value that changed and not the section around it;
  2. a removal, a correction or an exclusion made at the owner's word about what the page holds: the line
     quotes what was removed (`removed "…"`) or what the statement was and became, or says what was not
     kept without restating it (`not kept: …`), and gives the owner's words (marked as
     `record.owner_word_names` and `record.quote_marks` say);
  3. where the page's own lines came from or went: another page of the forest or its lanes, or an archive
     copy, named by its path in what the line says happened, not only in its `Source:`.

  Anything else a run could note about its own work (how many lines it touched, that it rechecked a page
  and changed nothing, that it rewrote a page's form) is the run's receipt and
  stays in the run's directory. A run that changes no statement of a page writes no record line on it.
  This holds for every page alike: a leaf, a board, an index, a tree page. A page whose links, list lines,
  registry lines or `about:` ids a tool rewrote because another page or subject moved gets no record
  line: the move log is the record of that change. A derived-list sync writes no record line.
  Every writer of the tools (the move tool and its plans, the collections, the levels, the record trim,
  the number starring, `forest repeats`) writes only these three kinds, and never a person's number whole:
  a number written in full in a record line is starred as section 4, Account and identity numbers, says,
  and a merge of a subject saved under its number (an id or a name that is nothing but a number) names it
  starred in every line it writes, its alias included.
- **Record trim.** A record is bounded on every page. When its lines, pointer lines included, hold more
  than `limits.record_keep_bytes` (2,048) bytes (each line counted with its newline; the pointer lines
  stay on top and do not count against the budget of item 1):
  1. it keeps the newest lines that fit in that budget, and, whatever their age, every removal,
     correction or exclusion at the owner's word (so that a later run does not add back what the owner
     took out or excluded), one copy of each, oldest first by their leading date;
  2. every other line, exact duplicates included, moves verbatim, in its order, to the page's **record
     history file**, `.forest/archive/record-history/<the page's path at its first trim>` (or the next
     free `-2`, `-3` name when another page's file holds that name). A line that is none of the three
     kinds moves there whatever the record's size;
  3. the page keeps one pointer line for life, written at its first trim and never changed, on top of the
     record: ``- Earlier record lines (<first date> – <last date>, <n> lines) are kept in `<file>`.``

  The history file is only ever appended to. Each trim appends one block, an HTML comment line (the day,
  the page's path then, the SHA-256 of the page text it was cut from, the number of lines and their first
  and last dates) followed by the moved lines, and the archive manifest lists the block with its offset,
  length and hash. A later trim adds nothing to the page; it takes out only what moves. `forest record
  trim` writes the page only after it has proved that the file begins with its bytes before the append,
  that every moved line stands in the block byte for byte and in order, and that nothing outside the
  record changed; a trim interrupted before its page write is completed by the next, which finds the
  block cut from the same page text and does not append it again, and a trim interrupted inside its append
  leaves a torn tail (a part of a block) that the next trim cuts off before it appends the block whole, so
  a killed trim leaves no half page and no stray block. A page that moves, is renamed or is
  re-cut keeps its pointer line and so its file; a page merged into another brings its pointer line to
  the merged page as a line of where its lines came from, and a trim there appends to the file the first
  such pointer names. `forest timeline subject <id>` reads the changes in a page's history file (and in
  the whole-page copies of forests trimmed before history files) as well as those on the page, so a
  subject's history stays whole after a trim.
- **Shape**, in this order:
  1. Frontmatter:
     - `entity: <id>` (required);
     - `subtopic: <slug>`, the sub-topic the leaf belongs to (leaves split from one sub-topic share it);
     - `kind: <kind>` (required; Kinds, below);
     - `about: <id>, <id>` or `about: none` (section 3, Pointers);
     - `standing: …` when one applies (section 3, Collections);
     - `timezone: <zone>` when the leaf holds times;
     - `updated_at: YYYY-MM-DD`, the date of the latest evidence the leaf holds;
     - `date: YYYY-MM-DD` only on a leaf with no `## Timeline`;
     - `status: superseded` with `superseded_by: <path>` when a newer leaf replaces it.
  2. `# Title`, then the body in free sections. The summary of a watched video or a read page is that
     item's own section, `## About the video: <title>` or `## About the page: <title>`, one per item, so a
     section read alone says which item it describes.
  3. `## Daily totals`, when the subject has usage or contact totals (section 7).
  4. `## Timeline`: one line per dated activity, `- YYYY-MM-DD HH:MM–HH:MM <zone> — what happened`, the end
     time and the zone being optional and the time left out when unknown. The time index reads these
     lines.
     - A time given relative to when it was said is written as the date or span it resolves to, with the
       words and the day they were said: `2042-04 (said "last month" on 2042-05-14)`. Calendar words
       resolve on the calendar: "last week" is the Monday-to-Sunday week before, "last month" the calendar
       month before, "in the last month" the span ending that day.
     - A span that runs up to the day it was said ("for ten years") also gets the time it began:
       `since about 2032 (said "for ten years" on 2042-05-14)`.
  5. `## Links`: `- up: [[03 mtm/<tree>/<branch>|<branch> MTM]]`. Every other path goes in a code span.
  6. `## Update record`, always last, with nothing after it: one dated line per change,
     ``- YYYY-MM-DD — what changed: was "…", now "…". Why: …. Source: `path`.``

### Kinds

A leaf's `kind:` names what its material is. It is one of this fixed list:

| Kind | What it holds |
| --- | --- |
| `conversation` | what the owner and others said: chats, calls, mail threads, the owner's questions to an agent with their answers |
| `event` | a dated happening the owner took part in or was invited to: a meeting, a class session, an appointment, a stay |
| `visit` | where the owner went and stayed, and reconstructed days |
| `trip` | getting somewhere: routes, commutes, rides, flights, bookings |
| `plan` | what is planned: a series' schedule, a day plan, calendar entries, to-dos, deadlines |
| `application` | an application or admission: applied, assessed, interviewed, offered or turned down |
| `purchase` | orders, payments, receipts, deliveries, refunds, charges |
| `received` | what a sender sent unasked: offers, newsletters, alerts, account and security notices |
| `notice` | an agent's notices (section 3): departure notices, reminders, polls, alerts |
| `brief` | an agent's brief or digest |
| `watched` | videos, shows, films, streams and matches watched |
| `read` | pages, posts, articles, searches and threads read |
| `link` | a reference kept to reuse, with why: a repository, a document, a paper, a site |
| `rule` | a rule or a decision: the owner's instructions, how an agent must behave |
| `profile` | what a subject is: identity, attributes, history, relations |
| `work` | something made or worked on: code, documents, research, analyses an agent made for the owner |
| `collection` | a collection page (section 3) |

### Changes

- When a fact changes, the page states the current fact and the old statement moves whole into
  `## Update record`, as a change line that quotes that statement and what replaced it.
- A superseded Timeline line keeps its text, with the prefix `[superseded YYYY-MM-DD]`.
- Every `## Update record` runs oldest first: a new line goes at the end.

### Language and quoted words

1. Memory text is written in the forest's `language` setting.
2. Every quoted turn, whoever said it (the owner, another person, an agent, a mail, a page), stays verbatim
   in its own language; the text around it is in the forest's language.
3. Fixed strings (names, titles, URLs, mail subjects, interface text) stay verbatim in their language.
   Text kept verbatim keeps its own spacing; a string that arrived without spaces stays that string.
4. The identity labels on the root index may carry, in parentheses, the same label in another language
   the owner uses, so that a question in that language finds them.
5. No JSON objects and no fenced JSON blocks in the forest.

### "Do not record"

When the owner says that something is not to be remembered:

1. The leaf where it came up keeps the instruction itself, in the owner's words, with its date and what it
   covered named in a few words.
2. Nothing of the excluded content is written as a fact in any leaf, board or tree page.
3. Lines already filed from it are taken out. Each page's earlier version goes to the archive first
   (`forest archive put PATH --keep --reason owner-exclusion`), and its `## Update record` says what was
   taken out and why, without restating it, in a line of the `not kept: …` form (section 4, What a
   record line is) that gives the owner's words.

### Account and identity numbers

1. A bank account number, an IBAN and a card number are written with the middle starred, the way a phone
   number is: the first three and the last four characters stay, so that the owner and the agents can
   tell which account it is and find the whole number in the message it came from.

   A person's phone number is written the same way: the first three and the last four characters stay.
   A line the settings name as one to keep whole (`numbers.keep_whole`: patterns for a company's or a
   service's line) stays whole.
2. A number is an account number because of what stands next to it: a bank's name or an account word,
   both listed in `numbers.account_words`, an IBAN whose check digits hold, or a card number whose check
   digit holds. An order number, a parcel number, an invoice reference (after a word of
   `numbers.reference_words`), a date and a bank's phone line stay whole.
3. A national identity number loses its last digits. Which formats the forest knows, how each is
   recognised (its leading digits read as a birth date, or an identity word, `numbers.identity_words`,
   stands before it) and how many
   trailing characters each hides are settings (`identity_formats`); the contract names no country's
   format as fact. One format is written `"<shape>:<hide>"`: in the shape `N` is a digit, `A` a Latin
   letter, and a space, `-`, `.` or `/` stands for itself; `<hide>` is `after-hyphen` (every character
   after the first hyphen) or `last-<n>` (the last n letters and digits).
4. Every page a stage writes is starred this way, every record line a tool writes too (section 4, What a
   record line is), and the tools star what they show: a page, an archived copy (`forest archive show`),
   record lines read back from a record history file (`forest record history`), example lines and registry
   entries. The archive keeps the bytes it was given, and the raw lane `06 istm` keeps its bytes; a
   page that holds such a number whole is the violation `account_number_in_full` (section 8).

## 5. Pages above the leaves

- **The root index** (`01 xltm/XLTM.md`) is an index with three sections:
  1. `## Identity`: the identity-level truths about the owner, and nothing else. It holds no job, study or
     project state; it points instead ("work → `studio`").
  2. `## Trees`: every tree, one line each, `` - `<tree>` — what lives there ``, followed by the placement
     rules (section 3, Home trees). It is the one list of trees.
  3. `## Agents`: every agent, one line each, `` - `<id>` — <name> · <role> ``, with `· ended` for a
     retired agent. It is the one list of agents.

  `forest init` writes these three. Like every page, the root index gains an `## Update record` at its
  end when its first record line is written.
- **A tree page** (`02 ltm/<tree>_LTM.md`) holds `## Scope`, `## Patterns and rules`, `## Current state`
  and `## Branches`, then `## Links` and `## Update record`, each present; `## Update record` comes with
  the first record line. `## Branches` has one line per branch,
  `` - `<branch>` — who or what it is at its core, and what it holds ``, the core being its identity,
  roles, lasting traits and ties in one or two sentences.
- **An agent's tree page** has frontmatter `entity: <agent id>` and holds `## Role` (the line `(not set)`
  while no role is given), `## Standing
  instructions`, `## Lessons`, `## Current state` and `## Branches`, then `## Links` and `## Update
  record`, each present; `## Update record` comes with the first record line.
- **A tree's index** (`03 mtm/<tree>/<tree>_MTM.md`) holds the tree's registry in `## Subjects`
  (section 2) and its board inventory in `## Branches`, one line per board, `` - `<branch>.md` — <what it
  is> ``, then `## Links` and `## Update record`, each present; `## Update record` comes with the first
  record line.
- **A branch board** (`03 mtm/<tree>/<branch>.md`) says what its subject or category is, its current state,
  the rules it carries, its leaves and its pointers. A subject's board has frontmatter `entity: <id>`; a
  category's board has none.
  1. Its sections, in this order: `## About`, `## Current state`, `## Rules` (only when it carries rules),
     `## Leaves`, `## Pointers`, `## Links`, `## Update record`, each present but `## Rules`, which stands
     only when it carries rules; `## Update record` comes with the first record line. Every board has
     `## Current state` and `## Pointers`, left empty while there is nothing for them.
  2. `## Leaves` lists every leaf in its folder: ``- `leaf.md` — what it holds (updated YYYY-MM-DD)``, the
     date being the leaf's `updated_at`.
  3. `## Pointers` lists the subject's material that lives elsewhere (section 3, Pointers).
  4. Its current state gathers what its leaves and pointers hold by kind (what the subject does and with
     whom, makes, has or bought, likes, what helps it, its people and places), one line per kind naming
     each item with its date.
- **Current-state lines** on tree pages and boards carry their date and source:
  ``- <statement> (as of YYYY-MM-DD; source `<path>`)``. The line ends with the closing parenthesis; a
  tool that reads a line with a full stop after it reads the same line.
  1. `as of` is the date of the evidence, the day the source recorded it, never the day a run wrote the
     line.
  2. A line about a value the subject holds (a balance, a count, a goal and the progress toward it)
     carries the latest recorded value with its date, and, where a goal or a limit and the progress are
     both numbers, what remains, worked out from the two. An earlier value stands until a later record
     changes it.
  3. A line is **stale** when its source leaf has changed since that date (its `updated_at` is later) or
     is gone. The upkeep run re-checks stale lines.
  4. **A line about one entry of a collection page cites the entry**: `<collection page>#<entry id>`. A
     collection page's `updated_at` follows the latest evidence of any of its entries, so a line that states
     one entry but cites the whole page would read as stale whenever another entry gains a line or is moved
     onto the page. An entry source is dated by that entry's own Timeline lines (by its page when it has
     none; the lines under its own `### Update record` only say when a tool folded, cut or moved it and do
     not count). A bare collection page means the page as a whole. A line that states several entries cites
     each, `` (as of YYYY-MM-DD; sources `<a>`, `<b>`) ``, and is dated by the latest of them.
  5. An entry source whose entry has left its page is stale either way: when the move log sends it to a
     place where it stands now, the lint names that place, and the line is restated or its source corrected;
     when it stands nowhere, it is gone.
- Pages above the leaves carry no dated headings, run ids or validation counts.

### What goes up

Upper layers hold what is established, not what is newest. One day of watching a sport does not make the
owner someone who likes that sport.

1. A board's `## Current state` and a tree page's lines hold what the leaves establish: the subject's
   state, its recurring patterns and its items by kind.
2. A preference, interest, habit or pattern is carried up only on repeated evidence (several distinct
   days) or on the owner's own explicit statement of it ("I like X", "every Thursday").
3. A one-off event stays in its leaf.
4. A single fact that is itself a state is carried up as that state with its date: an offer received, a
   move, a job started or ended. For an episode (section 4) the board carries its latest state with its
   date ("repaired 2042-05-09 after the May fault"); a pattern across episodes goes up only on repeated
   evidence.
5. A current-state line is a summary by kind, never the latest single message stamped again: a message of
   one day stays in its leaf and reaches the board only as an item of its kind, with its own date.
6. A line already carried up is revised when later evidence contradicts it (the owner said they stopped,
   the offer was withdrawn); the run that files that evidence makes the correction. Recency alone is no
   reason to rewrite a line.

## 6. Links

- Wikilinks join adjacent layers only, and each page has a single `up:` link to its parent: a leaf to its
  board, a board and a tree's index to the tree page, a tree page to the root index. Leaves never link to
  each other.
- Every other reference is a path in a code span, such as `` `04 stm/people/acquaintances/odile-brask.md` ``.
- When a page moves, the move tool rewrites, in the same run, every up-link, list line, registry line and
  code-span path that named it. When it archives a page, each code-span path to the page stays and is
  marked `` (archived)``.
- A path kept only as history (where something used to be) is quoted in double quotes, the way the move
  tool's `Path changed: was "…"` lines quote it. An `## Update record` is history throughout.

## 7. Machine records

- The forest holds no machine records: raw interval tables; hash, candidate and binding tables; provenance
  and revision tables; notification decisions without the message text; automation receipts and repeated
  runner prompts; views that code regenerates; merged originals and dumps of retired roots.
- They live in the lanes, in run directories, or in the archive `.forest/archive/`: one compressed copy
  per version of a page that an operation took out or rewrote whole (an archived or folded page, a page
  before its numbers were starred or an owner's exclusion was taken out), under the date it was archived
  and its old path, each listed in the archive manifest with its old path, hash, size, reason and, if
  any, the leaf it was folded into; and one record history file per page whose record was trimmed
  (section 4, Record trim), each block of it listed in the manifest. `forest archive check` verifies every
  copy and every block against the manifest. Nothing is deleted: a copy that a rolled-back commit stored
  stays, so the archive only grows. `forest archive show <old path>` prints
  the latest copy of a page, `forest archive list --path <old path>` every copy and history block, and
  `forest record history <path>` the record lines the history blocks keep.
- What a machine repeats becomes facts in the subject's leaf:
  - a stay or a visit is one Timeline line per stay, with the place, the arrival, the departure or the last
    time seen, and the longest dwell;
  - a video, page, article or match watched or read is one Timeline line in its own subject's leaf (the
    exact title, the channel or site, the specific URL, the position reached, start and end) and a short
    summary of what it was about, read from the page or from the video's description and captions. That
    text is fetched ahead by its stage and handed to the writer, which never fetches it itself. The
    recorder's rows (URL groups with clock lists, field dumps, accessibility observations, keystrokes, the
    same viewing seen again) stay in the lanes; a title that belongs to another subject goes to that
    subject's leaf; one source line, the daily page's path, stays;
  - an automation run adds one Timeline line with its time, its outcome and what differed, and the run
    directory's path when its receipts may be needed;
  - usage and contact are **daily totals** under `## Daily totals`, one line per day and subject:
    `- 2042-05-14 — studio chat with Odile Brask 18:10–18:40, 12 messages`. Totals are written from the
    lanes by code. They do not change what a leaf records: they leave the leaf's `updated_at` as it is, so
    a current-state line that cites the leaf stays fresh.

## 8. The lint

`forest lint` checks every rule that has a shape and reports each hit with a code and a path.

**Violations:**

| Code | Hit |
| --- | --- |
| `leaf_path_shape` | a leaf path out of the shape of sections 1 and 4 |
| `leaf_entity_missing`, `leaf_entity_unknown` | no `entity:`, or one that names no registered subject |
| `leaf_misplaced` | a leaf outside its subject's home |
| `leaf_too_large` | a leaf page over `limits.leaf_max_bytes` |
| `leaf_dated_name` | a date, period or numbered name outside the allowed suffixes |
| `leaf_recorder_dump` | recorder rows in a leaf (sections 4 and 7): outside its record, a line with three or more clock times to the second, a message, run or attachment id (a UUID) or a hash of 32 or more hex characters |
| `leaf_kind_invalid` | a `kind:` outside the list of section 4 |
| `about_unknown` | an `about:` id that names no registered subject, names the leaf's own entity, `owner.id` or the system as a whole, or more than 8 ids |
| `collection_entry_malformed` | a collection entry out of the shape of section 3 |
| `machine_page` | a machine record as a forest page (section 7): `status: merged`, a fenced JSON block or JSON object lines, or a table whose rows are mostly ids or hashes |
| `registry_line_invalid`, `registry_duplicate_id`, `registry_duplicate_alias`, `registry_home_missing` | a registry line out of shape, an id or alias used twice, a home that is not there |
| `registry_entry_is_subject` | a collection entry's name that a subject also holds |
| `board_leaves_stale`, `branch_list_stale` | a `## Leaves` or `## Branches` list that misses or names a page |
| `current_state_untagged`, `current_state_stale` | a current-state line without its date and source, or with a source changed since, gone, or (an entry source) moved to another place (section 5) |
| `reference_stale` | a code-span path to a forest page or folder that is not there and is not marked `` (archived)`` |
| `account_number_in_full` | an account, IBAN, card, identity or person's phone number written in full (section 4) |

**Upkeep**, the rules the forest still breaks on many pages, each counted with examples and the rule that
fixes it:

| Code | Hit |
| --- | --- |
| `leaf_over_cap` | a leaf whose content is over `limits.leaf_cap_bytes` |
| `record_over_budget` | a page whose record is over `limits.record_keep_bytes` while a trim would move lines |
| `record_run_note` | a record line that is none of the three kinds of section 4 |
| `leaf_kind_missing`, `leaf_about_missing`, `leaf_updated_missing` | a leaf without `kind:`, `about:` or `updated_at` (collection pages carry no `about:`) |
| `leaf_lines_repeated` | a leaf holding `limits.repeat_min_lines` (3) or more of the same fact lines (section 3, One home for each fact) as another leaf of its subject: a split that copied instead of moving; a line the judged ledger holds for the subject does not count |
| `board_pointers_stale` | a `## Pointers` list that differs from what the `about:` lines elsewhere make of it |
| `board_pointer_stale` | a pointer to the board's own leaf, or a bare name instead of a path |
| `board_entity_missing` | a subject's board without `entity:` |
| `board_state_empty` | a board with no line under `## Current state` while its folder holds a leaf that is not a collection page (section 5: its current state gathers what its leaves hold) |
| `current_state_date_ahead` | an `as of` date later than its source's `updated_at` |
| `leaf_channel_subtopic` | a sub-topic named after a channel |
| `timeline_zone_missing` | a timed Timeline line with no zone on a leaf with no `timezone` |
| `leaf_writer_remark` | a writer's note or a remark on verification in a leaf (section 4) |
| `daily_section_unfiled` | a daily section the filing run has filed whose specifics (names, quotes, titles, URLs, amounts, tools used) the leaves do not state as written; boards and tree pages do not count |
| `agent_lane_without_tree` | with `agents.auto_create` false, a raw lane `06 istm/<id>/` whose agent is neither declared in the settings nor registered (section 3, Agent trees) |
| `agent_settings_differ` | an agent whose settings entry and tree both set its name, or both set its role, to different values (an unset value is filled by `forest levels apply`, section 3, Agent trees) |

**Due work**, never an error:

| Code | Hit |
| --- | --- |
| `level_due` | a subject whose days or `standing` line give it a higher level than it has (section 3) |
| `category_too_large` | a category branch over `limits.category_max_leaves` |

How the lint holds a run:

- A run that writes pages takes a **baseline** when it starts. It must fix, before it completes, every
  violation its own pages added and every upkeep hit it added: a leaf whose content it pushed over the cap
  or grew while over it, a page its own record lines took over its record budget, a record line that is
  none of the three kinds (`record_run_note`), a leaf it saved without `about:`,
  a leaf it filled with lines copied from another. What was already there when it started is the upkeep
  run's work and never blocks the run. `daily_section_unfiled` is measured on the daily record and never
  holds a run.
- A `level_due` is carried out by the run that sees it (section 10).
- The derived lists are brought up to the pages before the lint runs (section 9, `forest check --sync`).

## 9. Tools

The tools are the `forest …` commands of the design's package map (design, section 13). Subcommand names
below may still change while the code is ported; the design's milestones say when each arrives.

- **`forest move`** moves a leaf; promotes a subject to a branch or a tree; merges two subjects; renames a
  branch or a subject; archives a page; moves a branch with its board, folder and registry lines to
  another tree; creates a tree (`new-tree`) or an agent's tree (`new-agent`, section 3, Agent trees);
  retires a tree (`retire-tree`); folds a tree into a branch of another (`fold-tree`, its old branches
  becoming its leaves' `subtopic:`); retypes a subject (`set-type`); sets a leaf's frontmatter (`kind`,
  `about`, `standing`); re-cuts a stream of event pages by period (`recut-stream`); and runs many
  operations from one plan with one verification.
  1. Each operation that moves something rewrites every up-link, list line, registry line and code-span
     path that named the old place, and appends to `.forest/moves.jsonl` one line per path that moves:
     each page, each collection entry and registry line (`<page>#<id>`), and each folder that moves whole.
     A moved page gets the record line `Path changed: was "…", now "…"`.
  2. `set-type` and `set` move nothing: each writes the change line of what it changed on the page it
     changed (the registry line's index, the leaf) and nothing in the move log.
  3. `retire-tree` on an agent's tree ends the agent (section 3, Agent trees: Ending). On any other tree it
     archives the emptied tree into the page that took its rules (`--into`), which gets the record line
     of where those rules came from.
  4. `recut-stream` writes each period page with the title `# <Name>: <stream words> (<period>)` and the
     description `The <stream words> of <Name> in <period>, one line each.`, the stream words being the
     pages' shared name without its period, its hyphens as spaces. For each year it takes the coarsest
     period whose page keeps within the cap: the year, else its quarters, else a passing quarter's
     months, else a passing month's runs of whole days (section 4, Names, size and shape), whose titles
     and descriptions give the days each holds (`(<first day> to <last day>)`, or the one day).
  5. **All or nothing.** An operation, or a plan of many, is written whole or not at all, also when its
     process is killed: before its first write it keeps an undo journal (`.forest/journal/commit.json`)
     of every page's text before and the move log's length, and removes it only once the pages and the
     move-log lines are written. A journal still standing is rolled back by the next writer to take the
     writer lock, or by `forest recover`, and until then `forest check` reports it
     (`commit_journal_standing`). The journal names pages by their forest paths, so a forest copied with
     a standing journal rolls back only the copy; a journal that cannot be read, or of a schema the
     version does not know, stops the next writer before it writes anything.
  `forest move resolve <old path>` follows the log through every operation to the present place, or to
  the archive entry and the leaf it was folded into, so evaluations and archives that hold old paths still
  find the page. An entry address `<page>#<id>` is followed the same way through every operation that can
  carry an entry: a cut of its page, a move of its category to another place, an admission into a leaf of
  its own and that leaf's later moves and merges, and a demotion back into a collection; it is current only
  while the page reached holds the entry. Each operation that moves an entry rewrites, in the same
  all-or-nothing write, every source and path that cited its old address, and a demotion makes a line that
  cited the leaf cite the entry.
- **`forest levels check | apply [--subjects ID …]`** computes every subject's level from the pages
  (section 3). `check` lists each `level_due`; `apply` carries each one out (an admission through the
  collections tool, a promotion through the move tool) and logs it. It lowers no level by count; a forest
  migration may need one explicit pass that does, and that pass says so in its log.
- **`forest collections demote | admit | recut | list | cite-entries`** keeps the collection pages: `demote`
  folds a leaf subject into its category's collection, `admit` moves an entry out into a leaf of its own,
  `recut` cuts a page past the cap by its entries' first days, and `list` names every page with its entries
  and their days. Each logs its moves, so `resolve` follows an entry to its page and back out.
  `cite-entries [--dry-run]` is a one-time upkeep for forests written before entry sources (section 5):
  each current-state line that cites a bare collection page cites instead the entries its statement names,
  by the registry's name comparison (section 2); a line that names none keeps the page. When the line's
  `as of` is later than the latest evidence of what it now cites, the date comes back to that evidence; an
  earlier date stays, and the lint reports the line as stale. Each changed page is archived first and its
  copy proven to hold the page's bytes; only the bytes inside the tag change, no record line is written (a
  corrected source changes no statement), and a second run changes nothing.
- **`forest record size | kinds | trim | history`** and **`forest archive put | show | list | check`** keep
  the update records and the archive (section 4); `record history` prints a page's moved record lines,
  oldest first, and `archive list` names each row's kind, a whole-page copy or a record history block.
- **`forest repeats plan | apply | check`** takes `leaf_lines_repeated` to zero (section 3, One home for
  each fact). `apply` removes, from the subject's other leaves, each reference line that stands in its
  index leaf, moves into the index leaf a period page's reference line to a sibling period, and takes each
  other shared fact line to the home a decisions file names (`--decisions FILE`: per subject and line
  digest, the leaf that keeps it or `both`, with the reason); a `both` is appended once to the judged
  ledger. A leaf left with nothing but its title, description, headings, `## Links` and `## Update
  record`, each of whose lines stands in the index leaf, is archived with the index leaf as its successor
  through the move tool. It runs as one checked plan under the writer lock, the ledger rows inside the undo
  journal: each removed line must stand in its home, and apart from the removed lines, the free sections
  they empty and the one record line naming where they are kept, no byte of a changed page differs. `plan`
  prints the same run without writing, with every line still waiting for a reading by subject, digest and
  line; `check` exits 1 while a run would change something or a line waits.
- **`forest registry build | find`** compiles the registry and finds a subject or a collection entry by any
  name or spelling.
- **`forest check [NAME …]`** runs every registered check, or the ones it names: the structure of the
  pages, the derived lists against the pages, the pointers, the agents' lanes and ids, the levels due and
  the categories over their limit. It prints one line per problem with its code and its path and exits 1
  when there is one. `forest check --sync` first brings the derived lists up to the pages on disk under
  the writer lock, then runs the same checks. The derived lists are a board's `## Leaves` with their dates
  and its `## Pointers`, a tree page's and an index's `## Branches`, and the root index's `## Trees` and
  `## Agents`. A sync keeps a line's hand-written description, makes its `(updated …)` date follow the
  page, drops a line whose page is gone and a second line for the same page, and adds a missing line with
  a description taken from the page. A forest that `forest check` calls clean has no lint hit of the codes
  the two share. **`forest lint`** checks the rules (section 8).
- **`forest timeline`**, **`forest search`**, **`forest read`** and **`forest mcp`** find things (design,
  sections 9 and 10).
- **`forest stage`** supplies the stages' input, cursors, receipts and commit steps (design, section 11).
  When a stage runs model work over many pages (rewrites, split plans, tags, board summaries), each result
  is checked by code before it is written through the move tool or the locked edit, each with its
  `## Update record` line. A merge proves that it kept every fact of the pages it merged.

## 10. Who does what

- **The daily stage** writes `05 daily` only. It writes a notice (section 3) as `<agent> notified` at the
  time it was sent, never as a reply, and an agent's reply as that agent's reply beside the message it
  answers.
- **The filing run** files each subject of its interval into the forest:
  1. find the subject through the registry (`forest registry find "<name>"`, which also finds collection
     entries) and search;
  2. update its leaf in place, or its entry on a collection page, or create a leaf for a new sub-topic in
     the subject's home, writing an episode as its section of the sub-topic's leaf (section 4);
  3. for a name the registry does not know, add an entry to the collection of its category in its home
     tree, the tree the placement rules give it; a `standing` line makes it a leaf subject at once, with
     its registry line and its leaf;
  4. set `kind:` and `about:` on every leaf it writes, and split a leaf it pushes over the cap in the same
     run (section 4);
  5. keep boards, `## Leaves` lists and current-state lines true for what it touched, carrying up only
     what section 5 allows;
  6. after its commit, run `forest levels apply --subjects` with the subjects it filed, and merge with the
     move tool any two ids it found to name one subject.

  It never writes machine pages. Under `agent-first` it files what an agent learned into that agent's
  tree (section 3, Agent trees).
- **The upkeep run** runs every night:
  - `forest levels apply` for the whole forest, and the lint's other due work: category splits, stale
    current-state lines;
  - sub-topic splits and merges (section 3), whatever the size;
  - registry upkeep: merged duplicates, new aliases, ended subjects;
  - under `agent-first`, moving shared facts to the shared subject's home (section 3, Agent trees);
  - a daily pass over the lint's upkeep list, the most-read pages first: remarks and disclaimers rewritten
    as open points; recorder dumps written as final text or as what was watched; leaves over the cap split
    by sub-topic; copies replaced by reference lines; the specifics that a filed daily section's leaves
    lack filed from that section, quoted turns verbatim; boards summarised again by kind from their leaves;
  - record trims, and the daily totals;
  - a retrieval evaluation, fixing what made a question miss.
- **Search** reads the registry's aliases, so a name in any spelling finds its subject or collection entry,
  and a question that names a subject also reaches the leaves that name it in `about:`. It answers from
  the forest first; the daily record serves chronology and what is not filed yet, and the raw lane serves
  provenance. It guesses the likely tree and branch before treating memory as one pool, prefers a concrete
  leaf for a concrete question and the upper pages for a broad current-state one, and keeps one long page
  from crowding out the rest. It answers each reader within its scope (section 3, Agent trees).

## 11. Changing the contract

Change this page, the tools and the stage prompts together, in one change. In this repository a change of
logic is listed in `CHANGELOG.md`. A forest that adapts the contract for its own use keeps the same
discipline and, for each change of logic, records in its own copy whose decision it follows, with their
words and the date.

## Changes

- **1.0.0 (unreleased).** Version 1 introduces this contract. It replaces the 0.3 layer contracts and the
  promotion plan with: the subject as the unit; a registry line per subject; levels set by code from
  distinct days; collections for subjects seen on one day; the 8 KB content cap with splits by sub-topic
  and by period; `kind:` from a fixed list and `about:` pointers; one home for each fact with reference
  lines; what goes up; the update record with its trim and archive; the move log; the lint with codes and
  baselines; notices, episodes, open points, "do not record" and number masking; and agent trees with
  two filing policies, reader scopes and a single writer.
