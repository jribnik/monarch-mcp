"""
Write-side MCP tools, built on monarch_client (plus the shared safety gates
the sibling write modules import from here).

Deliberately no generic "execute_mutation" escape hatch: only the vendored,
named mutations are callable, so a prompt-injection or a model mistake can't
reach an arbitrary mutation. Each function's signature mirrors its server.py
counterpart exactly, same as reads.py.

This module: create_tag, delete_tag, preview_transaction_rule,
create_transaction_rule, delete_transaction_rule, recategorize_transaction,
update_transaction, set_transaction_tags, mark_stream_as_not_recurring,
create_transaction, delete_transaction, create_manual_account -- 12
functions, 11 write tools (preview_transaction_rule is a dry-run query and is
counted with the reads). The rest of the 30 write tools live in
writes_categories.py (8), writes_accounts.py (8) and writes_splits_rules.py
(3). See README.md for the full tool table and operations/__init__.py's
PROVENANCE for each op's provenance.

Safety layers, outermost first:
  1. gate.py's MONARCH_CLIENT_ENABLE_WRITES master switch, checked by every
     tool (_require_writes) and again by _call / MonarchClient.call.
  2. _require_confirm: the 8 destructive tools (delete_transaction,
     delete_tag, delete_transaction_rule, mark_stream_as_not_recurring here;
     delete_account, delete_savings_goal in writes_accounts.py;
     delete_category_group, delete_category in writes_categories.py) take a
     REQUIRED `confirm` that must echo a value derived from a FRESH read done
     inside the tool: the target's name, except delete_transaction
     ("<merchant> <amount>", see transaction_confirm_token) and
     delete_transaction_rule (its first criterion value / category name, see
     rule_confirm_token). They refuse before sending anything otherwise.
     create_transaction_rule / update_transaction_rule with
     apply_to_existing_transactions=True need confirm == str(preview
     totalCount), and the apply_to_future=True budget tools need the
     category / goal name or (flex) the month's current amount, each from a
     fresh read. A confirm proves the caller looked at the target; it is
     not authentication (the model supplies it).
  3. Per-tool client-side validation: _require_manual (fail-closed
     manual-account check for create_transaction/update_account/
     delete_account) and _verify_merchant_name_exists (rule merchant names).

Verification history: the original write ops were captured from / verified
against a dedicated, disposable monarch-sandbox Monarch account, which has
since been DELETED (api-recon still registers a `monarch-sandbox` adapter
site name, with no account behind it) -- the "verified live against
monarch-sandbox" remarks in docstrings and PROVENANCE notes are dated history,
not a testing path you can still use. A NEW write op must now be verified some
other way (see operations/README.md, "Adding a write operation").
Three ops -- delete_transaction_rule, mark_stream_as_not_recurring and
delete_transaction -- were verified by direct functional call rather than a
UI-driven capture, each documented as an explicit exception in its own
.graphql file.

Gotchas discovered live and enforced here:
  - setMerchantAction (preview/create_transaction_rule's set_merchant_name)
    takes a merchant NAME string and has no server-side validation: a name
    that doesn't exactly match an existing merchant silently CREATES a new
    garbage merchant (preview doesn't catch it either; its `newName` just
    echoes the input). Hence _verify_merchant_name_exists.
  - create_transaction / create_manual_account DO validate accountId/
    categoryId/type/subtype existence server-side, but Monarch does not
    reject a valid-but-bank-synced accountId; hence the fail-closed
    _require_manual gate (a phantom manual entry on a bank feed confuses that
    account's balance/reconciliation even if it could later be deleted).
  - delete_transaction works on any transaction, bank-synced included, same
    as the web app's delete button, and has no undo -- hence its confirm gate.
    (Its query was recovered from the legacy monarchmoney-enhanced library,
    the same recovery pattern as delete_transaction_rule.)
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
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


async def _get_account_for_edit(account_id: str) -> dict[str, Any]:
    """Fresh single-account read (Common_GetAccountForEdit) shared by the
    manual-account write gates here and in writes_accounts.py."""
    data = await _call("Common_GetAccountForEdit", {"id": account_id})
    account = data.get("account")
    if not account:
        raise ValueError(
            f"account_id={account_id!r} wasn't found -- double-check the id."
        )
    return account


def _require_manual(account: dict[str, Any], tool: str) -> None:
    """Fail closed: refuse unless the account is POSITIVELY identified as
    manual (isManual is exactly True, no credential, no dataProvider).

    This replaced create_transaction's earlier _verify_account_is_manual,
    which only checked `credential is None` and so FAILED OPEN for any
    bank-synced account whose credential had been detached/nulled (e.g. a
    migrated duplicate): such an account has isManual false/missing and/or a
    dataProvider, and now stays refused. Monarch's own mutations only reject
    a NONEXISTENT accountId, not a valid-but-synced one, and a manual
    transaction forced onto a bank feed is a phantom entry that confuses the
    account's balance/reconciliation even if it could later be deleted."""
    if (
        account.get("isManual") is not True
        or account.get("credential") is not None
        or account.get("dataProvider")
    ):
        raise ValueError(
            f"{tool}: account {account.get('id')!r} ({account.get('displayName')!r}) "
            "is not positively identified as a manual account (bank-linked, or missing/false isManual). Only MANUAL "
            "accounts can be changed or deleted through this server."
        )


