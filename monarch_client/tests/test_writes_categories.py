"""Hermetic tests for writes_categories.py (mocked client, no network)."""

from __future__ import annotations

import hashlib
import inspect

import pytest

from monarch_client import errors, operations, writes, writes_categories as wc


class _FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def call(self, op_name, variables):
        self.calls.append((op_name, variables))
        return self.responses[op_name]


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setenv(writes.WRITES_ENV, "1")
    monkeypatch.setattr(writes, "_client", client)
    return client


# ---- gate ------------------------------------------------------------

_ARGS = {
    "create_category_group": ("G",),
    "update_category_group": ("g1",),
    "delete_category_group": ("g1",),
    "create_category": ("C", "g1"),
    "update_category": ("c1",),
    "delete_category": ("c1",),
    "update_tag": ("t1", "n", "#000000"),
    "update_merchant": ("m1", "N"),
}


def test_tools_list_matches_module_coroutines():
    found = {
        n
        for n, f in inspect.getmembers(wc, inspect.iscoroutinefunction)
        if not n.startswith("_") and f.__module__ == wc.__name__
    }
    assert found == set(wc.TOOLS) == set(_ARGS)


@pytest.mark.asyncio
@pytest.mark.parametrize("fn", wc.TOOLS)
async def test_gate_closed_refuses_and_sends_nothing(monkeypatch, fn):
    client = _FakeClient()
    monkeypatch.delenv(writes.WRITES_ENV, raising=False)
    monkeypatch.setattr(writes, "_client", client)
    with pytest.raises(errors.MonarchWriteBlocked):
        await getattr(wc, fn)(*_ARGS[fn])
    assert client.calls == []


# ---- category groups ----------------------------------------------------

_GROUP = {"id": "g1", "name": "G", "type": "expense", "order": 1, "color": None,
          "groupLevelBudgetingEnabled": False, "budgetVariability": None,
          "rolloverPeriod": None}


@pytest.mark.asyncio
async def test_create_category_group(fake_client):
    fake_client.responses["Common_CreateCategoryGroup"] = {
        "createCategoryGroup": {"categoryGroup": _GROUP}
    }
    r = await wc.create_category_group("G", rollover_start_month="2026-09-01")
    op, v = fake_client.calls[0]
    assert op == "Common_CreateCategoryGroup"
    assert v == {"input": {
        "name": "G", "type": "expense", "groupLevelBudgetingEnabled": False,
        "rolloverEnabled": False, "rolloverStartMonth": "2026-09-01",
        "rolloverType": "monthly"}}
    assert r["id"] == "g1" and r["errors"] is None


@pytest.mark.asyncio
async def test_update_category_group_sends_only_given_fields(fake_client):
    fake_client.responses["Common_UpdateCategoryGroup"] = {
        "updateCategoryGroup": {"categoryGroup": _GROUP}
    }
    await wc.update_category_group("g1", name="X")
    assert fake_client.calls == [
        ("Common_UpdateCategoryGroup", {"input": {"id": "g1", "name": "X"}})
    ]


@pytest.mark.asyncio
async def test_delete_category_group_and_error_passthrough(fake_client):
    fake_client.responses["Common_DeleteCategoryGroup"] = {
        "deleteCategoryGroup": {
            "deleted": None,
            "errors": {"message": "Category group is not empty", "code": None,
                       "fieldErrors": None},
        }
    }
    r = await wc.delete_category_group("g1")
    assert fake_client.calls == [("Common_DeleteCategoryGroup", {"id": "g1"})]
    assert r["deleted"] is None
    assert r["errors"]["message"] == "Category group is not empty"
    await wc.delete_category_group("g1", move_to_group_id="g2")
    assert fake_client.calls[1][1] == {"id": "g1", "moveToGroupId": "g2"}


# ---- categories -----------------------------------------------------------

_CAT = {"id": "c1", "name": "C", "icon": "x", "order": 0,
        "group": {"id": "g1"}, "budgetVariability": "flexible",
        "excludeFromBudget": False, "rolloverPeriod": None}


