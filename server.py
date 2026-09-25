#!/usr/bin/env python3
"""
Monarch Money MCP server.

Exposes read/write tools over your Monarch account so Claude can help clean
things up conversationally (find uncategorized transactions, recategorize, tag,
review budgets, etc.).

Auth: reuses the ENCRYPTED session created by auth.py. Run auth.py once before
starting the server. This process never sees your password -- only the saved
Monarch token, decrypted with the strong key in ~/.monarch-mcp/session.key.

Wraps `monarchmoney-enhanced`, pinned to an audited commit (see README).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

import backend
import config
from monarch_client import reads as client_reads
from monarch_client import writes as client_writes

mcp = FastMCP("monarch")

_client: Optional[Any] = None


def _mm() -> Any:
    """
    Return a session-authenticated legacy MonarchMoney client, loading it
    (and importing monarchmoney-enhanced) once, lazily -- so a server
    running entirely on the monarch_client backend
    (MONARCH_MCP_BACKEND=client) never imports monarchmoney/gql at all.
    """
    global _client
    if _client is not None:
        return _client

    import sys

    # Rule tools (create/preview/get_transaction_rules) need the patched
    # library at _audit -- the pip-installed package silently drops
    # merchantNameCriteria/originalStatementCriteria (both on create AND
    # preview) and several rule actions, and its get_transaction_rules read
    # fragment doesn't request those criteria fields either. See
    # monarch-mcp memory. _audit is a strict superset (same pinned commit +
    # additive fixes), so prefer it for everything.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_audit"))
    from monarchmoney import MonarchMoney

    if not config.session_exists():
        here = Path(__file__).resolve().parent
        raise RuntimeError(
            "No Monarch session found. Run the one-time login first:\n"
            f"  {here}/.venv/bin/python {here}/auth.py"
        )

    key = config.load_or_create_key()
    mm = config.new_client(key)
    mm.load_session(str(config.SESSION_FILE))
    _client = mm
    return mm


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #

@mcp.tool()
async def list_accounts() -> dict[str, Any]:
    """List all accounts (name, type, balance, institution)."""
    return await backend.dispatch(
        "list_accounts",
        library_call=lambda: _mm().get_accounts(),
        client_call=client_reads.list_accounts,
    )


@mcp.tool()
async def get_categories() -> dict[str, Any]:
    """List all transaction categories and their groups (id, name)."""
    return await backend.dispatch(
        "get_categories",
        library_call=lambda: _mm().get_transaction_categories(),
        client_call=client_reads.get_categories,
    )


@mcp.tool()
async def get_tags() -> dict[str, Any]:
    """List all transaction tags (id, name, color)."""
    return await backend.dispatch(
        "get_tags",
        library_call=lambda: _mm().get_transaction_tags(),
        client_call=client_reads.get_tags,
    )


@mcp.tool()
async def get_budgets(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict[str, Any]:
    """Get budgets. Optional start_date/end_date in 'YYYY-MM-DD' format."""
    return await backend.dispatch(
        "get_budgets",
        library_call=lambda: _mm().get_budgets(start_date=start_date, end_date=end_date),
        client_call=lambda: client_reads.get_budgets(
            start_date=start_date, end_date=end_date
        ),
    )


@mcp.tool()
async def get_cashflow_summary() -> dict[str, Any]:
    """Get the transactions summary (income/expense totals, counts)."""
    return await backend.dispatch(
        "get_cashflow_summary",
        library_call=lambda: _mm().get_transactions_summary(),
        client_call=client_reads.get_cashflow_summary,
    )


@mcp.tool()
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
    """
    Fetch transactions with filters. Dates are 'YYYY-MM-DD'.

    - search: free-text match on merchant/notes; "" for all.
    - category_ids / account_ids / tag_ids: filter to those ids.

    To find items needing cleanup (uncategorized / flagged for review), fetch a
    date range and inspect each transaction's category / needsReview fields.
    """
    return await backend.dispatch(
        "get_transactions",
        library_call=lambda: _mm().get_transactions(
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
            search=search,
            category_ids=category_ids,
            account_ids=account_ids,
            tag_ids=tag_ids,
            has_notes=has_notes,
            is_split=is_split,
        ),
        client_call=lambda: client_reads.get_transactions(
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
            search=search,
            category_ids=category_ids,
            account_ids=account_ids,
            tag_ids=tag_ids,
            has_notes=has_notes,
            is_split=is_split,
        ),
    )


@mcp.tool()
async def get_transaction_details(transaction_id: str) -> dict[str, Any]:
    """Get full details for one transaction (splits, category, tags, notes)."""
    return await backend.dispatch(
        "get_transaction_details",
        library_call=lambda: _mm().get_transaction_details(transaction_id),
        client_call=lambda: client_reads.get_transaction_details(transaction_id),
    )


@mcp.tool()
async def get_recurring_transactions(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict[str, Any]:
    """
    List recurring transaction streams (the Recurring tab) with merchant, amount,
    frequency, and account for each. Dates are 'YYYY-MM-DD'.

    Bank re-links periodically spawn a fresh merchant id for an existing recurring
    charge (e.g. a card reissue), splitting one subscription/bill into two streams
    under near-identical names. To find these: group streams by merchant name
    (case-insensitively) or by (amount, frequency, category) and inspect any group
    with more than one entry -- but confirm they're really the same thing before
    merging (different services can coincidentally share a price/category).
    To merge, rename every transaction under the duplicate merchant id to the
    canonical merchant's exact name via update_transaction(merchant_name=...) --
    Monarch collapses transactions under a merchant whose name matches exactly, so
    renaming == merging. Find those transactions with get_transactions(search=...).
    """
    return await backend.dispatch(
        "get_recurring_transactions",
        library_call=lambda: _mm().get_recurring_transactions(
            start_date=start_date, end_date=end_date
        ),
        client_call=lambda: client_reads.get_recurring_transactions(
            start_date=start_date, end_date=end_date
        ),
    )


# --------------------------------------------------------------------------- #
# Write tools
# --------------------------------------------------------------------------- #

@mcp.tool()
async def recategorize_transaction(
    transaction_id: str, category_id: str
) -> dict[str, Any]:
    """Set a transaction's category. Get valid category_id from get_categories."""
    return await backend.dispatch(
        "recategorize_transaction",
        library_call=lambda: _mm().update_transaction(
            transaction_id=transaction_id, category_id=category_id
        ),
        client_call=lambda: client_writes.recategorize_transaction(
            transaction_id=transaction_id, category_id=category_id
        ),
        mutating=True,
    )


