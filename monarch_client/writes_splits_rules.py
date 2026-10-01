"""
Split-transaction and update-rule write tools (added 2026-09-30).

  - split_transaction / unsplit_transaction -> Common_SplitTransactionMutation
  - update_transaction_rule                 -> Common_UpdateTransactionRuleMutationV2

Both operations were captured from the live web app on 2026-09-30 (see
api-recon's captured-mutations-2026-09-30.json) and verified live against the
disposable monarch-sandbox account; see each PROVENANCE note in
PROVENANCE_ENTRIES below. Conventions match writes.py: `_require_writes(tool)`
first, then `_call`, response passed through project-style (Monarch's own
`errors` payload is surfaced as-is, not raised).

split_transaction client-side gate: the splits' amounts must sum EXACTLY
(Decimal, cents) to the transaction's current amount, fetched fresh via
get_transaction_details. The server also checks this ("Split quantities do not
sum up to the original amount", verified live) but the gate refuses before
anything is sent and explains the sign convention (debits are negative).

update_transaction_rule replaces the WHOLE rule server-side, so it first reads
the existing rule (Web_GetTransactionRules) and merges: any argument left as
None keeps the rule's current value; an "empty" value ([], "", {}) clears a
field. See the function docstring.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from . import reads
from .writes import (
    _call,
    _require_writes,
    _verify_merchant_name_exists,
)

TOOLS = ["split_transaction", "unsplit_transaction", "update_transaction_rule"]

_EXPORTED_AT = "2026-09-30T00:00:00+00:00"


PROVENANCE_ENTRIES: dict[str, dict[str, Any]] = {
    "Common_SplitTransactionMutation": {
        "catalog_query_hash": None,
        "walk_reachable": False,
        "vendored_sha256": "3586024ac4f198faaceb1f702c2319d1ae56e5d9f5444a78a4627a7c43c7c99d",
        "exported_at": _EXPORTED_AT,
        "runs_seen": [],
        "hand_repaired": False,
        "note": (
            "backs split_transaction and unsplit_transaction. Captured from "
            "the live web app 2026-09-30 (unsplit form: splitData []). Split "
            "form verified live against monarch-sandbox: each splitData item "
            "is {amount, categoryId, merchantName, hideFromReports, "
            "ownerUserId, businessEntityId, businessEntityIsUnassigned}; "
            "amounts are signed like the parent (debits negative); null "
            "categoryId/merchantName default to the parent's category and "
            "merchant; re-splitting replaces the previous split; the server "
            "itself rejects a bad sum with errors.message 'Split quantities "
            "do not sum up to the original amount' (transaction=null). "
            "split_transaction adds a client-side exact-cents sum gate."
        ),
    },
    "Common_UpdateTransactionRuleMutationV2": {
        "catalog_query_hash": None,
        "walk_reachable": False,
        "vendored_sha256": "43baeb52d2e0d6df4449bec1427b123fbc26d649de2db9b415147d8377f7c549",
        "exported_at": _EXPORTED_AT,
        "runs_seen": [],
        "hand_repaired": False,
        "note": (
            "backs update_transaction_rule. Captured from the live web app "
            "2026-09-30. The input is the whole rule plus `id` (replace "
            "semantics), so update_transaction_rule reads the existing rule "
            "via Web_GetTransactionRules and merges before sending. The "
            "response selects only `errors` (no rule back) -- confirm by "
            "re-reading get_transaction_rules. Verified live against "
            "monarch-sandbox incl. addTagsAction/setHideFromReportsAction/"
            "reviewStatusAction; Common_CreateTransactionRuleMutationV2 "
            "accepts the same new fields."
        ),
    },
}


# ---------------------------------------------------------------- projectors


def split_transaction_result(
    data: dict[str, Any]
) -> dict[str, Any]:
    """Pass-through of `updateTransactionSplit.{errors,transaction}` -- the
    same convention as create_transaction_result: a rejected split comes
    back as a normal `errors.message` with transaction=None (verified live),
    surfaced as-is rather than raised."""
    out: dict[str, Any] = {"updateTransactionSplit": data.get("updateTransactionSplit")}
    return out


def update_transaction_rule_result(
    data: dict[str, Any]
) -> dict[str, Any]:
    """Pass-through of `updateTransactionRuleV2.errors`. The app's query
    selects no rule back (same limitation as create), so verify with
    get_transaction_rules()."""
    result = data.get("updateTransactionRuleV2") or {}
    errors = result.get("errors")
    out: dict[str, Any] = {"updateTransactionRuleV2": {"errors": errors}}
    if isinstance(errors, dict) and not (
        errors.get("message") or errors.get("code") or errors.get("fieldErrors")
    ):
        # Observed live: the server signals some rejections as a non-null but
        # EMPTY PayloadError. Make that visible instead of looking like success.
        out["updateTransactionRuleV2"]["hint"] = (
            "the server returned an empty error object, which means the "
            "update was REJECTED (nothing changed) -- re-read the rule with "
            "get_transaction_rules and check the arguments"
        )
    return out


# ------------------------------------------------------------------- splits

_CENT = Decimal("0.01")


def _to_cents(value: Any, what: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{what}={value!r} is not a valid amount") from None
    if not d.is_finite():
        raise ValueError(f"{what}={value!r} is not a finite amount")
    q = d.quantize(_CENT)
    if q != d:
        raise ValueError(f"{what}={value!r} has more than 2 decimal places")
    return q


def _split_item(
    split: dict[str, Any], index: int, amount: Decimal, default_hide: bool = False
) -> dict[str, Any]:
    unknown = set(split) - {"amount", "category_id", "merchant_name", "hide_from_reports"}
    if unknown:
        raise ValueError(f"splits[{index}] has unknown keys {sorted(unknown)}")
    return {
        "amount": float(amount),
        "categoryId": split.get("category_id"),
        "merchantName": split.get("merchant_name"),
        "hideFromReports": (
            default_hide
            if split.get("hide_from_reports") is None
            else bool(split["hide_from_reports"])
        ),
        "ownerUserId": None,
        "businessEntityId": None,
        "businessEntityIsUnassigned": False,
    }


async def split_transaction(
    transaction_id: str, splits: list[dict[str, Any]]
) -> dict[str, Any]:
    """Split a transaction into 2+ parts. Each split is {amount,
    category_id?, merchant_name?, hide_from_reports?}. Amounts are signed the
    same as the transaction (debits NEGATIVE) and must sum EXACTLY, to the
    cent, to the transaction's current amount -- checked client-side against
    a fresh get_transaction_details read before anything is sent. Omitted
    category_id / merchant_name default to the original transaction's
    (verified live). Splitting an already-split transaction replaces the
    earlier split; use unsplit_transaction to undo.

    Refuses zero-amount splits and splits whose sign differs from the
    transaction's (mixed-sign). hide_from_reports defaults to the PARENT's
    current hideFromReports (read from get_transaction_details) unless given.
    NOT carried from the parent: owner (ownerUserId) and business entity --
    these are always sent as unset/unassigned, so a split of a transaction
    owned by a household member or business entity will not inherit that."""
    _require_writes("split_transaction")
    if not isinstance(splits, list) or len(splits) < 2:
        raise ValueError(
            "splits must be a list of at least 2 items (use unsplit_transaction "
            "to remove a split)"
        )
    amounts: list[Decimal] = []
    for i, s in enumerate(splits):
        if not isinstance(s, dict) or "amount" not in s:
            raise ValueError(f"splits[{i}] must be a dict with an 'amount'")
        a = _to_cents(s["amount"], f"splits[{i}].amount")
        if a == 0:
            raise ValueError(f"splits[{i}].amount is zero -- zero-amount splits are not allowed")
        amounts.append(a)

    details = await reads.get_transaction_details(transaction_id)
    txn = details.get("getTransaction")
    if not txn or txn.get("amount") is None:
        raise ValueError(f"transaction_id={transaction_id!r} wasn't found")
    total = _to_cents(txn["amount"], "transaction amount")
    if total != 0 and any((a > 0) != (total > 0) for a in amounts):
        raise ValueError(
            f"every split must have the same sign as the transaction's amount "
            f"({total}); got {[str(a) for a in amounts]} (mixed-sign splits "
            "are refused). NOT sending."
        )
    if sum(amounts) != total:
        raise ValueError(
            f"splits sum to {sum(amounts)} but the transaction's amount is "
            f"{total} -- they must match exactly (amounts are signed like the "
            "transaction: a debit/expense is negative). NOT sending."
        )

    parent_hide = bool(txn.get("hideFromReports") or False)
    split_data = [
        _split_item(s, i, a, parent_hide) for i, (s, a) in enumerate(zip(splits, amounts))
    ]
    data = await _call(
        "Common_SplitTransactionMutation",
        {"input": {"transactionId": transaction_id, "splitData": split_data}},
    )
    return split_transaction_result(data)


async def unsplit_transaction(transaction_id: str) -> dict[str, Any]:
    """Remove a transaction's split (splitData [], as the web app sends)."""
    _require_writes("unsplit_transaction")
    data = await _call(
        "Common_SplitTransactionMutation",
        {"input": {"transactionId": transaction_id, "splitData": []}},
    )
    return split_transaction_result(data)


