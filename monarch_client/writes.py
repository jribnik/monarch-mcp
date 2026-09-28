"""
Write-side MCP tools, reimplemented on monarch_client.

Deliberately no generic "execute_mutation" escape hatch: only the vendored,
named mutations below are callable, so a prompt-injection or a model
mistake can't reach an arbitrary mutation. Each function's signature
mirrors its server.py counterpart exactly, same as reads.py.

11 write tools -- create_tag, delete_tag, preview_transaction_rule,
create_transaction_rule, delete_transaction_rule, recategorize_transaction,
update_transaction, set_transaction_tags, mark_stream_as_not_recurring,
create_transaction, create_manual_account -- each captured and/or verified
against a dedicated, disposable monarch-sandbox account (see
operations/__init__.py's PROVENANCE for each op's exact provenance; two --
delete_transaction_rule and mark_stream_as_not_recurring -- were verified by
direct functional call rather than a UI-driven HAR capture, each documented
as an explicit exception in its own .graphql file). See operations/README.md.

delete_tag was added 2026-09-28, closing a gap left open since the
original 8-tool build (create_tag shipped with no delete counterpart) --
added while monarch-sandbox still existed, ahead of its planned deletion
(it's the safety net for developing/verifying any write, so remaining
write-path work was prioritized while it was still available -- see
project memory).

set_merchant_name (preview/create_transaction_rule) was also added
2026-09-28. Its real shape -- a merchant NAME string, not an id -- and the
need for _verify_merchant_name_exists were both discovered by live testing
against monarch-sandbox: setMerchantAction has no server-side validation at
all and silently creates a brand-new garbage merchant named after whatever
string it's given (reproduced with both a raw id and a typo'd name; preview
doesn't catch it either -- its `newName` field just echoes the input back
unresolved). See _verify_merchant_name_exists's docstring below.

create_transaction and create_manual_account were also added 2026-09-28,
closing the last two capability gaps flagged by an earlier design review
(manual transaction/account creation). Both mutations DO validate their
inputs server-side (a bad accountId/categoryId/type/subtype raises or
returns a real errors.message rather than silently succeeding, verified
live) -- unlike setMerchantAction, so neither needed a client-side
validation gate of its own.
"""

from __future__ import annotations

from typing import Any, Optional

from . import MonarchClient, project
from . import reads

_client = MonarchClient()


async def _call(op_name: str, variables: dict[str, Any]) -> dict[str, Any]:
    return await _client.call(op_name, variables)


async def _verify_merchant_name_exists(name: str) -> None:
    """setMerchantAction has NO server-side validation: verified live
    2026-09-28 against monarch-sandbox that passing a name (or, worse, a raw
    merchant id) that doesn't exactly match an existing merchant does not
    error in either preview or create -- it SILENTLY CREATES a brand-new
    garbage merchant literally named after the string given (reproduced
    twice: once with a raw id, once with a typo'd name; preview's `newName`
    field just echoes the input back unresolved in both cases, so preview
    can't catch this either). Since Monarch won't validate this for us, we
    do it here: search existing transactions for an exact merchant.name
    match before ever sending the mutation."""
    found = await reads.get_transactions(search=name, limit=25)
    real_names = {
        t["merchant"]["name"]
        for t in (found.get("allTransactions") or {}).get("results") or []
        if t.get("merchant")
    }
    if name not in real_names:
        raise ValueError(
            f"set_merchant_name={name!r} doesn't exactly match any existing "
            f"merchant found via a transaction search (found: "
            f"{sorted(real_names) or 'none'}) -- setMerchantAction silently "
            "CREATES A NEW MERCHANT with this exact string as its name if it "
            "doesn't match one already, verified live. Get the exact name "
            "from an existing transaction (e.g. get_transactions(search=...)) "
            "before retrying."
        )


