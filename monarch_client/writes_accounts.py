"""
Account / budget / savings-goal write tools, reimplemented on monarch_client.

Same conventions as writes.py: only vendored, named mutations are callable
(no generic mutation escape hatch), every function calls _require_writes()
first, and every mutation goes through writes._call (so the transport-level
gate and test monkeypatching of writes._client keep working).

Functions: update_account, delete_account, set_budget_amount,
set_flex_budget_amount, create_savings_goal, update_savings_goal,
set_savings_goal_budget_amount, delete_savings_goal.

Deliberately NOT built: Common_CreateBudgetForHousehold and
Common_UpdateBudgetSettings (too easy to clobber a real budget).

All operations were captured from the sandbox web app on 2026-09-30 and
verified live against the disposable monarch-sandbox account; see each
PROVENANCE entry's note.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from . import operations
from .writes import _call, _require_writes

TOOLS = [
    "update_account",
    "delete_account",
    "set_budget_amount",
    "set_flex_budget_amount",
    "create_savings_goal",
    "update_savings_goal",
    "set_savings_goal_budget_amount",
    "delete_savings_goal",
]

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-01$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DEFAULT_GOAL_IMAGE = (
    "https://monarch-static-assets-us-east-2.s3.us-east-2.amazonaws.com/goals/savings.png"
)


def _sha(op: str) -> str:
    # Same computation as the loader: header stripped, then sha256.
    return hashlib.sha256(operations.load(op).encode()).hexdigest()


def _prov(op: str, note: str) -> dict:
    return {
        "catalog_query_hash": None,
        "vendored_sha256": _sha(op),
        "exported_at": "2026-09-30T00:00:00+00:00",
        "runs_seen": [],
        "hand_repaired": False,
        "walk_reachable": False,
        "note": note,
    }


PROVENANCE_ENTRIES: dict[str, dict] = {
    "Common_GetAccountForEdit": _prov(
        "Common_GetAccountForEdit",
        "backs update_account/delete_account's pre-read. HAND-WRITTEN trim of the "
        "catalog's Common_AccountDetails_getAccount EditAccountFormFields fragment "
        "(not a verbatim capture); variable {'id': UUID!}. Query, not a mutation.",
    ),
    "Common_UpdateAccount": _prov(
        "Common_UpdateAccount",
        "backs update_account. Sandbox UI capture 2026-09-30. Takes a FULL-ish "
        "input, so update_account pre-reads the account and resends every current "
        "value with only the requested fields changed.",
    ),
    "Common_DeleteAccount": _prov(
        "Common_DeleteAccount",
        "backs delete_account. IRREVERSIBLE. Sandbox UI capture 2026-09-30; "
        "variable {'id': UUID!}. Client-side gates: confirm_name must equal the "
        "account's displayName, and bank-linked accounts are refused.",
    ),
    "Common_UpdateBudgetItem": _prov(
        "Common_UpdateBudgetItem",
        "backs set_budget_amount. Sandbox UI capture 2026-09-30; input "
        "{startDate, timeframe:'month', amount, applyToFuture, categoryId}. The UI "
        "defaulted applyToFuture from a user setting; this client defaults False.",
    ),
    "Common_UpdateFlexBudgetMutation": _prov(
        "Common_UpdateFlexBudgetMutation",
        "backs set_flex_budget_amount. Sandbox UI capture 2026-09-30; input "
        "{startDate, amount, applyToFuture}. Meant for Flex budget mode.",
    ),
    "Common_CreateSavingsGoals": _prov(
        "Common_CreateSavingsGoals",
        "backs create_savings_goal. Sandbox UI capture 2026-09-30; wizard sends a "
        "list, this client sends a single goal.",
    ),
    "Common_UpdateSavingsGoal": _prov(
        "Common_UpdateSavingsGoal",
        "backs update_savings_goal. Sandbox UI capture 2026-09-30; input "
        "{id, name, targetAmount, isSinkingFund, targetDate}.",
    ),
    "Common_SetSavingsGoalBudgetAmount": _prov(
        "Common_SetSavingsGoalBudgetAmount",
        "backs set_savings_goal_budget_amount. Sandbox UI capture 2026-09-30; "
        "input {month, savingsGoalId, amount, applyToFuture, accountId}.",
    ),
    "Common_DeleteSavingsGoal": _prov(
        "Common_DeleteSavingsGoal",
        "backs delete_savings_goal. IRREVERSIBLE. Sandbox UI capture 2026-09-30; "
        "input {id}.",
    ),
}


# --- projectors -----------------------------------------------------------

def _passthrough(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Pass-through `{key: payload}`; the payload keeps Monarch's own
    `errors` so a failed write is visible, never swallowed."""
    return {key: data.get(key)}