# -------------------------------------------------------------------- rules

_SPLIT_ACTION_KEYS = {"percent", "category_id", "merchant_name", "hide_from_reports"}
_SPLIT_INFO_FIELDS = [
    "categoryId", "merchantName", "amount", "goalId", "savingsGoalId", "tags",
    "hideFromReports", "reviewStatus", "needsReviewByUserId", "ownerUserId",
    "ownerIsJoint", "businessEntityId", "businessEntityIsUnassigned",
]


def _build_split_action(split_action: dict[str, Any]) -> dict[str, Any]:
    """{splits: [{percent, category_id?, merchant_name?, hide_from_reports?}]}
    -> splitTransactionsAction {amountType: "PERCENTAGE", splitsInfo[]}.

    ONLY percentage splits are supported. Verified live 2026-09-30 that the
    server stores ANY amountType string without validating it, but only
    "PERCENTAGE" is a value Web_GetTransactionRules can read back -- anything
    else (incl. "AMOUNT", tried in many spellings) is accepted by the update
    and then makes get_transaction_rules fail for the WHOLE account ("Enum
    'SplitAmountType' cannot represent value") until the rule is deleted. So
    a fixed-amount split rule is refused here rather than guessed at.
    `percent` values are in percent and must sum to exactly 100; on the wire
    they are fractions (60 -> 0.6), which is what the server accepts (the
    server silently rejects 60/40-style values with an empty error object).
    splitsInfo always carries every field of the read-side fragment."""
    unknown_top = set(split_action) - {"amount_type", "splits"}
    if unknown_top:
        raise ValueError(f"split_action has unknown keys {sorted(unknown_top)}")
    amount_type = str(split_action.get("amount_type", "PERCENTAGE")).upper()
    if amount_type != "PERCENTAGE":
        raise ValueError(
            "split_action.amount_type must be 'PERCENTAGE' -- fixed-amount "
            "split rules are not supported (an unverified amountType is stored "
            "unvalidated and breaks get_transaction_rules; see "
            "_build_split_action)"
        )
    splits = split_action.get("splits")
    if not isinstance(splits, list) or len(splits) < 2:
        raise ValueError("split_action.splits must be a list of at least 2 items")
    infos = []
    percents: list[Decimal] = []
    for i, s in enumerate(splits):
        if not isinstance(s, dict) or "percent" not in s:
            raise ValueError(f"split_action.splits[{i}] must be a dict with 'percent'")
        unknown = set(s) - _SPLIT_ACTION_KEYS
        if unknown:
            raise ValueError(f"split_action.splits[{i}] has unknown keys {sorted(unknown)}")
        try:
            pct = Decimal(str(s["percent"]))
        except InvalidOperation:
            raise ValueError(f"split_action.splits[{i}].percent is not a number") from None
        if not pct.is_finite() or pct <= 0 or pct > 100:
            raise ValueError(f"split_action.splits[{i}].percent must be in (0, 100]")
        percents.append(pct)
        info: dict[str, Any] = {f: None for f in _SPLIT_INFO_FIELDS}
        info["categoryId"] = s.get("category_id")
        info["merchantName"] = s.get("merchant_name")
        info["amount"] = float(pct / 100)
        info["hideFromReports"] = bool(s.get("hide_from_reports") or False)
        info["ownerIsJoint"] = False
        info["businessEntityIsUnassigned"] = False
        infos.append(info)
    if sum(percents) != 100:
        raise ValueError(f"split_action percents sum to {sum(percents)}, must be exactly 100")
    return {"amountType": "PERCENTAGE", "splitsInfo": infos}