@pytest.mark.asyncio
async def test_create_category(fake_client):
    fake_client.responses["Web_CreateCategory"] = {
        "createCategory": {"category": _CAT, "errors": None}
    }
    r = await wc.create_category("C", "g1", rollover_start_month="2026-09-01")
    op, v = fake_client.calls[0]
    assert op == "Web_CreateCategory"
    assert v["input"]["group"] == "g1" and v["input"]["name"] == "C"
    assert v["input"]["rolloverFrequency"] == "monthly"
    assert r["id"] == "c1" and r["errors"] is None


@pytest.mark.asyncio
async def test_update_category_partial_and_move(fake_client):
    fake_client.responses["Web_UpdateCategory"] = {
        "updateCategory": {"category": _CAT, "errors": None}
    }
    await wc.update_category("c1", group_id="g2")
    assert fake_client.calls == [
        ("Web_UpdateCategory", {"input": {"id": "c1", "group": "g2"}})
    ]


@pytest.mark.asyncio
async def test_delete_category(fake_client):
    fake_client.responses["Web_DeleteCategory"] = {
        "deleteCategory": {"deleted": True, "errors": None}
    }
    r = await wc.delete_category("c1", move_to_category_id="c2")
    assert fake_client.calls == [
        ("Web_DeleteCategory", {"id": "c1", "moveToCategoryId": "c2"})
    ]
    assert r == {"deleted": True, "errors": None}


# ---- tag / merchant ---------------------------------------------------------

@pytest.mark.asyncio
async def test_update_tag(fake_client):
    fake_client.responses["Common_UpdateTransactionTag"] = {
        "updateTransactionTag": {
            "tag": {"id": "t1", "name": "n", "color": "#000000", "order": 1},
            "errors": None,
        }
    }
    r = await wc.update_tag("t1", "n", "#000000")
    assert fake_client.calls == [
        ("Common_UpdateTransactionTag",
         {"input": {"id": "t1", "name": "n", "color": "#000000"}})
    ]
    assert r["tag"]["id"] == "t1" and r["errors"] is None


_MERCH = {"updateMerchant": {"merchant": {"id": "m1", "name": "N"}, "errors": None}}


@pytest.mark.asyncio
async def test_update_merchant_non_recurring(fake_client):
    fake_client.responses["Common_UpdateMerchant"] = _MERCH
    r = await wc.update_merchant("m1", "N")
    assert fake_client.calls == [("Common_UpdateMerchant", {"input": {
        "merchantId": "m1", "name": "N", "defaultCategoryId": None,
        "defaultCategoryApplicationMode": "new_and_edits",
        "recurrence": {"isRecurring": False, "amount": 0, "isActive": True}}})]
    assert r["merchant"]["id"] == "m1"


@pytest.mark.asyncio
async def test_update_merchant_recurring(fake_client):
    fake_client.responses["Common_UpdateMerchant"] = _MERCH
    await wc.update_merchant(
        "m1", "N", is_recurring=True, recurring_amount=-5,
        recurring_frequency="monthly", recurring_base_date="2026-10-15")
    assert fake_client.calls[0][1]["input"]["recurrence"] == {
        "isRecurring": True, "amount": -5, "isActive": True,
        "frequency": "monthly", "baseDate": "2026-10-15"}


@pytest.mark.asyncio
async def test_client_side_guards_send_nothing(fake_client):
    with pytest.raises(ValueError):
        await wc.update_merchant("m1", "  ")
    with pytest.raises(ValueError):
        await wc.update_merchant("m1", "N", is_recurring=True)
    with pytest.raises(ValueError):
        await wc.create_category_group("")
    with pytest.raises(ValueError):
        await wc.create_category("", "g1")
    with pytest.raises(ValueError):
        await wc.update_tag("t1", "", "#000000")
    assert fake_client.calls == []


# ---- provenance ------------------------------------------------------------

@pytest.mark.parametrize("op_name", sorted(wc.PROVENANCE_ENTRIES))
def test_vendored_sha256_matches(op_name):
    text = operations.load(op_name)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == \
        wc.PROVENANCE_ENTRIES[op_name]["vendored_sha256"]
    assert "__recon_redacted__" not in text
    assert operations.is_mutation(op_name)


def test_provenance_entry_shape_matches_existing():
    ref = set(operations.PROVENANCE["Common_DeleteTransactionMutation"]) | {"walk_reachable"}
    for entry in wc.PROVENANCE_ENTRIES.values():
        assert set(entry) == ref
