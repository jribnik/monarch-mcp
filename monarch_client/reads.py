"""
The 9 read-side MCP tools, reimplemented on monarch_client instead of
monarchmoney-enhanced.

Each function's signature mirrors its server.py counterpart exactly (same
parameter names/defaults) so server.py's `backend.dispatch()` can call
either implementation interchangeably (see M3). Variable-building follows
the vendored library's own conventions where they're a known-working match
for the real app's query (filters.categories/.accounts/.tags, hasNotes/
isSplit -- cross-checked in _audit/monarchmoney/monarchmoney.py's
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


async def list_accounts() -> dict[str, Any]:
    data = await _call("Web_GetAccountsPage", {"filters": {}})
    return project.accounts(data)


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
    _audit/monarchmoney/services/budget_service.py:59-67 (1st of the
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


async def get_transactions(
    limit: int = 100,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: str = "",
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    tag_ids: Optional[list[str]] = None,
    has_notes: Optional[bool] = None,
    is_split: Optional[bool] = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "search": search,
        "categories": category_ids or [],
        "accounts": account_ids or [],
        "tags": tag_ids or [],
        "transactionVisibility": "non_hidden_transactions_only",
    }
    if has_notes is not None:
        filters["hasNotes"] = has_notes
    if is_split is not None:
        filters["isSplit"] = is_split
    if start_date and end_date:
        filters["startDate"] = start_date
        filters["endDate"] = end_date
    elif bool(start_date) != bool(end_date):
        raise ValueError("start_date and end_date must be given together, not just one")

    data = await _call(
        "Web_GetTransactionsList",
        {"offset": offset, "limit": limit, "orderBy": "date", "filters": filters},
    )
    return project.transactions(data)


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


async def get_transaction_rules() -> dict[str, Any]:
    data = await _call("Web_GetTransactionRules", {})
    return project.transaction_rules(data)
