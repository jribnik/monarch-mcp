"""
Write-side MCP tools, reimplemented on monarch_client.

Deliberately no generic "execute_mutation" escape hatch: only the vendored,
named mutations below are callable, so a prompt-injection or a model
mistake can't reach an arbitrary mutation. Each function's signature
mirrors its server.py counterpart exactly, same as reads.py.

12 functions in this module (11 write tools at the tool level: the README table
counts preview_transaction_rule, a dry-run, as a read) -- create_tag, delete_tag, preview_transaction_rule,
create_transaction_rule, delete_transaction_rule, recategorize_transaction,
update_transaction, set_transaction_tags, mark_stream_as_not_recurring,
create_transaction, delete_transaction, create_manual_account -- each
captured and/or verified against a dedicated, disposable monarch-sandbox
account (see operations/__init__.py's PROVENANCE for each op's exact
provenance; three -- delete_transaction_rule, mark_stream_as_not_recurring,
and delete_transaction -- were verified by direct functional call rather
than a UI-driven HAR capture, each documented as an explicit exception in
its own .graphql file). See operations/README.md.

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
(manual transaction/account creation). Both mutations DO validate
accountId/categoryId/type/subtype EXISTENCE server-side (a bad id raises
or returns a real errors.message rather than silently succeeding, verified
live) -- unlike setMerchantAction. But an Opus review the same day caught
that Monarch's existence check doesn't catch a valid-but-wrong-KIND id: a
real, bank-linked accountId passed to create_transaction wouldn't be
rejected, and would create a phantom entry on a real bank feed that has no
business being manually entered there regardless of whether it could later
be deleted -- see _verify_account_is_manual, added as this function's own
client-side gate. create_manual_account's type/subtype pair genuinely
doesn't need one (verified live: an invalid pair raises).

delete_transaction was added the same day (2026-09-28), closing
create_transaction's own missing counterpart -- unlike the accountId gate
above, this was a pure capability gap (no way to undo ANY mistaken
transaction, not just ones on the wrong account kind), also flagged by the
same Opus review. NOT captured via api-recon (never driven through its UI
automation) -- recovered from the old abandoned monarchmoney-enhanced
library's own hand-authored, long-production-tested query, same recovery
pattern as delete_transaction_rule. Verified live against monarch-sandbox:
created a real transaction, deleted it, confirmed via a follow-up read
that it's actually gone.
"""

from __future__ import annotations

from typing import Any, Optional

from . import MonarchClient, gate, operations, project
from . import reads
from .errors import MonarchWriteBlocked  # noqa: F401  (kept importable from here)

_client = MonarchClient()

# The master write switch (MONARCH_CLIENT_ENABLE_WRITES) lives in gate.py so
# MonarchClient.call's transport-level backstop can share it. Unset (or
# anything but "1") means every write tool below refuses BEFORE sending
# anything -- read tools and the dry-run preview_transaction_rule are
# unaffected. Re-exported here under the names doctor and the tests use.
WRITES_ENV = gate.WRITES_ENV


def writes_status() -> bool:
    """Public read-only view of the gate (for doctor and diagnostics):
    True iff MONARCH_CLIENT_ENABLE_WRITES=1."""
    return gate.writes_enabled()


def _require_writes(tool: str) -> None:
    gate.require_writes(tool)


