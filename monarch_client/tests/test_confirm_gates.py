"""Confirmation gates on destructive / wide-blast-radius writes (review H6).

Every gate must: refuse before sending any mutation when `confirm` is missing,
wrong, or the target is unknown, and let the call through when `confirm` echoes
the target from a fresh read."""

from __future__ import annotations

import pytest

from monarch_client import operations, reads, writes, writes_accounts as wa
from monarch_client import writes_categories as wc
from monarch_client import writes_splits_rules as wsr


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
    "Common_GetJointPlanningData": {
        "budgetData": {"monthlyAmountsForFlexExpense": {"monthlyAmounts": [
            {"month": "2026-10-01", "plannedCashFlowAmount": 250}]}},
        "savingsGoalMonthlyBudgetAmounts": [
            {"savingsGoal": {"id": "goal1", "name": "Trip"}}]},
    "Web_DeleteCategory": {"deleteCategory": {"deleted": True}},
    "Web_GetTransactionRules": {"transactionRules": [{
        "id": "r1", "merchantNameCriteria": [{"operator": "contains", "value": "acme"}],
        "setCategoryAction": {"id": "c1", "name": "Groceries"}}]},
    "Common_GetAggregatedRecurringItems": {"aggregatedRecurringItems": {
        "groups": [{"results": [{"stream": {
            "id": "s1", "name": "Netflix Sub", "merchant": {"name": "Netflix"}}}]}]}},
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
        lambda c: writes.delete_transaction("t1", c), "Coffee Shop -5.00",
        lambda: writes.delete_transaction("nope", "Coffee Shop -5.00"),
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
    "delete_category": (
        lambda c: wc.delete_category("c1", c, uncategorize_transactions=True), "Groceries",
        lambda: wc.delete_category("nope", "Groceries", uncategorize_transactions=True), None,
    ),
    "delete_transaction_rule": (
        lambda c: writes.delete_transaction_rule("r1", c), "acme",
        lambda: writes.delete_transaction_rule("nope", "acme"), None,
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
@pytest.mark.parametrize("bad", [None, "", "wrong name", "coffee shop", "vacation",
                                 "r1", "Coffee Shop", "Coffee Shop -5", "Coffee Shop 5.00"])
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
async def test_delete_transaction_without_merchant_uses_amount_alone(fake):
    fake.responses["Web_GetTransactionDrawer"] = {
        "getTransaction": {"id": "t9", "amount": -12.3, "merchant": None}}
    for bad in ("t9", "anything", "12.30", "-12.3"):
        with pytest.raises(ValueError, match="confirm"):
            await writes.delete_transaction("t9", bad)
    await writes.delete_transaction("t9", "-12.30")
    assert len(fake.mutations()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("amount,token", [
    (-12.34, "Amazon -12.34"), (12.34, "Amazon 12.34"), (-5, "Amazon -5.00"),
    (0, "Amazon 0.00"), (-0.0, "Amazon 0.00"), (1234.5, "Amazon 1234.50"),
])
async def test_delete_transaction_confirm_format(fake, amount, token):
    fake.responses["Web_GetTransactionDrawer"] = {"getTransaction": {
        "id": "t2", "amount": amount, "merchant": {"name": "Amazon"}}}
    with pytest.raises(ValueError, match="confirm"):
        await writes.delete_transaction("t2", "Amazon")
    await writes.delete_transaction("t2", token)
    assert len(fake.mutations()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [None, "abc", float("nan"), True])
async def test_delete_transaction_refused_without_usable_amount(fake, amount):
    fake.responses["Web_GetTransactionDrawer"] = {"getTransaction": {
        "id": "t2", "amount": amount, "merchant": {"name": "Amazon"}}}
    with pytest.raises(ValueError):
        await writes.delete_transaction("t2", "Amazon")
    assert fake.mutations() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rule,token", [
    ({"id": "r2", "merchantNameCriteria": [{"value": "acme"}],
      "originalStatementCriteria": [{"value": "ACME CO"}]}, "acme"),
    ({"id": "r2", "originalStatementCriteria": [{"value": "ACME CO"}]}, "ACME CO"),
    ({"id": "r2", "merchantCriteria": [{"value": "legacy"}]}, "legacy"),
    # merchantCriteria's real shape is uncaptured: a single object must work too
    ({"id": "r2", "merchantCriteria": {"operator": "contains", "value": "legacy"}}, "legacy"),
    ({"id": "r2", "merchantCriteria": {"operator": "contains"},
      "setCategoryAction": {"name": "Dining"}}, "Dining"),
    ({"id": "r2", "merchantCriteria": "weird"}, "rule r2"),
    ({"id": "r2", "amountCriteria": {"operator": "gt"},
      "setCategoryAction": {"name": "Dining"}}, "Dining"),
    ({"id": "r2", "amountCriteria": {"operator": "gt"}}, "rule r2"),
])
async def test_rule_confirm_token_derivation(fake, rule, token):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [rule]}
    with pytest.raises(ValueError, match="confirm"):
        await writes.delete_transaction_rule("r2", "r2" if token != "rule r2" else "r2 ")
    await writes.delete_transaction_rule("r2", token)
    assert len(fake.mutations()) == 1


