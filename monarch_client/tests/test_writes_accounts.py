"""Hermetic tests for writes_accounts.py (mocked client, no network)."""

from __future__ import annotations

import hashlib
import inspect

import pytest

from monarch_client import errors, operations, reads, writes, writes_accounts as wa

ACCOUNT = {
    "id": "a1", "displayName": "zz-acct", "notes": "n", "deactivatedAt": None,
    "displayBalance": 10.0, "includeInNetWorth": True, "hideFromList": False,
    "hideInBudget": True, "hideTransactionsFromReports": False, "dataProvider": "",
    "dataProviderAccountId": None, "isManual": True, "invertSyncedBalance": False,
    "useAvailableBalance": None, "limit": None, "apr": None, "interestRate": 1.5,
    "interestRateType": None, "minimumPayment": None, "plannedPayment": None,
    "excludeFromDebtPaydown": False, "type": {"name": "depository"},
    "subtype": {"name": "checking"}, "credential": None, "ownedByUser": None,
    "businessEntity": None,
}


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call(self, op_name, variables):
        self.calls.append((op_name, variables))
        return self.responses[op_name]


@pytest.fixture
def fake(monkeypatch):
    c = _FakeClient({"Common_GetAccountForEdit": {"account": dict(ACCOUNT)}})
    monkeypatch.setenv(writes.WRITES_ENV, "1")
    monkeypatch.setattr(writes, "_client", c)
    monkeypatch.setattr(reads, "_client", c)
    return c


def _mutations(c):
    return [x for x in c.calls if x[0] != "Common_GetAccountForEdit"]


CALLS = {
    "update_account": lambda: wa.update_account("a1", name="x"),
    "delete_account": lambda: wa.delete_account("a1", "zz-acct"),
    "set_budget_amount": lambda: wa.set_budget_amount("c1", 5, "2026-10-01"),
    "set_flex_budget_amount": lambda: wa.set_flex_budget_amount(5, "2026-10-01"),
    "create_savings_goal": lambda: wa.create_savings_goal("g"),
    "update_savings_goal": lambda: wa.update_savings_goal("g1", name="x"),
    "set_savings_goal_budget_amount": lambda: wa.set_savings_goal_budget_amount("g1", 5, "2026-10-01"),
    "delete_savings_goal": lambda: wa.delete_savings_goal("g1"),
}


def test_tools_list_matches_module():
    public = {n for n, f in inspect.getmembers(wa, inspect.iscoroutinefunction) if not n.startswith("_")}
    assert set(wa.TOOLS) == public == set(CALLS)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(CALLS))
async def test_gate_closed_refuses(name, monkeypatch):
    c = _FakeClient({})
    monkeypatch.delenv(writes.WRITES_ENV, raising=False)
    monkeypatch.setattr(writes, "_client", c)
    monkeypatch.setattr(reads, "_client", c)
    with pytest.raises(errors.MonarchWriteBlocked):
        await CALLS[name]()
    assert c.calls == []


@pytest.mark.asyncio
async def test_update_account_sends_full_input_with_only_changes(fake):
    fake.responses["Common_UpdateAccount"] = {"updateAccount": {"account": {"id": "a1"}, "errors": None}}
    r = await wa.update_account("a1", name="new", hide_from_list=True, include_in_net_worth=False)
    (op, v), = _mutations(fake)
    i = v["input"]
    assert op == "Common_UpdateAccount"
    assert i["name"] == "new" and i["hideFromList"] is True and i["includeInNetWorth"] is False
    assert i["displayBalance"] == 10.0 and i["notes"] == "n" and i["hideInBudget"] is True
    assert i["interestRate"] == 1.5 and i["type"] == "depository" and i["subtype"] == "checking"
    assert i["synced"] is False and i["recurrence"] == {} and i["id"] == "a1"
    assert r["updateAccount"]["account"]["id"] == "a1"


@pytest.mark.asyncio
async def test_update_account_type_pair_and_empty_notes(fake):
    fake.responses["Common_UpdateAccount"] = {"updateAccount": {"errors": None}}
    await wa.update_account("a1", account_type="depository", account_subtype="savings", notes="")
    i = _mutations(fake)[0][1]["input"]
    assert i["subtype"] == "savings" and i["notes"] == ""


