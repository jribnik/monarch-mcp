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
   see M0 in the design notes for a proposed loud-warning fix in
   `recon export-ops` itself, not yet built).

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

5. **Update the `PROVENANCE` entry** in `__init__.py`:
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

6. **Run the tests** (`tests/test_operations.py`) -- they assert every
   vendored file's hash matches what's recorded, and that no file contains
   an un-repaired redaction placeholder.

7. **Run `doctor`** to confirm the catalog-drift check now shows this op as
   unchanged:

   ```bash
   .venv/bin/python -m monarch_client.doctor
   ```

## Why files, not Python string constants

The provenance header (`query_hash`, `runs_seen`, `last_seen`, the "NOT
authoritative" note) travels with the query text and survives a re-vendor.
Copying the text into a `.py` triple-quoted string would strip that header
or require duplicating it by hand, and invites editing the query without
re-running the export. Files also keep git diffs readable when a
multi-kilobyte query changes by one field.

## Mutations

All 8 write ops are vendored (full coverage). Every one was captured and/or
verified against `monarch-sandbox` -- a dedicated, disposable Monarch
account registered in api-recon's adapter registry
(`~/src/api-recon/src/recon/adapters/__init__.py`), never the real
`monarch` site -- so mutations could be exercised freely without touching
real financial data. `recon login monarch-sandbox` once (a real, headful
browser login on that throwaway account), then normal
`recon walk`/`recon catalog`/`recon export-ops` commands work against it
exactly like the real site, just pointed at `monarch-sandbox` instead of
`monarch`.

This sidesteps the drift-watch merge-window hazard entirely: since nothing
runs a scheduled `recon catalog monarch-sandbox` diff, there's no
`OPERATION_REMOVED`/`not_in_latest_run` false-positive risk from merging a
mutation-only run into that site's `current.json` the way there would be
for the real `monarch` site's nightly drift-watch. `recon catalog
monarch-sandbox --all-runs --merge-runs 0` was used freely to keep its
catalog cumulative.

Two ops (`Common_DeleteTransactionRule`, `Common_MarkAsNotRecurring`) are
each an explicit, documented exception to normal export: their UI flows
proved too fragile/slow to drive reliably via Playwright (a nested
delete-confirmation dialog; Monarch's recurring detection being an
async backend batch job rather than something a fresh transaction triggers
immediately), so each was instead verified by a direct, functional live
call against monarch-sandbox with the library's own hand-authored query
text -- confirmed to succeed and confirmed to actually change server state
via a follow-up read. See each op's own `.graphql` file header for the
full rationale.

For a NEW write operation (or to re-verify an existing one):

1. Ensure `monarch-sandbox` has whatever test data the op needs (a manual
   account, a manual transaction, a manually-forced recurring stream via
   the Recurring page's "mark this merchant as recurring" override, etc.)
   -- see `writes.py`'s module docstring and each PROVENANCE note for what
   already exists there.
2. Drive the mutation via a real Playwright script against
   `session.load("monarch-sandbox")` (see api-recon's `capture_context`),
   capturing a HAR the same way `recon walk` does.
3. `recon catalog monarch-sandbox --all-runs --merge-runs 0`, then
   `recon export-ops monarch-sandbox --op <Name> --out
   monarch_client/operations/`.
4. Follow the same redaction check and PROVENANCE steps as any other
   vendored op (see above).
5. Verify live: call the new op directly, confirm no errors, confirm the
   expected state change via a follow-up read -- then verify again through
   the actual `server.py` tool function with `MONARCH_MCP_CLIENT_TOOLS` set
   and `MONARCH_CLIENT_SITE=monarch-sandbox` explicitly asserted in the
   script before anything mutates.
