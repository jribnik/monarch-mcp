"""Confirmation gates on destructive / wide-blast-radius writes (review H6).

Every gate must: refuse before sending any mutation when `confirm` is missing,
wrong, or the target is unknown, and let the call through when `confirm` echoes
the target from a fresh read."""

from __future__ import annotations

import pytest

from monarch_client import operations, reads, writes, writes_accounts as wa
from monarch_client import writes_categories as wc


class _Fake:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call(self, op_name, variables):
        self.calls.append((op_name, variables))
        return self.responses[op_name]

    def mutations(self):
        return [c for c in self.calls if operations.is_mutation(c[0])]


RESPONSES = {
    # fresh reads
    "Web_GetTransactionDrawer": {"getTransaction": {
        "id": "t1", "amount": -5, "date": "2026-09-01",
        "merchant": {"id": "m", "name": "Coffee Shop"}}},
    "Common_GetHouseholdTransactionTags": {
        "householdTransactionTags": [{"id": "tag1", "name": "Vacation"}]},
    "Common_GetCategories": {
        "categoryGroups": [{"id": "g1", "name": "Group One"}],
        "categories": [{"id": "c1", "name": "Groceries"}]},
    "Web_GetTransactionRules": {"transactionRules": [{"id": "r1"}]},
    "Common_GetAggregatedRecurringItems": {"aggregatedRecurringItems": {
        "groups": [{"results": [{"stream": {
            "id": "s1", "name": "Netflix Sub", "merchant": {"name": "Netflix"}}}]}]}},
    "Common_GetJointPlanningData": {"savingsGoalMonthlyBudgetAmounts": [
        {"savingsGoal": {"id": "goal1", "name": "Trip"}}]},
    "Common_PreviewTransactionRule": {"transactionRulePreview": {"totalCount": 7, "results": []}},
    # mutations
    "Common_DeleteTransactionMutation": {"deleteTransaction": {"deleted": True}},
    "Common_DeleteHouseholdTransactionTag": {"deleteTransactionTag": {"errors": None}},
    "Common_DeleteCategoryGroup": {"deleteCategoryGroup": {"deleted": True}},
    "Common_DeleteTransactionRule": {"deleteTransactionRule": {"deleted": False}},
    "Common_MarkAsNotRecurring": {"markStreamAsNotRecurring": {"success": True}},
    "Common_DeleteSavingsGoal": {"deleteSavingsGoal": {"success": True}},
    "Common_UpdateBudgetItem": {"updateOrCreateBudgetItem": {"budgetItem": {}}},
    "Common_UpdateFlexBudgetMutation": {"updateOrCreateFlexBudgetItem": {}},
    "Common_SetSavingsGoalBudgetAmount": {"setSavingsGoalBudgetAmount": {"success": True}},
    "Common_CreateTransactionRuleMutationV2": {"createTransactionRuleV2": {"errors": None}},
}


@pytest.fixture
def fake(monkeypatch):
    c = _Fake(dict(RESPONSES))
    monkeypatch.setenv(writes.WRITES_ENV, "1")
    monkeypatch.setattr(writes, "_client", c)
    monkeypatch.setattr(reads, "_client", c)
    return c


# (id, callable taking confirm, the right confirm, a call with an unknown id)
DESTRUCTIVE = {
    "delete_transaction": (
        lambda c: writes.delete_transaction("t1", c), "Coffee Shop",
        lambda: writes.delete_transaction("nope", "Coffee Shop"),
        lambda f: f.responses.update({"Web_GetTransactionDrawer": {"getTransaction": None}}),
    ),
    "delete_tag": (
        lambda c: writes.delete_tag("tag1", c), "Vacation",
        lambda: writes.delete_tag("nope", "Vacation"), None,
    ),
    "delete_category_group": (
        lambda c: wc.delete_category_group("g1", c), "Group One",
        lambda: wc.delete_category_group("nope", "Group One"), None,
    ),
    "delete_transaction_rule": (
        lambda c: writes.delete_transaction_rule("r1", c), "r1",
        lambda: writes.delete_transaction_rule("nope", "nope"), None,
    ),
    "mark_stream_as_not_recurring": (
        lambda c: writes.mark_stream_as_not_recurring("s1", c), "Netflix",
        lambda: writes.mark_stream_as_not_recurring("nope", "Netflix"), None,
    ),
    "delete_savings_goal": (
        lambda c: wa.delete_savings_goal("goal1", c), "Trip",
        lambda: wa.delete_savings_goal("nope", "Trip"), None,
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(DESTRUCTIVE))
@pytest.mark.parametrize("bad", [None, "", "wrong name", "coffee shop", "vacation"])
async def test_wrong_or_missing_confirm_refused_before_any_mutation(fake, name, bad):
    call, good, _, _ = DESTRUCTIVE[name]
    if bad == good:
        pytest.skip("not a wrong value for this tool")
    with pytest.raises(ValueError, match="confirm"):
        await call(bad)
    assert fake.mutations() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(DESTRUCTIVE))
async def test_right_confirm_sends_the_mutation(fake, name):
    call, good, _, _ = DESTRUCTIVE[name]
    await call(good)
    assert len(fake.mutations()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(DESTRUCTIVE))
