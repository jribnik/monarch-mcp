"""Hermetic tests for writes_splits_rules.py (mocked client, no network)."""

from __future__ import annotations

import hashlib

import pytest

from monarch_client import operations, reads, writes, writes_splits_rules as w
from monarch_client.errors import MonarchWriteBlocked


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call(self, op_name, variables):
        self.calls.append((op_name, variables))
        return self.responses[op_name]


@pytest.fixture
def fake(monkeypatch):
    client = _FakeClient({})
    monkeypatch.setenv(writes.WRITES_ENV, "1")
    monkeypatch.setattr(writes, "_client", client)
    monkeypatch.setattr(reads, "_client", client)
    return client


def _txn(amount):
    return {"getTransaction": {"id": "t1", "amount": amount}, "myHousehold": None}


SPLIT_OK = {"updateTransactionSplit": {"errors": None, "transaction": {"id": "t1"}}}


@pytest.mark.asyncio
async def test_gate_closed_refuses_before_any_call(monkeypatch):
    client = _FakeClient({})
    monkeypatch.delenv(writes.WRITES_ENV, raising=False)
    monkeypatch.setattr(writes, "_client", client)
    monkeypatch.setattr(reads, "_client", client)
    for coro in (
        w.split_transaction("t1", [{"amount": -1}, {"amount": -1}]),
        w.unsplit_transaction("t1"),
        w.update_transaction_rule("r1"),
    ):
        with pytest.raises(MonarchWriteBlocked):
            await coro
    assert client.calls == []


@pytest.mark.asyncio
async def test_split_sends_expected_variables(fake):
    fake.responses["Web_GetTransactionDrawer"] = _txn(-12.34)
    fake.responses["Common_SplitTransactionMutation"] = SPLIT_OK
    result = await w.split_transaction(
        "t1",
        [
            {"amount": -6},
            {"amount": "-6.34", "category_id": "c2", "merchant_name": "M", "hide_from_reports": True},
        ],
    )
    op, variables = fake.calls[-1]
    assert op == "Common_SplitTransactionMutation"
    assert variables == {
        "input": {
            "transactionId": "t1",
            "splitData": [
                {"amount": -6.0, "categoryId": None, "merchantName": None,
                 "hideFromReports": False, "ownerUserId": None,
                 "businessEntityId": None, "businessEntityIsUnassigned": False},
                {"amount": -6.34, "categoryId": "c2", "merchantName": "M",
                 "hideFromReports": True, "ownerUserId": None,
                 "businessEntityId": None, "businessEntityIsUnassigned": False},
            ],
        }
    }
    assert result["updateTransactionSplit"]["transaction"]["id"] == "t1"


@pytest.mark.asyncio
async def test_split_three_way_float_sum_is_exact(fake):
    # 0.1 + 0.2 style float noise must not cause a false refusal.
    fake.responses["Web_GetTransactionDrawer"] = _txn(-0.3)
    fake.responses["Common_SplitTransactionMutation"] = SPLIT_OK
    await w.split_transaction("t1", [{"amount": -0.1}, {"amount": -0.1}, {"amount": -0.1}])
    assert fake.calls[-1][0] == "Common_SplitTransactionMutation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "splits",
    [
        [{"amount": -6}, {"amount": -6}],        # sum -12 != -12.34
        [{"amount": 6}, {"amount": 6.34}],       # wrong sign
        [{"amount": -6}, {"amount": -6.341}],    # sub-cent
    ],
)
async def test_split_bad_sum_refused_without_mutation(fake, splits):
    fake.responses["Web_GetTransactionDrawer"] = _txn(-12.34)
    with pytest.raises(ValueError):
        await w.split_transaction("t1", splits)
    assert all(op != "Common_SplitTransactionMutation" for op, _ in fake.calls)


@pytest.mark.asyncio
async def test_split_needs_two_items_and_known_keys(fake):
    with pytest.raises(ValueError):
        await w.split_transaction("t1", [{"amount": -12.34}])
    fake.responses["Web_GetTransactionDrawer"] = _txn(-2)
    with pytest.raises(ValueError, match="unknown keys"):
        await w.split_transaction("t1", [{"amount": -1, "bogus": 1}, {"amount": -1}])


