"""
Hermetic tests for writes.py: assert each function sends the right
operation/variables and projects the response correctly, without any
network access. Fixture response shapes match what was observed live
against the disposable monarch-sandbox account (values are fake/marked).
"""

from __future__ import annotations

import pytest

from monarch_client import writes


class _FakeClient:
    def __init__(self, responses: dict[str, dict]):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def call(self, op_name, variables):
        self.calls.append((op_name, variables))
        return self.responses[op_name]


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient({})
    monkeypatch.setattr(writes, "_client", client)
    return client


@pytest.mark.asyncio
async def test_create_tag(fake_client):
    fake_client.responses["Common_CreateTransactionTag"] = {
        "createTransactionTag": {
            "tag": {"id": "t1", "name": "recon-test", "color": "#30A46C"},
            "errors": None,
        }
    }
    result = await writes.create_tag("recon-test", "#30A46C")
    assert fake_client.calls == [
        (
            "Common_CreateTransactionTag",
            {"input": {"name": "recon-test", "color": "#30A46C"}},
        )
    ]
    assert result["createTransactionTag"]["tag"]["id"] == "t1"


@pytest.mark.asyncio
async def test_preview_transaction_rule_builds_rule_input(fake_client):
    fake_client.responses["Common_PreviewTransactionRule"] = {
        "transactionRulePreview": {"totalCount": 0, "results": []}
    }
    await writes.preview_transaction_rule(
        merchant_name_criteria=[{"operator": "contains", "value": "Coffee"}],
        set_category_action="cat1",
    )
    op_name, variables = fake_client.calls[0]
    assert op_name == "Common_PreviewTransactionRule"
    assert variables == {
        "rule": {
            "merchantCriteriaUseOriginalStatement": False,
            "merchantCriteria": None,
            "amountCriteria": None,
            "categoryIds": None,
            "accountIds": None,
            "setCategoryAction": "cat1",
            "addTagsAction": None,
            "setMerchantAction": None,
            "splitTransactionsAction": None,
            "applyToExistingTransactions": False,
            "merchantNameCriteria": [{"operator": "contains", "value": "Coffee"}],
        },
        "offset": 0,
    }


@pytest.mark.asyncio
async def test_preview_transaction_rule_omits_criteria_keys_when_not_given(fake_client):
    fake_client.responses["Common_PreviewTransactionRule"] = {
        "transactionRulePreview": {"totalCount": 0, "results": []}
    }
    await writes.preview_transaction_rule()
    _, variables = fake_client.calls[0]
    assert "merchantNameCriteria" not in variables["rule"]
    assert "originalStatementCriteria" not in variables["rule"]


@pytest.mark.asyncio
async def test_create_transaction_rule_passes_apply_to_existing(fake_client):
    fake_client.responses["Common_CreateTransactionRuleMutationV2"] = {
        "createTransactionRuleV2": {"errors": None}
    }
    await writes.create_transaction_rule(
        original_statement_criteria=[{"operator": "contains", "value": "AMZN"}],
        set_category_action="cat2",
        apply_to_existing_transactions=True,
    )
    op_name, variables = fake_client.calls[0]
    assert op_name == "Common_CreateTransactionRuleMutationV2"
    assert variables["input"]["applyToExistingTransactions"] is True
    assert variables["input"]["originalStatementCriteria"] == [
        {"operator": "contains", "value": "AMZN"}
    ]
    assert "merchantNameCriteria" not in variables["input"]


@pytest.mark.asyncio
async def test_create_transaction_rule_result_never_has_a_real_id(fake_client):
    """The real app's vendored mutation only selects `errors`, not
    `transactionRule { id }` -- see project.py's docstring for why."""
    fake_client.responses["Common_CreateTransactionRuleMutationV2"] = {
        "createTransactionRuleV2": {"errors": None}
    }
    result = await writes.create_transaction_rule(set_category_action="cat1")
    assert result == {
        "createTransactionRuleV2": {"errors": None, "transactionRule": None}
    }


@pytest.mark.asyncio
async def test_create_transaction_rule_surfaces_errors(fake_client):
    fake_client.responses["Common_CreateTransactionRuleMutationV2"] = {
        "createTransactionRuleV2": {
            "errors": [{"message": "Transaction rule must have one action"}]
        }
    }
    result = await writes.create_transaction_rule()
    assert result["createTransactionRuleV2"]["errors"] == [
        {"message": "Transaction rule must have one action"}
    ]


@pytest.mark.asyncio
async def test_delete_transaction_rule_sends_bare_id(fake_client):
    fake_client.responses["Common_DeleteTransactionRule"] = {
        "deleteTransactionRule": {"deleted": False, "errors": None}
    }
    result = await writes.delete_transaction_rule("rule123")
    assert fake_client.calls == [("Common_DeleteTransactionRule", {"id": "rule123"})]
    # `deleted: False` even on success is a known API quirk -- must be
    # passed through as-is, not "corrected" to True.
    assert result == {"deleted_flag": False}


