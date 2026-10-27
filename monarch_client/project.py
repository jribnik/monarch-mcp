"""
Reshape the real Monarch web app's GraphQL responses into what each MCP
tool's docstring promises.

Two independent decisions, deliberately made separately (see the design
notes' D7): the REQUEST always sends the app's verbatim vendored query
(never trimmed -- that's what keeps the nightly drift-watch coverage
meaningful); the RESPONSE gets projected here into a stable, Claude-facing
shape.

Most of these operations turn out to share the field names of the legacy
monarchmoney-enhanced library (removed 2026-09-28; its output shapes are the
compatibility target) almost exactly (both talk to the same underlying
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
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any


def accounts(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten the app's `accountTypeSummaries[].accounts[]` grouping into
    one flat list (the old library's `get_accounts()` shape)."""
    flat: list[dict[str, Any]] = []
    for summary in data.get("accountTypeSummaries") or []:
        flat.extend(summary.get("accounts") or [])
    return {"accounts": flat}


def _money(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def accounts_compact(
    data: dict[str, Any],
    include_hidden: bool = False,
    account_types: list[str] | None = None,
) -> dict[str, Any]:
    """Slim account rows plus totals. Rows come from the same flattening as
    `accounts`; hidden accounts are dropped unless include_hidden and
    `account_types` (matched case-insensitively against `type.name`)
    restricts both rows and totals.

    Totals sum `signedBalance` over the rows whose `includeInNetWorth` flag
    is set: assets = isAsset rows, liabilities = the absolute sum of the
    others, net_worth = assets - liabilities. `is_manual` is inferred from
    the account having no linked credential (the list op does not select
    isManual)."""
    wanted = {t.lower() for t in account_types or []}
    rows: list[dict[str, Any]] = []
    assets = liabilities = 0.0
    for a in accounts(data)["accounts"]:
        if a.get("isHidden") and not include_hidden:
            continue
        type_name = (a.get("type") or {}).get("name")
        if wanted and (type_name or "").lower() not in wanted:
            continue
        cred_inst = ((a.get("credential") or {}).get("institution") or {}).get("name")
        rows.append(
            {
                "id": a.get("id"),
                "name": a.get("displayName"),
                "type": type_name,
                "subtype": (a.get("subtype") or {}).get("display"),
                "balance": a.get("signedBalance"),
                "institution": (a.get("institution") or {}).get("name") or cred_inst,
                "is_manual": not a.get("credential"),
                "include_in_net_worth": a.get("includeInNetWorth"),
                "is_hidden": a.get("isHidden"),
                "updated_at": a.get("updatedAt"),
            }
        )
        if a.get("includeInNetWorth"):
            if a.get("isAsset"):
                assets += _money(a.get("signedBalance"))
            else:
                liabilities += abs(_money(a.get("signedBalance")))
    return {
        "accounts": rows,
        "count": len(rows),
        "totals": {
            "assets": round(assets, 2),
            "liabilities": round(liabilities, 2),
            "net_worth": round(assets - liabilities, 2),
        },
    }


def categories(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `categoryGroups`/`categories` are the same top-level
    field names the old library's GetCategories query used."""
    return {
        "categoryGroups": data.get("categoryGroups") or [],
        "categories": data.get("categories") or [],
    }


def tags(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `householdTransactionTags` is the schema's own field
    name, same as the old library used."""
    return {"householdTransactionTags": data.get("householdTransactionTags") or []}


def merchants(
    data: dict[str, Any], limit: int, include_zero_txn: bool = False
) -> dict[str, Any]:
    """Compact merchant list from Common_SearchMerchantsByName's
    `merchants[]` (the hand-written op selects only id/name/transactionCount,
    so that is all there is to project). Merchants with no transactions are
    dropped unless include_zero_txn. `truncated` is True when the server
    returned a full page (len == limit), i.e. more matches may exist -- the
    op has no totalCount; it is computed BEFORE the zero-txn filter."""
    rows = data.get("merchants") or []
    kept = [
        {
            "id": m.get("id"),
            "name": m.get("name"),
            "transaction_count": m.get("transactionCount"),
        }
        for m in rows
        if include_zero_txn or (m.get("transactionCount") or 0) > 0
    ]
    return {
        "merchants": kept,
        "count": len(kept),
        "truncated": len(rows) >= limit,
    }


def _bucket_key(day: str, interval: str) -> str:
    if interval == "day":
        return day
    d = date.fromisoformat(day)
    if interval == "week":
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return day[:7]  # month


def net_worth_history(data: dict[str, Any], interval: str) -> dict[str, Any]:
    """Compact `aggregateSnapshots[]` (date, balance, assetsBalance,
    liabilitiesBalance) and downsample to `interval` ("day"/"week"/"month")
    by keeping the LAST snapshot of each bucket (its end-of-period value).
    `interval` must already be resolved (not "auto"); see
    reads.get_net_worth_history. Output is oldest-first."""
    snaps = sorted(
        (s for s in data.get("aggregateSnapshots") or [] if s.get("date")),
        key=lambda s: s["date"],
    )
    last: dict[str, dict[str, Any]] = {}
    for s in snaps:
        last[_bucket_key(s["date"], interval)] = s
    points = [
        {
            "date": s["date"],
            "net_worth": s.get("balance"),
            "assets": s.get("assetsBalance"),
            "liabilities": s.get("liabilitiesBalance"),
        }
        for s in last.values()
    ]
    return {"interval": interval, "points": points, "count": len(points)}


def credit_score_history(
    data: dict[str, Any], start_date: str, end_date: str
) -> dict[str, Any]:
    """ONLY date, score and the user's display name from
    `creditScoreSnapshots[]`; everything else the op returns (spinwheelUser
    onboarding/subscription ids and status, avatars, account-name emails) is
    deliberately dropped. The op takes no date arguments, so start/end
    (inclusive, 'YYYY-MM-DD') are applied here. User names are resolved
    from myHousehold.users by the snapshot's user id."""
    names: dict[str, Any] = {}
    for u in (data.get("myHousehold") or {}).get("users") or []:
        names[str(u.get("id"))] = u.get("displayName")
    su = (data.get("spinwheelUser") or {}).get("user") or {}
    if su.get("id") is not None:
        names.setdefault(str(su["id"]), su.get("displayName"))
    scores = []
    for snap in data.get("creditScoreSnapshots") or []:
        day = (snap.get("reportedDate") or "")[:10]
        if not day or day < start_date or day > end_date:
            continue
        uid = (snap.get("user") or {}).get("id")
        scores.append(
            {"date": day, "score": snap.get("score"), "user": names.get(str(uid))}
        )
    scores.sort(key=lambda r: (r["date"], str(r["user"])))
    return {"scores": scores, "count": len(scores)}


def household_members(data: dict[str, Any]) -> dict[str, Any]:
    """`myHousehold.users[]` without avatar fields, flagging the caller."""
    me_id = (data.get("me") or {}).get("id")
    members = [
        {
            "id": u.get("id"),
            "name": u.get("name"),
            "display_name": u.get("displayName"),
            "role": u.get("householdRole"),
            "is_me": me_id is not None and str(u.get("id")) == str(me_id),
        }
        for u in (data.get("myHousehold") or {}).get("users") or []
    ]
    return {"members": members}


def businesses(data: dict[str, Any]) -> dict[str, Any]:
    """`businessEntities[]` compacted: no logo/color/notes/timestamps."""
    out = [
        {
            "id": b.get("id"),
            "name": b.get("name"),
            "description": b.get("description"),
            "structure": b.get("structure"),
            "accounts": [
                {"id": a.get("id"), "name": a.get("displayName")}
                for a in b.get("accounts") or []
            ],
            "accounts_count": b.get("accountsCount"),
            "transactions_count": b.get("transactionsCount"),
        }
        for b in data.get("businessEntities") or []
    ]
    return {"businesses": out}


def goals(data: dict[str, Any]) -> dict[str, Any]:
    """`savingsGoals[]` (GoalSummaryFields) compacted to the figures a person
    reads on the Goals page, plus each goal's per-account allocation and the
    two household-level balances. Image/logo/icon fields are dropped."""
    out = []
    for g in data.get("savingsGoals") or []:
        out.append(
            {
                "id": g.get("id"),
                "name": g.get("name"),
                "type": g.get("type"),
                "status": g.get("status"),
                "archived": g.get("archivedAt") is not None,
                "progress": g.get("progress"),
                "current_balance": g.get("currentBalance"),
                "target_amount": g.get("targetAmount"),
                "target_date": g.get("targetDate"),
                "planned_monthly_contribution": g.get("plannedMonthlyContribution"),
                "balance_this_month": g.get("balanceThisMonth"),
                "forecasted_completion_date": g.get("forecastedCompletionDate"),
                "estimated_months_until_completion": g.get("estimatedMonthsUntilCompletion"),
                "is_sinking_fund": g.get("isSinkingFund"),
                "priority": g.get("priority"),
                "allocations": [
                    {
                        "account": (a.get("account") or {}).get("displayName"),
                        "amount": a.get("totalAmount"),
                    }
                    for a in g.get("allocationAmountsByAccount") or []
                ],
            }
        )
    return {
        "goals": out,
        "goals_balance_this_month": data.get("goalsBalanceThisMonth"),
        "current_total_balance_for_goals": data.get("currentTotalBalanceForGoals"),
    }


def budgets(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: the app's Common_GetJointPlanningData response is a
    strict superset of the old library's (same budgetData/categoryGroups/
    goalsV2 fields, plus savings-goal and debt-paydown budget amounts the
    library never fetched)."""
    return dict(data)


def cashflow_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: same `aggregates[].summary` shape the old library's
    get_transactions_summary() used (same operation, `GetTransactionsPage`
    vs. the app's `Web_GetTransactionsPage` -- identical query body)."""
    return {"aggregates": data.get("aggregates") or []}


def transactions(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `allTransactions.{totalCount,results}` plus
    `transactionRules` matches the old library's GetTransactionsList shape
    (the app additionally returns `totalSelectableCount`)."""
    return {
        "allTransactions": data.get("allTransactions") or {},
        "transactionRules": data.get("transactionRules") or [],
    }


# Serialized-size ceiling (len(json.dumps(result))) for one get_transactions
# page, so a single call can't flood the model's context.
TRANSACTIONS_BUDGET_CHARS = 40_000


def transaction_compact_row(t: dict[str, Any]) -> dict[str, Any]:
    """One transaction from `allTransactions.results[]` reduced to the fields
    a review/cleanup pass needs (no plaid names, logos, goals, attachments,
    recurrence groups ...). Use get_transaction_details for the rest."""
    merchant = t.get("merchant") or {}
    category = t.get("category") or {}
    account = t.get("account") or {}
    return {
        "id": t.get("id"),
        "date": t.get("date"),
        "amount": t.get("amount"),
        "pending": t.get("pending"),
        "merchant": merchant.get("name"),
        "merchant_id": merchant.get("id"),
        "category": category.get("name"),
        "category_id": category.get("id"),
        "account": account.get("displayName"),
        "account_id": account.get("id"),
        "review_status": t.get("reviewStatus"),
        "needs_review": t.get("needsReview"),
        "hide_from_reports": t.get("hideFromReports"),
        "is_split": t.get("isSplitTransaction"),
        "tag_ids": [x.get("id") for x in t.get("tags") or []],
        "has_notes": bool((t.get("notes") or "").strip()),
    }


def _size(obj: Any) -> int:
    return len(json.dumps(obj))


def _next_offset(offset: int, returned: int, total: Any) -> int | None:
    nxt = offset + returned
    if isinstance(total, int) and nxt >= total:
        return None
    return nxt if returned else None


def transactions_compact(
    data: dict[str, Any], offset: int, budget: int = TRANSACTIONS_BUDGET_CHARS
) -> dict[str, Any]:
    """Envelope `{transactions, total_count, returned, offset, next_offset,
    truncated}`. Rows are trimmed from the end until the serialized envelope
    fits `budget`; trimming sets `truncated` and next_offset resumes right
    after the last kept row (None when nothing remains)."""
    block = data.get("allTransactions") or {}
    total = block.get("totalCount")
    rows = [transaction_compact_row(t) for t in block.get("results") or []]
    fetched = len(rows)

    def build() -> dict[str, Any]:
        return {
            "transactions": rows,
            "total_count": total,
            "returned": len(rows),
            "offset": offset,
            "next_offset": _next_offset(offset, len(rows), total),
            "truncated": len(rows) < fetched,
        }

    out = build()
    while rows and _size(out) > budget:
        rows.pop()
        out = build()
    if len(rows) < fetched:
        # trimmed rows were fetched but not returned: resume right after the last kept row
        out["next_offset"] = offset + len(rows)
    return out


def transactions_full(
    data: dict[str, Any], offset: int, budget: int = TRANSACTIONS_BUDGET_CHARS
) -> dict[str, Any]:
    """The raw `transactions` shape, byte-for-byte, when it fits `budget`.
    Over budget, rows are trimmed from the end and `truncated: True` plus
    `next_offset` are added to the top level (only then)."""
    out = transactions(data)
    block = out["allTransactions"]
    if _size(out) <= budget:
        return out
    rows = list(block.get("results") or [])
    total = block.get("totalCount")
    out = {**out, "allTransactions": {**block, "results": rows}}
    while rows and _size(out) > budget:
        rows.pop()
    if not rows:
        raise ValueError(
            "a single transaction exceeds the output size budget; use detail='compact'"
        )
    out["truncated"] = True
    out["next_offset"] = offset + len(rows)
    return out


def transaction_details(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `getTransaction` is the schema's field name for a
    single transaction lookup, same as the old library used."""
    return {
        "getTransaction": data.get("getTransaction"),
        "myHousehold": data.get("myHousehold"),
    }


def split_dry_run(
    txn: dict[str, Any],
    proposed: list[dict[str, Any]],
    sum_ok: bool,
) -> dict[str, Any]:
    """Preview shape for split_transaction/unsplit_transaction(dry_run=True):
    the transaction's amount, its CURRENT splits (compacted from the drawer's
    `splitTransactions`), the splits that would be sent, and whether they sum
    exactly to the amount. `proposed` is already in tool-argument form."""
    return {
        "transaction_amount": txn.get("amount"),
        "current_splits": [
            {
                "id": s.get("id"),
                "amount": s.get("amount"),
                "merchant": (s.get("merchant") or {}).get("name"),
                "category": (s.get("category") or {}).get("name"),
            }
            for s in txn.get("splitTransactions") or []
        ],
        "proposed_splits": proposed,
        "sum_ok": sum_ok,
    }


def recurring_transactions(data: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "recurring_items": flat,
        "summary": aggregated.get("aggregatedSummary"),
    }


def create_tag_result(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `createTransactionTag.{tag,errors}` matches the old
    library's shape exactly -- same operation name, same query, same
    variables (`$input: CreateTransactionTagInput!`), verified byte-for-byte
    against keithah/monarchmoney-enhanced@159d36e monarchmoney/monarchmoney.py's create_transaction_tag."""
    return {"createTransactionTag": data.get("createTransactionTag")}


def preview_transaction_rule_result(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `transactionRulePreview.{totalCount,results}` matches
    the old library's shape (same underlying query, real op name
    Common_PreviewTransactionRule vs. the library's guessed
    PreviewTransactionRule -- see operations/__init__.py's PROVENANCE)."""
    return {"transactionRulePreview": data.get("transactionRulePreview")}


def create_transaction_rule_result(data: dict[str, Any]) -> dict[str, Any]:
    """`createTransactionRuleV2.errors` matches the old library's shape.
    `transactionRule` does NOT: the real app's own query (verbatim vendored)
    only selects `errors`, not `transactionRule { id }` the way the
    library's hand-authored version does -- so the new rule's id is simply
    not available from this mutation's response. `transactionRule` is still
    included, set to None, so the result keeps the legacy library's
    top-level keys; a caller that needs the new id must call
    get_transaction_rules() and match on the criteria it just set."""
    result = data.get("createTransactionRuleV2") or {}
    return {
        "createTransactionRuleV2": {
            "errors": result.get("errors"),
            "transactionRule": result.get("transactionRule"),  # always None here
        }
    }


def update_transaction_result(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `updateTransaction.{transaction,errors}` -- same
    operation/shape the old library's update_transaction (and its
    recategorize_transaction wrapper) used; verified live against
    monarch-sandbox."""
    return {"updateTransaction": data.get("updateTransaction")}


def set_transaction_tags_result(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `setTransactionTags.{transaction,errors}` -- verified
    live against monarch-sandbox (set a real transaction's tags, confirmed
    via a follow-up get_transaction_details call)."""
    return {"setTransactionTags": data.get("setTransactionTags")}


def mark_stream_as_not_recurring_result(data: dict[str, Any]) -> dict[str, Any]:
    """Reshaped to `{"success": ok}` (the legacy library returned a bare
    boolean) rather than a raw pass-through. Verified live (against the since-
    deleted monarch-sandbox account): a real stream was created, marked
    not-recurring (success:true), and confirmed gone via a follow-up
    get_recurring_transactions call."""
    success = (data.get("markStreamAsNotRecurring") or {}).get("success", False)
    return {"success": success}


def delete_transaction_rule_result(data: dict[str, Any]) -> dict[str, Any]:
    """Reshaped to `{"deleted_flag": ok}` (the legacy library returned a bare
    boolean) rather than a raw pass-through. `deleted` is unreliable and reads
    False even on a successful delete (verified live 2026-09-24, twice) --
    a quirk of the API itself that nothing client-side can fix; see
    server.py's delete_transaction_rule docstring."""
    deleted = (data.get("deleteTransactionRule") or {}).get("deleted", False)
    return {"deleted_flag": deleted}


def delete_tag_result(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through, same convention as create_tag_result (this mutation's
    natural counterpart): `deleteTransactionTag.errors` is Monarch's own
    APPLICATION-level error convention (the PayloadErrorFields fragment) --
    a normal field in `data`, not a protocol-level error, so
    transport.py's automatic MonarchGraphQLError-on-top-level-errors[]
    never sees it. Surfaced directly (errors=None on success, verified
    live 2026-09-28) rather than collapsed into a boolean, so a caller
    sees a real error message if one ever comes back instead of a bare
    True/False."""
    return {"deleteTransactionTag": data.get("deleteTransactionTag")}


def transaction_rules(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `transactionRules` is the schema's field name, same
    as the old library used."""
    return {"transactionRules": data.get("transactionRules") or []}


def account_type_options(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `accountTypeOptions` is the schema's field name.
    Each entry is `{type: {name, display, group, possibleSubtypes[]},
    subtype}` -- create_manual_account's `type`/`subtype` args must be a
    `type.name`/`possibleSubtypes[].name` pair from this list."""
    return {"accountTypeOptions": data.get("accountTypeOptions") or []}


def create_transaction_result(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `createTransaction.{transaction,errors}` -- verified
    live against monarch-sandbox 2026-09-28 (created a real manual
    transaction, confirmed via a follow-up get_transaction_details call;
    also confirmed a bad accountId/categoryId surfaces a real
    `errors.message` with transaction=null rather than failing silently)."""
    return {"createTransaction": data.get("createTransaction")}


def delete_transaction_result(data: dict[str, Any]) -> dict[str, Any]:
    """Reshaped to `{"deleted_flag": ok}`, same
    convention as delete_transaction_rule_result. Unlike that op, `deleted`
    reads true correctly on success here -- verified live against
    monarch-sandbox 2026-09-28 (a follow-up get_transaction_details call
    confirmed getTransaction now returns null for the deleted id)."""
    deleted = (data.get("deleteTransaction") or {}).get("deleted", False)
    return {"deleted_flag": deleted}


def create_manual_account_result(data: dict[str, Any]) -> dict[str, Any]:
    """Pass-through: `createManualAccount.{account,errors}` -- verified live
    against monarch-sandbox 2026-09-28 (created a real manual account,
    confirmed via a follow-up list_accounts call; also confirmed an invalid
    type/subtype raises rather than silently succeeding)."""
    return {"createManualAccount": data.get("createManualAccount")}