@pytest.mark.asyncio
async def test_split_unknown_transaction(fake):
    fake.responses["Web_GetTransactionDrawer"] = {"getTransaction": None}
    with pytest.raises(ValueError, match="wasn't found"):
        await w.split_transaction("nope", [{"amount": -1}, {"amount": -1}])


@pytest.mark.asyncio
async def test_split_surfaces_server_error_payload(fake):
    fake.responses["Web_GetTransactionDrawer"] = _txn(-2)
    fake.responses["Common_SplitTransactionMutation"] = {
        "updateTransactionSplit": {
            "errors": {"message": "Split quantities do not sum up to the original amount"},
            "transaction": None,
        }
    }
    result = await w.split_transaction("t1", [{"amount": -1}, {"amount": -1}])
    assert result["updateTransactionSplit"]["transaction"] is None
    assert "do not sum" in result["updateTransactionSplit"]["errors"]["message"]


@pytest.mark.asyncio
async def test_unsplit_sends_empty_split_data(fake):
    fake.responses["Common_SplitTransactionMutation"] = SPLIT_OK
    await w.unsplit_transaction("t1")
    assert fake.calls == [
        ("Common_SplitTransactionMutation", {"input": {"transactionId": "t1", "splitData": []}})
    ]


def _existing_rule(**over):
    rule = {
        "id": "r1",
        "order": 0,
        "merchantCriteriaUseOriginalStatement": False,
        "merchantCriteria": None,
        "originalStatementCriteria": None,
        "merchantNameCriteria": [{"operator": "contains", "value": "acme", "__typename": "MerchantCriterion"}],
        "amountCriteria": {"operator": "gt", "isExpense": True, "value": 5.0,
                           "valueRange": None, "__typename": "AmountCriteriaV2"},
        "categoryIds": None,
        "accountIds": ["a1"],
        "setMerchantAction": None,
        "setCategoryAction": {"id": "cat1", "name": "Groceries", "__typename": "Category"},
        "addTagsAction": [{"id": "tag1", "name": "Tax", "__typename": "TransactionTag"}],
        "linkGoalAction": None,
        "linkSavingsGoalAction": None,
        "needsReviewByUserAction": None,
        "setHideFromReportsAction": False,
        "reviewStatusAction": "needs_review",
        "splitTransactionsAction": None,
        # Fields update_transaction_rule cannot carry (default = unset).
        "criteriaOwnerIsJoint": False,
        "criteriaOwnerUserIds": None,
        "criteriaBusinessEntityIds": None,
        "criteriaBusinessEntityIsUnassigned": False,
        "sendNotificationAction": False,
        "actionSetOwner": None,
        "actionSetOwnerIsJoint": False,
        "actionSetBusinessEntity": None,
        "actionSetBusinessEntityIsUnassigned": False,
        "setLinkToPaydownBudgetAction": False,
        "unassignNeedsReviewByUserAction": False,
    }
    rule.update(over)
    return rule


UPDATE_OK = {"updateTransactionRuleV2": {"errors": None}}


@pytest.mark.asyncio
async def test_update_rule_noop_roundtrips_existing_fields(fake):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_existing_rule()]}
    fake.responses["Common_UpdateTransactionRuleMutationV2"] = UPDATE_OK
    await w.update_transaction_rule("r1", set_hide_from_reports=False)
    op, variables = fake.calls[-1]
    assert op == "Common_UpdateTransactionRuleMutationV2"
    inp = variables["input"]
    assert inp == {
        "id": "r1",
        "merchantCriteriaUseOriginalStatement": False,
        "merchantCriteria": None,
        "originalStatementCriteria": None,
        "merchantNameCriteria": [{"operator": "contains", "value": "acme"}],
        "amountCriteria": {"operator": "gt", "isExpense": True, "value": 5.0, "valueRange": None},
        "categoryIds": None,
        "accountIds": ["a1"],
        "setMerchantAction": None,
        "setCategoryAction": "cat1",
        "addTagsAction": ["tag1"],
        "linkGoalAction": None,
        "linkSavingsGoalAction": None,
        "needsReviewByUserAction": None,
        "setHideFromReportsAction": False,
        "reviewStatusAction": "needs_review",
        "splitTransactionsAction": None,
        "applyToExistingTransactions": False,
    }


