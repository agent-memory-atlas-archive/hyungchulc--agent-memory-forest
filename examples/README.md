# Examples

Everything in this directory is invented and safe to publish. It shows structure, not real knowledge.

## Synthetic forest (version 1)

[synthetic-forest](synthetic-forest/README.md) is a small invented forest that follows the
[version 1 contract](../docs/contract.md): an owner who runs a pottery studio, three shared trees, an
orchestrator and two bots each with a tree of its own, a collection page, a leaf subject, a branch subject
with four sub-topics, an event list split by period, a changed fact with its update record, two days of
daily pages and a raw lane per agent. It is the same invented world the [concepts](../docs/concepts.md)
and [agents](../docs/multi-agent.md) guides use.

It is a reference to read. The checker for its shapes, `forest lint`, arrives with milestone 1; the 0.3
`memory-forest` command does not read version 1 forests. Keep any new example visibly invented and run the
release audit before committing it.

## 0.3 examples

The two examples below belong to the 0.3 `memory-forest` command, which stays in this tree until the
version 1 commands replace it. Their documentation is in [`docs/archive-0.3/`](../docs/archive-0.3/README.md).

- [automation](automation/README.md) contains a lock-protected maintenance wrapper, a POSIX crontab
  example, a macOS LaunchAgent template and a bounded Codex Scheduled Task prompt for 0.3. They validate
  and rebuild private derived state; they do not capture or file memory.
- [memory-forest-retrieve](memory-forest-retrieve/README.md) contains a metadata-only turn gate and an
  advisory system prompt for hosts that require a bounded memory consultation before each user turn.
