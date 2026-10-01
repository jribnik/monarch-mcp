# Monarch MCP Server

A local [Model Context Protocol](https://modelcontextprotocol.io) server that lets
Claude read and edit your [Monarch Money](https://www.monarchmoney.com) account
conversationally — find uncategorized transactions, recategorize, tag, review
budgets, etc. Built to replace Monarch's own MCP server, which has been offline.

Built entirely on [`monarch_client`](monarch_client/) — a self-contained client
against Monarch's real GraphQL API, using plain HTTP + vendored query text
captured from the real web app by [api-recon](https://github.com/jribnik/api-recon).
**41 tools: 11 read-side (including the write-adjacent `preview_transaction_rule`
dry-run) and 30 write**. The write tools were captured and verified live against a
dedicated, disposable `monarch-sandbox` account before ever touching the real one;
**that account has since been deleted** (see "Adding or changing a write" below for
what that means for new write work). See `monarch_client/__init__.py`'s module
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
.venv/bin/pip install pytest pytest-asyncio   # only to run the tests
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

`MONARCH_CLIENT_SITE` selects which api-recon site's session `monarch_client`
reads (default `monarch`, the real account). It exists so a disposable test
account could be targeted; the old `monarch-sandbox` site was deleted, so any
other value only works if you have re-registered such a site in api-recon.
`doctor` and every `load()` call print which site is in use, precisely so
a mix-up against the real account can't go unnoticed. (`doctor` prints the
account id but deliberately never the email: its output is meant to be
pasteable into chat.)

## Register with Claude Code

```bash
claude mcp add monarch -- ~/src/monarch-mcp/.venv/bin/python ~/src/monarch-mcp/server.py
```

Writes are **off by default**. To let the server change your account, set
`MONARCH_CLIENT_ENABLE_WRITES=1` in the MCP server's environment (this makes
all 30 write tools live at once, so see "Security" for the per-call confirm
gates on the destructive ones), e.g.:

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
| `update_transaction` | **write** | edit merchant/amount/date/notes/flags; `reviewed=True` = Mark as reviewed |
| `set_transaction_tags` | **write** | replace a transaction's tags |
| `create_tag` | **write** | create a new tag |
| `delete_tag` | **write, destructive** | delete a tag; needs `confirm` = the tag's name |
| `create_transaction_rule` | **write** | create an auto-categorization rule (incl. renaming merchants); `apply_to_existing_transactions=True` needs `confirm=str(count)` of a fresh preview |
| `delete_transaction_rule` | **write, destructive** | delete a rule; needs `confirm` = the rule id |
| `mark_stream_as_not_recurring` | **write, destructive** | dismiss a recurring stream; needs `confirm` = its merchant name |
| `create_transaction` | **write** | create a manual transaction; manual accounts only (fail-closed: anything not positively identified as manual is refused) |
| `delete_transaction` | **write, destructive** | delete a transaction (bank-synced too); needs `confirm` = its merchant name (id if none) |
| `create_manual_account` | **write** | create a manual (non-bank-linked) account |
| `create_category_group` / `update_category_group` / `delete_category_group` | **write** (delete is destructive) | category group CRUD (delete needs `confirm` = the group's name, and an empty group or `move_to_group_id`) |
| `create_category` / `update_category` / `delete_category` | **write** | category CRUD (delete needs `move_to_category_id` or explicit `uncategorize_transactions=True`) |
| `update_tag` | **write** | rename / recolor a tag |
| `update_merchant` | **write** | rename, default category, recurring stream (reads current values first, unset args keep them; renaming onto an existing merchant is rejected by Monarch -- merge via `update_transaction` instead) |
| `update_account` / `delete_account` | **write** (delete is destructive) | edit a manual account; delete needs `confirm_name`; both refuse anything not positively identified as manual |
| `set_budget_amount` / `set_flex_budget_amount` | **write** | planned amounts; `apply_to_future` defaults False, and `True` needs `confirm` (category name / the month) |
| `create_savings_goal` / `update_savings_goal` / `set_savings_goal_budget_amount` / `delete_savings_goal` | **write** (delete is destructive) | savings goals; create returns the new goal plus `followUpError` if applying the target fails; delete needs `confirm` = the goal's name; `set_savings_goal_budget_amount(apply_to_future=True)` needs `confirm` = the goal's name |
| `split_transaction` / `unsplit_transaction` | **write** | split must sum exactly to the parent amount |
| `update_transaction_rule` | **write** | full-rule update with merge; adds tag/hide/review/percentage-split actions |

## Security

- **Writes are gated, in layers.**
  1. *Master switch.* With `MONARCH_CLIENT_ENABLE_WRITES` unset (or anything
     other than `1`), **all 30 write tools** raise `MonarchWriteBlocked` before
     sending anything. Reads and the dry-run `preview_transaction_rule` are
     unaffected. A second check inside `writes._call`, and a third in
     `MonarchClient.call`, refuses any vendored mutation while the gate is
     closed, so a future write that forgets the per-tool check still can't
     fire. `python -m monarch_client.doctor` prints whether the gate is open.
     It is all-or-nothing (not an allowlist): with it open, every write tool
     touches the real, bank-synced account.
  2. *Confirm gates.* Because that switch is usually left open, the **7
     destructive tools** each require a `confirm` argument that must exactly
     echo (case-sensitive) the target as returned by a **fresh read made inside
     the tool**; a wrong, stale or missing value, or an unknown id, is refused
     before any mutation is sent:

     | Tool | `confirm` must equal |
     |------|----------------------|
     | `delete_transaction` | the transaction's merchant name (its id if it has none) |
     | `delete_tag` | the tag's name |
     | `delete_transaction_rule` | the rule's id (rules have no name) |
     | `delete_category_group` | the group's name |
     | `delete_savings_goal` | the goal's name |
     | `mark_stream_as_not_recurring` | the stream's merchant name (or stream name) |
     | `delete_account` (`confirm_name`) | the account's name |

     Two flags with a wide blast radius need an explicit `confirm` too (and
     only when set): `set_budget_amount` / `set_savings_goal_budget_amount`
     with `apply_to_future=True` (the category / goal name),
     `set_flex_budget_amount(apply_to_future=True)` (the month), and
     `create_transaction_rule(apply_to_existing_transactions=True)` (the number
     of existing transactions the rule will change, from a fresh preview).
     `delete_category` keeps its own explicit `move_to_category_id` /
     `uncategorize_transactions=True` choice.
  3. *Manual-account guard.* `create_transaction`, `update_account` and
     `delete_account` act only on accounts positively identified as manual
     (`isManual` true, no credential, no data provider), failing closed on
     anything else -- including a bank-synced account whose credential was
     detached.
  4. *Name validation.* Rule `set_merchant_name` must exactly match an
     existing merchant (Monarch itself would silently create a new one).
  Not gated yet: `update_transaction_rule(apply_to_existing_transactions=True)`.
- **No password ever seen or stored.** Auth comes entirely from an
  api-recon-exported session (cookies plus a few headers, in a plain JSON
  file); this server never has a password-login path of its own.
- **Secrets stay out of the repo.** The auth file lives under
  `~/.monarch-mcp` (`0700`), never in this directory.
- **Real backend, real account.** There is no synthetic sandbox for reads or
  writes any more: the disposable `monarch-sandbox` account the write tools were
  originally developed against has been deleted.

## Adding or changing a write

With the sandbox gone there is no free place to try a new mutation. Follow
`monarch_client/operations/README.md` ("Adding a write operation (no
sandbox)"): re-register a disposable account in api-recon, or test on a
throwaway object against the real account with explicit approval. A new
destructive tool must get a fresh-read `confirm` gate, a keyword-argument
wrapper in `server.py`, and entries in `tests/test_server_tools.py`'s expected
tool sets.

## Tests

```bash
HOME=$(mktemp -d) MONARCH_CLIENT_HOME=$(mktemp -d) API_RECON_HOME=$(mktemp -d) \
    .venv/bin/python -m pytest
```

Hermetic (fakes and `httpx.MockTransport`, no network, no auth file); the
throwaway `HOME`/`MONARCH_CLIENT_HOME` are belt-and-braces so a test can never
touch the real `~/.monarch-mcp`. CI runs the same suite (`.github/workflows/test.yml`).

## Layout

```
server.py            FastMCP server (the 41 tools above)
monarch_client/      the client -- see monarch_client/__init__.py
  operations/        vendored GraphQL text + PROVENANCE (see its README.md)
  tests/             hermetic pytest suite
contrib/             HISTORICAL drafts of upstream PRs for the abandoned
                     monarchmoney-enhanced library; not active (each file is marked)
.venv/               python3.11 environment
```