@pytest.mark.asyncio
async def test_update_account_validation(fake):
    with pytest.raises(ValueError, match="nothing to change"):
        await wa.update_account("a1")
    with pytest.raises(ValueError, match="together"):
        await wa.update_account("a1", account_type="depository")
    assert _mutations(fake) == []


@pytest.mark.asyncio
async def test_update_account_refuses_linked_and_missing(fake):
    fake.responses["Common_GetAccountForEdit"] = {"account": {**ACCOUNT, "credential": {"id": "c"}}}
    with pytest.raises(ValueError, match="bank-linked"):
        await wa.update_account("a1", name="x")
    fake.responses["Common_GetAccountForEdit"] = {"account": None}
    with pytest.raises(ValueError, match="wasn't found"):
        await wa.update_account("a1", name="x")
    assert _mutations(fake) == []


@pytest.mark.asyncio
async def test_update_account_surfaces_errors(fake):
    fake.responses["Common_UpdateAccount"] = {"updateAccount": {"account": None, "errors": {"message": "bad"}}}
    r = await wa.update_account("a1", name="x")
    assert r["updateAccount"]["errors"] == {"message": "bad"}


@pytest.mark.asyncio
async def test_delete_account_wrong_name_refused(fake):
    with pytest.raises(ValueError, match="does not exactly match"):
        await wa.delete_account("a1", "ZZ-ACCT")
    assert _mutations(fake) == []


@pytest.mark.asyncio
async def test_delete_account_linked_refused(fake):
    fake.responses["Common_GetAccountForEdit"] = {"account": {**ACCOUNT, "credential": {"id": "c"}}}
    with pytest.raises(ValueError, match="bank-linked"):
        await wa.delete_account("a1", "zz-acct")
    assert _mutations(fake) == []


@pytest.mark.asyncio
async def test_delete_account_ok_and_error(fake):
    fake.responses["Common_DeleteAccount"] = {"deleteAccount": {"deleted": True, "errors": None}}
    assert await wa.delete_account("a1", "zz-acct") == {"deleted_flag": True, "errors": None}
    assert _mutations(fake) == [("Common_DeleteAccount", {"id": "a1"})]
    fake.responses["Common_DeleteAccount"] = {"deleteAccount": {"deleted": False, "errors": {"message": "no"}}}
    r = await wa.delete_account("a1", "zz-acct")
    assert r["deleted_flag"] is False and r["errors"] == {"message": "no"}


@pytest.mark.asyncio
async def test_set_budget_amount_defaults_no_future(fake):
    fake.responses["Common_UpdateBudgetItem"] = {"updateOrCreateBudgetItem": {"budgetItem": {"id": "b"}}}
    await wa.set_budget_amount("c1", 25, "2026-10-01")
    assert fake.calls == [("Common_UpdateBudgetItem", {"input": {
        "startDate": "2026-10-01", "timeframe": "month", "amount": 25,
        "applyToFuture": False, "categoryId": "c1"}})]


@pytest.mark.asyncio
@pytest.mark.parametrize("month", ["2026-10-15", "2026-13-01", "Oct 2026", ""])
async def test_bad_month_rejected(fake, month):
    with pytest.raises(ValueError, match="YYYY-MM-01"):
        await wa.set_budget_amount("c1", 1, month)
    with pytest.raises(ValueError, match="YYYY-MM-01"):
        await wa.set_flex_budget_amount(1, month)
    with pytest.raises(ValueError, match="YYYY-MM-01"):
        await wa.set_savings_goal_budget_amount("g", 1, month)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_negative_amount_rejected(fake):
    with pytest.raises(ValueError, match="non-negative"):
        await wa.set_budget_amount("c1", -1, "2026-10-01")


@pytest.mark.asyncio
async def test_set_flex_budget_amount(fake):
    fake.responses["Common_UpdateFlexBudgetMutation"] = {"updateOrCreateFlexBudgetItem": {"budgetItem": {"budgetAmount": 9}}}
    r = await wa.set_flex_budget_amount(9, "2026-10-01", apply_to_future=True)
    assert fake.calls == [("Common_UpdateFlexBudgetMutation", {"input": {
        "startDate": "2026-10-01", "amount": 9, "applyToFuture": True}})]
    assert r["updateOrCreateFlexBudgetItem"]["budgetItem"]["budgetAmount"] == 9