@pytest.mark.asyncio
async def test_update_rule_merges_only_given_fields(fake):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_existing_rule()]}
    fake.responses["Common_UpdateTransactionRuleMutationV2"] = UPDATE_OK
    await w.update_transaction_rule(
        "r1",
        merchant_name_criteria=[{"operator": "eq", "value": "zed"}],
        add_tag_ids=["tag2"],
        set_hide_from_reports=True,
        apply_to_existing_transactions=True,
    )
    inp = fake.calls[-1][1]["input"]
    assert inp["merchantNameCriteria"] == [{"operator": "eq", "value": "zed"}]
    assert inp["addTagsAction"] == ["tag2"]
    assert inp["setHideFromReportsAction"] is True
    assert inp["applyToExistingTransactions"] is True
    # untouched
    assert inp["setCategoryAction"] == "cat1"
    assert inp["accountIds"] == ["a1"]
    assert inp["amountCriteria"]["value"] == 5.0
    assert inp["reviewStatusAction"] == "needs_review"


@pytest.mark.asyncio
async def test_update_rule_empty_values_clear_and_null_review_sent_as_empty_string(fake):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_existing_rule()]}
    fake.responses["Common_UpdateTransactionRuleMutationV2"] = UPDATE_OK
    await w.update_transaction_rule("r1", add_tag_ids=[], review_status="", account_ids=[])
    inp = fake.calls[-1][1]["input"]
    assert inp["addTagsAction"] is None
    assert inp["accountIds"] is None
    # The server rejects a null reviewStatusAction on update (verified live).
    assert inp["reviewStatusAction"] == ""


@pytest.mark.asyncio
async def test_update_rule_refuses_to_strip_all_actions_or_criteria(fake):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_existing_rule()]}
    with pytest.raises(ValueError, match="no actions"):
        await w.update_transaction_rule(
            "r1", set_category_action="", add_tag_ids=[], review_status=""
        )
    with pytest.raises(ValueError, match="no criteria"):
        await w.update_transaction_rule(
            "r1", merchant_name_criteria=[], amount_criteria={}, account_ids=[]
        )
    assert all(op != "Common_UpdateTransactionRuleMutationV2" for op, _ in fake.calls)


@pytest.mark.asyncio
async def test_update_rule_unknown_id(fake):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_existing_rule()]}
    with pytest.raises(ValueError, match="wasn't found"):
        await w.update_transaction_rule("missing", set_hide_from_reports=True)


@pytest.mark.asyncio
async def test_update_rule_merchant_name_gate(fake):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_existing_rule()]}
    fake.responses["Web_GetTransactionsList"] = {
        "allTransactions": {"totalCount": 0, "results": []}
    }
    with pytest.raises(ValueError, match="doesn't exactly match"):
        await w.update_transaction_rule("r1", set_merchant_name="Nope")
    assert all(op != "Common_UpdateTransactionRuleMutationV2" for op, _ in fake.calls)

    fake.responses["Web_GetTransactionsList"] = {
        "allTransactions": {"totalCount": 1, "results": [{"merchant": {"name": "Real"}}]}
    }
    fake.responses["Common_UpdateTransactionRuleMutationV2"] = UPDATE_OK
    await w.update_transaction_rule("r1", set_merchant_name="Real")
    assert fake.calls[-1][1]["input"]["setMerchantAction"] == "Real"


