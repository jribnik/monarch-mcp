# Monarch MCP Server

A local [Model Context Protocol](https://modelcontextprotocol.io) server that lets
Claude read and edit your [Monarch Money](https://www.monarchmoney.com) account
conversationally — find uncategorized transactions, recategorize, tag, review
budgets, etc. Built to replace Monarch's own MCP server, which has been offline.

Built entirely on [`monarch_client`](monarch_client/) — a self-contained client
against Monarch's real GraphQL API, using plain HTTP + vendored query text
captured from the real web app by [api-recon](https://github.com/jribnik/api-recon).
**Full coverage: 10 read tools and 12 write tools**, all captured
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
and the `monarchmoney-enhanced`/`gql` dependencies were removed in the same
pass — `_audit/` (a shallow clone of the audited commit) is left on disk,
gitignored, purely as historical reference.

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

1. **Check `monarch_client.doctor`** (below) — it compares each vendored
   operation's recorded query hash against api-recon's live catalog and
   flags anything that's drifted.
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

Reads the current auth file, makes one live smoke call, and flags any
vendored operation whose text has drifted from api-recon's current catalog
(skipping mutations the nightly walk can never re-observe — see
`monarch_client/operations/__init__.py`'s `walk_reachable` note).
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

## Security

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
_audit/           shallow clone of the formerly-used library, at its audited
                   commit -- historical reference only, gitignored, not a
                   runtime dependency
.venv/            python3.11 environment
```
