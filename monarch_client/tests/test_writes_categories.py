"""Hermetic tests for writes_categories.py (mocked client, no network)."""

from __future__ import annotations

import hashlib
import inspect
from importlib import resources

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
_STREAM = {"id": "s1", "frequency": "monthly", "amount": -9.5,
           "baseDate": "2026-10-03", "isActive": True}


def _cur(category=None, stream=None, name="Old"):
    return {"merchant": {"id": "m1", "name": name,
                         "defaultCategoryApplicationMode": "new_only",
                         "defaultCategory": category,
                         "recurringTransactionStream": stream}}


def _setup(fake_client, cur=None, others=None):
    fake_client.responses["Common_GetMerchantForEdit"] = cur or _cur()
    fake_client.responses["Common_SearchMerchantsByName"] = {"merchants": others or []}
    fake_client.responses["Common_UpdateMerchant"] = _MERCH


def _sent(fake_client):
    ups = [v for n, v in fake_client.calls if n == "Common_UpdateMerchant"]
    assert len(ups) == 1
    return ups[0]["input"]


@pytest.mark.asyncio
async def test_update_merchant_plain_rename_preserves_category_and_stream(fake_client):
    _setup(fake_client, _cur({"id": "cat9", "name": "X"}, _STREAM))
    r = await wc.update_merchant("m1", name="New")
    assert _sent(fake_client) == {
        "merchantId": "m1", "name": "New", "defaultCategoryId": "cat9",
        "defaultCategoryApplicationMode": "new_only",
        "recurrence": {"isRecurring": True, "amount": -9.5, "isActive": True,
                       "frequency": "monthly", "baseDate": "2026-10-03"}}
    assert r["merchant"]["id"] == "m1"


@pytest.mark.asyncio
async def test_update_merchant_no_category_no_stream_stays_empty(fake_client):
    _setup(fake_client)
    await wc.update_merchant("m1", default_category_id="c5")
    i = _sent(fake_client)
    assert i["name"] == "Old" and i["defaultCategoryId"] == "c5"
    assert i["recurrence"] == {"isRecurring": False, "amount": 0, "isActive": True}
    # no rename -> no merge-guard search
    assert all(n != "Common_SearchMerchantsByName" for n, _ in fake_client.calls)


@pytest.mark.asyncio
async def test_update_merchant_clear_category_and_stop_recurring(fake_client):
    _setup(fake_client, _cur({"id": "cat9", "name": "X"}, _STREAM))
    await wc.update_merchant("m1", clear_default_category=True, is_recurring=False)
    i = _sent(fake_client)
    assert i["defaultCategoryId"] is None
    assert i["recurrence"]["isRecurring"] is False


@pytest.mark.asyncio
async def test_update_merchant_partial_recurring_override_keeps_rest(fake_client):
    _setup(fake_client, _cur(None, _STREAM))
    await wc.update_merchant("m1", recurring_amount=-12)
    assert _sent(fake_client)["recurrence"] == {
        "isRecurring": True, "amount": -12, "isActive": True,
        "frequency": "monthly", "baseDate": "2026-10-03"}


@pytest.mark.asyncio
async def test_update_merchant_new_recurring(fake_client):
    _setup(fake_client)
    await wc.update_merchant(
        "m1", is_recurring=True, recurring_amount=-5,
        recurring_frequency="monthly", recurring_base_date="2026-10-15")
    assert _sent(fake_client)["recurrence"] == {
        "isRecurring": True, "amount": -5, "isActive": True,
        "frequency": "monthly", "baseDate": "2026-10-15"}


@pytest.mark.asyncio
async def test_update_merchant_rename_onto_existing_refused(fake_client):
    _setup(fake_client, others=[
        {"id": "m2", "name": "  new ", "transactionCount": 4},
        {"id": "m1", "name": "New", "transactionCount": 1}])
    with pytest.raises(ValueError, match="MERGE"):
        await wc.update_merchant("m1", name="New")
    assert all(n != "Common_UpdateMerchant" for n, _ in fake_client.calls)