async def _call(op_name: str, variables: dict[str, Any]) -> dict[str, Any]:
    # Second line of defense behind each tool's own _require_writes():
    # MonarchClient.call also refuses mutation-kind ops while the gate is
    # closed, but this check stays so the guarantee holds even when a test
    # or a future caller swaps in a different client object.
    if operations.is_mutation(op_name):
        gate.require_writes(op_name)
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
    match before ever sending the mutation.

    Opus review 2026-09-28 caught a real gap: `search` is a substring match
    over merchant name AND notes, date-ordered, and was originally capped at
    25 -- on an account with years of history a short/common real merchant
    name can get pushed out of the top N by newer transactions that merely
    *contain* the string, producing a FALSE reject. That's safe by itself
    (the gate can only over-block, never under-block), but the error
    message used to just assert the name "doesn't exist" and list whatever
    near-miss names it happened to see -- which could steer a retry toward
    creating the WRONG merchant. Fixed by using `totalCount` to tell
    "genuinely not found" apart from "search was truncated, can't confirm
    either way" and saying so explicitly in the latter case instead of
    implying the name search was exhaustive."""
    found = await reads.get_transactions(search=name, limit=100)
    all_transactions = found.get("allTransactions") or {}
    results = all_transactions.get("results") or []
    total_count = all_transactions.get("totalCount") or 0
    real_names = {
        t["merchant"]["name"] for t in results if t.get("merchant")
    }
    if name in real_names:
        return
    if total_count > len(results):
        raise ValueError(
            f"set_merchant_name={name!r} wasn't among the first "
            f"{len(results)} of {total_count} transactions matching that "
            "search, so this check can't confirm whether it's a real "
            "merchant name or not -- NOT proceeding, since a false negative "
            "here is unsafe (setMerchantAction silently creates a new "
            "merchant on a non-match, verified live). Narrow the search "
            "(e.g. get_transactions(search=<name>, account_ids=[...])) and "
            "copy the exact merchant.name from a real result."
        )
    raise ValueError(
        f"set_merchant_name={name!r} doesn't exactly match any existing "
        f"merchant found via a transaction search (found: "
        f"{sorted(real_names) or 'none'}) -- setMerchantAction silently "
        "CREATES A NEW MERCHANT with this exact string as its name if it "
        "doesn't match one already, verified live. Get the exact name "
        "from an existing transaction (e.g. get_transactions(search=...)) "
        "before retrying."
    )


async def _verify_account_is_manual(account_id: str) -> None:
    """Added after Opus review 2026-09-28. create_transaction is meant for
    manual (non-Plaid) accounts only, but nothing was stopping it being
    called against a real, bank-linked account -- Monarch's own mutation
    only rejects a NONEXISTENT accountId (verified live: "Account matching
    query does not exist."), it doesn't reject a valid-but-linked one. This
    gate stays even now that delete_transaction exists (below): a manual
    transaction forced onto a linked account is still a phantom entry that
    doesn't belong on a real bank feed, whether or not it could later be
    cleaned up -- a linked account's transactions are supposed to come from
    the bank sync, not manual entry, and quietly injecting one risks
    confusing that account's balance/reconciliation regardless. Gate here
    instead: a manual account's `credential` field is null; a linked one
    always has a real credential object (dataProvider, institution, etc.)
    -- see Web_GetAccountsPage.graphql's AccountListItemFields fragment."""
    accounts = await reads.list_accounts()
    match = next(
        (a for a in accounts.get("accounts") or [] if a.get("id") == account_id),
        None,
    )
    if match is None:
        raise ValueError(
            f"account_id={account_id!r} wasn't found in list_accounts() -- "
            "double-check the id before retrying."
        )
    if match.get("credential") is not None:
        raise ValueError(
            f"account_id={account_id!r} ({match.get('displayName')!r}) is a "
            "bank-linked account (it has a credential), not a manual one. "
            "create_transaction is for MANUAL accounts only -- a linked "
            "account's transactions are supposed to come from the bank "
            "sync, not manual entry. Use create_manual_account first if "
            "you need a new account to record this against."
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
    keithah/monarchmoney-enhanced@159d36e monarchmoney/monarchmoney.py's own rule_input construction
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
    _require_writes("create_tag")
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
    _require_writes("delete_tag")
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
    _require_writes("create_transaction_rule")
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
    _require_writes("delete_transaction_rule")
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
    live against monarch-sandbox). Monarch validates accountId/categoryId
    server-side for EXISTENCE and returns a real errors.message with
    transaction=null on a nonexistent id, verified live -- see
    Common_CreateTransactionMutation's PROVENANCE note -- but does not
    reject a valid, bank-linked accountId, so _verify_account_is_manual
    runs first (see its own docstring for why this gate stays even though
    delete_transaction below exists). merchant_name itself is NOT
    validated by either Monarch or this client -- an arbitrary string
    creates a new merchant if it doesn't match an existing one exactly,
    same as the real web app's manual-entry form; that's expected here,
    unlike set_merchant_name on the rule tools."""
    _require_writes("create_transaction")
    await _verify_account_is_manual(account_id)
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


async def delete_transaction(transaction_id: str) -> dict[str, Any]:
    """Added 2026-09-28, closing create_transaction's missing counterpart --
    flagged as a real irreversibility gap by an Opus review (a mistaken
    manual transaction had no way to be undone through this server).
    Recovered from the old abandoned library (see
    Common_DeleteTransactionMutation's PROVENANCE note), verified live
    against monarch-sandbox: created a real transaction, deleted it,
    confirmed via a follow-up get_transaction_details call that it's
    actually gone. Works on any transaction this account can see, not just
    manually-created ones -- same as the real web app's delete button --
    so use with the same care as any other destructive write."""
    _require_writes("delete_transaction")
    data = await _call(
        "Common_DeleteTransactionMutation", {"input": {"transactionId": transaction_id}}
    )
    return project.delete_transaction_result(data)


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
    _require_writes("create_manual_account")
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
    reviewed: Optional[bool] = None,
) -> dict[str, Any]:
    """Shared by recategorize_transaction and update_transaction -- same
    mutation, same field-name mapping verified live against
    monarch-sandbox (see operations/__init__.py's PROVENANCE note: `name`
    for merchant, not `merchantName`; amount/date only sent when truthy,
    matching keithah/monarchmoney-enhanced@159d36e monarchmoney/monarchmoney.py's own guard against the
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
    if reviewed is not None:
        # The web app's "Mark as reviewed" button sends {reviewed: true}, which
        # sets reviewStatus='reviewed' -- a DIFFERENT state from
        # needsReview=False (which leaves reviewStatus null). Verified live
        # 2026-09-30. Only True is supported; False is unverified.
        if reviewed is not True:
            raise ValueError("update_transaction: reviewed only supports True")
        if needs_review is not None:
            raise ValueError(
                "update_transaction: pass reviewed=True OR needs_review, not both"
            )
        input_["reviewed"] = True
    if notes is not None:
        input_["notes"] = notes

    data = await _call(
        "Web_TransactionDrawerUpdateTransaction",
        {"debugActivityLogEnabled": False, "input": input_},
    )
    return project.update_transaction_result(data)


async def recategorize_transaction(transaction_id: str, category_id: str) -> dict[str, Any]:
    _require_writes("recategorize_transaction")
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
    reviewed: Optional[bool] = None,
) -> dict[str, Any]:
    _require_writes("update_transaction")
    return await _update_transaction(
        transaction_id,
        category_id=category_id,
        merchant_name=merchant_name,
        amount=amount,
        date=date,
        hide_from_reports=hide_from_reports,
        needs_review=needs_review,
        notes=notes,
        reviewed=reviewed,
    )


async def set_transaction_tags(transaction_id: str, tag_ids: list[str]) -> dict[str, Any]:
    _require_writes("set_transaction_tags")
    data = await _call(
        "Web_SetTransactionTags",
        {
            "debugActivityLogEnabled": False,
            "input": {"transactionId": transaction_id, "tagIds": tag_ids},
        },
    )
    return project.set_transaction_tags_result(data)


async def mark_stream_as_not_recurring(stream_id: str) -> dict[str, Any]:
    _require_writes("mark_stream_as_not_recurring")
    data = await _call("Common_MarkAsNotRecurring", {"streamId": stream_id})
    return project.mark_stream_as_not_recurring_result(data)
