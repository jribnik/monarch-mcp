# Vendoring Monarch GraphQL operations

Files in this directory are `.graphql` query/mutation text captured from the
real Monarch web app by [api-recon](https://github.com/jribnik/api-recon)
(`~/src/api-recon`), a Playwright-based traffic-capture tool, and committed
here for `monarch_client` to load at runtime. This is a deliberate,
reviewed, human step -- api-recon's own design stance (DESIGN.md sec 6b) is
that its catalog is "observed, not authoritative", so nothing here is pulled
in automatically.

## Re-vendoring an operation (e.g. after a drift-watch warning)

1. Refresh the catalog and re-export the operation:

   ```bash
   cd ~/src/api-recon
   .venv/bin/recon walk monarch        # or: recon map monarch
   .venv/bin/recon catalog monarch
   .venv/bin/recon export-ops monarch --op <OpName> \
       --out ~/src/monarch-mcp/monarch_client/operations/
   ```

2. **Check for silently-redacted literals.** api-recon's
   `dissect/graphql.py::_RedactLiterals` replaces every string literal in a
   captured query with the placeholder `__recon_redacted__`, and every
   int/float literal with `0` / `0.0` -- **with no placeholder at all** for
   the numeric case. Grep for the string case:

   ```bash
   grep -n "__recon_redacted__" ~/src/monarch-mcp/monarch_client/operations/<OpName>.graphql
   ```

   and eyeball the diff for suspicious `: 0` / `: 0.0` arguments that don't
   look like they should be zero (there's no automated way to catch these --
   a loud-warning fix in `recon export-ops` itself was proposed but is
   not yet built).

3. **Recover any redacted/zeroed literal from the raw HAR.** Request
   *bodies* are never redacted, only headers -- so the true value lives in
   whichever run's `capture.har` observed this operation:

   ```bash
   ls ~/src/api-recon/artifacts/monarch/runs/
   # find the request for <OpName> in the newest run's capture.har and
   # read the real literal out of its `query` or `variables` field.
   ```

4. **Hand-edit the vendored file** to restore the real literal, and add a
   `# HAND-REPAIRED:` comment block explaining what was restored and where
   it came from (see `Common_GetAggregatedRecurringItems.graphql` for the
   pattern). Comment lines are stripped before the query is sent -- they're
   for the next human, not for Monarch's API.

5. **Update the `PROVENANCE` entry** in `__init__.py` (or in the owning
   `writes_*.py` module's `PROVENANCE_ENTRIES`, see "Mutations" below):
   - `catalog_query_hash` -- copy from the newly-exported file's `# query_hash:`
     header line (this is a hash of the catalog's, possibly-redacted, text).
   - `vendored_sha256` -- sha256 of the file's query text *with the header
     stripped*. Compute it the same way the loader does:
     ```python
     from monarch_client.operations import _strip_header
     import hashlib
     text = _strip_header(open("<OpName>.graphql").read())
     print(hashlib.sha256(text.encode()).hexdigest())
     ```
   - `hand_repaired` -- `True` if step 4 applied. A hand-repaired file's
     `vendored_sha256` will **never** equal `catalog_query_hash` -- the
     catalog's hash is computed over the *redacted* text, so this mismatch
     is expected and not a bug.
   - `note` -- what this op backs, and any caveats worth a future reader
     knowing (e.g. unverified filter fields, missing drift coverage).

6. **Run the tests** (`monarch_client/tests/test_operations.py`) -- they assert every
   vendored file's hash matches what's recorded, and that no file contains
   an un-repaired redaction placeholder.

7. **Confirm the drift check now sees this op as unchanged.** Catalog
   drift is checked by api-recon's drift-watch, which reads this package's
   records:

   ```bash
   .venv/bin/python -m monarch_client.operations --provenance-json | grep -A3 '"<OpName>"'
   ```

   (the `catalog_query_hash` shown must equal the catalog's current hash for
   that op -- re-run api-recon's drift-watch to confirm). `doctor` itself no
   longer compares hashes.

## Why files, not Python string constants

The provenance header (`query_hash`, `runs_seen`, `last_seen`, the "NOT
authoritative" note) travels with the query text and survives a re-vendor.
Copying the text into a `.py` triple-quoted string would strip that header
or require duplicating it by hand, and invites editing the query without
re-running the export. Files also keep git diffs readable when a
multi-kilobyte query changes by one field.

## Mutations

43 operations are vendored: 28 mutations (backing the 30 write tools --
`Web_TransactionDrawerUpdateTransaction` backs both `recategorize_transaction`
and `update_transaction`, `Common_SplitTransactionMutation` backs both
`split_transaction` and `unsplit_transaction`) and 15 queries (the read tools'
queries, `Common_PreviewTransactionRule` -- a QUERY grouped with the writes
only because its purpose is dry-running a rule -- and the write tools' fresh
pre-reads `Common_GetAccountForEdit`, `Common_GetMerchantForEdit`,
`Common_SearchMerchantsByName`). Where each op's `PROVENANCE` entry lives:

- `operations/__init__.py`: the 09-24..09-28 ops (reads, tags, rules,
  transactions, manual accounts).
- `writes_categories.py`, `writes_accounts.py`, `writes_splits_rules.py`
  (`PROVENANCE_ENTRIES`): the 21 ops added by the 2026-09-30 UI sweep -- 18
  mutations backing 19 tools (category/group/tag/merchant updates, account
  update/delete, budgets, savings goals, split, rule update) plus 3
  hand-written reads (`Common_GetAccountForEdit`, `Common_GetMerchantForEdit`,
  `Common_SearchMerchantsByName`). `monarch_client/__init__.py` merges them into
  `PROVENANCE` at import (they can't be imported from here -- circular).
  Their `catalog_query_hash` is `None` and `walk_reachable` is `False`: they
  were captured as raw request bodies by an api-recon UI script, never
  catalog-exported, so api-recon's drift-watch skips them.

Every `.graphql` file must have a `PROVENANCE` entry with a `vendored_sha256`:
`tests/test_operations.py` asserts it, and `verify_integrity` refuses to send
a file with no entry or a mismatched hash. Hashes are hardcoded in the entries
(never computed from the file at import time, which would make the integrity
check compare a file with itself).

Three mutations are explicit exceptions to normal export, each documented in
its own `.graphql` header: `Common_DeleteTransactionRule`,
`Common_MarkAsNotRecurring` and `Common_DeleteTransactionMutation` (never
driven through api-recon's UI automation -- nested-dialog / async-backend
flows that proved too fragile, or recovered from the legacy
monarchmoney-enhanced library -- and verified by a direct functional call plus
a follow-up read). The three hand-written reads above are likewise not
catalog-exported.

### The monarch-sandbox account no longer exists

Everything above was captured and verified live against `monarch-sandbox`, a
dedicated disposable Monarch account registered in api-recon's adapter
registry. That account has been **deleted** (it cost money to keep), so
"verified live against monarch-sandbox" in `.graphql` headers, PROVENANCE notes
and docstrings is dated history -- the `monarch-sandbox` site in api-recon, its
`recon login`, and `MONARCH_CLIENT_SITE=monarch-sandbox` no longer work. (A
leftover `~/.monarch-mcp/api-auth.monarch-sandbox.json` is dead local state.)
Vendoring and re-vendoring READ operations is unaffected: it needs only the
real `monarch` site, via the checklist at the top of this file.

### Adding a write operation (no sandbox)

A new mutation can no longer be driven freely against a throwaway account, so
there is no cheap way to verify it. Options, safest first:

1. Re-register and `recon login` a fresh disposable Monarch account in
   api-recon's adapter registry (`~/src/api-recon/src/recon/adapters/__init__.py`)
   and follow the old workflow: drive the mutation with a Playwright script,
   `recon catalog <site> --all-runs --merge-runs 0`, `recon export-ops <site>
   --op <Name> --out monarch_client/operations/`, then verify through the real
   `server.py` tool with `MONARCH_CLIENT_SITE=<site>` asserted before anything
   mutates.
2. Against the real account, only with the owner's explicit go-ahead, on a
   throwaway object the test itself creates and then removes (a tag, a manual
   account/transaction, a rule with a criterion nothing matches), reading the
   state back before and after. Never test on pre-existing data.

Either way: vendor the file, add its `PROVENANCE` entry with the hash, give
the tool its fresh-read confirm gate if it is destructive
(`writes._require_confirm`), add the wrapper to `server.py` (keyword
arguments) and the tool to `tests/test_server_tools.py`'s expected sets, and
update the README tool table and counts.