@pytest.mark.asyncio
async def test_update_rule_unchanged_merchant_skips_gate(fake):
    fake.responses["Web_GetTransactionRules"] = {
        "transactionRules": [_existing_rule(setMerchantAction={"id": "m", "name": "Real"})]
    }
    fake.responses["Common_UpdateTransactionRuleMutationV2"] = UPDATE_OK
    await w.update_transaction_rule("r1", set_merchant_name="Real")
    assert all(op != "Web_GetTransactionsList" for op, _ in fake.calls)


@pytest.mark.asyncio
async def test_update_rule_percentage_split_action(fake):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_existing_rule()]}
    fake.responses["Common_UpdateTransactionRuleMutationV2"] = UPDATE_OK
    await w.update_transaction_rule(
        "r1", split_action={"splits": [{"percent": 60, "category_id": "c"}, {"percent": 40}]}
    )
    action = fake.calls[-1][1]["input"]["splitTransactionsAction"]
    assert action["amountType"] == "PERCENTAGE"
    assert [s["amount"] for s in action["splitsInfo"]] == [0.6, 0.4]
    assert action["splitsInfo"][0]["categoryId"] == "c"
    assert set(action["splitsInfo"][0]) == set(w._SPLIT_INFO_FIELDS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        {"amount_type": "AMOUNT", "splits": [{"percent": 50}, {"percent": 50}]},
        {"splits": [{"percent": 60}, {"percent": 30}]},
        {"splits": [{"percent": 100}]},
    ],
)
async def test_update_rule_bad_split_action_refused(fake, action):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_existing_rule()]}
    with pytest.raises(ValueError):
        await w.update_transaction_rule("r1", split_action=action)
    assert all(op != "Common_UpdateTransactionRuleMutationV2" for op, _ in fake.calls)


@pytest.mark.asyncio
async def test_update_rule_empty_split_action_clears(fake):
    existing = _existing_rule(
        splitTransactionsAction={
            "amountType": "PERCENTAGE",
            "splitsInfo": [{"amount": 0.5, "__typename": "x"}, {"amount": 0.5}],
        }
    )
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [existing]}
    fake.responses["Common_UpdateTransactionRuleMutationV2"] = UPDATE_OK
    await w.update_transaction_rule("r1", set_hide_from_reports=False)
    kept = fake.calls[-1][1]["input"]["splitTransactionsAction"]
    assert kept["amountType"] == "PERCENTAGE" and "__typename" not in kept["splitsInfo"][0]
    await w.update_transaction_rule("r1", split_action={})
    assert fake.calls[-1][1]["input"]["splitTransactionsAction"] is None


def test_update_result_flags_empty_error_object():
    r = w.update_transaction_rule_result(
        {"updateTransactionRuleV2": {"errors": {"message": None, "code": None, "fieldErrors": None}}}
    )
    assert "REJECTED" in r["updateTransactionRuleV2"]["hint"]
    ok = w.update_transaction_rule_result({"updateTransactionRuleV2": {"errors": None}})
    assert ok == {"updateTransactionRuleV2": {"errors": None}}
    real = w.update_transaction_rule_result(
        {"updateTransactionRuleV2": {"errors": {"message": "boom"}}}
    )
    assert "hint" not in real["updateTransactionRuleV2"]


def test_provenance_entries_match_vendored_files():
    assert set(w.PROVENANCE_ENTRIES) == {
        "Common_SplitTransactionMutation",
        "Common_UpdateTransactionRuleMutationV2",
    }
    for name, entry in w.PROVENANCE_ENTRIES.items():
        text = operations.load(name)
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == entry["vendored_sha256"]
        assert entry["catalog_query_hash"] is None
        assert entry["walk_reachable"] is False and entry["hand_repaired"] is False
        assert entry["runs_seen"] == []
        assert operations.is_mutation(name)
    assert w.TOOLS == ["split_transaction", "unsplit_transaction", "update_transaction_rule"]


# ----------------------------------------------------- review-fix additions