def _require_confirm(
    tool: str, confirm: Any, accepted: Any, target: str
) -> None:
    """Confirmation gate for destructive / wide-blast-radius writes.

    All write tools are live whenever MONARCH_CLIENT_ENABLE_WRITES=1, so a
    model mistake (wrong id, stale id, prompt injection) goes straight to the
    real account. Each destructive tool therefore takes a REQUIRED `confirm`
    argument that must exactly echo (case-sensitive) the target as returned
    by a FRESH read performed inside the tool -- forcing the caller to have
    looked at what it is about to destroy, and refusing a stale or mistyped
    id. `accepted` is the set of acceptable echoes (falsy entries ignored);
    the refusal names them so the caller can retry only after the user
    has confirmed that specific target."""
    options = sorted({a for a in accepted if a})
    if not isinstance(confirm, str) or not confirm or confirm not in options:
        raise ValueError(
            f"{tool}: confirm={confirm!r} does not exactly match {target} "
            f"(expected one of {options}) -- NOT proceeding. Only retry with "
            "the exact value if the user has explicitly confirmed this "
            "specific target."
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


async def delete_tag(tag_id: str, confirm: str) -> dict[str, Any]:
    """Vendored 2026-09-28 (closing create_tag's missing counterpart);
    verified live then: created a tag, deleted it, confirmed via a follow-up
    get_tags call it was gone. `confirm` must exactly equal the tag's current
    name, checked against a fresh get_tags read (unknown ids are refused)."""
    _require_writes("delete_tag")
    tags = (await reads.get_tags()).get("householdTransactionTags") or []
    tag = next((x for x in tags if x.get("id") == tag_id), None)
    if tag is None:
        raise ValueError(f"delete_tag: tag_id={tag_id!r} wasn't found in get_tags.")
    _require_confirm("delete_tag", confirm, [tag.get("name")], f"the tag's name {tag.get('name')!r}")
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
    confirm: Optional[str] = None,
) -> dict[str, Any]:
    """With apply_to_existing_transactions=True the rule rewrites every
    matching HISTORICAL transaction, so `confirm` is then required and must
    equal str(totalCount) of a fresh preview of this exact rule -- i.e. the
    caller must have looked at how many existing transactions it will change.
    Ignored (not required) for a forward-only rule."""
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
    if apply_to_existing_transactions:
        preview = await _call(
            "Common_PreviewTransactionRule",
            {"rule": {**rule_input, "applyToExistingTransactions": False}, "offset": 0},
        )
        total = (project.preview_transaction_rule_result(preview)
                 .get("transactionRulePreview") or {}).get("totalCount")
        if total is None:
            raise ValueError(
                "create_transaction_rule: couldn't determine how many existing "
                "transactions this rule matches (preview returned no totalCount) "
                "-- NOT applying it retroactively."
            )
        _require_confirm(
            "create_transaction_rule", confirm, [str(total)],
            f"the number of existing transactions this rule would change ({total}); "
            "pass confirm=str(that count) after running preview_transaction_rule",
        )
    rule_input["applyToExistingTransactions"] = apply_to_existing_transactions
    data = await _call("Common_CreateTransactionRuleMutationV2", {"input": rule_input})
    return project.create_transaction_rule_result(data)


