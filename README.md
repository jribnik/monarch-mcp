# Monarch MCP Server

A local [Model Context Protocol](https://modelcontextprotocol.io) server that lets
Claude read and edit your [Monarch Money](https://www.monarchmoney.com) account
conversationally — find uncategorized transactions, recategorize, tag, review
budgets, etc. Built to replace Monarch's own MCP server, which has been offline.

It wraps the unofficial GraphQL API via
[`monarchmoney-enhanced`](https://github.com/keithah/monarchmoney-enhanced),
**pinned to an audited commit** (see [Security](#security)).

## Setup

```bash
# One-time: create the venv and install pinned deps (already done in this repo)
python3.11 -m venv ~/src/monarch-mcp/.venv
~/src/monarch-mcp/.venv/bin/pip install \
  "git+https://github.com/keithah/monarchmoney-enhanced@159d36e7ea07dbdc4b3a28193d89ef5c2c36f548" \
  "mcp[cli]>=1.2.0,<2" \
  "gql<4"          # neither pin is optional: mcp 2.x renamed FastMCP -> MCPServer
                    # and changed the API (server.py is 1.x code); gql 4.x removed
                    # what the library's "enhanced" GraphQL path calls (see Compatibility)
```

### Compatibility fixes (in `config.py`, not edits to the library)

The pinned library predates some dependency changes; `config.py` papers over them
so the audited code stays untouched:

- **`gql<4`** — the library imports/constructs things the gql 4.x transport removed.
- **`connector` kwarg** — its "enhanced" GraphQL path calls
  `AIOHTTPTransport(connector=…)`, which no released gql accepts; a shim forwards it
  via `client_session_args` instead.
- **TLS verification forced on** — gql's `AIOHTTPTransport` defaults to *not*
  verifying certificates; the shim sets `ssl=True` so the token-bearing queries
  can't be MITM'd.
- **Email OTP** — Monarch verifies new logins with an emailed 6-digit code
  (`EMAIL_OTP_REQUIRED`), which the library surfaces as a bare 403; `auth.py`
  routes it into the code prompt.

## When the API drifts (schema recovery)

Monarch's unofficial GraphQL schema changes without notice, so library queries
break with a generic `"Something went wrong while processing"` error. Introspection
is **disabled** for non-admin tokens, so you can't just dump the schema. Recover it
with this ladder, cheapest first:

1. **Field-probe the live API.** Send a query selecting one candidate field and see
   whether it's accepted or rejected; the error's `locations: [{line, column}]`
   points straight at the offending field. Bisect a broken query this way to find
   exactly which field/arg the server no longer accepts.
2. **Write-then-read-back.** Create an object with a mutation whose input you know,
   then read it back — this reveals the *read* schema's field names and nesting
   (how we learned rules store criteria in `merchantNameCriteria` /
   `originalStatementCriteria`, not `merchantCriteria`).
3. **Capture a HAR (authoritative).** In the web app: DevTools → Network → filter
   `graphql` → click the relevant page → right-click → *Save all as HAR with
   content*. The captured request carries the exact operation name, query text,
   fields, and argument shape the app itself uses. This is the ground truth for
   what a dead field *became*.
4. **Verify the fix end-to-end.** Edit the vendored copy under `_audit/` and run it
   with `PYTHONPATH=_audit` against a live account — the venv installs the pinned
   package separately, so this is the only way to exercise your edits before
   sending them upstream.

## Log in (once)

Run the interactive login **yourself** in a terminal. Your password is sent only
to Monarch and is never stored or echoed:

```bash
~/src/monarch-mcp/.venv/bin/python ~/src/monarch-mcp/auth.py
```

This saves an **encrypted** session to `~/.monarch-mcp/session.mmsession` so the
server can reuse the login without your password. Re-run it whenever the session
expires (you'll see auth errors from the tools).

## Backends (monarch_client, experimental)

There are now two ways this server can talk to Monarch:

- **`library`** (default, unchanged) — `monarchmoney-enhanced`, as described
  above.
- **`client`** — [`monarch_client`](monarch_client/), a self-contained
  replacement built on plain HTTP + vendored query text captured from the
  real web app by [api-recon](https://github.com/jribnik/api-recon). Auth
  comes from api-recon's own encrypted browser session (`recon login
  monarch`), not from this repo's `auth.py`/`session.mmsession`. **Full
  coverage: all 9 read tools and all 8 write tools.** Every write op was
  captured and/or verified against a dedicated, disposable
  `monarch-sandbox` account (see api-recon's `adapters/__init__.py`) before
  it ever touched the real one — including a manual "Cash" test account
  with real test transactions (the sandbox starts with none) and, for
  `mark_stream_as_not_recurring`, a manually-forced recurring stream
  (Monarch's own detection is a backend batch job, not real-time, so
  waiting for it wasn't practical — the Recurring page's "mark this
  merchant as recurring" override was used instead). Two of the eight write
  ops (`delete_transaction_rule`, `mark_stream_as_not_recurring`) were
  verified by direct functional call rather than a UI-driven HAR capture,
  each documented as an explicit exception in its own `.graphql` file
  under `monarch_client/operations/`. Set `MONARCH_CLIENT_SITE=monarch-sandbox`
  to point `monarch_client` itself at that account for any future testing
  (`doctor` and every auth refresh print which site they're using,
  precisely so this can't happen by accident — it did, once, before this
  existed).

Backend selection is per-tool, via environment variables read at server
startup:

```bash
# Global default for every tool (unset = "library").
export MONARCH_MCP_BACKEND=library   # or "client", or "dual"

# Force specific tools onto one backend regardless of the global default.
# A tool named in both lists uses "library" (the safer fallback).
export MONARCH_MCP_CLIENT_TOOLS=get_tags,get_categories
export MONARCH_MCP_LIBRARY_TOOLS=get_transactions
```

`dual` is a **read-only, diagnostic** mode: it calls both backends, always
returns the library's result (so nothing changes for Claude), and appends a
structure-only diff (key names and value *types*, never values) to
`~/.monarch-mcp/parity.log` whenever the two disagree. Use it to build
confidence in a tool before flipping it to `client` for real.

**Flipping a tool:** set `MONARCH_MCP_CLIENT_TOOLS` to include it and restart
the MCP server (`claude mcp` reconnects on Claude Code restart, or restart
the process directly). **Rolling back:** unset it, or add the tool to
`MONARCH_MCP_LIBRARY_TOOLS`, and restart — the change takes effect
immediately, no re-login or state cleanup needed.

**When something on the `client` backend breaks:**

```bash
~/src/monarch-mcp/.venv/bin/python -m monarch_client.doctor
```

checks that `recon` is findable, that auth material loads (and refreshes it
with `--refresh-auth`), makes one live smoke call, and flags any vendored
operation whose text has drifted from api-recon's current catalog. If it
reports an auth failure, the fix is almost always:

```bash
~/src/api-recon/.venv/bin/recon login monarch
```

(a real, headful browser login — MFA included — completed by you; `client`
backend never has a password-login path of its own).

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
| `recategorize_transaction` | **write** | set a transaction's category |
| `update_transaction` | **write** | edit merchant/amount/date/notes/flags |
| `set_transaction_tags` | **write** | replace a transaction's tags |
| `create_tag` | **write** | create a new tag |

## Security

- **Audited dependency.** `monarchmoney-enhanced` was code-reviewed at commit
  `159d36e` before use: it contacts only Monarch's own domains
  (`api.monarchmoney.com`, `app.monarchmoney.com`); no `eval`/`exec`/`subprocess`/
  `socket`/`pickle`, no telemetry/phone-home, no install hooks, no import-time
  network calls; standard reputable deps (aiohttp, gql, cryptography, oathtool);
  the password is never logged. The install is **pinned to that commit**, not the
  unaudited PyPI build. The installed source is byte-identical to the audit.
  One transparent override (in `config.py`): the client is pointed at Monarch's
  current domains (`api.monarch.com` / `app.monarch.com`) because the library
  still hardcodes the retired `*.monarchmoney.com` hosts. Same first party, no
  new network surface — see the "Domain migration" note in `config.py`.
- **Strong session encryption.** Sessions are encrypted with a random 256-bit key
  (`~/.monarch-mcp/session.key`, `0600`), not the library's weak `$HOME`-derived
  default, and never saved as plain JSON. Override with `MONARCH_SESSION_KEY`.
- **Secrets stay out of the repo.** The key and encrypted session live under
  `~/.monarch-mcp` (`0700`), never in this directory. Credentials are never
  written to disk — only the resulting Monarch token, encrypted.

## Layout

```
config.py         key/session paths + strong-key management (library backend)
auth.py           one-time interactive login → encrypted session (library backend)
backend.py        per-tool library/client/dual selection -- see "Backends" above
server.py         FastMCP server (the tools above)
monarch_client/   experimental replacement backend -- see monarch_client/__init__.py
_audit/           shallow clone at the audited commit (for reference; gitignored)
.venv/            python3.11 environment
```
