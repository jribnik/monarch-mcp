"""
Reshape the real Monarch web app's GraphQL responses into what each MCP
tool's docstring promises.

Two independent decisions, deliberately made separately (see the design
notes' D7): the REQUEST always sends the app's verbatim vendored query
(never trimmed -- that's what keeps the nightly drift-watch coverage
meaningful); the RESPONSE gets projected here into a stable, Claude-facing
shape.

Most of these operations turn out to share the old monarchmoney-enhanced
library's field names almost exactly (both talk to the same underlying
schema) -- those projectors are near pass-throughs, documented as such.
Two are materially different because the app's own UI groups data
differently than the library's hand-written queries did:
  - accounts: the app groups by account TYPE (`accountTypeSummaries[]`);
    the old library returned one flat list. Flattened back to a flat list
    here.
  - recurring transactions: the app groups by STATUS
    (`aggregatedRecurringItems.groups[]`); the old library returned one
    flat `recurringTransactionStreams`-shaped list. Flattened here too.

Every projector tolerates missing keys (returns `None`/omits rather than
raising) -- a field genuinely being absent is exactly the kind of thing the
drift-watch should catch upstream in api-recon's catalog, not something a
tool call should crash on.

`include_raw=True` on any projector attaches the untouched response under
`_raw`, for `doctor` and any future side-by-side parity check to inspect
without needing a second network call.
"""

from __future__ import annotations

from typing import Any


def _with_raw(projected: dict[str, Any], raw: dict[str, Any], include_raw: bool) -> dict[str, Any]:
    if include_raw:
        projected["_raw"] = raw
    return projected