@mcp.tool()
async def update_transaction(
    transaction_id: str,
    category_id: Optional[str] = None,
    merchant_name: Optional[str] = None,
    amount: Optional[float] = None,
    date: Optional[str] = None,
    hide_from_reports: Optional[bool] = None,
    needs_review: Optional[bool] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """
    Update fields on a transaction. Only pass the fields you want to change.
    date is 'YYYY-MM-DD'. Use get_categories for a valid category_id.
    """
    return await backend.dispatch(
        "update_transaction",
        library_call=lambda: _mm().update_transaction(
            transaction_id=transaction_id,
            category_id=category_id,
            merchant_name=merchant_name,
            amount=amount,
            date=date,
            hide_from_reports=hide_from_reports,
            needs_review=needs_review,
            notes=notes,
        ),
        client_call=lambda: client_writes.update_transaction(
            transaction_id,
            category_id=category_id,
            merchant_name=merchant_name,
            amount=amount,
            date=date,
            hide_from_reports=hide_from_reports,
            needs_review=needs_review,
            notes=notes,
        ),
        mutating=True,
    )


@mcp.tool()
async def set_transaction_tags(
    transaction_id: str, tag_ids: list[str]
) -> dict[str, Any]:
    """Set (replace) the tags on a transaction. Use get_tags for valid ids."""
    return await backend.dispatch(
        "set_transaction_tags",
        library_call=lambda: _mm().set_transaction_tags(
            transaction_id=transaction_id, tag_ids=tag_ids
        ),
        client_call=lambda: client_writes.set_transaction_tags(
            transaction_id=transaction_id, tag_ids=tag_ids
        ),
        mutating=True,
    )


@mcp.tool()
async def create_tag(name: str, color: str) -> dict[str, Any]:
    """Create a new transaction tag. color is a hex string like '#22aa55'."""
    return await backend.dispatch(
        "create_tag",
        library_call=lambda: _mm().create_transaction_tag(name=name, color=color),
        client_call=lambda: client_writes.create_tag(name=name, color=color),
        mutating=True,
    )


@mcp.tool()
async def get_transaction_rules() -> dict[str, Any]:
    """
    List all transaction rules (auto-categorization rules), in priority order.
    Each rule's criteria (merchant_criteria, merchant_name_criteria,
    original_statement_criteria, amount_criteria, category_ids, account_ids) and
    actions (set_category_action, set_merchant_action, etc.) are returned in full.
    Use this before creating a new rule to check for an existing/overlapping one.
    """
    return await backend.dispatch(
        "get_transaction_rules",
        library_call=lambda: _mm().get_transaction_rules(),
        client_call=client_reads.get_transaction_rules,
    )


@mcp.tool()
async def preview_transaction_rule(
    merchant_name_criteria: Optional[list[dict[str, str]]] = None,
    original_statement_criteria: Optional[list[dict[str, str]]] = None,
    amount_criteria: Optional[dict[str, Any]] = None,
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    set_category_action: Optional[str] = None,
) -> dict[str, Any]:
    """
    Preview which EXISTING transactions a candidate rule would match and what it
    would set on them, WITHOUT creating the rule or changing anything. ALWAYS call
    this before create_transaction_rule and sanity-check totalCount/results against
    what you actually intend to match -- rule criteria can silently match the wrong
    set (e.g. amount_criteria's isExpense flag: True = the amount interpreted as a
    real expense/debit, False = a credit/incoming-payment amount; getting this
    backwards is an easy, quiet mistake -- verified via a live HAR capture 2026-09).

    - merchant_name_criteria: e.g. [{"operator": "contains", "value": "Capital One"}]
      (this, not merchant_criteria, is what the UI actually uses for name matching)
    - original_statement_criteria: same shape, matches raw plaidName/statement text
    - amount_criteria: e.g. {"operator": "gt", "isExpense": False, "value": 0}
      (isExpense False = matches positive/credit amounts; True = matches real
      expenses regardless of raw sign)
    - category_ids / account_ids: restrict to transactions currently in these
      categories/accounts
    - set_category_action: the category_id the rule would assign (for preview
      display only -- pass the id from get_categories)
    """
    return await backend.dispatch(
        "preview_transaction_rule",
        library_call=lambda: _mm().preview_transaction_rule(
            merchant_name_criteria=merchant_name_criteria,
            original_statement_criteria=original_statement_criteria,
            amount_criteria=amount_criteria,
            category_ids=category_ids,
            account_ids=account_ids,
            set_category_action=set_category_action,
        ),
        client_call=lambda: client_writes.preview_transaction_rule(
            merchant_name_criteria=merchant_name_criteria,
            original_statement_criteria=original_statement_criteria,
            amount_criteria=amount_criteria,
            category_ids=category_ids,
            account_ids=account_ids,
            set_category_action=set_category_action,
        ),
        # NOT mutating=True: this is a QUERY server-side (confirmed by the
        # `query` keyword in its vendored text, and by re-checking rule
        # state after calling it live) -- it's grouped here because it's
        # write-adjacent, not because it writes anything. Safe to soak via
        # MONARCH_MCP_BACKEND=dual.
    )


@mcp.tool()
async def create_transaction_rule(
    merchant_name_criteria: Optional[list[dict[str, str]]] = None,
    original_statement_criteria: Optional[list[dict[str, str]]] = None,
    amount_criteria: Optional[dict[str, Any]] = None,
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    set_category_action: Optional[str] = None,
    apply_to_existing_transactions: bool = False,
) -> dict[str, Any]:
    """
    Create a new auto-categorization rule. ALWAYS call preview_transaction_rule
    with the identical criteria first and confirm the match set is what you
    intend -- a wrong rule silently miscategorizes every future matching
    transaction, which is much harder to notice than a single bad transaction.

    Criteria/actions use the same shapes as preview_transaction_rule. Set
    apply_to_existing_transactions=True only if you also want it retroactively
    applied to every matching historical transaction (defaults to False --
    forward-looking only, which is usually what you want if you've already
    fixed the historical ones by hand).
    """
    return await backend.dispatch(
        "create_transaction_rule",
        library_call=lambda: _mm().create_transaction_rule(
            merchant_name_criteria=merchant_name_criteria,
            original_statement_criteria=original_statement_criteria,
            amount_criteria=amount_criteria,
            category_ids=category_ids,
            account_ids=account_ids,
            set_category_action=set_category_action,
            apply_to_existing_transactions=apply_to_existing_transactions,
        ),
        client_call=lambda: client_writes.create_transaction_rule(
            merchant_name_criteria=merchant_name_criteria,
            original_statement_criteria=original_statement_criteria,
            amount_criteria=amount_criteria,
            category_ids=category_ids,
            account_ids=account_ids,
            set_category_action=set_category_action,
            apply_to_existing_transactions=apply_to_existing_transactions,
        ),
        mutating=True,
    )


async def _library_delete_transaction_rule(rule_id: str) -> dict[str, Any]:
    ok = await _mm().delete_transaction_rule(rule_id)
    return {"deleted_flag": ok}


@mcp.tool()
async def delete_transaction_rule(rule_id: str) -> dict[str, Any]:
    """
    Delete a transaction rule by id (from get_transaction_rules). Note: the API's
    `deleted` flag is unreliable (returns False even on success; only an actual
    failure raises an error) -- verify by calling get_transaction_rules again.
    """
    return await backend.dispatch(
        "delete_transaction_rule",
        library_call=lambda: _library_delete_transaction_rule(rule_id),
        client_call=lambda: client_writes.delete_transaction_rule(rule_id),
        mutating=True,
    )


@mcp.tool()
async def mark_stream_as_not_recurring(stream_id: str) -> dict[str, Any]:
    """
    Dismiss a recurring transaction stream (get its id from get_recurring_transactions).
    Use when a subscription was cancelled but old transactions still get grouped as
    recurring, or one-time purchases got mistakenly detected as a pattern. NOT for
    duplicate streams caused by a merchant split -- merge those instead (see
    get_recurring_transactions' docstring); once merged, the duplicate stream
    disappears on its own without needing this.
    """
    return await backend.dispatch(
        "mark_stream_as_not_recurring",
        library_call=lambda: _library_mark_stream_as_not_recurring(stream_id),
        client_call=lambda: client_writes.mark_stream_as_not_recurring(stream_id),
        mutating=True,
    )


async def _library_mark_stream_as_not_recurring(stream_id: str) -> dict[str, Any]:
    ok = await _mm().mark_stream_as_not_recurring(stream_id=stream_id)
    return {"success": ok}


if __name__ == "__main__":
    mcp.run()
