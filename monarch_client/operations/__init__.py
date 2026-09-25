"""
Vendored Monarch GraphQL operation text.

Each `<op>.graphql` file in this directory is exported verbatim from
api-recon's operation catalog (`recon export-ops monarch --op <op> --out
monarch_client/operations/`) and committed here -- this is the one place
monarch_client depends on api-recon's *output*; it never imports api-recon's
code or reads its live (gitignored, 0700) artifacts directory at runtime.
See DESIGN.md sec 6b in api-recon: the catalog is "observed, not
authoritative", so vendoring is a deliberate, reviewed, human step -- not an
automatic pull. See README.md in this directory for the vendoring
checklist.

PROVENANCE records, per operation, what was true at vendoring time:
  - catalog_query_hash: the hash api-recon's catalog assigned this query's
    text (computed over its own REDACTED form -- see below). `doctor`
    compares this against the live catalog to flag when a re-vendor is due.
  - vendored_sha256: sha256 of this file's query text (header stripped) as
    committed here. tests/test_operations.py asserts this still matches, so
    an accidental hand-edit doesn't silently drift from what was reviewed.
  - hand_repaired: True if api-recon's literal-redaction (its
    QUERY_LITERAL_PLACEHOLDER for strings, plus int/float literals silently
    zeroed with no placeholder at all) clobbered a real value in this
    query, and it was manually restored from the raw HAR request body. A
    hand-repaired file's vendored_sha256 will NEVER match
    catalog_query_hash, by construction -- see `note` for what was restored
    and where it came from.
"""

from __future__ import annotations

import hashlib
from importlib import resources

from ..errors import VendoredOperationError

# Batch export timestamp for the 9 read ops vendored together in one
# `recon export-ops` run (Common_GetMe was vendored separately, slightly
# earlier, hence its own exported_at below).
_READ_OPS_EXPORTED_AT = "2026-09-24T15:56:34+00:00"

