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
from monarchmoney import MonarchMoney

import config

mcp = FastMCP("monarch")

_client: Optional[MonarchMoney] = None


def _mm() -> MonarchMoney:
    """Return a session-authenticated Monarch client, loading it once."""
    global _client
    if _client is not None:
        return _client

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
    return await _mm().get_accounts()


@mcp.tool()
async def get_categories() -> dict[str, Any]:
    """List all transaction categories and their groups (id, name)."""
    return await _mm().get_transaction_categories()


@mcp.tool()
async def get_tags() -> dict[str, Any]:
    """List all transaction tags (id, name, color)."""
    return await _mm().get_transaction_tags()


@mcp.tool()
async def get_budgets(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict[str, Any]:
    """Get budgets. Optional start_date/end_date in 'YYYY-MM-DD' format."""
    return await _mm().get_budgets(start_date=start_date, end_date=end_date)


@mcp.tool()
async def get_cashflow_summary() -> dict[str, Any]:
    """Get the transactions summary (income/expense totals, counts)."""
    return await _mm().get_transactions_summary()


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
    return await _mm().get_transactions(
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
    )


@mcp.tool()
async def get_transaction_details(transaction_id: str) -> dict[str, Any]:
    """Get full details for one transaction (splits, category, tags, notes)."""
    return await _mm().get_transaction_details(transaction_id)


# --------------------------------------------------------------------------- #
# Write tools
# --------------------------------------------------------------------------- #

@mcp.tool()
async def recategorize_transaction(
    transaction_id: str, category_id: str
) -> dict[str, Any]:
    """Set a transaction's category. Get valid category_id from get_categories."""
    return await _mm().update_transaction(
        transaction_id=transaction_id, category_id=category_id
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
    return await _mm().update_transaction(
        transaction_id=transaction_id,
        category_id=category_id,
        merchant_name=merchant_name,
        amount=amount,
        date=date,
        hide_from_reports=hide_from_reports,
        needs_review=needs_review,
        notes=notes,
    )


@mcp.tool()
async def set_transaction_tags(
    transaction_id: str, tag_ids: list[str]
) -> dict[str, Any]:
    """Set (replace) the tags on a transaction. Use get_tags for valid ids."""
    return await _mm().set_transaction_tags(
        transaction_id=transaction_id, tag_ids=tag_ids
    )


@mcp.tool()
async def create_tag(name: str, color: str) -> dict[str, Any]:
    """Create a new transaction tag. color is a hex string like '#22aa55'."""
    return await _mm().create_transaction_tag(name=name, color=color)


if __name__ == "__main__":
    mcp.run()