def _rule_input_common(
    *,
    merchant_name_criteria: Optional[list[dict[str, str]]],
    original_statement_criteria: Optional[list[dict[str, str]]],
    amount_criteria: Optional[dict[str, Any]],
    category_ids: Optional[list[str]],
    account_ids: Optional[list[str]],
    set_category_action: Optional[str],
    set_merchant_name: Optional[str],
) -> dict[str, Any]:
    """Shared field-building for preview/create_transaction_rule -- matches
    _audit/monarchmoney/monarchmoney.py's own rule_input construction
    (always-sent keys default to None; merchantNameCriteria/
    originalStatementCriteria are added only when given, since the library
    demonstrated live that this is what the API expects).

    set_merchant_name is the real (verified live 2026-09-28) shape of
    setMerchantAction: a plain merchant NAME string, not an id -- an id
    silently creates a garbage merchant named after the id string instead of
    linking the existing one. See _verify_merchant_name_exists, which both
    callers below run first."""
    rule_input: dict[str, Any] = {
        "merchantCriteriaUseOriginalStatement": False,
        "merchantCriteria": None,
        "amountCriteria": amount_criteria,
        "categoryIds": category_ids,
        "accountIds": account_ids,
        "setCategoryAction": set_category_action,
        "addTagsAction": None,
        "setMerchantAction": set_merchant_name,
        "splitTransactionsAction": None,
    }
    if merchant_name_criteria is not None:
        rule_input["merchantNameCriteria"] = merchant_name_criteria
    if original_statement_criteria is not None:
        rule_input["originalStatementCriteria"] = original_statement_criteria
    return rule_input


async def create_tag(name: str, color: str) -> dict[str, Any]:
    data = await _call(
        "Common_CreateTransactionTag", {"input": {"name": name, "color": color}}
    )
    return project.create_tag_result(data)


async def delete_tag(tag_id: str) -> dict[str, Any]:
    """Vendored 2026-09-28 (was previously a documented gap -- create_tag
    with no delete counterpart). Verified live against monarch-sandbox:
    created a real tag, deleted it via this mutation, confirmed via a
    follow-up get_tags call the account was back to exactly its original
    5 default tags."""
    data = await _call("Common_DeleteHouseholdTransactionTag", {"tagId": tag_id})
    return project.delete_tag_result(data)


async def preview_transaction_rule(
    merchant_name_criteria: Optional[list[dict[str, str]]] = None,
    original_statement_criteria: Optional[list[dict[str, str]]] = None,
    amount_criteria: Optional[dict[str, Any]] = None,
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    set_category_action: Optional[str] = None,
    set_merchant_name: Optional[str] = None,
) -> dict[str, Any]:
    if set_merchant_name is not None:
        await _verify_merchant_name_exists(set_merchant_name)
    rule_input = _rule_input_common(
        merchant_name_criteria=merchant_name_criteria,
        original_statement_criteria=original_statement_criteria,
        amount_criteria=amount_criteria,
        category_ids=category_ids,
        account_ids=account_ids,
        set_category_action=set_category_action,
        set_merchant_name=set_merchant_name,
    )
    rule_input["applyToExistingTransactions"] = False
    data = await _call(
        "Common_PreviewTransactionRule", {"rule": rule_input, "offset": 0}
    )
    return project.preview_transaction_rule_result(data)


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
    if set_merchant_name is not None:
        await _verify_merchant_name_exists(set_merchant_name)
    rule_input = _rule_input_common(
        merchant_name_criteria=merchant_name_criteria,
        original_statement_criteria=original_statement_criteria,
        amount_criteria=amount_criteria,
        category_ids=category_ids,
        account_ids=account_ids,
        set_category_action=set_category_action,
        set_merchant_name=set_merchant_name,
    )
    rule_input["applyToExistingTransactions"] = apply_to_existing_transactions
    data = await _call("Common_CreateTransactionRuleMutationV2", {"input": rule_input})
    return project.create_transaction_rule_result(data)


async def delete_transaction_rule(rule_id: str) -> dict[str, Any]:
    data = await _call("Common_DeleteTransactionRule", {"id": rule_id})
    return project.delete_transaction_rule_result(data)


