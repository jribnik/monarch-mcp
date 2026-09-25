"""
Write-side MCP tools, reimplemented on monarch_client.

Deliberately no generic "execute_mutation" escape hatch: only the vendored,
named mutations below are callable, so a prompt-injection or a model
mistake can't reach an arbitrary mutation. Each function's signature
mirrors its server.py counterpart exactly, same as reads.py.

Full coverage: all 8 write tools -- create_tag, preview_transaction_rule,
create_transaction_rule, delete_transaction_rule, recategorize_transaction,
update_transaction, set_transaction_tags, mark_stream_as_not_recurring --
each captured and/or verified against a dedicated, disposable
monarch-sandbox account (see operations/__init__.py's PROVENANCE for each
op's exact provenance; two of the eight -- delete_transaction_rule and
mark_stream_as_not_recurring -- were verified by direct functional call
rather than a UI-driven HAR capture, each documented as an explicit
exception in its own .graphql file). See operations/README.md.
"""

from __future__ import annotations

from typing import Any, Optional

from . import MonarchClient, project

_client = MonarchClient()


async def _call(op_name: str, variables: dict[str, Any]) -> dict[str, Any]:
    return await _client.call(op_name, variables)


def _rule_input_common(
    *,
    merchant_name_criteria: Optional[list[dict[str, str]]],
    original_statement_criteria: Optional[list[dict[str, str]]],
    amount_criteria: Optional[dict[str, Any]],
    category_ids: Optional[list[str]],
    account_ids: Optional[list[str]],
    set_category_action: Optional[str],
) -> dict[str, Any]:
    """Shared field-building for preview/create_transaction_rule -- matches
    _audit/monarchmoney/monarchmoney.py's own rule_input construction
    (always-sent keys default to None; merchantNameCriteria/
    originalStatementCriteria are added only when given, since the library
    demonstrated live that this is what the API expects)."""
    rule_input: dict[str, Any] = {
        "merchantCriteriaUseOriginalStatement": False,
        "merchantCriteria": None,
        "amountCriteria": amount_criteria,
        "categoryIds": category_ids,
        "accountIds": account_ids,
        "setCategoryAction": set_category_action,
        "addTagsAction": None,
        "setMerchantAction": None,
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


async def preview_transaction_rule(
    merchant_name_criteria: Optional[list[dict[str, str]]] = None,
    original_statement_criteria: Optional[list[dict[str, str]]] = None,
    amount_criteria: Optional[dict[str, Any]] = None,
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    set_category_action: Optional[str] = None,
) -> dict[str, Any]:
    rule_input = _rule_input_common(
        merchant_name_criteria=merchant_name_criteria,
        original_statement_criteria=original_statement_criteria,
        amount_criteria=amount_criteria,
        category_ids=category_ids,
        account_ids=account_ids,
        set_category_action=set_category_action,
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
    apply_to_existing_transactions: bool = False,
) -> dict[str, Any]:
    rule_input = _rule_input_common(
        merchant_name_criteria=merchant_name_criteria,
        original_statement_criteria=original_statement_criteria,
        amount_criteria=amount_criteria,
        category_ids=category_ids,
        account_ids=account_ids,
        set_category_action=set_category_action,
    )
    rule_input["applyToExistingTransactions"] = apply_to_existing_transactions
    data = await _call("Common_CreateTransactionRuleMutationV2", {"input": rule_input})
    return project.create_transaction_rule_result(data)


async def delete_transaction_rule(rule_id: str) -> dict[str, Any]:
    data = await _call("Common_DeleteTransactionRule", {"id": rule_id})
    return project.delete_transaction_rule_result(data)


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
