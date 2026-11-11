> Archived from 0.3. Version 1 replaces it with the `05 daily` and `06 istm` lanes of [the forest contract](../contract.md) (section 1) and the pipeline of [the v1 design](../v1-design.md) (section 11).

# Daily and ISTM companion projects

Memory Forest defines what the `05 daily` and `06 istm` layers own, but the
core CLI deliberately does not collect conversations, read Apple application
data, or compact a private source stream. Those jobs belong to small,
source-specific companion projects.

## Companion boundaries

| Project | Owns | Does not own |
|---|---|---|
| [Codex Context for ISTM](https://github.com/hyungchulc/codex-context-for-istm) | macOS-only local Codex session ingestion, bounded Daily digests, launchd examples | Mail, Calendar, Reminders, Notification Center |
| [Mac Context for ISTM](https://github.com/hyungchulc/mac-context-for-istm) | macOS-only local Apple app snapshots and event retrieval | Codex conversations, Memory Forest promotion |
| [Memory Retrieval Lab](https://github.com/hyungchulc/memory-retrieval-lab) | synthetic retrieval evaluation, metrics, ranker adapters | canonical memory storage or private source collection |

Each project keeps private runtime data outside Git. None of them publishes a
real forest, session transcript, mailbox, notification database, reminder, or
calendar event.

## Bounded Daily contract

A long-running Daily lane needs a growth boundary as well as a provenance
boundary. The public companion implementation follows these invariants.

1. Admit only new source records with stable identifiers and hashes.
2. Render a bounded causal digest instead of copying every raw payload.
3. Keep a deterministic compact manifest that can locate every admitted source
   record in ISTM.
4. Treat a missing, malformed, duplicated, or partially written manifest as a
   hard failure.
5. Advance the source cursor only after the Daily block and its manifest verify.
6. Archive or prune older data only after the replacement and its restore
   evidence verify.
7. Keep source-specific retention limits configurable. A public example must
   not silently copy one operator's private thresholds.

These rules preserve recoverability without making an unbounded Markdown file
the second raw archive.

## Model-processing boundary

Collection, hashing, cursor commits, validation, and deterministic retrieval
baselines work without a model. An integrator that adds model-written summaries
must document its own provider, model, retention, review, and data-processing
boundaries. No named model or reasoning profile is part of the portable
contract.

## macOS scope

The two ISTM collectors are macOS projects. They use local Codex session files
or Apple application data and launchd examples. Apple data access can require
Automation permission or Full Disk Access, and Notification Center storage is
an undocumented best-effort surface that can change between macOS releases.

Memory Forest itself remains portable across supported POSIX systems. The
companion repositories do not change the core filesystem or retrieval schema.