def _strip_typename(items: Optional[list[dict[str, Any]]]) -> Optional[list[dict[str, Any]]]:
    if items is None:
        return None
    return [{k: v for k, v in i.items() if k != "__typename"} for i in items]


def _existing_to_input(rule: dict[str, Any]) -> dict[str, Any]:
    """Convert a rule as returned by Web_GetTransactionRules (nested objects
    for actions) back into UpdateTransactionRuleInput shape (ids / names)."""

    def _id(o: Optional[dict[str, Any]]) -> Optional[str]:
        return o["id"] if o else None

    amount = rule.get("amountCriteria")
    if amount:
        amount = {k: v for k, v in amount.items() if k != "__typename"}
        if amount.get("valueRange"):
            amount["valueRange"] = {
                k: v for k, v in amount["valueRange"].items() if k != "__typename"
            }
    split = rule.get("splitTransactionsAction")
    if split:
        split = {
            "amountType": split.get("amountType"),
            "splitsInfo": [
                {k: v for k, v in s.items() if k != "__typename"}
                for s in split.get("splitsInfo") or []
            ],
        }
    tags = rule.get("addTagsAction")
    return {
        "id": rule["id"],
        "merchantCriteriaUseOriginalStatement": bool(
            rule.get("merchantCriteriaUseOriginalStatement") or False
        ),
        "merchantCriteria": _strip_typename(rule.get("merchantCriteria")),
        "originalStatementCriteria": _strip_typename(rule.get("originalStatementCriteria")),
        "merchantNameCriteria": _strip_typename(rule.get("merchantNameCriteria")),
        "amountCriteria": amount or None,
        "categoryIds": rule.get("categoryIds"),
        "accountIds": rule.get("accountIds"),
        "setMerchantAction": (rule.get("setMerchantAction") or {}).get("name"),
        "setCategoryAction": _id(rule.get("setCategoryAction")),
        "addTagsAction": [t["id"] for t in tags] if tags else None,
        "linkGoalAction": _id(rule.get("linkGoalAction")),
        "linkSavingsGoalAction": _id(rule.get("linkSavingsGoalAction")),
        "needsReviewByUserAction": _id(rule.get("needsReviewByUserAction")),
        "setHideFromReportsAction": rule.get("setHideFromReportsAction"),
        "reviewStatusAction": rule.get("reviewStatusAction"),
        "splitTransactionsAction": split,
    }