@pytest.mark.asyncio
async def test_delete_transaction_rule_defaults_missing_deleted_to_false(fake_client):
    fake_client.responses["Common_DeleteTransactionRule"] = {
        "deleteTransactionRule": {"errors": None}
    }
    result = await writes.delete_transaction_rule("rule123")
    assert result == {"deleted_flag": False}


@pytest.mark.asyncio
async def test_recategorize_transaction_sends_only_category(fake_client):
    fake_client.responses["Web_TransactionDrawerUpdateTransaction"] = {
        "updateTransaction": {"transaction": {"id": "t1"}, "errors": None}
    }
    result = await writes.recategorize_transaction("t1", "cat1")
    assert fake_client.calls == [
        (
            "Web_TransactionDrawerUpdateTransaction",
            {"debugActivityLogEnabled": False, "input": {"id": "t1", "category": "cat1"}},
        )
    ]
    assert result == {
        "updateTransaction": {"transaction": {"id": "t1"}, "errors": None}
    }


@pytest.mark.asyncio
async def test_update_transaction_only_includes_given_fields(fake_client):
    fake_client.responses["Web_TransactionDrawerUpdateTransaction"] = {
        "updateTransaction": {"transaction": {"id": "t1"}, "errors": None}
    }
    await writes.update_transaction("t1", notes="hello")
    _, variables = fake_client.calls[0]
    assert variables["input"] == {"id": "t1", "notes": "hello"}


@pytest.mark.asyncio
async def test_update_transaction_maps_merchant_name_to_name_field(fake_client):
    fake_client.responses["Web_TransactionDrawerUpdateTransaction"] = {
        "updateTransaction": {"transaction": {"id": "t1"}, "errors": None}
    }
    await writes.update_transaction("t1", merchant_name="Amazon")
    _, variables = fake_client.calls[0]
    assert variables["input"] == {"id": "t1", "name": "Amazon"}


@pytest.mark.asyncio
async def test_update_transaction_ignores_falsy_amount_and_date(fake_client):
    fake_client.responses["Web_TransactionDrawerUpdateTransaction"] = {
        "updateTransaction": {"transaction": {"id": "t1"}, "errors": None}
    }
    await writes.update_transaction("t1", amount=0, date="")
    _, variables = fake_client.calls[0]
    assert variables["input"] == {"id": "t1"}


@pytest.mark.asyncio
async def test_update_transaction_sends_all_fields_together(fake_client):
    fake_client.responses["Web_TransactionDrawerUpdateTransaction"] = {
        "updateTransaction": {"transaction": {"id": "t1"}, "errors": None}
    }
    await writes.update_transaction(
        "t1",
        category_id="cat1",
        merchant_name="Amazon",
        amount=12.5,
        date="2026-01-01",
        hide_from_reports=True,
        needs_review=False,
        notes="note",
    )
    _, variables = fake_client.calls[0]
    assert variables["input"] == {
        "id": "t1",
        "category": "cat1",
        "name": "Amazon",
        "amount": 12.5,
        "date": "2026-01-01",
        "hideFromReports": True,
        "needsReview": False,
        "notes": "note",
    }


@pytest.mark.asyncio
async def test_set_transaction_tags_replaces_full_set(fake_client):
    fake_client.responses["Web_SetTransactionTags"] = {
        "setTransactionTags": {"transaction": {"id": "t1", "tags": []}, "errors": None}
    }
    result = await writes.set_transaction_tags("t1", ["tag1", "tag2"])
    assert fake_client.calls == [
        (
            "Web_SetTransactionTags",
            {
                "debugActivityLogEnabled": False,
                "input": {"transactionId": "t1", "tagIds": ["tag1", "tag2"]},
            },
        )
    ]
    assert result == {
        "setTransactionTags": {"transaction": {"id": "t1", "tags": []}, "errors": None}
    }


@pytest.mark.asyncio
async def test_set_transaction_tags_empty_list_clears_tags(fake_client):
    fake_client.responses["Web_SetTransactionTags"] = {
        "setTransactionTags": {"transaction": {"id": "t1", "tags": []}, "errors": None}
    }
    await writes.set_transaction_tags("t1", [])
    _, variables = fake_client.calls[0]
    assert variables["input"]["tagIds"] == []


@pytest.mark.asyncio
async def test_mark_stream_as_not_recurring_sends_bare_stream_id(fake_client):
    fake_client.responses["Common_MarkAsNotRecurring"] = {
        "markStreamAsNotRecurring": {"success": True, "errors": None}
    }
    result = await writes.mark_stream_as_not_recurring("stream123")
    assert fake_client.calls == [
        ("Common_MarkAsNotRecurring", {"streamId": "stream123"})
    ]
    assert result == {"success": True}


@pytest.mark.asyncio
async def test_mark_stream_as_not_recurring_defaults_missing_success_to_false(fake_client):
    fake_client.responses["Common_MarkAsNotRecurring"] = {
        "markStreamAsNotRecurring": {"errors": None}
    }
    result = await writes.mark_stream_as_not_recurring("stream123")
    assert result == {"success": False}