@pytest.mark.asyncio
async def test_create_savings_goal_with_followup(fake):
    fake.responses["Common_CreateSavingsGoals"] = {"createSavingsGoals": {"savingsGoals": [{"id": "g1", "type": "savings"}]}}
    fake.responses["Common_UpdateSavingsGoal"] = {"updateSavingsGoal": {"savingsGoal": {"id": "g1"}, "errors": None}}
    r = await wa.create_savings_goal("Trip", target_amount=100)
    assert fake.calls[0][1]["input"]["goals"][0]["name"] == "Trip"
    assert fake.calls[0][1]["input"]["goals"][0]["type"] == "savings"
    assert fake.calls[1] == ("Common_UpdateSavingsGoal", {"input": {"id": "g1", "targetAmount": 100}})
    assert r["createSavingsGoals"]["savingsGoals"][0]["id"] == "g1" and "followUpUpdate" in r


@pytest.mark.asyncio
async def test_create_savings_goal_name_only_and_validation(fake):
    fake.responses["Common_CreateSavingsGoals"] = {"createSavingsGoals": {"savingsGoals": [{"id": "g1"}]}}
    await wa.create_savings_goal("Trip")
    assert len(fake.calls) == 1
    with pytest.raises(ValueError):
        await wa.create_savings_goal("  ")
    with pytest.raises(ValueError):
        await wa.create_savings_goal("x", target_date="06/2027")


@pytest.mark.asyncio
async def test_update_savings_goal_sends_only_given(fake):
    fake.responses["Common_UpdateSavingsGoal"] = {"updateSavingsGoal": {"errors": {"message": "x"}}}
    r = await wa.update_savings_goal("g1", target_date="2027-06-30", is_sinking_fund=True)
    assert fake.calls == [("Common_UpdateSavingsGoal", {"input": {"id": "g1", "targetDate": "2027-06-30", "isSinkingFund": True}})]
    assert r["updateSavingsGoal"]["errors"] == {"message": "x"}
    with pytest.raises(ValueError, match="nothing to change"):
        await wa.update_savings_goal("g1")


@pytest.mark.asyncio
async def test_set_savings_goal_budget_amount(fake):
    fake.responses["Common_SetSavingsGoalBudgetAmount"] = {"setSavingsGoalBudgetAmount": {"success": True, "errors": None}}
    await wa.set_savings_goal_budget_amount("g1", 5, "2026-10-01")
    assert fake.calls == [("Common_SetSavingsGoalBudgetAmount", {"input": {
        "month": "2026-10-01", "savingsGoalId": "g1", "amount": 5,
        "applyToFuture": False, "accountId": None}})]


@pytest.mark.asyncio
async def test_delete_savings_goal(fake):
    fake.responses["Common_DeleteSavingsGoal"] = {"deleteSavingsGoal": {"success": True, "errors": None}}
    assert await wa.delete_savings_goal("g1") == {"deleted_flag": True, "errors": None}
    assert fake.calls == [("Common_DeleteSavingsGoal", {"input": {"id": "g1"}})]
    fake.responses["Common_DeleteSavingsGoal"] = {"deleteSavingsGoal": {"success": False, "errors": {"message": "m"}}}
    assert (await wa.delete_savings_goal("g1"))["deleted_flag"] is False


def test_provenance_shape_and_sha():
    assert set(wa.PROVENANCE_ENTRIES) >= {
        "Common_UpdateAccount", "Common_DeleteAccount", "Common_UpdateBudgetItem",
        "Common_UpdateFlexBudgetMutation", "Common_CreateSavingsGoals",
        "Common_UpdateSavingsGoal", "Common_SetSavingsGoalBudgetAmount",
        "Common_DeleteSavingsGoal", "Common_GetAccountForEdit",
    }
    for op, p in wa.PROVENANCE_ENTRIES.items():
        assert set(p) == {"catalog_query_hash", "vendored_sha256", "exported_at", "runs_seen",
                          "hand_repaired", "walk_reachable", "note"}
        assert p["catalog_query_hash"] is None
        assert p["vendored_sha256"] == hashlib.sha256(operations.load(op).encode()).hexdigest()
        assert p["note"]
        assert operations.is_mutation(op) == (op != "Common_GetAccountForEdit")