@pytest.mark.asyncio
async def test_delete_category_confirm_precedes_move_target(fake):
    with pytest.raises(ValueError, match="confirm"):
        await wc.delete_category("c1", "wrong", move_to_category_id="c2")
    assert fake.mutations() == []


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
async def test_flex_budget_apply_to_future_requires_current_amount(fake):
    with pytest.raises(ValueError, match="confirm"):
        await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True)
    with pytest.raises(ValueError, match="confirm"):
        await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True, confirm="2026-11-01")
    assert fake.mutations() == []
    with pytest.raises(ValueError, match="confirm"):  # the month is no longer accepted
        await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True, confirm="2026-10-01")
    with pytest.raises(ValueError, match="confirm"):  # unformatted
        await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True, confirm="250")
    assert fake.mutations() == []
    await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True, confirm="250.00")
    assert len(fake.mutations()) == 1
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


@pytest.mark.asyncio
async def test_flex_apply_to_future_refused_when_month_missing(fake):
    fake.responses["Common_GetJointPlanningData"] = {
        "budgetData": {"monthlyAmountsForFlexExpense": {"monthlyAmounts": []}}}
    with pytest.raises(ValueError, match="wasn't found"):
        await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True, confirm="0.00")
    assert fake.mutations() == []


def _flex(amount, **extra):
    entry = {"month": "2026-10-01", **extra}
    if amount is not _MISSING:
        entry["plannedCashFlowAmount"] = amount
    return {"budgetData": {"monthlyAmountsForFlexExpense": {"monthlyAmounts": [entry]}}}


_MISSING = object()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, _MISSING, "abc", float("nan"), float("inf"), True, [1]])
async def test_flex_apply_to_future_refused_on_unusable_amount(fake, bad):
    fake.responses["Common_GetJointPlanningData"] = _flex(bad)
    for confirm in ("0.00", "NaN", "Infinity", "1.00", "None"):
        with pytest.raises(ValueError, match="NOT proceeding"):
            await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True, confirm=confirm)
    assert fake.mutations() == []


@pytest.mark.parametrize("raw,token", [
    (0, "0.00"), (0.0, "0.00"), (-0.0, "0.00"), ("-0.00", "0.00"), (250, "250.00"),
    (250.5, "250.50"), ("12.345", "12.34"), (0.125, "0.12"), (-3.5, "-3.50"),
])
def test_flex_amount_token_formats(raw, token):
    assert wa._flex_amount_token(_flex(raw), "2026-10-01") == token


@pytest.mark.asyncio
async def test_flex_apply_to_future_real_zero_is_zero_token(fake):
    fake.responses["Common_GetJointPlanningData"] = _flex(0)
    await wa.set_flex_budget_amount(5, "2026-10-01", apply_to_future=True, confirm="0.00")
    assert len(fake.mutations()) == 1


# ---- update_transaction_rule(apply_to_existing_transactions=True) ----------

def _upd_rule():
    return {
        "id": "r1", "merchantCriteriaUseOriginalStatement": False,
        "merchantNameCriteria": [{"operator": "contains", "value": "acme"}],
        "originalStatementCriteria": None, "amountCriteria": None,
        "categoryIds": None, "accountIds": ["a1"],
        "setCategoryAction": {"id": "c1", "name": "Groceries"},
    }


@pytest.fixture
def upd(fake):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_upd_rule()]}
    fake.responses["Common_UpdateTransactionRuleMutationV2"] = {
        "updateTransactionRuleV2": {"errors": None}}
    return fake


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, "", "yes", "8", 7])
async def test_update_rule_apply_to_existing_requires_matching_count(upd, bad):
    with pytest.raises(ValueError, match="confirm"):
        await wsr.update_transaction_rule(
            "r1", add_tag_ids=["t"], apply_to_existing_transactions=True, confirm=bad)
    assert upd.mutations() == []


@pytest.mark.asyncio
async def test_update_rule_apply_to_existing_with_right_count_previews_merged_criteria(upd):
    await wsr.update_transaction_rule(
        "r1", merchant_name_criteria=[{"operator": "eq", "value": "zed"}],
        apply_to_existing_transactions=True, confirm="7")
    ops = [c[0] for c in upd.calls]
    assert ops == ["Web_GetTransactionRules", "Common_PreviewTransactionRule",
                   "Common_UpdateTransactionRuleMutationV2"]
    prev = upd.calls[1][1]["rule"]
    assert prev["merchantNameCriteria"] == [{"operator": "eq", "value": "zed"}]
    assert prev["accountIds"] == ["a1"]
    assert prev["applyToExistingTransactions"] is False
    assert prev["setCategoryAction"] is None and "originalStatementCriteria" not in prev
    assert upd.calls[2][1]["input"]["applyToExistingTransactions"] is True


@pytest.mark.asyncio
async def test_update_rule_apply_to_existing_refused_without_preview_count(upd):
    upd.responses["Common_PreviewTransactionRule"] = {"transactionRulePreview": None}
    with pytest.raises(ValueError, match="totalCount"):
        await wsr.update_transaction_rule(
            "r1", add_tag_ids=["t"], apply_to_existing_transactions=True, confirm="0")
    assert upd.mutations() == []


@pytest.mark.asyncio
async def test_update_rule_forward_only_needs_no_confirm_and_no_preview(upd):
    await wsr.update_transaction_rule("r1", add_tag_ids=["t"])
    assert "Common_PreviewTransactionRule" not in [c[0] for c in upd.calls]
    assert len(upd.mutations()) == 1