@pytest.mark.asyncio
async def test_update_merchant_allow_merge_skips_guard(fake_client):
    _setup(fake_client, others=[{"id": "m2", "name": "New", "transactionCount": 4}])
    await wc.update_merchant("m1", name="New", allow_merge=True)
    assert _sent(fake_client)["name"] == "New"
    assert all(n != "Common_SearchMerchantsByName" for n, _ in fake_client.calls)


@pytest.mark.asyncio
async def test_update_merchant_unique_rename_allowed_and_self_ignored(fake_client):
    _setup(fake_client, others=[{"id": "m3", "name": "New Wave", "transactionCount": 2}])
    await wc.update_merchant("m1", name="New")
    assert _sent(fake_client)["name"] == "New"


@pytest.mark.asyncio
async def test_update_merchant_truncated_search_fails_closed(fake_client):
    _setup(fake_client, others=[
        {"id": f"x{i}", "name": f"New{i}", "transactionCount": 1}
        for i in range(wc._MERCHANT_SEARCH_LIMIT)])
    with pytest.raises(ValueError, match="page limit"):
        await wc.update_merchant("m1", name="New")
    assert all(n != "Common_UpdateMerchant" for n, _ in fake_client.calls)


@pytest.mark.asyncio
async def test_update_merchant_unknown_id(fake_client):
    fake_client.responses["Common_GetMerchantForEdit"] = {"merchant": None}
    with pytest.raises(ValueError, match="wasn't found"):
        await wc.update_merchant("nope", name="N")
    assert [n for n, _ in fake_client.calls] == ["Common_GetMerchantForEdit"]


@pytest.mark.asyncio
async def test_delete_category_requires_target_or_explicit_uncategorize(fake_client):
    with pytest.raises(ValueError, match="uncategorize_transactions"):
        await wc.delete_category("c1")
    with pytest.raises(ValueError, match="not both"):
        await wc.delete_category("c1", "c2", uncategorize_transactions=True)
    assert fake_client.calls == []
    fake_client.responses["Web_DeleteCategory"] = {
        "deleteCategory": {"deleted": True, "errors": None}}
    await wc.delete_category("c1", uncategorize_transactions=True)
    assert fake_client.calls == [("Web_DeleteCategory", {"id": "c1"})]


@pytest.mark.asyncio
async def test_client_side_guards_send_nothing(fake_client):
    with pytest.raises(ValueError):
        await wc.update_merchant("m1", "  ")
    with pytest.raises(ValueError):
        await wc.update_merchant("m1", default_category_id="c", clear_default_category=True)
    with pytest.raises(ValueError):
        await wc.create_category_group("")
    with pytest.raises(ValueError):
        await wc.create_category("", "g1")
    with pytest.raises(ValueError):
        await wc.update_tag("t1", "", "#000000")
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_update_merchant_recurring_needs_freq_when_no_stream(fake_client):
    _setup(fake_client)
    with pytest.raises(ValueError, match="requires recurring_frequency"):
        await wc.update_merchant("m1", is_recurring=True)
    assert all(n != "Common_UpdateMerchant" for n, _ in fake_client.calls)


_READ_OPS = {"Common_GetMerchantForEdit", "Common_SearchMerchantsByName"}


# ---- provenance ------------------------------------------------------------

@pytest.mark.parametrize("op_name", sorted(wc.PROVENANCE_ENTRIES))
def test_vendored_sha256_matches(op_name):
    text = operations.load(op_name)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == \
        wc.PROVENANCE_ENTRIES[op_name]["vendored_sha256"]
    assert "__recon_redacted__" not in text
    if op_name in _READ_OPS:
        assert not operations.is_mutation(op_name)
        assert wc.PROVENANCE_ENTRIES[op_name]["walk_reachable"] is False
        assert "HAND-WRITTEN" in wc.PROVENANCE_ENTRIES[op_name]["note"]
        raw = (resources.files(operations.__package__) / f"{op_name}.graphql").read_text()
        assert "HAND-WRITTEN" in raw
    else:
        assert operations.is_mutation(op_name)


def test_provenance_entry_shape_matches_existing():
    ref = set(operations.PROVENANCE["Common_DeleteTransactionMutation"]) | {"walk_reachable"}
    for entry in wc.PROVENANCE_ENTRIES.values():
        assert set(entry) == ref
