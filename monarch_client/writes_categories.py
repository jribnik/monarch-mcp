"""
Category / category-group / tag-update / merchant-update write operations.

Split out of writes.py (2026-09-30) so it can be developed independently;
follows writes.py conventions exactly: every function calls
_require_writes(<tool>) FIRST, then _call(...). The shared helpers are
imported from .writes (not re-implemented) so monkeypatching
`writes._client` in tests covers this module too.

All eight operations were captured from the live web app against the
disposable monarch-sandbox account on 2026-09-30 (api-recon UI capture,
not catalog-exported; see PROVENANCE_ENTRIES below and operations/README.md)
and verified live against that sandbox. No merchant DELETE is built
(deliberately: Common_DeleteMerchant is destructive and out of scope).

Error convention: like the other write results (project.delete_tag_result
et al.), application-level `errors` from the payload are passed through as a
field (None on success) rather than raised; a protocol-level top-level
errors[] is still raised by transport.py. Client-side guards (empty names,
recurrence requirements) raise ValueError before anything is sent.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from .writes import _call, _require_writes

TOOLS = [
    "create_category_group",
    "update_category_group",
    "delete_category_group",
    "create_category",
    "update_category",
    "delete_category",
    "update_tag",
    "update_merchant",
]

_VENDOR_NOTE = (
    "captured 2026-09-30 from the live web app against monarch-sandbox "
    "(api-recon UI capture, raw request body, not catalog-exported)"
)


def _prov(sha: str, note: str) -> dict[str, Any]:
    return {
        "catalog_query_hash": None,  # not catalog-exported -- see the .graphql file's header
        "vendored_sha256": sha,
        "exported_at": "2026-09-30T00:00:00+00:00",
        "runs_seen": [],
        "hand_repaired": False,
        "walk_reachable": False,
        "note": f"{note} -- {_VENDOR_NOTE}",
    }


def _prov_hw(op: str, note: str) -> dict[str, Any]:
    import hashlib

    from . import operations

    return {
        "catalog_query_hash": None,
        "vendored_sha256": hashlib.sha256(operations.load(op).encode()).hexdigest(),
        "exported_at": "2026-09-30T00:00:00+00:00",
        "runs_seen": [],
        "hand_repaired": False,
        "walk_reachable": False,
        "note": note,
    }


PROVENANCE_ENTRIES: dict[str, dict] = {
    "Common_CreateCategoryGroup": _prov(
        "8c76e2a174dc9ebf39b82b81909f63ec6e2530347c8015ced77de9fc6fd86c07",
        "backs create_category_group. Note this payload selects NO `errors` "
        "field (only categoryGroup)",
    ),
    "Common_UpdateCategoryGroup": _prov(
        "0eb68ffde53f0a1a9eb8ac2ac684215f89e33b554372f0279ff1b4ac59498706",
        "backs update_category_group. Payload selects no `errors` field",
    ),
    "Common_DeleteCategoryGroup": _prov(
        "ea5322bf3bc5a9ed47b69c3ee129619d27c199e0ab8b571f7bc13a02ddc8bc9e",
        "backs delete_category_group (optional moveToGroupId)",
    ),
    "Web_CreateCategory": _prov(
        "593bae9582f4a586968dd9ac3024ea6cb6c7b8bde5be084d117451afd94f25ce",
        "backs create_category",
    ),
    "Web_UpdateCategory": _prov(
        "56336ebf7e7b8834a23493aac9a5e41fc6c66a1385d79c5f35ab9ad98a2fde72",
        "backs update_category",
    ),
    "Web_DeleteCategory": _prov(
        "6c831eff037492438ec68293eb5f8d37742d67bc08545101bb89fc9ef8c95eca",
        "backs delete_category (optional moveToCategoryId reassigns its "
        "transactions)",
    ),
    "Common_UpdateTransactionTag": _prov(
        "edd843175c213291cc1a94518b4cc9ddbe9df2a175e570916a49b1cd3d681072",
        "backs update_tag (rename/recolor); errors selects only `message`",
    ),
    "Common_UpdateMerchant": _prov(
        "c2ca4041faf3004c8de83aefcc2eda95db0113f85cb3d0c1293ba275a00a0774",
        "backs update_merchant. Monarch REJECTS renaming a merchant onto an "
        "EXISTING merchant's name (fieldError 'A merchant with this name "
        "already exists', verified live 2026-09-30) -- unlike update_transaction's "
        "merchant rename, which merges",
    ),
    "Common_GetMerchantForEdit": _prov_hw(
        "Common_GetMerchantForEdit",
        "backs update_merchant's read-before-write merge. HAND-WRITTEN read-only "
        "query (field selection copied from the captured Common_UpdateMerchant "
        "response; root field merchant(id: ID!)); verified live on monarch-sandbox",
    ),
    "Common_SearchMerchantsByName": _prov_hw(
        "Common_SearchMerchantsByName",
        "backs update_merchant's rename-merge guard. HAND-WRITTEN read-only "
        "query merchants(search, limit, offset); verified live on monarch-sandbox",
    ),
}


# --------------------------------------------------------------------------
# Projectors. Pass-through of the payload's own fields (errors=None on
# success), matching project.py's convention for the other write results.
# --------------------------------------------------------------------------

def _payload(data: dict[str, Any], key: str) -> dict[str, Any]:
    return data.get(key) or {}


def category_group_result(data: dict[str, Any], key: str) -> dict[str, Any]:
    p = _payload(data, key)
    g = p.get("categoryGroup") or {}
    return {
        "id": g.get("id"),
        "name": g.get("name"),
        "type": g.get("type"),
        "order": g.get("order"),
        "color": g.get("color"),
        "groupLevelBudgetingEnabled": g.get("groupLevelBudgetingEnabled"),
        "budgetVariability": g.get("budgetVariability"),
        "rolloverPeriod": g.get("rolloverPeriod"),
        "errors": p.get("errors"),
    }


def category_result(data: dict[str, Any], key: str) -> dict[str, Any]:
    p = _payload(data, key)
    c = p.get("category") or {}
    return {
        "id": c.get("id"),
        "name": c.get("name"),
        "icon": c.get("icon"),
        "order": c.get("order"),
        "group": c.get("group"),
        "budgetVariability": c.get("budgetVariability"),
        "excludeFromBudget": c.get("excludeFromBudget"),
        "rolloverPeriod": c.get("rolloverPeriod"),
        "errors": p.get("errors"),
    }


def delete_result(data: dict[str, Any], key: str) -> dict[str, Any]:
    p = _payload(data, key)
    return {"deleted": p.get("deleted"), "errors": p.get("errors")}


def tag_result(data: dict[str, Any]) -> dict[str, Any]:
    p = _payload(data, "updateTransactionTag")
    return {"tag": p.get("tag"), "errors": p.get("errors")}


def merchant_result(data: dict[str, Any]) -> dict[str, Any]:
    p = _payload(data, "updateMerchant")
    return {"merchant": p.get("merchant"), "errors": p.get("errors")}


def _first_of_month() -> str:
    return date.today().replace(day=1).isoformat()


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# --------------------------------------------------------------------------
# Category groups
# --------------------------------------------------------------------------

async def create_category_group(
    name: str,
    type: str = "expense",
    group_level_budgeting_enabled: bool = False,
    rollover_enabled: bool = False,
    rollover_start_month: Optional[str] = None,
    rollover_type: str = "monthly",
) -> dict[str, Any]:
    """Create a category group. type is 'expense', 'income' or 'transfer'.
    rollover_start_month is an ISO date (first of a month; defaults to the
    current month). Verified live against monarch-sandbox 2026-09-30."""
    _require_writes("create_category_group")
    if not name or not name.strip():
        raise ValueError("create_category_group: name must not be empty")
    data = await _call(
        "Common_CreateCategoryGroup",
        {
            "input": {
                "name": name,
                "type": type,
                "groupLevelBudgetingEnabled": group_level_budgeting_enabled,
                "rolloverEnabled": rollover_enabled,
                "rolloverStartMonth": rollover_start_month or _first_of_month(),
                "rolloverType": rollover_type,
            }
        },
    )
    return category_group_result(data, "createCategoryGroup")


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
    """Update a category group. Only fields passed (not None) are sent.
    budget_variability is 'fixed' or 'flexible' per the captured UI values
    ('flexible' observed). Verified live against monarch-sandbox."""
    _require_writes("update_category_group")
    if name is not None and not name.strip():
        raise ValueError("update_category_group: name must not be empty")
    inp = _clean(
        {
            "id": group_id,
            "name": name,
            "groupLevelBudgetingEnabled": group_level_budgeting_enabled,
            "rolloverEnabled": rollover_enabled,
            "rolloverStartMonth": rollover_start_month,
            "rolloverType": rollover_type,
            "rolloverStartingBalance": rollover_starting_balance,
            "budgetVariability": budget_variability,
        }
    )
    data = await _call("Common_UpdateCategoryGroup", {"input": inp})
    return category_group_result(data, "updateCategoryGroup")


async def delete_category_group(
    group_id: str, move_to_group_id: Optional[str] = None
) -> dict[str, Any]:
    """Delete a category group. If it still contains categories, pass
    move_to_group_id to re-home them; otherwise the server refuses a
    non-empty group ("Category group is not empty"). Verified live against monarch-sandbox."""
    _require_writes("delete_category_group")
    variables: dict[str, Any] = {"id": group_id}
    if move_to_group_id is not None:
        variables["moveToGroupId"] = move_to_group_id
    data = await _call("Common_DeleteCategoryGroup", variables)
    return delete_result(data, "deleteCategoryGroup")


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------

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
    """Create a category inside a group (group_id). type is 'expense',
    'income' or 'transfer'. Verified live against monarch-sandbox."""
    _require_writes("create_category")
    if not name or not name.strip():
        raise ValueError("create_category: name must not be empty")
    data = await _call(
        "Web_CreateCategory",
        {
            "input": {
                "name": name,
                "icon": icon,
                "excludeFromBudget": exclude_from_budget,
                "group": group_id,
                "type": type,
                "budgetVariability": budget_variability,
                "rolloverEnabled": rollover_enabled,
                "rolloverStartMonth": rollover_start_month or _first_of_month(),
                "rolloverStartingBalance": rollover_starting_balance,
                "rolloverFrequency": rollover_frequency,
            }
        },
    )
    return category_result(data, "createCategory")


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
    """Update a category (rename, move to another group via group_id, icon,
    budget flags). Only fields passed (not None) are sent. Verified live
    against monarch-sandbox."""
    _require_writes("update_category")
    if name is not None and not name.strip():
        raise ValueError("update_category: name must not be empty")
    inp = _clean(
        {
            "id": category_id,
            "name": name,
            "icon": icon,
            "group": group_id,
            "type": type,
            "excludeFromBudget": exclude_from_budget,
            "budgetVariability": budget_variability,
            "rolloverEnabled": rollover_enabled,
            "rolloverStartMonth": rollover_start_month,
            "rolloverStartingBalance": rollover_starting_balance,
            "rolloverFrequency": rollover_frequency,
        }
    )
    data = await _call("Web_UpdateCategory", {"input": inp})
    return category_result(data, "updateCategory")


async def delete_category(
    category_id: str,
    move_to_category_id: Optional[str] = None,
    uncategorize_transactions: bool = False,
) -> dict[str, Any]:
    """Delete a category. Transactions in it are reassigned to
    move_to_category_id. Deleting WITHOUT a move target un-categorizes every
    transaction in the category, so that must be requested explicitly with
    uncategorize_transactions=True -- with neither argument a ValueError is
    raised before anything is sent. Verified live against monarch-sandbox."""
    _require_writes("delete_category")
    if move_to_category_id is None and not uncategorize_transactions:
        raise ValueError(
            "delete_category: pass move_to_category_id to reassign this "
            "category's transactions, or uncategorize_transactions=True to "
            "knowingly leave them uncategorized"
        )
    if move_to_category_id is not None and uncategorize_transactions:
        raise ValueError(
            "delete_category: pass move_to_category_id OR "
            "uncategorize_transactions=True, not both"
        )
    variables: dict[str, Any] = {"id": category_id}
    if move_to_category_id is not None:
        variables["moveToCategoryId"] = move_to_category_id
    data = await _call("Web_DeleteCategory", variables)
    return delete_result(data, "deleteCategory")


# --------------------------------------------------------------------------
# Tags / merchants
# --------------------------------------------------------------------------

async def update_tag(
    tag_id: str, name: str, color: str
) -> dict[str, Any]:
    """Rename and/or recolor a tag (color like '#E5484D'). Both name and
    color are sent (the captured UI always sends both). Verified live
    against monarch-sandbox."""
    _require_writes("update_tag")
    if not name or not name.strip():
        raise ValueError("update_tag: name must not be empty")
    data = await _call(
        "Common_UpdateTransactionTag",
        {"input": {"id": tag_id, "name": name, "color": color}},
    )
    return tag_result(data)


_MERCHANT_SEARCH_LIMIT = 100


async def _get_merchant(merchant_id: str) -> dict[str, Any]:
    data = await _call("Common_GetMerchantForEdit", {"id": merchant_id})
    merchant = data.get("merchant")
    if not merchant:
        raise ValueError(
            f"update_merchant: merchant_id={merchant_id!r} wasn't found -- "
            "double-check the id."
        )
    return merchant


def _norm_name(name: Optional[str]) -> str:
    return (name or "").strip().casefold()


async def _verify_no_other_merchant_named(merchant_id: str, name: str) -> None:
    """Fast-fail pre-check: Monarch itself REJECTS renaming a merchant onto
    another merchant's exact name (fieldError "A merchant with this name
    already exists", verified live 2026-09-30 -- update_merchant can never
    merge; merging happens only via update_transaction's merchant rename).
    This just turns that opaque server error into a clear one, comparing
    case-insensitively and whitespace-trimmed. Best-effort only: if the
    (substring) search hit its page limit and shows no clash we proceed and
    let the server decide, since it is authoritative."""
    data = await _call(
        "Common_SearchMerchantsByName",
        {"search": name.strip(), "limit": _MERCHANT_SEARCH_LIMIT, "offset": 0},
    )
    results = data.get("merchants") or []
    target = _norm_name(name)
    clash = [
        m for m in results
        if str(m.get("id")) != str(merchant_id) and _norm_name(m.get("name")) == target
    ]
    if clash:
        c = clash[0]
        raise ValueError(
            f"update_merchant: a merchant named {c.get('name')!r} already exists "
            f"(id {c.get('id')}, {c.get('transactionCount')} transactions) and "
            "Monarch does not allow renaming onto it. To merge this merchant's "
            "transactions into it, use update_transaction with "
            "merchant_name set to that name on each transaction."
        )


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
) -> dict[str, Any]:
    """Update a merchant: name, default category, and recurrence.

    The mutation takes the whole object (the UI always sends everything), so
    this reads the merchant FIRST and merges: every argument left at None
    KEEPS the merchant's current value (name, default category, application
    mode, and its recurring stream). default_category_id=None therefore means
    "keep"; to actively remove the default category pass
    clear_default_category=True. To switch recurrence off pass
    is_recurring=False. When turning recurrence ON for a merchant with no
    stream, recurring_frequency (e.g. 'monthly') and recurring_base_date (ISO
    date) are REQUIRED; with an existing stream any you omit are kept.

    Renaming onto ANOTHER existing merchant's name is not possible here:
    Monarch rejects it (verified live), and this refuses up front with a
    clear message when the name clash is visible. Merging merchants is done
    by renaming the transactions instead (update_transaction). Empty/blank
    names are refused."""
    _require_writes("update_merchant")
    if name is not None and not name.strip():
        raise ValueError("update_merchant: name must not be empty")
    if clear_default_category and default_category_id is not None:
        raise ValueError(
            "update_merchant: pass default_category_id OR "
            "clear_default_category=True, not both"
        )
    cur = await _get_merchant(merchant_id)
    new_name = name if name is not None else cur.get("name")
    if name is not None and name != cur.get("name"):
        await _verify_no_other_merchant_named(merchant_id, name)

    if clear_default_category:
        cat_id = None
    elif default_category_id is not None:
        cat_id = default_category_id
    else:
        cat_id = (cur.get("defaultCategory") or {}).get("id")

    stream = cur.get("recurringTransactionStream")
    want_recurring = is_recurring if is_recurring is not None else bool(stream)
    if want_recurring:
        s = stream or {}
        freq = recurring_frequency or s.get("frequency")
        base = recurring_base_date or s.get("baseDate")
        if not freq or not base:
            raise ValueError(
                "update_merchant: is_recurring=True requires recurring_frequency "
                "and recurring_base_date (this merchant has no existing stream "
                "to inherit them from)"
            )
        amount = recurring_amount if recurring_amount is not None else s.get("amount")
        active = (
            recurring_is_active
            if recurring_is_active is not None
            else s.get("isActive", True)
        )
        recurrence: dict[str, Any] = {
            "isRecurring": True,
            "amount": amount if amount is not None else 0,
            "isActive": active,
            "frequency": freq,
            "baseDate": base,
        }
    else:
        recurrence = {
            "isRecurring": False,
            "amount": recurring_amount if recurring_amount is not None else 0,
            "isActive": recurring_is_active if recurring_is_active is not None else True,
        }
    mode = default_category_application_mode or cur.get(
        "defaultCategoryApplicationMode"
    ) or "new_and_edits"
    data = await _call(
        "Common_UpdateMerchant",
        {
            "input": {
                "merchantId": merchant_id,
                "name": new_name,
                "defaultCategoryId": cat_id,
                "defaultCategoryApplicationMode": mode,
                "recurrence": recurrence,
            }
        },
    )
    return merchant_result(data)