async def create_transaction(
    account_id: str,
    date: str,
    amount: float,
    merchant_name: str,
    category_id: str,
) -> dict[str, Any]:
    """Added 2026-09-28. Creates a manual transaction on a manual (non-
    Plaid-linked) account. amount is signed the same way as every other
    write op here: negative = expense, positive = credit/income (verified
    live against monarch-sandbox). Unlike set_merchant_name, no client-side
    validation gate is needed: Monarch validates accountId/categoryId
    server-side and returns a real errors.message with transaction=null on
    a bad id, verified live -- see Common_CreateTransactionMutation's
    PROVENANCE note."""
    data = await _call(
        "Common_CreateTransactionMutation",
        {
            "input": {
                "date": date,
                "shouldUpdateBalance": True,
                "accountId": account_id,
                "ownerUserId": None,
                "amount": amount,
                "merchantName": merchant_name,
                "categoryId": category_id,
            }
        },
    )
    return project.create_transaction_result(data)


async def create_manual_account(
    name: str,
    account_type: str,
    account_subtype: str,
    display_balance: float,
    include_in_net_worth: bool = True,
) -> dict[str, Any]:
    """Added 2026-09-28. Creates a manual (non-Plaid) account. account_type/
    account_subtype must be a (type.name, subtype.name) pair from
    get_account_type_options -- verified live that Monarch rejects an
    invalid pair server-side (raises), so no extra client-side gate is
    needed here either -- see Web_CreateManualAccount's PROVENANCE note."""
    data = await _call(
        "Web_CreateManualAccount",
        {
            "input": {
                "type": account_type,
                "subtype": account_subtype,
                "includeInNetWorth": include_in_net_worth,
                "name": name,
                "displayBalance": display_balance,
                "ownerUserId": None,
            }
        },
    )
    return project.create_manual_account_result(data)


async def _update_transaction(
    transaction_id: str,
    *,
    category_id: Optional[str] = None,
    merchant_name: Optional[str] = None,
    amount: Optional[float] = None,
    date: Optional[str] = None,
    hide_from_reports: Optional[bool] = None,
    needs_review: Optional[bool] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Shared by recategorize_transaction and update_transaction -- same
    mutation, same field-name mapping verified live against
    monarch-sandbox (see operations/__init__.py's PROVENANCE note: `name`
    for merchant, not `merchantName`; amount/date only sent when truthy,
    matching _audit/monarchmoney/monarchmoney.py's own guard against the
    API rejecting empty values for those two fields)."""
    input_: dict[str, Any] = {"id": transaction_id}
    if category_id is not None:
        input_["category"] = category_id
    if merchant_name is not None:
        input_["name"] = merchant_name
    if amount:
        input_["amount"] = amount
    if date:
        input_["date"] = date
    if hide_from_reports is not None:
        input_["hideFromReports"] = bool(hide_from_reports)
    if needs_review is not None:
        input_["needsReview"] = bool(needs_review)
    if notes is not None:
        input_["notes"] = notes

    data = await _call(
        "Web_TransactionDrawerUpdateTransaction",
        {"debugActivityLogEnabled": False, "input": input_},
    )
    return project.update_transaction_result(data)


async def recategorize_transaction(transaction_id: str, category_id: str) -> dict[str, Any]:
    return await _update_transaction(transaction_id, category_id=category_id)


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
    return await _update_transaction(
        transaction_id,
        category_id=category_id,
        merchant_name=merchant_name,
        amount=amount,
        date=date,
        hide_from_reports=hide_from_reports,
        needs_review=needs_review,
        notes=notes,
    )


async def set_transaction_tags(transaction_id: str, tag_ids: list[str]) -> dict[str, Any]:
    data = await _call(
        "Web_SetTransactionTags",
        {
            "debugActivityLogEnabled": False,
            "input": {"transactionId": transaction_id, "tagIds": tag_ids},
        },
    )
    return project.set_transaction_tags_result(data)


async def mark_stream_as_not_recurring(stream_id: str) -> dict[str, Any]:
    data = await _call("Common_MarkAsNotRecurring", {"streamId": stream_id})
    return project.mark_stream_as_not_recurring_result(data)
