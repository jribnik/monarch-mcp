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
        "backs update_merchant. Renaming a merchant to an EXISTING merchant's "
        "name MERGES them (same as rename-by-rule behaviour noted in writes.py)",
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
    move_to_group_id to re-home them; otherwise the server decides (see the
    live-verification notes). Verified live against monarch-sandbox."""
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
    category_id: str, move_to_category_id: Optional[str] = None
) -> dict[str, Any]:
    """Delete a category. Transactions in it are reassigned to
    move_to_category_id when given. Destructive: if omitted, the server
    decides what happens to existing transactions (see live notes).
    Verified live against monarch-sandbox."""
    _require_writes("delete_category")
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


async def update_merchant(
    merchant_id: str,
    name: str,
    default_category_id: Optional[str] = None,
    default_category_application_mode: str = "new_and_edits",
    is_recurring: bool = False,
    recurring_amount: float = 0,
    recurring_is_active: bool = True,
    recurring_frequency: Optional[str] = None,
    recurring_base_date: Optional[str] = None,
) -> dict[str, Any]:
    """Update a merchant: name, default category, and recurrence.

    WARNING -- renaming == merging: setting `name` to the name of ANOTHER
    existing merchant MERGES this merchant into that one (its transactions
    move over, this merchant id ceases to exist). This is irreversible
    through this server (renaming back does not un-merge). Only pass a name
    you know is unique, or one you intend to merge into. Empty/blank names
    are refused client-side.

    The captured UI always sends the whole object, so name and recurrence
    are always sent. To leave a merchant non-recurring pass
    is_recurring=False (recurrence {isRecurring:false, amount:0,
    isActive:true}). When is_recurring=True, recurring_frequency (e.g.
    'monthly', 'weekly') and recurring_base_date (ISO date of a typical
    occurrence) are REQUIRED client-side. default_category_id=None clears
    nothing special -- it is sent as null exactly as the UI does.
    default_category_application_mode: 'new_and_edits' (as captured).
    Verified live against monarch-sandbox."""
    _require_writes("update_merchant")
    if not name or not name.strip():
        raise ValueError("update_merchant: name must not be empty")
    recurrence: dict[str, Any] = {
        "isRecurring": is_recurring,
        "amount": recurring_amount,
        "isActive": recurring_is_active,
    }
    if is_recurring:
        if not recurring_frequency or not recurring_base_date:
            raise ValueError(
                "update_merchant: is_recurring=True requires recurring_frequency "
                "and recurring_base_date"
            )
        recurrence["frequency"] = recurring_frequency
        recurrence["baseDate"] = recurring_base_date
    data = await _call(
        "Common_UpdateMerchant",
        {
            "input": {
                "merchantId": merchant_id,
                "name": name,
                "defaultCategoryId": default_category_id,
                "defaultCategoryApplicationMode": default_category_application_mode,
                "recurrence": recurrence,
            }
        },
    )
    return merchant_result(data)