def rule_confirm_token(rule: dict[str, Any]) -> str:
    """What delete_transaction_rule's `confirm` must equal: a human-readable
    value that only a fresh get_transaction_rules read reveals (rules have no
    name, and their id is already passed as rule_id, so echoing the id would
    prove nothing). In order: the first merchant-name criterion's value, else
    the first original-statement criterion's, else the first merchantCriteria
    value, else the setCategoryAction category's name, else the literal
    fallback "rule <id>". All of those fields are selected by
    Web_GetTransactionRules. This is NOT unique (two rules may match
    "amazon"); the target is still pinned by rule_id -- the gate only proves
    the caller looked at the rule it is about to delete."""
    for key in ("merchantNameCriteria", "originalStatementCriteria", "merchantCriteria"):
        crits = rule.get(key) or []
        # No api-recon capture shows merchantCriteria's real shape, so accept a
        # list of criteria or a single criterion object (iterating a dict would
        # yield key strings and make such a rule undeletable).
        if isinstance(crits, dict):
            crits = [crits]
        elif not isinstance(crits, list):
            continue
        for crit in crits:
            if not isinstance(crit, dict):
                continue
            value = crit.get("value")
            if isinstance(value, str) and value:
                return value
    category = (rule.get("setCategoryAction") or {}).get("name")
    if isinstance(category, str) and category:
        return category
    return f"rule {rule.get('id')}"


