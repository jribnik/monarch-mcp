# Monarch MCP Server

A local [Model Context Protocol](https://modelcontextprotocol.io) server that lets
Claude read and edit your [Monarch Money](https://www.monarchmoney.com) account
conversationally — find uncategorized transactions, recategorize, tag, review
budgets, etc. Built to replace Monarch's own MCP server, which has been offline.

Built entirely on [`monarch_client`](monarch_client/) — a self-contained client
against Monarch's real GraphQL API, using plain HTTP + vendored query text
captured from the real web app by [api-recon](https://github.com/jribnik/api-recon).
**Full coverage: 41 tools (11 read, including one write-adjacent dry-run, and 30 write)**, all captured
and/or verified against a dedicated, disposable `monarch-sandbox` account
before ever touching the real one. See `monarch_client/__init__.py`'s module
docstring for the package's full shape.

**Migration history:** this server originally wrapped the abandoned
[`monarchmoney-enhanced`](https://github.com/keithah/monarchmoney-enhanced)
library (pinned to an audited commit) while `monarch_client` was built and
proven out via a dual-backend soak (`MONARCH_MCP_BACKEND=dual`, comparing
both backends' shapes on every read, logged to `~/.monarch-mcp/parity.log`).
Flipped to `monarch_client` exclusively on 2026-09-28 after 3 days of clean
soak plus a field-by-field review of the parity log turned up nothing
concerning. The legacy dispatch layer, its `config.py`/`auth.py`/`diag_login.py`,
the `_audit/` clone (gitignored, so never in this repo's history), and the
`monarchmoney-enhanced`/`gql` dependencies were removed in the same pass. The
upstream code those comments cite lives at keithah/monarchmoney-enhanced@159d36e.

## Setup

```bash
cd ~/src/monarch-mcp
python3.11 -m venv .venv
.venv/bin/pip install "mcp[cli]<2" httpx
```

(`mcp[cli]<2` matters, not just style: a fresh `pip install "mcp[cli]"` today
resolves to mcp 2.x, which renamed `FastMCP` to `MCPServer` and changed other
APIs — `server.py` is written against the v1 API, same as `sleeper-mcp`'s and
`skylight-mcp`'s own `server.py`.)

**Auth** — this server never logs into Monarch itself. Instead it reads a
session exported from an already-completed api-recon Monarch login:

```bash
cd ~/src/api-recon && .venv/bin/recon login monarch        # once, interactive (MFA included)
.venv/bin/recon export-session monarch --api-host api.monarch.com \
    --out ~/.monarch-mcp/api-auth.monarch.json
```

A session lasts months (unlike Skylight's 24h token) — `doctor` (below) flags
the file if it looks stale, and always prints the exact command to fix it.

## When the API drifts (schema recovery)

Monarch's unofficial GraphQL schema changes without notice. Nightly
drift-watch (`~/src/api-recon/scripts/drift-watch.sh monarch`, a launchd job)
catches this automatically, scoped to only the operations `monarch_client`
actually vendors — see api-recon's DESIGN.md §6e. To investigate by hand:

1. **Run api-recon's drift check** — it reads this repo's vendored-op
   records (`python -m monarch_client.operations --provenance-json`) and
   compares each recorded query hash against api-recon's live catalog.
   (`monarch_client.doctor` no longer does this comparison itself; see below.)
2. **Re-capture via api-recon.** `recon walk monarch --headless && recon
   catalog monarch` rebuilds the catalog from real traffic; `recon
   export-ops monarch --op <Name> --out monarch_client/operations/` re-vendors
   one operation. See `monarch_client/operations/README.md` for the full
   checklist.
3. **A HAR capture is the ground truth** when a field's real current shape
   isn't obvious from the catalog alone: DevTools → Network → filter
   `graphql` → *Save all as HAR with content* against the live web app.

## Health check

```bash
.venv/bin/python -m monarch_client.doctor
```

Reads the current auth file, makes one live smoke call, and prints whether
the write gate is open. It does **not** check catalog drift: that comparison
lives in api-recon, which consumes
`python -m monarch_client.operations --provenance-json` (each vendored op's
name, path, recorded hash, and `walk_reachable` flag — the contract is
documented in `monarch_client/operations/__main__.py`).
`monarch_client` never shells out to `recon` itself — if auth looks stale,
the fix is the two commands above, run again.

Set `MONARCH_CLIENT_SITE=monarch-sandbox` to point `monarch_client` at the
disposable sandbox account instead, for any future write-path testing
(`doctor` and every `load()` call print which site they're using, precisely
so this can't happen by accident against the real account).

## Register with Claude Code

```bash
claude mcp add monarch -- ~/src/monarch-mcp/.venv/bin/python ~/src/monarch-mcp/server.py
```

Writes are **off by default**. To let the server change your account, set
`MONARCH_CLIENT_ENABLE_WRITES=1` in the MCP server's environment, e.g.:

```bash
claude mcp add monarch -e MONARCH_CLIENT_ENABLE_WRITES=1 -- ~/src/monarch-mcp/.venv/bin/python ~/src/monarch-mcp/server.py
```

Then restart Claude Code so it connects. Ask things like:
*"Show me uncategorized transactions from June"* → *"Recategorize these three as Groceries."*

## Tools

| Tool | Kind | What |
|------|------|------|
| `list_accounts` | read | accounts, balances, institutions |
| `get_categories` | read | categories + groups (for valid ids) |
| `get_tags` | read | tags (for valid ids) |
| `get_budgets` | read | budgets for a date range |
| `get_cashflow_summary` | read | income/expense totals |
| `get_transactions` | read | filtered transaction search |
| `get_transaction_details` | read | one transaction incl. splits |
| `get_transaction_rules` | read | auto-categorization rules |
| `preview_transaction_rule` | read (write-adjacent) | dry-run a rule's match set |
| `get_recurring_transactions` | read | recurring transaction streams |
| `get_account_type_options` | read | valid type/subtype pairs for create_manual_account |
| `recategorize_transaction` | **write** | set a transaction's category |
| `update_transaction` | **write** | edit merchant/amount/date/notes/flags |
| `set_transaction_tags` | **write** | replace a transaction's tags |
| `create_tag` | **write** | create a new tag |
| `delete_tag` | **write** | delete a tag by id |
| `create_transaction_rule` | **write** | create an auto-categorization rule (incl. renaming merchants) |
| `delete_transaction_rule` | **write** | delete a rule by id |
| `mark_stream_as_not_recurring` | **write** | dismiss a recurring stream |
| `create_transaction` | **write** | create a manual transaction |
| `delete_transaction` | **write** | delete a transaction by id |
| `create_manual_account` | **write** | create a manual (non-bank-linked) account |
| `create_category_group` / `update_category_group` / `delete_category_group` | **write** | category group CRUD (delete needs an empty group or `move_to_group_id`) |
| `create_category` / `update_category` / `delete_category` | **write** | category CRUD (delete needs `move_to_category_id` or explicit `uncategorize_transactions=True`) |
| `update_tag` | **write** | rename / recolor a tag |
| `update_merchant` | **write** | rename, default category, recurring stream (reads current values first, unset args keep them; renaming onto an existing merchant is rejected by Monarch -- merge via `update_transaction` instead) |
| `update_account` / `delete_account` | **write** | edit a manual account; delete needs `confirm_name`, refuses bank-linked |
| `set_budget_amount` / `set_flex_budget_amount` | **write** | planned amounts; `apply_to_future` defaults False |
| `create_savings_goal` / `update_savings_goal` / `set_savings_goal_budget_amount` / `delete_savings_goal` | **write** | savings goals |
| `split_transaction` / `unsplit_transaction` | **write** | split must sum exactly to the parent amount |
| `update_transaction_rule` | **write** | full-rule update with merge; adds tag/hide/review/percentage-split actions |

## Security

- **Writes are gated.** With `MONARCH_CLIENT_ENABLE_WRITES` unset (or anything
  other than `1`), all 11 write tools -- including the three deletes
  (`delete_transaction`, `delete_tag`, `delete_transaction_rule`) -- raise
  `MonarchWriteBlocked` before sending anything. Reads and the dry-run
  `preview_transaction_rule` are unaffected. A second check inside
  `writes._call` refuses any vendored mutation while the gate is closed, so
  a future write that forgets the per-tool check still can't fire. This is a
  single master switch, not an allowlist: unlike Sleeper/Skylight there is no
  sandbox league or single blessed list -- a write here touches the real,
  bank-synced account. `python -m monarch_client.doctor` prints whether the
  gate is open.
- **No password ever seen or stored.** Auth comes entirely from an
  api-recon-exported session (a plain cookie file); this server never has a
  password-login path of its own.
- **Secrets stay out of the repo.** The auth file lives under
  `~/.monarch-mcp` (`0700`), never in this directory.
- **Real backend, real account, no synthetic sandbox for reads.** Writes
  were developed and verified against a dedicated, disposable
  `monarch-sandbox` account (see api-recon's `adapters/__init__.py`) before
  ever touching the real one.

## Layout

```
server.py         FastMCP server (the tools above)
monarch_client/   the client -- see monarch_client/__init__.py
.venv/            python3.11 environment
```
