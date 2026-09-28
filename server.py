#!/usr/bin/env python3
"""
Monarch Money MCP server.

Exposes read/write tools over your Monarch account so Claude can help clean
things up conversationally (find uncategorized transactions, recategorize, tag,
review budgets, etc.).

Auth: reuses an access token imported from an already-completed api-recon
Monarch login (see monarch_client/auth.py). This server never sees your
password -- only the saved Monarch session, exported via `recon
export-session`.

Built entirely on `monarch_client` (this repo's own package, see its module
docstring) -- no dependency on the abandoned `monarchmoney-enhanced` library.
That dependency existed through 2026-09-28 as a legacy fallback backend
(dual-run against monarch_client for parity verification, then flipped once
production hours confirmed the two backends agreed) -- see git history
(commit removing backend.py) for that migration if useful context.
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from monarch_client import reads as client_reads
from monarch_client import writes as client_writes

mcp = FastMCP("monarch")


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #

@mcp.tool()
async def list_accounts() -> dict[str, Any]:
    """List all accounts (name, type, balance, institution)."""
    return await client_reads.list_accounts()


@mcp.tool()
async def get_categories() -> dict[str, Any]:
    """List all transaction categories and their groups (id, name)."""
    return await client_reads.get_categories()


@mcp.tool()
async def get_tags() -> dict[str, Any]:
    """List all transaction tags (id, name, color)."""
    return await client_reads.get_tags()


@mcp.tool()
async def get_budgets(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict[str, Any]:
    """Get budgets. Optional start_date/end_date in 'YYYY-MM-DD' format."""
    return await client_reads.get_budgets(start_date=start_date, end_date=end_date)


@mcp.tool()
async def get_cashflow_summary() -> dict[str, Any]:
    """Get the transactions summary (income/expense totals, counts)."""
    return await client_reads.get_cashflow_summary()


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
    return await client_reads.get_transactions(
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
    return await client_reads.get_transaction_details(transaction_id)


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
    return await client_reads.get_recurring_transactions(
        start_date=start_date, end_date=end_date
    )


# --------------------------------------------------------------------------- #
# Write tools
# --------------------------------------------------------------------------- #

@mcp.tool()
async def recategorize_transaction(
    transaction_id: str, category_id: str
) -> dict[str, Any]:
    """Set a transaction's category. Get valid category_id from get_categories."""
    return await client_writes.recategorize_transaction(
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
    return await client_writes.update_transaction(
        transaction_id,
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
    return await client_writes.set_transaction_tags(
        transaction_id=transaction_id, tag_ids=tag_ids
    )


@mcp.tool()
async def create_tag(name: str, color: str) -> dict[str, Any]:
    """Create a new transaction tag. color is a hex string like '#22aa55'."""
    return await client_writes.create_tag(name=name, color=color)


@mcp.tool()
async def delete_tag(tag_id: str) -> dict[str, Any]:
    """Delete a transaction tag by id (from get_tags). Does not affect the
    transactions it was applied to, only removes the tag itself."""
    return await client_writes.delete_tag(tag_id)


@mcp.tool()
async def get_account_type_options() -> dict[str, Any]:
    """List every valid (type.name, subtype.name) pair for create_manual_account.
    Use before calling it -- Monarch rejects an unlisted pair."""
    return await client_reads.get_account_type_options()


@mcp.tool()
async def get_transaction_rules() -> dict[str, Any]:
    """
    List all transaction rules (auto-categorization rules), in priority order.
    Each rule's criteria (merchant_criteria, merchant_name_criteria,
    original_statement_criteria, amount_criteria, category_ids, account_ids) and
    actions (set_category_action, set_merchant_action, etc.) are returned in full.
    Use this before creating a new rule to check for an existing/overlapping one.
    """
    return await client_reads.get_transaction_rules()


@mcp.tool()
async def preview_transaction_rule(
    merchant_name_criteria: Optional[list[dict[str, str]]] = None,
    original_statement_criteria: Optional[list[dict[str, str]]] = None,
    amount_criteria: Optional[dict[str, Any]] = None,
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    set_category_action: Optional[str] = None,
    set_merchant_name: Optional[str] = None,
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
    - set_merchant_name: the EXACT existing merchant name the rule would rename
      matches to (get it from an existing transaction, e.g. get_transactions or
      get_transaction_rules -- never guess or pass an id). This is enforced: a
      name that doesn't exactly match an existing merchant raises an error here,
      because Monarch itself does not validate this and will silently create a
      brand-new garbage merchant named after whatever string it's given.
    """
    return await client_writes.preview_transaction_rule(
        merchant_name_criteria=merchant_name_criteria,
        original_statement_criteria=original_statement_criteria,
        amount_criteria=amount_criteria,
        category_ids=category_ids,
        account_ids=account_ids,
        set_category_action=set_category_action,
        set_merchant_name=set_merchant_name,
    )


@mcp.tool()
async def create_transaction_rule(
    merchant_name_criteria: Optional[list[dict[str, str]]] = None,
    original_statement_criteria: Optional[list[dict[str, str]]] = None,
    amount_criteria: Optional[dict[str, Any]] = None,
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    set_category_action: Optional[str] = None,
    set_merchant_name: Optional[str] = None,
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

    set_merchant_name must be an EXACT existing merchant name (see
    preview_transaction_rule's docstring) -- this is enforced, since Monarch
    itself will silently create a new garbage merchant instead of erroring on
    a name that doesn't match one exactly.
    """
    return await client_writes.create_transaction_rule(
        merchant_name_criteria=merchant_name_criteria,
        original_statement_criteria=original_statement_criteria,
        amount_criteria=amount_criteria,
        category_ids=category_ids,
        account_ids=account_ids,
        set_category_action=set_category_action,
        set_merchant_name=set_merchant_name,
        apply_to_existing_transactions=apply_to_existing_transactions,
    )


@mcp.tool()
async def delete_transaction_rule(rule_id: str) -> dict[str, Any]:
    """
    Delete a transaction rule by id (from get_transaction_rules). Note: the API's
    `deleted` flag is unreliable (returns False even on success; only an actual
    failure raises an error) -- verify by calling get_transaction_rules again.
    """
    return await client_writes.delete_transaction_rule(rule_id)


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
    return await client_writes.mark_stream_as_not_recurring(stream_id)


@mcp.tool()
async def create_transaction(
    account_id: str,
    date: str,
    amount: float,
    merchant_name: str,
    category_id: str,
) -> dict[str, Any]:
    """
    Create a manual transaction on a manual (non-bank-linked) account -- e.g. cash
    spending. date is 'YYYY-MM-DD'. amount is signed like every other tool here:
    negative = expense, positive = income/credit. Use get_categories for category_id
    and list_accounts for account_id (must be a manual account, not one synced from a
    bank). A bad account_id/category_id returns a real error, doesn't fail silently.
    """
    return await client_writes.create_transaction(
        account_id=account_id,
        date=date,
        amount=amount,
        merchant_name=merchant_name,
        category_id=category_id,
    )


@mcp.tool()
async def create_manual_account(
    name: str,
    account_type: str,
    account_subtype: str,
    display_balance: float,
    include_in_net_worth: bool = True,
) -> dict[str, Any]:
    """
    Create a manual (non-bank-linked) account, e.g. cash or a manually-tracked
    asset. account_type/account_subtype must be a valid pair from
    get_account_type_options -- call that first. A bad pair returns a real error,
    doesn't fail silently.
    """
    return await client_writes.create_manual_account(
        name=name,
        account_type=account_type,
        account_subtype=account_subtype,
        display_balance=display_balance,
        include_in_net_worth=include_in_net_worth,
    )


if __name__ == "__main__":
    mcp.run()
