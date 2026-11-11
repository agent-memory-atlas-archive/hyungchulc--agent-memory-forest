# XLTM

The root index of Juno's forest: who Juno is, the trees, the agents.

## Identity

- Name: Juno Hale; memory text calls her Juno.
- Work: runs Brindlemoor Clayworks, a two-person pottery studio; its matters are in `studio`.
- Home: Saltmere; her own admin, accounts and shopping are in `admin`.
- Languages: English.

## Trees

- `studio` — Brindlemoor Clayworks: its equipment, its making and firing, its commissions and customers.
- `people` — the people Juno knows for themselves, and the groups she meets them in.
- `admin` — Juno herself, her accounts, money, calendars and the shops she buys from.

Placement rules:

1. A place where Juno buys things is a shop, in `admin` under `shops`, whatever it sold.
2. Equipment the studio uses belongs to `studio`; the same kind of device at home belongs to `admin`.
3. A person Juno knows for themself belongs to `people`; a company she deals with only as a customer
   belongs to `studio`.

## Agents

- `studio-hub` — Studio Hub · orchestrator: talks with Juno, hands work to the bots, reads the whole forest
- `kiln-bot` — Kiln Bot · runs and watches the kiln firings
- `ledger-bot` — Ledger Bot · keeps the studio's books and sends its invoices

## Update record

- 2042-01-20 — `kiln-bot` and `ledger-bot` added under Agents: was "studio-hub only", now three agents. Why: Juno set up the two bots. Source: `02 ltm/kiln-bot_LTM.md`, `02 ltm/ledger-bot_LTM.md`.