# Populated as operations are vendored -- see README.md in this directory.
PROVENANCE: dict[str, dict] = {
    "Common_GetMe": {
        "catalog_query_hash": (
            "45b63a9705a457f5a8500e84a13b4e9373a409b5c798b103fbc8f0a51bcf1d50"
        ),
        "vendored_sha256": (
            "f7cff7b0ab4e43186599a322fb20c6628ad8412939479d567aa49a5762987efb"
        ),
        "exported_at": "2026-09-24T15:53:35.162223+00:00",
        "runs_seen": [
            "2026-09-23T15-40-10Z",
            "2026-09-23T16-27-05Z",
            "2026-09-24T14-30-04Z",
        ],
        "hand_repaired": False,
        "note": "no variables, no redacted literals -- used as doctor's auth smoke test",
    },
    "Web_GetAccountsPage": {
        "catalog_query_hash": "dceb5b0ae3a7fb07440b28c414ecf90dabe5649223b1c8e51ea9b04410dbb61a",
        "vendored_sha256": "c31bb6941e36410dd523994e8f7f31f46d567621a2bd3647d3e19e52e9f17b35",
        "exported_at": _READ_OPS_EXPORTED_AT,
        "runs_seen": ["2026-09-23T15-40-10Z", "2026-09-23T16-27-05Z", "2026-09-24T14-30-04Z"],
        "hand_repaired": False,
        "note": "backs list_accounts; called with variables {'filters': {}}",
    },
    "Common_GetCategories": {
        "catalog_query_hash": "2d4082910767b2668a466114c1d8c4015aa57ad9ba2fa5ce46313b1be9590fde",
        "vendored_sha256": "c344e4529cddbd88bfaa88357bd9639cfbc2f5df34b45fae9e7f8d6ed31a36e7",
        "exported_at": _READ_OPS_EXPORTED_AT,
        "runs_seen": ["2026-09-23T15-40-10Z", "2026-09-23T16-27-05Z", "2026-09-24T14-30-04Z"],
        "hand_repaired": False,
        "note": "backs get_categories; called with variables {}",
    },
    "Common_GetHouseholdTransactionTags": {
        "catalog_query_hash": "08a49040cd401644b18642e185797d89e265c8322bd8a5aedc2736a825a88aad",
        "vendored_sha256": "6603570145db8620836f85eeee1203c00186ae0b27109ec8bb6e00541755e641",
        "exported_at": _READ_OPS_EXPORTED_AT,
        "runs_seen": ["2026-09-23T16-27-05Z", "2026-09-24T14-30-04Z"],
        "hand_repaired": False,
        "note": (
            "backs get_tags; called with variables {'includeTransactionCount': "
            "False}. The `tags:list` walk step is selector-rotted as of "
            "2026-09-24 -- this op has NO nightly drift coverage right now "
            "(see D5 in the design notes / doctor's catalog-drift check)."
        ),
    },
    "Common_GetJointPlanningData": {
        "catalog_query_hash": "a72d1aca4c167a4f81fc67ecbfb82b3b0fd8789d054e549b47385abe847cb244",
        "vendored_sha256": "fb14a17b98b0e2970ffd58fc535f297d4d7804fc6a0dfc89fbafe5ffa519b967",
        "exported_at": _READ_OPS_EXPORTED_AT,
        "runs_seen": ["2026-09-23T15-40-10Z", "2026-09-23T16-27-05Z", "2026-09-24T14-30-04Z"],
        "hand_repaired": False,
        "note": "backs get_budgets; both startDate/endDate are required (Date!)",
    },
    "Web_GetTransactionsPage": {
        "catalog_query_hash": "67ad5b1f7cb00d85bb873e88a0f56392fb79be5e51788e894d91267b002e37aa",
        "vendored_sha256": "11e5d24a2fb40d57725f9b695bae7f3ff6936ce3b437e6fed11134e8f97e8c43",
        "exported_at": _READ_OPS_EXPORTED_AT,
        "runs_seen": ["2026-09-23T15-40-10Z", "2026-09-23T16-27-05Z", "2026-09-24T14-30-04Z"],
        "hand_repaired": False,
        "note": "backs get_cashflow_summary",
    },
    "Web_GetTransactionsList": {
        "catalog_query_hash": "1a6be82e01555ce9a1f484ae7000f04f98b75656262e4e927516a5d6b1e26232",
        "vendored_sha256": "26f13921c626930567737b13e787e65a344c4660cc22ac70226dc5cba9d911c7",
        "exported_at": _READ_OPS_EXPORTED_AT,
        "runs_seen": ["2026-09-23T15-40-10Z", "2026-09-23T16-27-05Z", "2026-09-24T14-30-04Z"],
        "hand_repaired": False,
        "note": (
            "backs get_transactions. filters.categories/.accounts/.tags key "
            "names are UNVERIFIED against a live account (the catalog has "
            "never observed those filter fields populated) -- confirm once "
            "live before relying on category_ids/account_ids/tag_ids."
        ),
    },
    "Web_GetTransactionDrawer": {
        "catalog_query_hash": "5bf4ab6d55be1c57510c3847bfc5f57700f4ecfeb4fe80e70c3087dd09d5ba76",
        "vendored_sha256": "b8a469416b7a7a27dd7c7663d8c3a921265a3e3eee9b97d8815d30d544b11610",
        "exported_at": _READ_OPS_EXPORTED_AT,
        "runs_seen": ["2026-09-23T15-40-10Z", "2026-09-23T16-27-05Z", "2026-09-24T14-30-04Z"],
        "hand_repaired": False,
        "note": "backs get_transaction_details",
    },
    "Common_GetAggregatedRecurringItems": {
        "catalog_query_hash": "8ca5f3dc9d36264382287c5e2c294357292f7d3c1701de873b1516e947d981a6",
        "vendored_sha256": "53b69ab22e477d2e4b3ed65314ea480994687da88aafabe9b204022da7fbfab3",
        "exported_at": _READ_OPS_EXPORTED_AT,
        "runs_seen": ["2026-09-23T15-40-10Z", "2026-09-23T16-27-05Z", "2026-09-24T14-30-04Z"],
        "hand_repaired": True,
        "note": (
            "backs get_recurring_transactions. `groupBy` was redacted to "
            "\"__recon_redacted__\" by the catalog's literal-redaction; hand-"
            "repaired to the real value \"status\", recovered from the raw "
            "HAR body in artifacts/monarch/runs/2026-09-24T14-30-04Z/"
            "capture.har. vendored_sha256 will never match catalog_query_hash "
            "for this reason -- that's expected, not a bug."
        ),
    },
    "Common_CreateTransactionTag": {
        "catalog_query_hash": "8545d3e93331912b0c209c33d145ce9b28f0daba8bbb4272e32892cb87fbdc94",
        "vendored_sha256": "e4b64d141271fce99969b24ec7fd0d1714399aca26aada45c2364d25006fee32",
        "exported_at": "2026-09-24T18:47:14.885538+00:00",
        "runs_seen": ["2026-09-24T18-44-59Z"],
        "hand_repaired": False,
        "note": (
            "backs create_tag (the first write op, M4/M5). Captured against "
            "a dedicated, disposable 'monarch-sandbox' account (see "
            "adapters/__init__.py in api-recon), NOT the real 'monarch' "
            "site's catalog -- verified live end-to-end: created a tag, "
            "confirmed it appeared, deleted it via "
            "Common_DeleteHouseholdTransactionTag (not yet vendored -- no "
            "delete_tag MCP tool exists), confirmed it was gone. Variables: "
            "{'input': {'name': str, 'color': hex str}}. Query text is "
            "account-agnostic (same schema for every Monarch user), so a "
            "sandbox-observed op is exactly as valid to vendor as one "
            "observed on the real account."
        ),
    },
    "Web_GetTransactionRules": {
        "catalog_query_hash": "785cc8173a2ccbfd2f3ad7276377cee85675b2b39fba0d3dd00e2d0c0c218764",
        "vendored_sha256": "06cc49759a6c53c8ec52a44842112eeb1a111c6f80c9249d1d759eaeae4f83b7",
        "exported_at": _READ_OPS_EXPORTED_AT,
        "runs_seen": ["2026-09-23T15-40-10Z", "2026-09-23T16-27-05Z", "2026-09-24T14-30-04Z"],
        "hand_repaired": False,
        "note": "backs get_transaction_rules; called with variables {}",
    },
    "Common_PreviewTransactionRule": {
        "catalog_query_hash": "4e7341ba4d62c4556764600293ec79e875fb7485c1683eada0d2a24971000617",
        "vendored_sha256": "018ee6ed6e8d8e9b51b93cd041a572c208278e405c9335d0af79b3ddbd39f583",
        "exported_at": "2026-09-24T20:53:48.396427+00:00",
        "runs_seen": ["2026-09-24T19-44-40Z", "2026-09-24T20-52-07Z"],
        "hand_repaired": True,
        "note": (
            "backs preview_transaction_rule. Captured against monarch-sandbox "
            "(the app's real op name, 'Common_PreviewTransactionRule', differs "
            "from the old library's guessed name 'PreviewTransactionRule' -- "
            "verified live 2026-09-24). `limit` was silently zeroed by the "
            "catalog's INT literal redaction (no placeholder, unlike string "
            "literals) -- hand-repaired to the real value 30, recovered from "
            "artifacts/monarch-sandbox/runs/2026-09-24T19-44-40Z/capture.har. "
            "This op is itself a QUERY, not a mutation (confirmed both by the "
            "'query' keyword and by re-checking rule state after calling it) "
            "-- it's grouped with monarch-mcp's 'write tools' only because "
            "its natural workflow companion (create_transaction_rule) is one."
        ),
    },
    "Common_CreateTransactionRuleMutationV2": {
        "catalog_query_hash": "ae5c96e2335256ff62d0e9527b51b3630d59b5960699aacbf30ea74591a132b6",
        "vendored_sha256": "553fd17351a0db2631c2191490c65981e8ec33bf776df9c3f06d53db3484c186",
        "exported_at": "2026-09-24T20:53:48.397164+00:00",
        "runs_seen": ["2026-09-24T19-44-40Z", "2026-09-24T20-52-07Z"],
        "hand_repaired": False,
        "note": (
            "backs create_transaction_rule. Captured against monarch-sandbox: "
            "created a rule with a merchant-name criterion nothing would ever "
            "match, confirmed it appeared via get_transaction_rules, deleted "
            "it (see Common_DeleteTransactionRule), confirmed it was gone -- "
            "twice, on two separate test rules, 2026-09-24. Variables: "
            "{'input': {...}} -- see writes.py for the full field set."
        ),
    },
    "Web_TransactionDrawerUpdateTransaction": {
        "catalog_query_hash": "caff6fe20bf0acd3e483469b3d7934cec81b5134514d0edbcbb6f3cf02268792",
        "vendored_sha256": "b7991f170284c316cfb129d7c9352fe127229d28719cda1f64e538d0ff6b334e",
        "exported_at": "2026-09-24T22:54:24.715141+00:00",
        "runs_seen": ["2026-09-24T22-51-40Z"],
        "hand_repaired": False,
        "note": (
            "backs BOTH recategorize_transaction and update_transaction -- "
            "same underlying mutation, just a different subset of `input` "
            "fields set (matches _audit/monarchmoney/monarchmoney.py's own "
            "update_transaction, which recategorize_transaction is itself a "
            "thin wrapper around). Field-name mapping confirmed live against "
            "monarch-sandbox: category->'category', merchant_name->'name' "
            "(NOT 'merchantName'), amount/date only sent when truthy, "
            "hide_from_reports->'hideFromReports', needs_review->'needsReview', "
            "notes->'notes'. Variables: {'debugActivityLogEnabled': False, "
            "'input': {'id': <txn_id>, ...}}."
        ),
    },
    "Web_SetTransactionTags": {
        "catalog_query_hash": "f6c8172275e0001ed57bb548f58bda80a269d1a425c88811a6b65610f582723e",
        "vendored_sha256": "5f99fb1a1e41fad51667e6856a08e9fef51485dfcefe91e5e2df78f482d203e5",
        "exported_at": "2026-09-24T23:02:55.662381+00:00",
        "runs_seen": ["2026-09-24T23-00-38Z"],
        "hand_repaired": False,
        "note": (
            "backs set_transaction_tags. Captured against monarch-sandbox: "
            "set a real transaction's tags via the UI, confirmed the change "
            "via get_transaction_details. Variables: "
            "{'debugActivityLogEnabled': False, 'input': {'transactionId': "
            "<txn_id>, 'tagIds': [<tag_id>, ...]}} -- tagIds REPLACES the "
            "full tag set (matches the tool's own docstring: \"Set "
            "(replace) the tags\")."
        ),
    },
    "Common_MarkAsNotRecurring": {
        "catalog_query_hash": None,  # not catalog-exported -- see the .graphql file's own header
        "vendored_sha256": "0cd2740e7ed91d2ace6682a738701b4937101693f8ee456f4c8464adfdb87f6a",
        "exported_at": "2026-09-24T23:45:00+00:00",  # approximate -- hand-verified, not export-ops-timestamped
        "runs_seen": [],
        "hand_repaired": False,
        "note": (
            "backs mark_stream_as_not_recurring -- the last write tool, "
            "completing full 8/8 write coverage. NOT captured via `recon "
            "export-ops`: Monarch's recurring-stream detection is a backend "
            "batch process, not real-time, so a real stream had to be "
            "created via the UI's manual 'mark merchant as recurring' "
            "override rather than waiting for automatic detection. Verified "
            "directly and functionally against monarch-sandbox: created a "
            "real stream (multiple manual transactions, same merchant/"
            "amount, across 4 months, then manually marked recurring via "
            "the Recurring page), called this mutation with its real id, "
            "confirmed success:true, confirmed via a follow-up "
            "get_recurring_transactions call that the stream was completely "
            "gone -- 2026-09-24."
        ),
    },
    "Common_DeleteTransactionRule": {
        "catalog_query_hash": None,  # not catalog-exported -- see the .graphql file's own header
        "vendored_sha256": "f5c831059997cdca2227f942885579f62f1d537d73e06755f4e1d87a40347795",
        "exported_at": "2026-09-24T20:00:00+00:00",  # approximate -- hand-verified, not export-ops-timestamped
        "runs_seen": [],
        "hand_repaired": False,
        "note": (
            "backs delete_transaction_rule. NOT captured via `recon "
            "export-ops` -- the UI's nested delete-confirmation dialog proved "
            "fragile to automate reliably; see the .graphql file's own header "
            "for the full exception rationale. Verified directly and "
            "functionally instead: called live against monarch-sandbox twice "
            "(on two separate test rules), confirmed no errors, and confirmed "
            "via a follow-up get_transaction_rules call that each target rule "
            "was actually gone. `deleted` is false even on success -- matches "
            "server.py's existing docstring for this tool."
        ),
    },
}