# Fields of a rule (as returned by Web_GetTransactionRules) that the update
# input has no verified slot for. If any is set to a non-default value, an
# update would silently widen/alter the rule, so update_transaction_rule
# REFUSES. name -> predicate "is non-default". Every name here is selected by
# Web_GetTransactionRules.graphql (criteriaOwnerUsers / criteriaBusinessEntities
# / actionSetOwner / actionSetBusinessEntity nested objects are not needed:
# the id/flag fields below carry the same information).
UNCARRIED_RULE_FIELDS: dict[str, str] = {
    "criteriaOwnerIsJoint": "truthy",
    "criteriaOwnerUserIds": "nonempty",
    "criteriaBusinessEntityIds": "nonempty",
    "criteriaBusinessEntityIsUnassigned": "truthy",
    "sendNotificationAction": "truthy",
    "actionSetOwner": "nonempty",
    "actionSetOwnerIsJoint": "truthy",
    "actionSetBusinessEntity": "nonempty",
    "actionSetBusinessEntityIsUnassigned": "truthy",
    "setLinkToPaydownBudgetAction": "truthy",
    "unassignNeedsReviewByUserAction": "truthy",
}

VALID_REVIEW_STATUSES = {"needs_review", "reviewed"}


def _uncarried_fields_set(rule: dict[str, Any]) -> list[str]:
    """Fields the update input cannot carry that the existing rule has set.

    unassignNeedsReviewByUserAction is DERIVED: Monarch sets it to true itself
    whenever reviewStatusAction is 'needs_review' (verified live 2026-09-30 --
    a rule this tool had just updated with review_status='needs_review' came
    back with it true, and the next update was refused). Sending
    reviewStatusAction='needs_review' re-derives it, so it is not an
    uncarried setting in that case."""
    derived = set()
    if rule.get("reviewStatusAction") == "needs_review":
        derived.add("unassignNeedsReviewByUserAction")
    return [k for k in UNCARRIED_RULE_FIELDS if k not in derived and rule.get(k)]


