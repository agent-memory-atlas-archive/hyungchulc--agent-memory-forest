# The routine: how the forest grows when you keep it

You read this because the owner chose you as the forest's keeper. You run three passes: the daily record,
the filing run and the upkeep run (the contract, section 10). Each pass ends with the commands that judge
it. In the examples the forest is `studio-forest` and you are `studio-hub`, the owner's main agent;
replace both with what setup printed for you.

**One keeper writes at a time.** Never run two passes at once, and never write while another writer works:
a command that finds the writer lock held refuses with `lock_busy`; wait and run it again.

**Which model for which pass** (advice; the owner's answers at setup decide):

- Bringing turns in needs no model: it is the intake command setup printed.
- The daily record is fine on the cheapest model the owner has.
- Filing and upkeep are best on the strongest model; the middle tier works.

Use the model the owner named for each pass, and run each pass when the owner answered at setup.

## 1. The daily record

The daily record is `05 daily/<YYYY-MM-DD>.md`, one page per day in the forest's time zone. It keeps what
was said close to the original and turns work into short episodes, with pointers into the raw lanes.

1. Find the turns of the day across every lane:

   ```sh
   forest timeline query "yesterday" --as studio-hub --root studio-forest
   forest timeline query "2042-05-13..2042-05-14" --as studio-hub --root studio-forest
   ```

2. Read a lane's lines where you need them whole:

   ```sh
   forest read "06 istm/kiln-bot/2042-05-13.jsonl" --as studio-hub --root studio-forest
   ```

3. Append one block per agent and stretch of time to the day's page (create it if it is missing). Quote the
   owner and other people word for word; write work, tool use and results as short causal episodes; keep
   every failure and change; never copy what a tool printed; take out secrets. Each block opens with its
   marker line and ends its episodes with the raw lines it came from.
4. Judge the pass, then bring the search index up to the pages you wrote, so that every agent finds them:

   ```sh
   forest check --sync --root studio-forest
   forest lint --root studio-forest
   forest index build --root studio-forest
   ```

A minimal daily block:

```markdown
<!-- block: kiln-bot 2042-05-13T07:40 -->

## 07:40–19:10 kiln-bot — glaze firing on Kiln Two

Kiln Bot started program G6-slow on Kiln Two at 07:40, 38 pieces. Cone 6 was reached at 18:55 and the
firing ended at 19:10. Raw: `06 istm/kiln-bot/2042-05-13.jsonl`, lines 1–3.
```

## 2. The filing run

The filing run files each subject of the day's record into the forest's pages.

1. Find each subject the day names, by name or alias, and where it lives:

   ```sh
   forest registry show "Kiln Two" --root studio-forest
   forest search "kiln two" --as studio-hub --root studio-forest
   ```

2. Update its leaf in place, or its entry on a collection page, or write a new leaf for a new sub-topic in
   the subject's home. A name the registry does not know becomes an entry on the collection page of its
   category in its home tree.
3. Set `kind:` and `about:` on every leaf you write, keep each leaf under the cap, and keep the boards'
   `## Leaves` lists and current-state lines true for what you touched. Carry up only what the contract,
   section 5 allows: what is established, not the newest thing said.
4. Judge the pass, let code settle the levels of what you filed, and bring the search index up to date:

   ```sh
   forest check --sync --root studio-forest
   forest lint --root studio-forest
   forest levels apply --subjects kiln-two --root studio-forest
   forest index build --root studio-forest
   ```

A minimal leaf:

```markdown
---
entity: kiln-two
subtopic: repairs
kind: event
about: none
timezone: America/Halifax
updated_at: 2042-05-09
---

# Kiln Two repairs

## 2042-05-06 Element fault

The kiln stopped at 940 °C during a bisque firing; Juno found two of the four elements broken.

## Timeline

- 2042-05-06 14:20 America/Halifax — Element fault: firing stopped at 940 °C; two elements broken.

## Links

- up: [[03 mtm/studio/kiln-two|kiln-two MTM]]

## Update record
```

## 3. The upkeep run

The upkeep run keeps the whole forest in shape, as often as the owner answered.

1. Settle every level, and see what the lint lists as due and as upkeep:

   ```sh
   forest levels apply --root studio-forest
   forest lint --root studio-forest
   ```

2. Work the lint's upkeep list, the most-read pages first: rewrite remarks as open points, split leaves over
   the cap by sub-topic, replace copies with reference lines, and summarise boards again from their leaves.
   Move and merge only through the move tool:

   ```sh
   forest move merge kiln-two kiln-2 --dry-run --root studio-forest
   forest collections list --root studio-forest
   forest record trim --all --root studio-forest
   forest repeats check --root studio-forest
   ```

3. Judge the pass and bring the search index up to date:

   ```sh
   forest check --sync --root studio-forest
   forest lint --root studio-forest
   forest levels apply --root studio-forest
   forest index build --root studio-forest
   ```

## Make it run by itself

The package ships no engine and installs no timer. You build the automation yourself, with what your own
environment has. It has to make these true:

- the daily record is written at least once on every day that has new turns;
- the filing run follows the daily record;
- the upkeep run runs as often as the owner answered at setup.

How to get there:

1. If your own tool has scheduled tasks or automations, use them: one task per pass, at the times the owner
   answered, each starting you with this routine's pass as its task.
2. Otherwise use the operating system's scheduler (cron, launchd or a systemd timer) to start your own
   command line with the pass as its task. One line, with placeholders for your own command (fill in the
   command your tool runs non-interactively; this guide names no program):

   ```text
   30 23 * * *  cd <forest folder> && <your agent command> "<the daily pass, then the filing pass, of forest guide routine>"
   ```

3. A scheduled session is memory work, not a conversation: open its prompt with the line
   `MEMORY_FOREST_INTERNAL`, so that it is never brought in as a conversation.
4. Keep within what the owner allowed: one run of each pass a day unless the owner said otherwise, and the
   model the owner named for each pass.

Then tell the owner what you scheduled and how to turn it off. From then on the doctor shows whether the
daily record keeps up:

```sh
forest doctor --as studio-hub --root studio-forest
```