_cache: dict[str, str] = {}


def _strip_header(raw: str) -> str:
    """`recon export-ops` prefixes each file with `#`-comment provenance
    lines followed by a blank line; strip that, keep the query text."""
    lines = raw.splitlines()
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:]).strip() + "\n"


def load(op_name: str) -> str:
    """Return `op_name`'s vendored query text (provenance header stripped),
    read once and cached. Raises VendoredOperationError if no file has been
    vendored for it yet."""
    if op_name in _cache:
        return _cache[op_name]

    try:
        raw = resources.files(__package__).joinpath(f"{op_name}.graphql").read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, OSError) as e:
        raise VendoredOperationError(
            f"no vendored query text for {op_name!r} -- run `recon export-ops "
            f"monarch --op {op_name} --out monarch_client/operations/` "
            "(see operations/README.md)"
        ) from e

    text = _strip_header(raw)
    _cache[op_name] = text
    return text


def verify_integrity(op_name: str) -> None:
    """Raise VendoredOperationError if the vendored file's text no longer
    matches its recorded vendored_sha256 -- catches a hand-edit that wasn't
    re-recorded in PROVENANCE. No-op if nothing is recorded for op_name
    yet."""
    entry = PROVENANCE.get(op_name)
    expected = entry.get("vendored_sha256") if entry else None
    if not expected:
        return

    text = load(op_name)
    actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if actual != expected:
        raise VendoredOperationError(
            f"{op_name}.graphql has changed since it was vendored (expected "
            f"sha256 {expected[:12]}…, got {actual[:12]}…) -- re-run "
            "the vendoring checklist in operations/README.md"
        )
