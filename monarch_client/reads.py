"""
The 16 read-side MCP tools defined in this module (17 read-side tools at the
tool level: the README table also counts preview_transaction_rule, a dry-run
that lives in writes.py), built on monarch_client; the legacy
monarchmoney-enhanced backend they once ran alongside is gone.

Each function's signature mirrors its server.py counterpart exactly (same
parameter names/defaults); server.py calls them by keyword, and
tests/test_server_tools.py asserts the argument mapping. These reads are also
the "fresh reads" behind the confirm gates in writes*.py. Variable-building follows
the vendored library's own conventions where they're a known-working match
for the real app's query (filters.categories/.accounts/.tags, hasNotes/
isSplit -- cross-checked in keithah/monarchmoney-enhanced@159d36e monarchmoney/monarchmoney.py's
get_transactions against the app's actual captured `Web_GetTransactionsList`
variable declaration: both take the same `$filters: TransactionFilterInput`).
Where the real app's default differs from the library's (dates, budget
window), the app's own observed behavior wins -- see each function's
docstring.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any, Optional

from . import MonarchClient, project

_client = MonarchClient()


async def _call(op_name: str, variables: dict[str, Any]) -> dict[str, Any]:
    return await _client.call(op_name, variables)


def _first_of_previous_month(today: date) -> str:
    first_of_this_month = today.replace(day=1)
    last_of_previous_month = first_of_this_month - timedelta(days=1)
    return last_of_previous_month.replace(day=1).isoformat()


def _first_of_next_month(today: date) -> str:
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    return (today.replace(day=days_in_month) + timedelta(days=1)).isoformat()


def _current_month_bounds(today: date) -> tuple[str, str]:
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    return today.replace(day=1).isoformat(), today.replace(day=days_in_month).isoformat()


_DETAILS = ("compact", "full")


def _check_detail(detail: str) -> None:
    if detail not in _DETAILS:
        raise ValueError(f"detail={detail!r} must be one of {list(_DETAILS)}")


async def list_accounts(
    include_hidden: bool = False,
    account_types: Optional[list[str]] = None,
    detail: str = "compact",
) -> dict[str, Any]:
    """detail="full" is the raw flattened account list (every account, the
    other two parameters ignored) -- the shape this tool had before compact
    became the default. detail="compact" returns slim rows plus asset /
    liability / net-worth totals; hidden accounts are dropped unless
    include_hidden, and account_types (type names, case-insensitive, e.g.
    "depository") restricts the rows AND the totals."""
    _check_detail(detail)
    data = await _call("Web_GetAccountsPage", {"filters": {}})
    if detail == "full":
        return project.accounts(data)
    return project.accounts_compact(
        data, include_hidden=include_hidden, account_types=account_types
    )


async def get_categories() -> dict[str, Any]:
    data = await _call("Common_GetCategories", {})
    return project.categories(data)


async def get_tags() -> dict[str, Any]:
    data = await _call(
        "Common_GetHouseholdTransactionTags", {"includeTransactionCount": False}
    )
    return project.tags(data)


async def get_budgets(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict[str, Any]:
    """startDate/endDate are required (Date!) by the API even though this
    tool's own params are optional -- default window matches
    keithah/monarchmoney-enhanced@159d36e monarchmoney/services/budget_service.py:59-67 (1st of the
    previous calendar month through 1st of the next), so Claude sees the
    same default window it always has."""
    today = date.today()
    if not start_date:
        start_date = _first_of_previous_month(today)
    if not end_date:
        end_date = _first_of_next_month(today)
    data = await _call(
        "Common_GetJointPlanningData", {"startDate": start_date, "endDate": end_date}
    )
    return project.budgets(data)


async def get_cashflow_summary() -> dict[str, Any]:
    data = await _call(
        "Web_GetTransactionsPage",
        {"filters": {"transactionVisibility": "non_hidden_transactions_only"}},
    )
    return project.cashflow_summary(data)


# Largest page of detail="full" rows (the raw shape is ~1.5K chars a row).
FULL_DETAIL_MAX_LIMIT = 25
COMPACT_MAX_LIMIT = 200
# visibility -> transactionVisibility. The app sends transactionVisibility on
# EVERY call and only 'non_hidden_transactions_only' has ever been observed.
# Omitting the key might silently equal "visible", and a hidden/all value has
# never been captured, so anything but "visible" is refused rather than guessed.
_VISIBILITY = {"visible": "non_hidden_transactions_only"}
_ORDER_BY = ("date",)  # the only TransactionOrdering value confirmed


async def fetch_transactions_raw(
    limit: int = 100,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: str = "",
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    tag_ids: Optional[list[str]] = None,
    merchant_ids: Optional[list[str]] = None,
    has_notes: Optional[bool] = None,
    is_split: Optional[bool] = None,
    credits_only: Optional[bool] = None,
    visibility: str = "visible",
    order_by: str = "date",
) -> dict[str, Any]:
    """Raw, unbudgeted Web_GetTransactionsList projection (the shape
    get_transactions had before compact output). Internal callers that need
    raw rows (e.g. the merchant-name safety check in writes.py) use this; the
    public tool is get_transactions."""
    if visibility in ("all", "hidden"):
        raise ValueError(
            f"visibility={visibility!r} is not yet supported -- needs a captured "
            "app request (api-recon): only the 'non_hidden_transactions_only' "
            "value of Monarch's transactionVisibility filter has been observed"
        )
    if visibility not in _VISIBILITY:
        raise ValueError(f"visibility={visibility!r} must be 'visible'")
    if order_by not in _ORDER_BY:
        raise ValueError(f"order_by={order_by!r} must be one of {list(_ORDER_BY)}")
    filters: dict[str, Any] = {
        "search": search,
        "categories": category_ids or [],
        "accounts": account_ids or [],
        "tags": tag_ids or [],
        "transactionVisibility": _VISIBILITY[visibility],
    }
    if merchant_ids:
        filters["merchants"] = merchant_ids
    if has_notes is not None:
        filters["hasNotes"] = has_notes
    if is_split is not None:
        filters["isSplit"] = is_split
    if credits_only:  # only ever sent as True; False/None omit the key
        filters["creditsOnly"] = True
    if start_date and end_date:
        filters["startDate"] = start_date
        filters["endDate"] = end_date
    elif bool(start_date) != bool(end_date):
        raise ValueError("start_date and end_date must be given together, not just one")

    data = await _call(
        "Web_GetTransactionsList",
        {"offset": offset, "limit": limit, "orderBy": order_by, "filters": filters},
    )
    return project.transactions(data)


async def get_transactions(
    limit: int = 50,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: str = "",
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    tag_ids: Optional[list[str]] = None,
    merchant_ids: Optional[list[str]] = None,
    has_notes: Optional[bool] = None,
    is_split: Optional[bool] = None,
    credits_only: Optional[bool] = None,
    visibility: str = "visible",
    order_by: str = "date",
    detail: str = "compact",
    total_count_only: bool = False,
) -> dict[str, Any]:
    """Compact by default (project.transactions_compact); detail="full"
    returns the raw projection plus truncated/next_offset envelope fields and
    RAISES if limit > FULL_DETAIL_MAX_LIMIT (no silent clamping). Compact
    limit may be at most COMPACT_MAX_LIMIT (also an error above, not clamped).
    Every page is trimmed to project.TRANSACTIONS_BUDGET_CHARS serialized
    characters; trimming sets truncated and next_offset. total_count_only
    fetches one row and returns just {total_count}. `merchant_ids` maps to
    filters.merchants, which is UNVERIFIED against a live call."""
    _check_detail(detail)
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    cap = FULL_DETAIL_MAX_LIMIT if detail == "full" else COMPACT_MAX_LIMIT
    if limit > cap and not total_count_only:
        hint = (
            f"use detail='compact' or limit<={cap}" if detail == "full"
            else f"use limit<={cap} and page with next_offset"
        )
        raise ValueError(f"limit={limit} exceeds the maximum {cap} for detail={detail!r}; {hint}")
    limit = 1 if total_count_only else limit
    raw = await fetch_transactions_raw(
        limit=limit, offset=offset, start_date=start_date, end_date=end_date,
        search=search, category_ids=category_ids, account_ids=account_ids,
        tag_ids=tag_ids, merchant_ids=merchant_ids, has_notes=has_notes,
        is_split=is_split, credits_only=credits_only, visibility=visibility,
        order_by=order_by,
    )
    if total_count_only:
        return {"total_count": (raw.get("allTransactions") or {}).get("totalCount")}
    if detail == "full":
        return project.transactions_full(raw, offset=offset, limit=limit)
    return project.transactions_compact(raw, offset=offset, limit=limit)


async def get_transaction_details(transaction_id: str) -> dict[str, Any]:
    data = await _call(
        "Web_GetTransactionDrawer",
        {
            "id": transaction_id,
            "redirectPosted": True,
            "debugActivityLogEnabled": False,
        },
    )
    return project.transaction_details(data)


async def get_recurring_transactions(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict[str, Any]:
    """Defaults to the current calendar month -- what the real app sends
    when the Recurring tab is opened with no filter."""
    if not start_date or not end_date:
        month_start, month_end = _current_month_bounds(date.today())
        start_date = start_date or month_start
        end_date = end_date or month_end

    data = await _call(
        "Common_GetAggregatedRecurringItems",
        {
            "startDate": start_date,
            "endDate": end_date,
            "filters": {},
            "includeFinancialInsights": False,
        },
    )
    return project.recurring_transactions(data)


async def get_recurring_merchant_reviews() -> dict[str, Any]:
    """Streams awaiting confirmation in the Recurring tab. Sends the same
    `includeLiabilities: true` the web app does."""
    data = await _call("Common_GetRecurringStreams", {"includeLiabilities": True})
    return project.recurring_merchant_reviews(data)


async def get_transaction_rules() -> dict[str, Any]:
    data = await _call("Web_GetTransactionRules", {})
    return project.transaction_rules(data)


async def get_account_type_options() -> dict[str, Any]:
    """Added 2026-09-28 as create_manual_account's discoverability
    companion (same role get_categories/get_tags play for other writes) --
    lists every valid (type.name, subtype.name) pair."""
    data = await _call("Common_GetAccountTypeOptions", {})
    return project.account_type_options(data)


async def get_merchants(
    search: Optional[str] = None,
    limit: int = 100,
    include_zero_txn: bool = False,
    offset: int = 0,
) -> dict[str, Any]:
    """Promotes the hand-written Common_SearchMerchantsByName (previously only
    update_merchant's duplicate-name pre-check) to a public read. `search` is
    a substring match on the merchant name; None/"" lists merchants (order is
    server-defined without `search`). Page with `offset`; when
    `more_may_exist` the result carries `next_offset`."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    variables: dict[str, Any] = {"limit": limit, "offset": offset}
    if search:
        variables["search"] = search.strip()
    data = await _call("Common_SearchMerchantsByName", variables)
    return project.merchants(data, limit=limit, include_zero_txn=include_zero_txn,
                             offset=offset)


_INTERVALS = ("auto", "day", "week", "month")


def _parse_day(value: str, what: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what}={value!r} must be 'YYYY-MM-DD'") from None


async def get_net_worth_history(
    start_date: str, end_date: Optional[str] = None, interval: str = "auto"
) -> dict[str, Any]:
    """Common_GetAggregateSnapshots, downsampled client-side. auto = day for
    spans <= 60 days, week for <= 1 year, else month. end_date defaults to
    today. The request's endDate is sent ONLY when end_date is given and is not
    today (the web app always sends null; a concrete endDate was never
    observed), otherwise null. The server's adaptive granularity
    (useAdaptiveGranularity) may return coarser points than `interval` for
    long ranges, so "day" over a long span can still come back coarser; each
    returned point is labelled with its own snapshot date, not a bucket
    boundary."""
    if interval not in _INTERVALS:
        raise ValueError(f"interval={interval!r} must be one of {list(_INTERVALS)}")
    start = _parse_day(start_date, "start_date")
    end = _parse_day(end_date, "end_date") if end_date else date.today()
    if end < start:
        raise ValueError("end_date must not be before start_date")
    if interval == "auto":
        span = (end - start).days
        interval = "day" if span <= 60 else "week" if span <= 366 else "month"
    data = await _call(
        "Common_GetAggregateSnapshots",
        {
            "filters": {
                "startDate": start.isoformat(),
                "endDate": (
                    end.isoformat() if end_date and end != date.today() else None
                ),
                "useAdaptiveGranularity": True,
            }
        },
    )
    return project.net_worth_history(data, interval)


async def get_credit_score_history(start_date: str, end_date: str) -> dict[str, Any]:
    start = _parse_day(start_date, "start_date")
    end = _parse_day(end_date, "end_date")
    if end < start:
        raise ValueError("end_date must not be before start_date")
    data = await _call("Common_GetSpinwheelCreditScoreSnapshots", {})
    return project.credit_score_history(data, start.isoformat(), end.isoformat())


async def get_household_members() -> dict[str, Any]:
    data = await _call("Common_GetHouseholdMembers", {})
    return project.household_members(data)


async def get_businesses() -> dict[str, Any]:
    data = await _call("Common_GetBusinessEntities", {})
    return project.businesses(data)


async def get_goals(include_archived: bool = False) -> dict[str, Any]:
    """Savings goals; archived goals are excluded unless include_archived."""
    data = await _call("Common_SavingsGoalsWithThisMonthBalances", {})
    return project.goals(data, include_archived=include_archived)
