"""
Hermetic tests for writes.py: assert each function sends the right
operation/variables and projects the response correctly, without any
network access. Fixture response shapes match what was observed live
against the disposable monarch-sandbox account (values are fake/marked).
"""

from __future__ import annotations

import pytest

from monarch_client import reads, writes


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
    # These tests exercise each write's request/projection logic, so open
    # the master write gate; the gate itself is covered in test_write_gate.py.
    monkeypatch.setenv(writes.WRITES_ENV, "1")
    monkeypatch.setattr(writes, "_client", client)
    # set_merchant_name's safety check (_verify_merchant_name_exists) calls
    # reads.get_transactions, which uses reads.py's own module-level
    # _client -- share the same fake so both modules' calls are visible on
    # one .calls log.
    monkeypatch.setattr(reads, "_client", client)
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
async def test_delete_tag_sends_bare_tag_id(fake_client):
    fake_client.responses["Common_DeleteHouseholdTransactionTag"] = {
        "deleteTransactionTag": {"errors": None}
    }
    result = await writes.delete_tag("t1")
    assert fake_client.calls == [
        ("Common_DeleteHouseholdTransactionTag", {"tagId": "t1"})
    ]
    assert result == {"deleteTransactionTag": {"errors": None}}


@pytest.mark.asyncio
async def test_delete_tag_surfaces_errors(fake_client):
    fake_client.responses["Common_DeleteHouseholdTransactionTag"] = {
        "deleteTransactionTag": {"errors": [{"message": "Tag not found"}]}
    }
    result = await writes.delete_tag("bogus")
    assert result["deleteTransactionTag"]["errors"] == [{"message": "Tag not found"}]


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
async def test_preview_transaction_rule_accepts_exact_matching_merchant_name(fake_client):
    fake_client.responses["Web_GetTransactionsList"] = {
        "allTransactions": {
            "totalCount": 1,
            "results": [{"merchant": {"name": "Recon Test Coffee Shop"}}],
        }
    }
    fake_client.responses["Common_PreviewTransactionRule"] = {
        "transactionRulePreview": {"totalCount": 0, "results": []}
    }
    await writes.preview_transaction_rule(set_merchant_name="Recon Test Coffee Shop")
    op_names = [call[0] for call in fake_client.calls]
    assert op_names == ["Web_GetTransactionsList", "Common_PreviewTransactionRule"]
    _, rule_variables = fake_client.calls[1]
    assert rule_variables["rule"]["setMerchantAction"] == "Recon Test Coffee Shop"