def accounts(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Flatten the app's `accountTypeSummaries[].accounts[]` grouping into
    one flat list (the old library's `get_accounts()` shape)."""
    flat: list[dict[str, Any]] = []
    for summary in data.get("accountTypeSummaries") or []:
        flat.extend(summary.get("accounts") or [])
    return _with_raw({"accounts": flat}, data, include_raw)


def categories(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Pass-through: `categoryGroups`/`categories` are the same top-level
    field names the old library's GetCategories query used."""
    return _with_raw(
        {
            "categoryGroups": data.get("categoryGroups") or [],
            "categories": data.get("categories") or [],
        },
        data,
        include_raw,
    )


def tags(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Pass-through: `householdTransactionTags` is the schema's own field
    name, same as the old library used."""
    return _with_raw(
        {"householdTransactionTags": data.get("householdTransactionTags") or []},
        data,
        include_raw,
    )


def budgets(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Pass-through: the app's Common_GetJointPlanningData response is a
    strict superset of the old library's (same budgetData/categoryGroups/
    goalsV2 fields, plus savings-goal and debt-paydown budget amounts the
    library never fetched)."""
    return _with_raw(dict(data), data, include_raw)


def cashflow_summary(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Pass-through: same `aggregates[].summary` shape the old library's
    get_transactions_summary() used (same operation, `GetTransactionsPage`
    vs. the app's `Web_GetTransactionsPage` -- identical query body)."""
    return _with_raw({"aggregates": data.get("aggregates") or []}, data, include_raw)


def transactions(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Pass-through: `allTransactions.{totalCount,results}` plus
    `transactionRules` matches the old library's GetTransactionsList shape
    (the app additionally returns `totalSelectableCount`)."""
    return _with_raw(
        {
            "allTransactions": data.get("allTransactions") or {},
            "transactionRules": data.get("transactionRules") or [],
        },
        data,
        include_raw,
    )


def transaction_details(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Pass-through: `getTransaction` is the schema's field name for a
    single transaction lookup, same as the old library used."""
    return _with_raw(
        {
            "getTransaction": data.get("getTransaction"),
            "myHousehold": data.get("myHousehold"),
        },
        data,
        include_raw,
    )


def recurring_transactions(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Flatten the app's status-grouped
    `aggregatedRecurringItems.groups[].results[]` into one flat
    `recurring_items` list (each item still carries its own `stream` with
    merchant/amount/frequency/account -- see
    operations/Common_GetAggregatedRecurringItems.graphql's
    RecurringItemFields fragment), alongside the aggregate totals the old
    library's flat list never provided."""
    aggregated = data.get("aggregatedRecurringItems") or {}
    flat: list[dict[str, Any]] = []
    for group in aggregated.get("groups") or []:
        flat.extend(group.get("results") or [])
    return _with_raw(
        {
            "recurring_items": flat,
            "summary": aggregated.get("aggregatedSummary"),
        },
        data,
        include_raw,
    )


def create_tag_result(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Pass-through: `createTransactionTag.{tag,errors}` matches the old
    library's shape exactly -- same operation name, same query, same
    variables (`$input: CreateTransactionTagInput!`), verified byte-for-byte
    against _audit/monarchmoney/monarchmoney.py's create_transaction_tag."""
    return _with_raw(
        {"createTransactionTag": data.get("createTransactionTag")}, data, include_raw
    )


def preview_transaction_rule_result(
    data: dict[str, Any], *, include_raw: bool = False
) -> dict[str, Any]:
    """Pass-through: `transactionRulePreview.{totalCount,results}` matches
    the old library's shape (same underlying query, real op name
    Common_PreviewTransactionRule vs. the library's guessed
    PreviewTransactionRule -- see operations/__init__.py's PROVENANCE)."""
    return _with_raw(
        {"transactionRulePreview": data.get("transactionRulePreview")}, data, include_raw
    )


def create_transaction_rule_result(
    data: dict[str, Any], *, include_raw: bool = False
) -> dict[str, Any]:
    """`createTransactionRuleV2.errors` matches the old library's shape.
    `transactionRule` does NOT: the real app's own query (verbatim vendored)
    only selects `errors`, not `transactionRule { id }` the way the
    library's hand-authored version does -- so the new rule's id is simply
    not available from this mutation's response on the client backend.
    `transactionRule` is still included, set to None, so both backends
    return the same top-level keys; a caller that needs the new id must
    call get_transaction_rules() and match on the criteria it just set."""
    result = data.get("createTransactionRuleV2") or {}
    return _with_raw(
        {
            "createTransactionRuleV2": {
                "errors": result.get("errors"),
                "transactionRule": result.get("transactionRule"),  # always None here
            }
        },
        data,
        include_raw,
    )


def update_transaction_result(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Pass-through: `updateTransaction.{transaction,errors}` -- same
    operation/shape the old library's update_transaction (and its
    recategorize_transaction wrapper) used; verified live against
    monarch-sandbox."""
    return _with_raw(
        {"updateTransaction": data.get("updateTransaction")}, data, include_raw
    )


def set_transaction_tags_result(
    data: dict[str, Any], *, include_raw: bool = False
) -> dict[str, Any]:
    """Pass-through: `setTransactionTags.{transaction,errors}` -- verified
    live against monarch-sandbox (set a real transaction's tags, confirmed
    via a follow-up get_transaction_details call)."""
    return _with_raw(
        {"setTransactionTags": data.get("setTransactionTags")}, data, include_raw
    )


def mark_stream_as_not_recurring_result(
    data: dict[str, Any], *, include_raw: bool = False
) -> dict[str, Any]:
    """Matches server.py's own wrapping (`{"success": ok}`), not a raw
    pass-through -- server.py already reshapes the library's boolean
    return this way. Verified live against monarch-sandbox: a real stream
    was created, marked not-recurring (success:true), and confirmed gone
    via a follow-up get_recurring_transactions call."""
    success = (data.get("markStreamAsNotRecurring") or {}).get("success", False)
    return _with_raw({"success": success}, data, include_raw)


def delete_transaction_rule_result(
    data: dict[str, Any], *, include_raw: bool = False
) -> dict[str, Any]:
    """Matches server.py's own wrapping (`{"deleted_flag": ok}`), not a
    raw pass-through -- server.py already reshapes the library's boolean
    return this way. `deleted` is unreliable and reads False even on a
    successful delete (verified live 2026-09-24, twice) -- this is a
    known, pre-existing quirk of the API itself, not something either
    backend can fix; see server.py's delete_transaction_rule docstring."""
    deleted = (data.get("deleteTransactionRule") or {}).get("deleted", False)
    return _with_raw({"deleted_flag": deleted}, data, include_raw)


def transaction_rules(data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Pass-through: `transactionRules` is the schema's field name, same
    as the old library used."""
    return _with_raw({"transactionRules": data.get("transactionRules") or []}, data, include_raw)