async def test_unknown_target_refused(fake, name):
    _, _, unknown_call, prep = DESTRUCTIVE[name]
    if prep:
        prep(fake)
    with pytest.raises(ValueError, match="wasn't found"):
        await unknown_call()
    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_delete_transaction_without_merchant_falls_back_to_id(fake):
    fake.responses["Web_GetTransactionDrawer"] = {"getTransaction": {"id": "t9", "merchant": None}}
    with pytest.raises(ValueError, match="confirm"):
        await writes.delete_transaction("t9", "anything")
    await writes.delete_transaction("t9", "t9")
    assert len(fake.mutations()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("good", ["Netflix", "Netflix Sub"])
async def test_stream_confirm_accepts_merchant_or_stream_name(fake, good):
    await writes.mark_stream_as_not_recurring("s1", good)
    assert len(fake.mutations()) == 1


@pytest.mark.asyncio
async def test_delete_tag_confirm_is_case_sensitive(fake):
    with pytest.raises(ValueError):
        await writes.delete_tag("tag1", "vacation")


@pytest.mark.asyncio
async def test_delete_category_group_confirm_precedes_move_target(fake):
    with pytest.raises(ValueError):
        await wc.delete_category_group("g1", "wrong", move_to_group_id="g2")
    assert fake.mutations() == []


# ---- apply_to_future / apply_to_existing -----------------------------------

@pytest.mark.asyncio
async def test_set_budget_amount_single_month_needs_no_confirm(fake):
    await wa.set_budget_amount("c1", 5, "2026-10-01")
    assert len(fake.mutations()) == 1
    assert fake.calls[0][0] == "Common_UpdateBudgetItem"  # no pre-read either


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, "", "groceries", "Dining"])
async def test_set_budget_amount_apply_to_future_requires_category_name(fake, bad):
    with pytest.raises(ValueError, match="confirm"):
        await wa.set_budget_amount("c1", 5, "2026-10-01", apply_to_future=True, confirm=bad)
    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_set_budget_amount_apply_to_future_ok_and_unknown_category(fake):
    await wa.set_budget_amount("c1", 5, "2026-10-01", apply_to_future=True, confirm="Groceries")
    assert fake.mutations()[0][1]["input"]["applyToFuture"] is True
    with pytest.raises(ValueError, match="wasn't found"):
        await wa.set_budget_amount("nope", 5, "2026-10-01", apply_to_future=True, confirm="Groceries")
    assert len(fake.mutations()) == 1


@pytest.mark.asyncio
async def test_flex_budget_apply_to_future_requires_month(fake):
    with pytest.raises(ValueError, match="confirm"):
        await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True)
    with pytest.raises(ValueError, match="confirm"):
        await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True, confirm="2026-11-01")
    assert fake.mutations() == []
    await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True, confirm="2026-10-01")
    await wa.set_flex_budget_amount(5, "2026-10-01")  # single month: no confirm
    assert len(fake.mutations()) == 2


@pytest.mark.asyncio
async def test_goal_budget_apply_to_future_requires_goal_name(fake):
    with pytest.raises(ValueError, match="confirm"):
        await wa.set_savings_goal_budget_amount("goal1", 5, "2026-10-01", apply_to_future=True)
    assert fake.mutations() == []
    await wa.set_savings_goal_budget_amount("goal1", 5, "2026-10-01", apply_to_future=True, confirm="Trip")
    await wa.set_savings_goal_budget_amount("goal1", 5, "2026-10-01")
    assert len(fake.mutations()) == 2


RULE = dict(original_statement_criteria=[{"operator": "contains", "value": "AMZN"}],
            set_category_action="c1")


@pytest.mark.asyncio
async def test_create_rule_forward_only_needs_no_confirm_or_preview(fake):
    await writes.create_transaction_rule(**RULE)
    assert [c[0] for c in fake.calls] == ["Common_CreateTransactionRuleMutationV2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, "", "yes", "8", 7])
async def test_create_rule_apply_to_existing_requires_matching_count(fake, bad):
    with pytest.raises(ValueError, match="confirm"):
        await writes.create_transaction_rule(**RULE, apply_to_existing_transactions=True, confirm=bad)
    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_create_rule_apply_to_existing_with_right_count(fake):
    await writes.create_transaction_rule(**RULE, apply_to_existing_transactions=True, confirm="7")
    ops = [c[0] for c in fake.calls]
    assert ops == ["Common_PreviewTransactionRule", "Common_CreateTransactionRuleMutationV2"]
    assert fake.calls[0][1]["rule"]["applyToExistingTransactions"] is False
    assert fake.calls[1][1]["input"]["applyToExistingTransactions"] is True


@pytest.mark.asyncio
async def test_create_rule_apply_to_existing_refused_without_preview_count(fake):
    fake.responses["Common_PreviewTransactionRule"] = {"transactionRulePreview": None}
    with pytest.raises(ValueError, match="totalCount"):
        await writes.create_transaction_rule(**RULE, apply_to_existing_transactions=True, confirm="0")
    assert fake.mutations() == []