@pytest.mark.asyncio
async def test_preview_transaction_rule_rejects_nonexistent_merchant_name(fake_client):
    """setMerchantAction has no server-side validation -- verified live
    2026-09-28 that a non-matching name silently creates a garbage merchant
    instead of erroring. This client-side check is what actually prevents
    that; must reject before ever calling the real op."""
    fake_client.responses["Web_GetTransactionsList"] = {
        "allTransactions": {"totalCount": 0, "results": []}
    }
    with pytest.raises(ValueError, match="doesn't exactly match"):
        await writes.preview_transaction_rule(
            set_merchant_name="Definitely Not A Real Merchant XYZ123"
        )
    # must reject before ever calling the real (write-adjacent) op
    assert fake_client.calls == [
        (
            "Web_GetTransactionsList",
            {
                "offset": 0,
                "limit": 100,
                "orderBy": "date",
                "filters": {
                    "search": "Definitely Not A Real Merchant XYZ123",
                    "categories": [],
                    "accounts": [],
                    "tags": [],
                    "transactionVisibility": "non_hidden_transactions_only",
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_verify_merchant_name_exists_flags_truncated_search_distinctly(fake_client):
    """Opus review 2026-09-28: on the real account (years of history) a
    short/common merchant name's exact match can be pushed out of the
    result window by newer transactions that merely contain the string.
    That must be reported as "can't confirm" (and still reject), not
    conflated with "doesn't exist" -- the latter's error message lists
    near-miss names that could steer a retry toward the WRONG merchant."""
    fake_client.responses["Web_GetTransactionsList"] = {
        "allTransactions": {
            "totalCount": 150,
            "results": [{"merchant": {"name": "Some Other Merchant"}}] * 100,
        }
    }
    with pytest.raises(ValueError, match="can't confirm"):
        await writes.preview_transaction_rule(set_merchant_name="Target")


@pytest.mark.asyncio
async def test_create_transaction_rule_rejects_nonexistent_merchant_name(fake_client):
    fake_client.responses["Web_GetTransactionsList"] = {
        "allTransactions": {"totalCount": 0, "results": []}
    }
    with pytest.raises(ValueError, match="doesn't exactly match"):
        await writes.create_transaction_rule(set_merchant_name="Not Real")
    assert fake_client.calls == [
        (
            "Web_GetTransactionsList",
            {
                "offset": 0,
                "limit": 100,
                "orderBy": "date",
                "filters": {
                    "search": "Not Real",
                    "categories": [],
                    "accounts": [],
                    "tags": [],
                    "transactionVisibility": "non_hidden_transactions_only",
                },
            },
        )
    ]


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


def _accounts_page_response(accounts: list[dict]) -> dict:
    return {
        "accountTypeSummaries": [
            {"type": {"name": "depository"}, "accounts": accounts}
        ]
    }


@pytest.mark.asyncio
async def test_create_transaction_sends_full_input(fake_client):
    fake_client.responses["Web_GetAccountsPage"] = _accounts_page_response(
        [{"id": "acc1", "displayName": "Manual Cash", "credential": None}]
    )
    fake_client.responses["Common_CreateTransactionMutation"] = {
        "createTransaction": {"transaction": {"id": "t1"}, "errors": None}
    }
    result = await writes.create_transaction(
        account_id="acc1",
        date="2026-09-28",
        amount=-12.34,
        merchant_name="Recon Test Coffee Shop",
        category_id="cat1",
    )
    assert fake_client.calls[-1] == (
        "Common_CreateTransactionMutation",
        {
            "input": {
                "date": "2026-09-28",
                "shouldUpdateBalance": True,
                "accountId": "acc1",
                "ownerUserId": None,
                "amount": -12.34,
                "merchantName": "Recon Test Coffee Shop",
                "categoryId": "cat1",
            }
        },
    )
    assert result == {
        "createTransaction": {"transaction": {"id": "t1"}, "errors": None}
    }


@pytest.mark.asyncio
async def test_create_transaction_surfaces_errors_on_bad_id(fake_client):
    fake_client.responses["Web_GetAccountsPage"] = _accounts_page_response(
        [{"id": "bogus", "displayName": "Manual Cash", "credential": None}]
    )
    fake_client.responses["Common_CreateTransactionMutation"] = {
        "createTransaction": {
            "transaction": None,
            "errors": {"message": "Account matching query does not exist."},
        }
    }
    result = await writes.create_transaction(
        account_id="bogus", date="2026-09-28", amount=-1,
        merchant_name="x", category_id="cat1",
    )
    assert result["createTransaction"]["transaction"] is None
    assert result["createTransaction"]["errors"]["message"] == (
        "Account matching query does not exist."
    )


@pytest.mark.asyncio
async def test_create_transaction_rejects_unknown_account_id(fake_client):
    fake_client.responses["Web_GetAccountsPage"] = _accounts_page_response(
        [{"id": "acc1", "displayName": "Manual Cash", "credential": None}]
    )
    with pytest.raises(ValueError, match="wasn't found"):
        await writes.create_transaction(
            account_id="does-not-exist", date="2026-09-28", amount=-1,
            merchant_name="x", category_id="cat1",
        )
    # must reject before ever calling the real mutation
    assert fake_client.calls == [("Web_GetAccountsPage", {"filters": {}})]


@pytest.mark.asyncio
async def test_create_transaction_rejects_bank_linked_account(fake_client):
    """Opus review 2026-09-28: Monarch's own mutation only rejects a
    NONEXISTENT accountId, not a valid-but-linked one -- a linked account's
    transactions are supposed to come from the bank sync, not manual entry.
    A manual account's `credential` is null; a linked one has a real
    credential object."""
    fake_client.responses["Web_GetAccountsPage"] = _accounts_page_response(
        [
            {
                "id": "linked1",
                "displayName": "Real Checking",
                "credential": {"id": "cred1", "dataProvider": "plaid"},
            }
        ]
    )
    with pytest.raises(ValueError, match="bank-linked account"):
        await writes.create_transaction(
            account_id="linked1", date="2026-09-28", amount=-1,
            merchant_name="x", category_id="cat1",
        )
    assert fake_client.calls == [("Web_GetAccountsPage", {"filters": {}})]


@pytest.mark.asyncio
async def test_delete_transaction_sends_wrapped_id(fake_client):
    fake_client.responses["Common_DeleteTransactionMutation"] = {
        "deleteTransaction": {"deleted": True, "errors": None}
    }
    result = await writes.delete_transaction("t1")
    assert fake_client.calls == [
        ("Common_DeleteTransactionMutation", {"input": {"transactionId": "t1"}})
    ]
    assert result == {"deleted_flag": True}


@pytest.mark.asyncio
async def test_delete_transaction_defaults_missing_deleted_to_false(fake_client):
    fake_client.responses["Common_DeleteTransactionMutation"] = {
        "deleteTransaction": {"errors": None}
    }
    result = await writes.delete_transaction("t1")
    assert result == {"deleted_flag": False}


@pytest.mark.asyncio
async def test_create_manual_account_sends_full_input(fake_client):
    fake_client.responses["Web_CreateManualAccount"] = {
        "createManualAccount": {"account": {"id": "acc1"}, "errors": None}
    }
    result = await writes.create_manual_account(
        name="ZZZ Test Account",
        account_type="depository",
        account_subtype="checking",
        display_balance=42.5,
    )
    assert fake_client.calls == [
        (
            "Web_CreateManualAccount",
            {
                "input": {
                    "type": "depository",
                    "subtype": "checking",
                    "includeInNetWorth": True,
                    "name": "ZZZ Test Account",
                    "displayBalance": 42.5,
                    "ownerUserId": None,
                }
            },
        )
    ]
    assert result == {
        "createManualAccount": {"account": {"id": "acc1"}, "errors": None}
    }


@pytest.mark.asyncio
async def test_create_manual_account_respects_include_in_net_worth_false(fake_client):
    fake_client.responses["Web_CreateManualAccount"] = {
        "createManualAccount": {"account": {"id": "acc1"}, "errors": None}
    }
    await writes.create_manual_account(
        name="x", account_type="depository", account_subtype="checking",
        display_balance=1, include_in_net_worth=False,
    )
    _, variables = fake_client.calls[0]
    assert variables["input"]["includeInNetWorth"] is False


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


@pytest.mark.asyncio
async def test_update_transaction_reviewed_true_sends_reviewed(fake_client):
    fake_client.responses["Web_TransactionDrawerUpdateTransaction"] = {
        "updateTransaction": {"transaction": {"id": "t1"}, "errors": None}
    }
    await writes.update_transaction("t1", reviewed=True)
    assert fake_client.calls[0][1]["input"] == {"id": "t1", "reviewed": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"reviewed": False},
    {"reviewed": True, "needs_review": False},
    {"reviewed": True, "needs_review": True},
])
async def test_update_transaction_reviewed_misuse_refused(fake_client, kwargs):
    with pytest.raises(ValueError):
        await writes.update_transaction("t1", **kwargs)
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_update_transaction_reviewed_with_other_fields(fake_client):
    fake_client.responses["Web_TransactionDrawerUpdateTransaction"] = {
        "updateTransaction": {"transaction": {"id": "t1"}, "errors": None}
    }
    await writes.update_transaction("t1", notes="n", reviewed=True)
    assert fake_client.calls[0][1]["input"] == {"id": "t1", "notes": "n", "reviewed": True}