_NON_DEFAULT = {
    "criteriaOwnerIsJoint": True,
    "criteriaOwnerUserIds": ["u1"],
    "criteriaBusinessEntityIds": ["b1"],
    "criteriaBusinessEntityIsUnassigned": True,
    "sendNotificationAction": True,
    "actionSetOwner": {"id": "u1", "displayName": "X"},
    "actionSetOwnerIsJoint": True,
    "actionSetBusinessEntity": {"id": "b1", "name": "Biz"},
    "actionSetBusinessEntityIsUnassigned": True,
    "setLinkToPaydownBudgetAction": True,
    "unassignNeedsReviewByUserAction": True,
}


def test_uncarried_field_list_is_exactly_the_tested_set():
    assert set(w.UNCARRIED_RULE_FIELDS) == set(_NON_DEFAULT)


def test_uncarried_fields_exist_in_the_rules_query():
    text = operations.load("Web_GetTransactionRules")
    for name in w.UNCARRIED_RULE_FIELDS:
        assert name in text, name


@pytest.mark.asyncio
@pytest.mark.parametrize("field", sorted(_NON_DEFAULT))
async def test_update_rule_refuses_rule_using_uncarried_field(fake, field):
    fake.responses["Web_GetTransactionRules"] = {
        "transactionRules": [_existing_rule(**{field: _NON_DEFAULT[field]})]
    }
    with pytest.raises(ValueError, match=field):
        await w.update_transaction_rule(
            "r1", add_tag_ids=["t2"], apply_to_existing_transactions=True
        )
    assert all(op != "Common_UpdateTransactionRuleMutationV2" for op, _ in fake.calls)


@pytest.mark.asyncio
async def test_update_rule_default_uncarried_fields_allowed(fake):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_existing_rule()]}
    fake.responses["Common_UpdateTransactionRuleMutationV2"] = UPDATE_OK
    await w.update_transaction_rule("r1", add_tag_ids=["t2"])
    assert fake.calls[-1][0] == "Common_UpdateTransactionRuleMutationV2"


@pytest.mark.asyncio
async def test_update_rule_noop_refused(fake):
    with pytest.raises(ValueError, match="no fields to update"):
        await w.update_transaction_rule("r1")
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["needs_review", "reviewed", ""])
async def test_update_rule_review_status_allowed(fake, status):
    fake.responses["Web_GetTransactionRules"] = {"transactionRules": [_existing_rule()]}
    fake.responses["Common_UpdateTransactionRuleMutationV2"] = UPDATE_OK
    await w.update_transaction_rule("r1", review_status=status)
    assert fake.calls[-1][1]["input"]["reviewStatusAction"] == (status or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["bogus", "Reviewed", "NEEDS_REVIEW", " "])
async def test_update_rule_review_status_rejected(fake, status):
    with pytest.raises(ValueError, match="review_status"):
        await w.update_transaction_rule("r1", review_status=status)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_split_zero_amount_refused(fake):
    fake.responses["Web_GetTransactionDrawer"] = _txn(-5)
    with pytest.raises(ValueError, match="zero"):
        await w.split_transaction("t1", [{"amount": 0}, {"amount": -5}])
    assert fake.calls == []


@pytest.mark.asyncio
async def test_split_mixed_sign_refused(fake):
    # sums correctly (-8 + -4 + 7 = -5) but one split has the wrong sign
    fake.responses["Web_GetTransactionDrawer"] = _txn(-5)
    with pytest.raises(ValueError, match="same sign"):
        await w.split_transaction(
            "t1", [{"amount": -8}, {"amount": -4}, {"amount": 7}]
        )
    assert all(op != "Common_SplitTransactionMutation" for op, _ in fake.calls)


@pytest.mark.asyncio
async def test_split_hide_from_reports_defaults_to_parent(fake):
    fake.responses["Web_GetTransactionDrawer"] = {
        "getTransaction": {"id": "t1", "amount": -10, "hideFromReports": True}
    }
    fake.responses["Common_SplitTransactionMutation"] = SPLIT_OK
    await w.split_transaction(
        "t1", [{"amount": -6}, {"amount": -4, "hide_from_reports": False}]
    )
    items = fake.calls[-1][1]["input"]["splitData"]
    assert [i["hideFromReports"] for i in items] == [True, False]
