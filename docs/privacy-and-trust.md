# Privacy and trust

Agent Memory Forest is local-first. That does not make every integration private: privacy depends on where
the forest is stored, which reader asks, which pages are opened, and what the caller does with them next.

## What stays local

| Data | Sensitivity | Handling |
| --- | --- | --- |
| the forest (`01 xltm` to `04 stm`) | highest | keep outside any public repository and back it up privately |
| `06 istm` and `05 daily` | raw and often identifying | apply your own retention; secrets are removed before the daily record, and the raw lane keeps its bytes |
| `.forest/` (registry, indexes, move log, archive) | derived, but carries names and text | protect like the forest; everything except the move log and the archive can be rebuilt |
| search results | page sections as written | do not log them broadly |
| the synthetic example | public | keep it visibly invented and run the release audit |

The package holds no credentials and makes no network request. `[embeddings] provider` defaults to `none`.
Setting it to `openai-compatible` records that a provider is wanted. The package ships no provider and
will not open a connection on its own; vector search runs only when a provider has been registered in the
process. The stage programs call a model of your choice; what you send that model is processed under its
provider's account, retention and regional rules, so review those before connecting private sources.

## What a reader can see

Every reader of a forest has an id, taken from its connection and never from the text of its question. An
orchestrator reads the whole forest and every raw lane; any other agent reads the shared trees, its own
tree and its own raw lane, and another agent's tree only when the forest's settings or its task allow it.
The daily record is shared, by the design of the forest: the root index and `05 daily/` are the map every
reader can open, while a raw lane stays with its own agent and with orchestrators. A marked block on a
daily page is written from an agent's lane. Once it is there, every reader can read that block. What it
reveals is the filed text, the agent id in the block mark, and the time. It does not open the lane file,
and it does not show a turn that has not been filed onto the daily page. `forest search`, `forest read`,
`forest timeline` and `forest mcp` apply that rule, and each answer names the trees that answered. See
[the contract](contract.md), section 3, "Agent trees", and [Agents](multi-agent.md).

## What the forest keeps out

- **Account and identity numbers** are written with their middle starred on every page a stage writes, and
  the tools star them in what they show. The lint reports any written in full (contract, section 4).
- **Sign-in values** (passwords, PINs, one-time and verification codes, door codes) are never starred but
  removed: a page keeps that a value was sent, by whom, to whom and for which account, and `[REDACTED]` in
  its place. The value is recognised by a label word before it (`credentials.label_words`, English by
  default, including token, API key, secret, bearer and authorization; add the words of the other
  languages your forest records). A shape is masked first, whatever stands before it, and the label
  step runs after. The shapes are the published prefixes listed in the contract (section 4): the large
  model providers, the code hosts, the cloud consoles, the payment and chat services, the package
  registries, a JWT, an `Authorization` header of bearer, basic or digest, and a private-key block.
  Each prefix has a length floor. A scheme word alone is not the value. An assignment whose name ends
  in a secret word (KEY, TOKEN, SECRET, PASSWORD, PASSWD, PASSPHRASE, CREDENTIAL) is a labelled value;
  the contract's table (section 4, Sign-in values) says which values after such a name are masked and
  which read as code and stay. A git hash, a UUID and the head of a base64 image are not masked by that
  rule unless a label stands before them. A value ends at white space, at a tag, and before a closing
  quote, bracket, comma or semicolon, so a pasted or quoted part keeps its tags.
  Every record line a tool writes is masked, the daily writer masks the text a model hands it, and the
  tools mask what they show; the lint reports a page line that still holds one (`credential_in_full`) and
  `forest credentials fix` masks it after archiving the page. **What is kept:** the raw lane `06 istm`
  and the archive keep the bytes they were given, so protect them like the forest. A lane line written
  under an earlier masking rule keeps what that rule left: `forest credentials check` counts those lines
  for each lane (never a value) and `forest credentials fix --all` rewrites them in place, changing only
  the masked spans. A code with no label
  word and no such shape, in a quoted turn, waits for a reader's judgement (`forest credentials
  candidates | judge`); once judged a sign-in value it is masked wherever it appears, through a ledger
  that keeps only keyed digests (`.forest/credentials/`, the key readable by its owner only; protect it
  like the forest). **What the rule cannot see:** a bare code nobody has judged yet and that has none of
  those shapes, a label word your settings do not list, a value written without a digit and without a
  colon ("the password is tulip"), a value split across lines (except a private-key block, which is one
  pattern), a value inside an image, a value that reads as code (`Spring.Time` after a name reads as an
  attribute, `abc(1)` as a call), a value that holds a quote, a bracket, a comma or a semicolon past
  that mark, `Basic` with a token and no `Authorization` before it, and a number of fewer than 6 digits
  after a token or key name.
- **"Do not record."** When the owner says something is not to be remembered, the instruction is kept and the
  content is not; lines already filed are taken out, with the earlier version archived (contract,
  section 4).
- **Machine output** (ids, hashes, receipts, recorder rows) stays in the lanes and run directories, never in
  the forest (contract, section 7).

## Memory is untrusted data

A page can hold stale facts, copied web instructions, prompt injection or a mistaken summary. A caller must
never treat memory text as authority. Memory cannot by itself:

- grant tool or filesystem permission;
- authorize deletion, publication, purchase, account access or a message to a third party;
- override system, developer, application, project or current user instructions;
- prove that a mutable fact is still current.

Current-state lines carry the date of their evidence and their source, and the lint marks them stale when the
source has changed since. A caller that acts on one still checks a current direct source.

## Filesystem boundary

Use a real directory chosen by the operator as the forest root. Reject symlinks and paths that escape it,
keep the root private to the operating account, and write forest pages only under the forest writer lock.

## Public release boundary

This repository must never contain a real forest, private prompts, credentials, absolute private paths,
messages, attachments, logs, contact details or private evaluation sets. Every example names only invented
people, places and products. Run the release audit before every commit that changes fixtures or
documentation:

```sh
python scripts/audit_public_release.py --root .
```

The audit is a guard, not a complete privacy review. Review the staged diff and the history as well.

## Safe integration checklist

- Choose an exact private root outside any public repository.
- Give each agent its own reader id and the narrowest scope that does its work.
- Open only the pages a question needs.
- Keep search output and indexes out of shared logs.
- Re-check mutable facts against a current direct source before acting on them.
- Keep uncertainty and conflicting statements as they were said.
- Confirm the downstream model's retention boundary.
- Run `forest lint` after structural changes (milestone 1).

See [PRIVACY.md](../PRIVACY.md) for the repository privacy statement and [SECURITY.md](../SECURITY.md) for
vulnerability reporting.