async def delete_transaction_rule(rule_id: str, confirm: str) -> dict[str, Any]:
    """IRREVERSIBLE. A rule has no name and its id is already the rule_id
    argument, so `confirm` must instead equal rule_confirm_token(rule): the
    rule's first merchant-name criterion value, else its first
    original-statement criterion value, else its first merchantCriteria value,
    else its set-category action's category name, else "rule <id>", taken from a fresh
    get_transaction_rules read. The gate proves the caller read this rule
    (and an unknown id is refused); it does not make the target unique -- the
    id does that."""
    _require_writes("delete_transaction_rule")
    rules = (await reads.get_transaction_rules()).get("transactionRules") or []
    rule = next((r for r in rules if str(r.get("id")) == str(rule_id)), None)
    if rule is None:
        raise ValueError(
            f"delete_transaction_rule: rule_id={rule_id!r} wasn't found in "
            "get_transaction_rules."
        )
    token = rule_confirm_token(rule)
    _require_confirm(
        "delete_transaction_rule", confirm, [token],
        f"the rule's first criterion value / category name {token!r} "
        "(see get_transaction_rules)",
    )
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
    reject a valid, bank-linked accountId, so the fail-closed
    _require_manual gate runs first against a fresh Common_GetAccountForEdit
    read (see its docstring; this gate stays even though delete_transaction
    below exists). merchant_name itself is NOT
    validated by either Monarch or this client -- an arbitrary string
    creates a new merchant if it doesn't match an existing one exactly,
    same as the real web app's manual-entry form; that's expected here,
    unlike set_merchant_name on the rule tools."""
    _require_writes("create_transaction")
    _require_manual(await _get_account_for_edit(account_id), "create_transaction")
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


def transaction_confirm_token(txn: dict[str, Any]) -> str:
    """What delete_transaction's `confirm` must equal: "<merchant name>
    <amount>" with the amount signed, fixed to two decimals and last, e.g.
    "Amazon -12.34" (expense) or "Acme Payroll 2500.00" (credit); when the
    transaction has no merchant, just the amount ("-12.34"). The amount is
    rounded half-even; the merchant name is compared exactly as read -- no
    Unicode normalization, exact spacing. A mismatch refuses the call and the
    error shows the expected value (a confirm is not authentication, see the
    README). Built from a fresh get_transaction_details read. The merchant name alone isn't unique
    (every Amazon purchase shares it), so the amount is what pins it to this
    transaction. Raises ValueError if the read has no usable amount (the gate
    fails closed rather than falling back to something weaker)."""
    amount = txn.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float, str)):
        raise ValueError("delete_transaction: the transaction has no readable amount")
    try:
        d = Decimal(str(amount))
        if not d.is_finite():
            raise InvalidOperation
        d = d.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(
            f"delete_transaction: unreadable transaction amount {amount!r}"
        ) from None
    if d == 0:
        d = abs(d)  # never "-0.00"
    merchant = (txn.get("merchant") or {}).get("name")
    return f"{merchant} {d}" if merchant else str(d)


async def delete_transaction(transaction_id: str, confirm: str) -> dict[str, Any]:
    """IRREVERSIBLE. Added 2026-09-28, closing create_transaction's missing
    counterpart -- flagged as a real irreversibility gap by an Opus review (a
    mistaken manual transaction had no way to be undone through this server).
    Recovered from the old abandoned library (see
    Common_DeleteTransactionMutation's PROVENANCE note), verified live
    against monarch-sandbox: created a real transaction, deleted it,
    confirmed via a follow-up get_transaction_details call that it's
    actually gone. Works on any transaction this account can see, not just
    manually-created ones, INCLUDING bank-synced transactions -- same as the
    real web app's delete button -- so it is gated: `confirm` must exactly
    equal "<merchant name> <amount>" (amount signed, two decimals, e.g.
    "Amazon -12.34"), or just the amount ("-12.34") when the transaction has
    no merchant -- see transaction_confirm_token -- as returned by a FRESH
    get_transaction_details read. The amount is included because merchant
    names repeat. An unknown id is refused."""
    _require_writes("delete_transaction")
    txn = (await reads.get_transaction_details(transaction_id)).get("getTransaction")
    if not txn:
        raise ValueError(
            f"delete_transaction: transaction_id={transaction_id!r} wasn't found."
        )
    token = transaction_confirm_token(txn)
    _require_confirm(
        "delete_transaction", confirm, [token],
        f"\"<merchant name> <amount>\" (just the amount if no merchant) = {token!r} "
        f"(date {txn.get('date')})",
    )
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
    mutation, same field-name mapping verified live (see
    operations/__init__.py's PROVENANCE note: `name` for merchant, not
    `merchantName`). Only fields that are not None are sent; an update that
    would send nothing but the id is refused."""
    input_: dict[str, Any] = {"id": transaction_id}
    if category_id is not None:
        input_["category"] = category_id
    if merchant_name is not None:
        input_["name"] = merchant_name
    if amount is not None:
        # `is not None`, not truthiness: amount=0 is a real value that an
        # earlier `if amount:` silently dropped, sending {id} alone while
        # reporting success.
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) \
                or not math.isfinite(amount):
            raise ValueError(f"update_transaction: amount={amount!r} must be a finite number")
        input_["amount"] = amount
    if date is not None:
        if not date:
            raise ValueError("update_transaction: date must not be empty")
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

    if len(input_) == 1:
        raise ValueError(
            "update_transaction: nothing to change -- pass at least one field "
            "besides transaction_id"
        )

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


async def mark_stream_as_not_recurring(stream_id: str, confirm: str) -> dict[str, Any]:
    """`confirm` must exactly equal the stream's merchant name (or stream
    name), checked against a fresh get_recurring_transactions read (current
    month window); a stream not visible there is refused."""
    _require_writes("mark_stream_as_not_recurring")
    items = (await reads.get_recurring_transactions()).get("recurring_items") or []
    stream = next(
        (
            i["stream"] for i in items
            if (i.get("stream") or {}).get("id") == stream_id
        ),
        None,
    )
    if stream is None:
        raise ValueError(
            f"mark_stream_as_not_recurring: stream_id={stream_id!r} wasn't found "
            "in get_recurring_transactions (current month)."
        )
    _require_confirm(
        "mark_stream_as_not_recurring", confirm,
        [(stream.get("merchant") or {}).get("name"), stream.get("name")],
        "the stream's merchant name",
    )
    data = await _call("Common_MarkAsNotRecurring", {"streamId": stream_id})
    return project.mark_stream_as_not_recurring_result(data)