def _merge(current: Any, new: Any) -> Any:
    """None = keep current; empty ([], "", {}) = clear to None; else replace."""
    if new is None:
        return current
    if new == [] or new == "" or new == {}:
        return None
    return new


async def update_transaction_rule(
    rule_id: str,
    merchant_name_criteria: Optional[list[dict[str, str]]] = None,
    original_statement_criteria: Optional[list[dict[str, str]]] = None,
    amount_criteria: Optional[dict[str, Any]] = None,
    category_ids: Optional[list[str]] = None,
    account_ids: Optional[list[str]] = None,
    set_category_action: Optional[str] = None,
    set_merchant_name: Optional[str] = None,
    add_tag_ids: Optional[list[str]] = None,
    set_hide_from_reports: Optional[bool] = None,
    review_status: Optional[str] = None,
    needs_review_by_user_id: Optional[str] = None,
    link_goal_id: Optional[str] = None,
    link_savings_goal_id: Optional[str] = None,
    split_action: Optional[dict[str, Any]] = None,
    apply_to_existing_transactions: bool = False,
) -> dict[str, Any]:
    """Update an existing rule. The mutation replaces the whole rule, so this
    reads the current rule first and merges: an argument left as None KEEPS
    the rule's current value; an empty value ([] / "" / {}) CLEARS that
    field; anything else replaces it. set_hide_from_reports takes a bool
    (False is a real value, not 'clear'). review_status must be "needs_review" or
    "reviewed" ("" clears; anything else raises).
    split_action = {splits: [{percent, category_id?, merchant_name?,
    hide_from_reports?}, ...]} with percents summing to exactly 100 (only
    percentage splits are supported -- see _build_split_action). set_merchant_name is gated by the
    same exact-existing-merchant check as create_transaction_rule (skipped
    when unchanged). Fails closed: if the existing rule sets any of
    UNCARRIED_RULE_FIELDS (owner / business-entity criteria, owner and
    business-entity actions, send-notification, paydown-budget link,
    unassign-needs-review -- the last one except when it is derived from
    reviewStatusAction='needs_review', which Monarch sets itself and clears when
    a reviewer is assigned; verified live 2026-09-30) the update is REFUSED, since the update input has
    no verified slot for them and sending would reset them. A call with no
    arguments besides rule_id is also refused."""
    _require_writes("update_transaction_rule")
    if review_status is not None and review_status != "" and (
        review_status not in VALID_REVIEW_STATUSES
    ):
        raise ValueError(
            f"review_status={review_status!r} must be one of "
            f"{sorted(VALID_REVIEW_STATUSES)} (or '' to clear); other strings "
            "are stored unvalidated and can break get_transaction_rules"
        )
    if all(
        v is None
        for v in (
            merchant_name_criteria, original_statement_criteria, amount_criteria,
            category_ids, account_ids, set_category_action, set_merchant_name,
            add_tag_ids, set_hide_from_reports, review_status,
            needs_review_by_user_id, link_goal_id, link_savings_goal_id,
            split_action,
        )
    ):
        raise ValueError("no fields to update: pass at least one argument besides rule_id")
    rules = (await reads.get_transaction_rules()).get("transactionRules") or []
    rule = next((r for r in rules if r.get("id") == rule_id), None)
    if rule is None:
        raise ValueError(
            f"rule_id={rule_id!r} wasn't found in get_transaction_rules() -- "
            "double-check the id."
        )
    blocked = _uncarried_fields_set(rule)
    if blocked:
        raise ValueError(
            f"rule {rule_id!r} uses fields update_transaction_rule cannot "
            f"preserve ({', '.join(blocked)}): the update mutation would reset "
            "them and silently widen the rule (e.g. from one household member "
            "or business entity to everything). Refusing; edit this rule in "
            "the Monarch web app instead. NOT sending."
        )
    current = _existing_to_input(rule)

    if set_merchant_name and set_merchant_name != current["setMerchantAction"]:
        await _verify_merchant_name_exists(set_merchant_name)

    merged = dict(current)
    merged["merchantNameCriteria"] = _merge(current["merchantNameCriteria"], merchant_name_criteria)
    merged["originalStatementCriteria"] = _merge(
        current["originalStatementCriteria"], original_statement_criteria
    )
    merged["amountCriteria"] = _merge(current["amountCriteria"], amount_criteria)
    merged["categoryIds"] = _merge(current["categoryIds"], category_ids)
    merged["accountIds"] = _merge(current["accountIds"], account_ids)
    merged["setCategoryAction"] = _merge(current["setCategoryAction"], set_category_action)
    merged["setMerchantAction"] = _merge(current["setMerchantAction"], set_merchant_name)
    merged["addTagsAction"] = _merge(current["addTagsAction"], add_tag_ids)
    if set_hide_from_reports is not None:
        merged["setHideFromReportsAction"] = bool(set_hide_from_reports)
    merged["reviewStatusAction"] = _merge(current["reviewStatusAction"], review_status)
    # Verified live 2026-09-30: the update mutation fails with an EMPTY
    # PayloadError ({message: null}) whenever reviewStatusAction is sent as
    # null; "" is accepted and stored as no review action. (Create is fine
    # with null.) So never send null here.
    if merged["reviewStatusAction"] is None:
        merged["reviewStatusAction"] = ""
    merged["needsReviewByUserAction"] = _merge(
        current["needsReviewByUserAction"], needs_review_by_user_id
    )
    merged["linkGoalAction"] = _merge(current["linkGoalAction"], link_goal_id)
    merged["linkSavingsGoalAction"] = _merge(
        current["linkSavingsGoalAction"], link_savings_goal_id
    )
    if split_action is not None:
        merged["splitTransactionsAction"] = (
            _build_split_action(split_action) if split_action else None
        )
    merged["applyToExistingTransactions"] = apply_to_existing_transactions

    # Verified live 2026-09-30: clearing a rule's last action is rejected by
    # the server with an empty PayloadError; refuse up front with a clear
    # message instead.
    has_criteria = any(
        merged.get(k)
        for k in (
            "merchantNameCriteria", "originalStatementCriteria", "merchantCriteria",
            "amountCriteria", "categoryIds", "accountIds",
        )
    )
    if not has_criteria:
        raise ValueError(
            "this update would leave the rule with no criteria, which Monarch "
            "rejects (empty error object, verified live)"
        )
    has_action = any(
        merged.get(k)
        for k in (
            "setMerchantAction", "setCategoryAction", "addTagsAction",
            "linkGoalAction", "linkSavingsGoalAction", "needsReviewByUserAction",
            "setHideFromReportsAction", "reviewStatusAction", "splitTransactionsAction",
        )
    )
    if not has_action:
        raise ValueError(
            "this update would leave the rule with no actions, which Monarch "
            "rejects -- use delete_transaction_rule to remove a rule instead"
        )

    data = await _call("Common_UpdateTransactionRuleMutationV2", {"input": merged})
    return update_transaction_rule_result(data)