def update_account_result(data: dict[str, Any]) -> dict[str, Any]:
    return _passthrough(data, "updateAccount")


def delete_account_result(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("deleteAccount") or {}
    return {"deleted_flag": bool(payload.get("deleted")), "errors": payload.get("errors")}


def budget_item_result(data: dict[str, Any]) -> dict[str, Any]:
    return _passthrough(data, "updateOrCreateBudgetItem")


def flex_budget_item_result(data: dict[str, Any]) -> dict[str, Any]:
    return _passthrough(data, "updateOrCreateFlexBudgetItem")


def create_savings_goals_result(data: dict[str, Any]) -> dict[str, Any]:
    return _passthrough(data, "createSavingsGoals")


def update_savings_goal_result(data: dict[str, Any]) -> dict[str, Any]:
    return _passthrough(data, "updateSavingsGoal")


def set_savings_goal_budget_amount_result(data: dict[str, Any]) -> dict[str, Any]:
    return _passthrough(data, "setSavingsGoalBudgetAmount")


def delete_savings_goal_result(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("deleteSavingsGoal") or {}
    return {"deleted_flag": bool(payload.get("success")), "errors": payload.get("errors")}


# --- helpers --------------------------------------------------------------

def _check_month(month: str) -> None:
    if not isinstance(month, str) or not _MONTH_RE.match(month):
        raise ValueError(
            f"month={month!r} must be the first day of a month, 'YYYY-MM-01'."
        )


def _check_amount(amount: Any) -> None:
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
        raise ValueError(f"amount={amount!r} must be a non-negative number.")


async def _get_account_for_edit(account_id: str) -> dict[str, Any]:
    data = await _call("Common_GetAccountForEdit", {"id": account_id})
    account = data.get("account")
    if not account:
        raise ValueError(
            f"account_id={account_id!r} wasn't found -- double-check the id."
        )
    return account


def _require_manual(account: dict[str, Any], tool: str) -> None:
    if account.get("credential") is not None or not account.get("isManual", True):
        raise ValueError(
            f"{tool}: account {account.get('id')!r} ({account.get('displayName')!r}) "
            "is bank-linked (it has a credential), not manual. Only MANUAL "
            "accounts can be changed or deleted through this server."
        )


# --- accounts -------------------------------------------------------------

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
) -> dict[str, Any]:
    """Edit a MANUAL account. Only the fields you pass change: the account's
    current values are read first and resent in full (Monarch's input is
    full-ish). notes="" clears the notes. account_type and account_subtype
    must be given together (a pair from get_account_type_options)."""
    _require_writes("update_account")
    changes = {
        "name": name, "display_balance": display_balance, "notes": notes,
        "account_type": account_type, "account_subtype": account_subtype,
        "hide_from_list": hide_from_list, "hide_in_budget": hide_in_budget,
        "hide_transactions_from_reports": hide_transactions_from_reports,
        "include_in_net_worth": include_in_net_worth, "interest_rate": interest_rate,
    }
    if all(v is None for v in changes.values()):
        raise ValueError("update_account: nothing to change -- pass at least one field.")
    if (account_type is None) != (account_subtype is None):
        raise ValueError(
            "update_account: account_type and account_subtype must be given together."
        )
    if name is not None and not name.strip():
        raise ValueError("update_account: name must not be empty.")

    cur = await _get_account_for_edit(account_id)
    _require_manual(cur, "update_account")

    input_: dict[str, Any] = {
        "id": cur["id"],
        "dataProvider": cur.get("dataProvider") or "",
        "dataProviderAccountId": cur.get("dataProviderAccountId"),
        "name": cur.get("displayName"),
        "notes": cur.get("notes"),
        "type": (cur.get("type") or {}).get("name"),
        "subtype": (cur.get("subtype") or {}).get("name"),
        "displayBalance": cur.get("displayBalance"),
        "invertSyncedBalance": bool(cur.get("invertSyncedBalance")),
        "useAvailableBalance": bool(cur.get("useAvailableBalance")),
        "hideFromList": bool(cur.get("hideFromList")),
        "hideInBudget": bool(cur.get("hideInBudget")),
        "hideTransactionsFromReports": bool(cur.get("hideTransactionsFromReports")),
        "synced": False,
        "apr": cur.get("apr"),
        "excludeFromDebtPaydown": bool(cur.get("excludeFromDebtPaydown")),
        "deactivatedAt": cur.get("deactivatedAt"),
        "includeInNetWorth": bool(cur.get("includeInNetWorth")),
        "limit": cur.get("limit"),
        "plannedPayment": cur.get("plannedPayment"),
        "minimumPayment": cur.get("minimumPayment"),
        "interestRate": cur.get("interestRate"),
        "recurrence": {},
        "ownerUserId": (cur.get("ownedByUser") or {}).get("id"),
        "businessEntityId": (cur.get("businessEntity") or {}).get("id"),
        "interestRateType": cur.get("interestRateType"),
    }
    if name is not None:
        input_["name"] = name
    if display_balance is not None:
        input_["displayBalance"] = display_balance
    if notes is not None:
        input_["notes"] = notes
    if account_type is not None:
        input_["type"] = account_type
        input_["subtype"] = account_subtype
    if hide_from_list is not None:
        input_["hideFromList"] = hide_from_list
    if hide_in_budget is not None:
        input_["hideInBudget"] = hide_in_budget
    if hide_transactions_from_reports is not None:
        input_["hideTransactionsFromReports"] = hide_transactions_from_reports
    if include_in_net_worth is not None:
        input_["includeInNetWorth"] = include_in_net_worth
    if interest_rate is not None:
        input_["interestRate"] = interest_rate

    data = await _call("Common_UpdateAccount", {"input": input_})
    return update_account_result(data)


async def delete_account(account_id: str, confirm_name: str) -> dict[str, Any]:
    """IRREVERSIBLE. Deletes a MANUAL account and all its transactions.
    confirm_name must exactly equal the account's current name; bank-linked
    accounts are refused."""
    _require_writes("delete_account")
    cur = await _get_account_for_edit(account_id)
    _require_manual(cur, "delete_account")
    if confirm_name != cur.get("displayName"):
        raise ValueError(
            f"delete_account: confirm_name={confirm_name!r} does not exactly match the "
            f"account's name {cur.get('displayName')!r} -- NOT deleting."
        )
    data = await _call("Common_DeleteAccount", {"id": account_id})
    return delete_account_result(data)


# --- budget ---------------------------------------------------------------

async def set_budget_amount(
    category_id: str,
    amount: float,
    month: str,
    apply_to_future: bool = False,
) -> dict[str, Any]:
    """Set one category's budget for one month (month = 'YYYY-MM-01'). With
    apply_to_future=True the amount also carries to all LATER months."""
    _require_writes("set_budget_amount")
    _check_month(month)
    _check_amount(amount)
    data = await _call(
        "Common_UpdateBudgetItem",
        {
            "input": {
                "startDate": month,
                "timeframe": "month",
                "amount": amount,
                "applyToFuture": bool(apply_to_future),
                "categoryId": category_id,
            }
        },
    )
    return budget_item_result(data)


async def set_flex_budget_amount(
    amount: float,
    month: str,
    apply_to_future: bool = False,
) -> dict[str, Any]:
    """Set the Flex-mode flexible-spending budget for one month
    (month = 'YYYY-MM-01')."""
    _require_writes("set_flex_budget_amount")
    _check_month(month)
    _check_amount(amount)
    data = await _call(
        "Common_UpdateFlexBudgetMutation",
        {
            "input": {
                "startDate": month,
                "amount": amount,
                "applyToFuture": bool(apply_to_future),
            }
        },
    )
    return flex_budget_item_result(data)


# --- savings goals --------------------------------------------------------

async def create_savings_goal(
    name: str,
    target_amount: Optional[float] = None,
    target_date: Optional[str] = None,
    is_sinking_fund: bool = False,
) -> dict[str, Any]:
    """Create one savings goal. Creation takes only a name (and type); a
    target amount/date/sinking-fund flag are applied with a follow-up
    update_savings_goal call."""
    _require_writes("create_savings_goal")
    if not name or not name.strip():
        raise ValueError("create_savings_goal: name must not be empty.")
    if target_date is not None and not _DATE_RE.match(target_date):
        raise ValueError("create_savings_goal: target_date must be 'YYYY-MM-DD'.")
    data = await _call(
        "Common_CreateSavingsGoals",
        {
            "input": {
                "goals": [
                    {
                        "name": name,
                        "type": "savings",
                        "imageStorageProvider": "s3",
                        "imageStorageProviderId": _DEFAULT_GOAL_IMAGE,
                    }
                ]
            }
        },
    )
    result = create_savings_goals_result(data)
    goals = (result.get("createSavingsGoals") or {}).get("savingsGoals") or []
    if goals and (target_amount is not None or target_date is not None or is_sinking_fund):
        upd = await update_savings_goal(
            goals[0]["id"],
            target_amount=target_amount,
            target_date=target_date,
            is_sinking_fund=True if is_sinking_fund else None,
        )
        result["followUpUpdate"] = upd
    return result


async def update_savings_goal(
    goal_id: str,
    name: Optional[str] = None,
    target_amount: Optional[float] = None,
    target_date: Optional[str] = None,
    is_sinking_fund: Optional[bool] = None,
) -> dict[str, Any]:
    """Update a savings goal; only the fields passed are sent."""
    _require_writes("update_savings_goal")
    if all(v is None for v in (name, target_amount, target_date, is_sinking_fund)):
        raise ValueError("update_savings_goal: nothing to change.")
    if target_amount is not None:
        _check_amount(target_amount)
    if target_date is not None and not _DATE_RE.match(target_date):
        raise ValueError("update_savings_goal: target_date must be 'YYYY-MM-DD'.")
    input_: dict[str, Any] = {"id": goal_id}
    if name is not None:
        input_["name"] = name
    if target_amount is not None:
        input_["targetAmount"] = target_amount
    if target_date is not None:
        input_["targetDate"] = target_date
    if is_sinking_fund is not None:
        input_["isSinkingFund"] = bool(is_sinking_fund)
    data = await _call("Common_UpdateSavingsGoal", {"input": input_})
    return update_savings_goal_result(data)


async def set_savings_goal_budget_amount(
    goal_id: str,
    amount: float,
    month: str,
    apply_to_future: bool = False,
    account_id: Optional[str] = None,
) -> dict[str, Any]:
    """Set the monthly budgeted contribution to a savings goal
    (month = 'YYYY-MM-01')."""
    _require_writes("set_savings_goal_budget_amount")
    _check_month(month)
    _check_amount(amount)
    data = await _call(
        "Common_SetSavingsGoalBudgetAmount",
        {
            "input": {
                "month": month,
                "savingsGoalId": goal_id,
                "amount": amount,
                "applyToFuture": bool(apply_to_future),
                "accountId": account_id,
            }
        },
    )
    return set_savings_goal_budget_amount_result(data)


async def delete_savings_goal(goal_id: str) -> dict[str, Any]:
    """IRREVERSIBLE. Deletes a savings goal."""
    _require_writes("delete_savings_goal")
    data = await _call("Common_DeleteSavingsGoal", {"input": {"id": goal_id}})
    return delete_savings_goal_result(data)
