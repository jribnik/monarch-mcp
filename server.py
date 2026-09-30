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
from monarch_client import writes_accounts as client_writes_accounts
from monarch_client import writes_categories as client_writes_categories
from monarch_client import writes_splits_rules as client_writes_splits_rules

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
    and list_accounts for account_id -- account_id MUST be a manual account (this is
    enforced: a bank-linked account_id is rejected before the mutation ever fires, since
    a linked account's transactions are supposed to come from the bank sync, not manual
    entry). A nonexistent account_id/category_id also returns a real error, doesn't fail
    silently. merchant_name is NOT validated against existing merchants (unlike
    create_transaction_rule's set_merchant_name) -- a new string creates a new merchant,
    same as the real app's manual-entry form. Made a mistake? Use delete_transaction.
    """
    return await client_writes.create_transaction(
        account_id=account_id,
        date=date,
        amount=amount,
        merchant_name=merchant_name,
        category_id=category_id,
    )


@mcp.tool()
async def delete_transaction(transaction_id: str) -> dict[str, Any]:
    """
    Delete a transaction by id. Works on any transaction this account can see
    (manual or bank-synced), same as the real app's delete button -- not limited to
    ones created via create_transaction. There is no undo; use get_transaction_details
    first if you're not certain you have the right id.
    """
    return await client_writes.delete_transaction(transaction_id)


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
    get_account_type_options -- call that first. A bad pair raises a real error
    (the call fails loudly), doesn't fail silently.
    """
    return await client_writes.create_manual_account(
        name=name,
        account_type=account_type,
        account_subtype=account_subtype,
        display_balance=display_balance,
        include_in_net_worth=include_in_net_worth,
    )


@mcp.tool()
async def create_category_group(
    name: str,
    type: str = "expense",
    group_level_budgeting_enabled: bool = False,
    rollover_enabled: bool = False,
    rollover_start_month: Optional[str] = None,
    rollover_type: str = "monthly",
) -> dict[str, Any]:
    """Create a category group. type is 'expense', 'income' or 'transfer'.
    rollover_start_month is an ISO date (first of a month, default current
    month). Returns the new group (id, name, type, order, ...)."""
    return await client_writes_categories.create_category_group(
        name, type, group_level_budgeting_enabled, rollover_enabled,
        rollover_start_month, rollover_type)


@mcp.tool()
async def update_category_group(
    group_id: str,
    name: Optional[str] = None,
    group_level_budgeting_enabled: Optional[bool] = None,
    rollover_enabled: Optional[bool] = None,
    rollover_start_month: Optional[str] = None,
    rollover_type: Optional[str] = None,
    rollover_starting_balance: Optional[float] = None,
    budget_variability: Optional[str] = None,
) -> dict[str, Any]:
    """Update a category group (rename, budgeting flags). Only the fields you
    pass are changed. Get group ids from get_categories."""
    return await client_writes_categories.update_category_group(
        group_id, name, group_level_budgeting_enabled, rollover_enabled,
        rollover_start_month, rollover_type, rollover_starting_balance,
        budget_variability)


@mcp.tool()
async def delete_category_group(
    group_id: str, move_to_group_id: Optional[str] = None
) -> dict[str, Any]:
    """Delete a category group. Fails with "Category group is not empty" if it
    still has categories -- pass move_to_group_id to re-home them, or move
    them first (update_category with group_id). Returns {deleted, errors}."""
    return await client_writes_categories.delete_category_group(
        group_id, move_to_group_id)


@mcp.tool()
async def create_category(
    name: str,
    group_id: str,
    icon: str = "❓",
    type: str = "expense",
    exclude_from_budget: bool = False,
    budget_variability: str = "flexible",
    rollover_enabled: bool = False,
    rollover_start_month: Optional[str] = None,
    rollover_starting_balance: float = 0,
    rollover_frequency: str = "monthly",
) -> dict[str, Any]:
    """Create a category inside a category group (group_id from
    get_categories). icon is a single emoji. Returns the new category."""
    return await client_writes_categories.create_category(
        name, group_id, icon, type, exclude_from_budget, budget_variability,
        rollover_enabled, rollover_start_month, rollover_starting_balance,
        rollover_frequency)


@mcp.tool()
async def update_category(
    category_id: str,
    name: Optional[str] = None,
    icon: Optional[str] = None,
    group_id: Optional[str] = None,
    type: Optional[str] = None,
    exclude_from_budget: Optional[bool] = None,
    budget_variability: Optional[str] = None,
    rollover_enabled: Optional[bool] = None,
    rollover_start_month: Optional[str] = None,
    rollover_starting_balance: Optional[float] = None,
    rollover_frequency: Optional[str] = None,
) -> dict[str, Any]:
    """Update a category: rename, change icon, move to another group
    (group_id), budget flags. Only the fields you pass are changed."""
    return await client_writes_categories.update_category(
        category_id, name, icon, group_id, type, exclude_from_budget,
        budget_variability, rollover_enabled, rollover_start_month,
        rollover_starting_balance, rollover_frequency)


@mcp.tool()
async def delete_category(
    category_id: str,
    move_to_category_id: Optional[str] = None,
    uncategorize_transactions: bool = False,
) -> dict[str, Any]:
    """Delete a category. You must say what happens to its transactions: pass
    move_to_category_id to reassign them, or uncategorize_transactions=True to
    knowingly leave them Uncategorized (budget history for the category is
    lost either way). With neither (or both) the call is refused before
    anything is sent. Returns {deleted, errors}."""
    return await client_writes_categories.delete_category(
        category_id, move_to_category_id=move_to_category_id,
        uncategorize_transactions=uncategorize_transactions)


@mcp.tool()
async def update_tag(tag_id: str, name: str, color: str) -> dict[str, Any]:
    """Rename and/or recolor a transaction tag (tag_id from get_tags; color is
    a hex string like '#E5484D'). Pass the existing value for whichever of
    name/color you are not changing."""
    return await client_writes_categories.update_tag(tag_id, name, color)


@mcp.tool()
async def update_merchant(
    merchant_id: str,
    name: Optional[str] = None,
    default_category_id: Optional[str] = None,
    clear_default_category: bool = False,
    default_category_application_mode: Optional[str] = None,
    is_recurring: Optional[bool] = None,
    recurring_amount: Optional[float] = None,
    recurring_is_active: Optional[bool] = None,
    recurring_frequency: Optional[str] = None,
    recurring_base_date: Optional[str] = None,
    allow_merge: bool = False,
) -> dict[str, Any]:
    """Update a merchant's name, default category, and recurring-stream
    settings. The merchant is read first and any argument left unset KEEPS its
    current value (so a plain rename preserves the default category and
    recurring stream). To remove the default category pass
    clear_default_category=True; to stop recurrence pass is_recurring=False.
    Turning recurrence on for a merchant with no stream needs
    recurring_frequency (e.g. 'monthly') and recurring_base_date (ISO date);
    recurring_amount is negative for expenses. WARNING: renaming to another
    EXISTING merchant's name MERGES the two (irreversible here), so such a
    rename is refused unless allow_merge=True (also refused if the name check
    can't be completed)."""
    return await client_writes_categories.update_merchant(
        merchant_id, name=name, default_category_id=default_category_id,
        clear_default_category=clear_default_category,
        default_category_application_mode=default_category_application_mode,
        is_recurring=is_recurring, recurring_amount=recurring_amount,
        recurring_is_active=recurring_is_active,
        recurring_frequency=recurring_frequency,
        recurring_base_date=recurring_base_date, allow_merge=allow_merge)


@mcp.tool()
async def update_account(
    account_id: str,
    name: Optional[str] = None,
    display_balance: Optional[float] = None,
    notes: Optional[str] = None,
    account_type: Optional[str] = None,
    account_subtype: Optional[str] = None,
    hide_from_list: Optional[bool] = None,
    hide_in_budget: Optional[bool] = None,
    hide_transactions_from_reports: Optional[bool] = None,
    include_in_net_worth: Optional[bool] = None,
    interest_rate: Optional[float] = None,
) -> dict:
    """Edit a MANUAL (non-bank-linked) account. Only the fields you pass change;
    everything else is preserved. notes="" clears notes. account_type and
    account_subtype must be passed together (a pair from get_account_type_options).
    Bank-linked accounts are refused. Requires writes enabled."""
    return await client_writes_accounts.update_account(
        account_id, name, display_balance, notes, account_type, account_subtype,
        hide_from_list, hide_in_budget, hide_transactions_from_reports,
        include_in_net_worth, interest_rate)


@mcp.tool()
async def delete_account(account_id: str, confirm_name: str) -> dict:
    """IRREVERSIBLE: permanently deletes a MANUAL account AND ALL ITS TRANSACTIONS
    and balance history. confirm_name must exactly equal the account's current
    name (case-sensitive) or nothing happens. Bank-linked accounts are refused.
    Only call when the user has explicitly asked to delete that specific account."""
    return await client_writes_accounts.delete_account(account_id, confirm_name)


@mcp.tool()
async def set_budget_amount(category_id: str, amount: float, month: str,
                            apply_to_future: bool = False) -> dict:
    """Set one category's budgeted amount for one month (month = 'YYYY-MM-01').
    apply_to_future defaults to False (this month only); True also overwrites the
    budget for ALL later months, so only set it when the user asked for that."""
    return await client_writes_accounts.set_budget_amount(category_id, amount, month, apply_to_future)


@mcp.tool()
async def set_flex_budget_amount(amount: float, month: str,
                                 apply_to_future: bool = False) -> dict:
    """Set the flexible-spending budget total for one month (month = 'YYYY-MM-01').
    apply_to_future defaults to False; True overwrites all later months too."""
    return await client_writes_accounts.set_flex_budget_amount(amount, month, apply_to_future)


@mcp.tool()
async def create_savings_goal(name: str, target_amount: Optional[float] = None,
                              target_date: Optional[str] = None,
                              is_sinking_fund: bool = False) -> dict:
    """Create one savings goal (target_date 'YYYY-MM-DD'). Creates the goal, then
    applies target amount/date/sinking-fund via a follow-up update."""
    return await client_writes_accounts.create_savings_goal(name, target_amount, target_date, is_sinking_fund)


@mcp.tool()
async def update_savings_goal(goal_id: str, name: Optional[str] = None,
                              target_amount: Optional[float] = None,
                              target_date: Optional[str] = None,
                              is_sinking_fund: Optional[bool] = None) -> dict:
    """Update a savings goal. Only the fields passed change."""
    return await client_writes_accounts.update_savings_goal(goal_id, name, target_amount, target_date, is_sinking_fund)


@mcp.tool()
async def set_savings_goal_budget_amount(goal_id: str, amount: float, month: str,
                                         apply_to_future: bool = False,
                                         account_id: Optional[str] = None) -> dict:
    """Set the budgeted monthly contribution to a savings goal (month = 'YYYY-MM-01').
    apply_to_future defaults to False; True overwrites all later months too."""
    return await client_writes_accounts.set_savings_goal_budget_amount(goal_id, amount, month, apply_to_future, account_id)


@mcp.tool()
async def delete_savings_goal(goal_id: str) -> dict:
    """IRREVERSIBLE: permanently deletes a savings goal and its contribution
    history. Only call when the user explicitly asked to delete that goal."""
    return await client_writes_accounts.delete_savings_goal(goal_id)

@mcp.tool()
async def split_transaction(transaction_id: str, splits: list[dict]) -> dict[str, Any]:
    """Split a transaction into 2+ parts (WRITE). Each split is
    {amount, category_id?, merchant_name?, hide_from_reports?}. Amounts are
    signed like the transaction (an expense is NEGATIVE) and must sum EXACTLY,
    to the cent, to the transaction's current amount, otherwise nothing is sent.
    Omitted category_id / merchant_name default to the original transaction's.
    Splitting an already-split transaction replaces the old split. Check
    `errors` in the result; use unsplit_transaction to undo."""
    return await client_writes_splits_rules.split_transaction(transaction_id, splits)


@mcp.tool()
async def unsplit_transaction(transaction_id: str) -> dict[str, Any]:
    """Remove a transaction's split, restoring a single transaction (WRITE)."""
    return await client_writes_splits_rules.unsplit_transaction(transaction_id)


@mcp.tool()
async def update_transaction_rule(
    rule_id: str,
    merchant_name_criteria: list[dict] | None = None,
    original_statement_criteria: list[dict] | None = None,
    amount_criteria: dict | None = None,
    category_ids: list[str] | None = None,
    account_ids: list[str] | None = None,
    set_category_action: str | None = None,
    set_merchant_name: str | None = None,
    add_tag_ids: list[str] | None = None,
    set_hide_from_reports: bool | None = None,
    review_status: str | None = None,
    needs_review_by_user_id: str | None = None,
    link_goal_id: str | None = None,
    link_savings_goal_id: str | None = None,
    split_action: dict | None = None,
    apply_to_existing_transactions: bool = False,
) -> dict[str, Any]:
    """Update an existing transaction rule (WRITE). Same criteria/actions as
    create_transaction_rule plus add_tag_ids, set_hide_from_reports,
    review_status ("needs_review" / "reviewed"), needs_review_by_user_id,
    link_goal_id, link_savings_goal_id and split_action. The rule is read first
    and merged: an argument left unset KEEPS the rule's current value; an empty
    value ([] / "" / {}) CLEARS that field. A rule must keep at least one
    criterion and one action. split_action = {"splits": [{"percent": 60,
    "category_id": ..., "merchant_name": ..., "hide_from_reports": ...}, ...]}
    with percents summing to exactly 100 (percentage splits only). set_merchant_name
    must exactly match an existing merchant. The response has no rule in it:
    confirm with get_transaction_rules. `errors` non-null (even an empty
    object, flagged with a `hint`) means the update was rejected."""
    return await client_writes_splits_rules.update_transaction_rule(
        rule_id, merchant_name_criteria=merchant_name_criteria,
        original_statement_criteria=original_statement_criteria,
        amount_criteria=amount_criteria, category_ids=category_ids, account_ids=account_ids,
        set_category_action=set_category_action, set_merchant_name=set_merchant_name,
        add_tag_ids=add_tag_ids, set_hide_from_reports=set_hide_from_reports,
        review_status=review_status, needs_review_by_user_id=needs_review_by_user_id,
        link_goal_id=link_goal_id, link_savings_goal_id=link_savings_goal_id,
        split_action=split_action,
        apply_to_existing_transactions=apply_to_existing_transactions)


if __name__ == "__main__":
    mcp.run()
