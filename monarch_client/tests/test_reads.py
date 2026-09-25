"""
Hermetic tests for reads.py: assert each function sends the right
operation/variables and projects the response correctly, without any
network access. Fixture response shapes are hand-built to match what was
observed live against the real API (structure only -- values are fake).
"""

from __future__ import annotations

from datetime import date

import pytest

from monarch_client import reads


class _FakeClient:
    """Records every call and returns a canned response keyed by op name."""

    def __init__(self, responses: dict[str, dict]):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def call(self, op_name, variables):
        self.calls.append((op_name, variables))
        return self.responses[op_name]


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient({})
    monkeypatch.setattr(reads, "_client", client)
    return client


@pytest.mark.asyncio
async def test_list_accounts_flattens_type_groups(fake_client):
    fake_client.responses["Web_GetAccountsPage"] = {
        "accountTypeSummaries": [
            {"type": {"name": "depository"}, "accounts": [{"id": "a1", "displayName": "Checking"}]},
            {"type": {"name": "credit"}, "accounts": [{"id": "a2", "displayName": "Card"}]},
        ]
    }
    result = await reads.list_accounts()
    assert fake_client.calls == [("Web_GetAccountsPage", {"filters": {}})]
    assert [a["id"] for a in result["accounts"]] == ["a1", "a2"]


@pytest.mark.asyncio
async def test_get_categories_passthrough(fake_client):
    fake_client.responses["Common_GetCategories"] = {
        "categoryGroups": [{"id": "g1"}],
        "categories": [{"id": "c1"}],
    }
    result = await reads.get_categories()
    assert fake_client.calls == [("Common_GetCategories", {})]
    assert result == {"categoryGroups": [{"id": "g1"}], "categories": [{"id": "c1"}]}


@pytest.mark.asyncio
async def test_get_tags(fake_client):
    fake_client.responses["Common_GetHouseholdTransactionTags"] = {
        "householdTransactionTags": [{"id": "t1", "name": "Travel"}]
    }
    result = await reads.get_tags()
    assert fake_client.calls == [
        ("Common_GetHouseholdTransactionTags", {"includeTransactionCount": False})
    ]
    assert result["householdTransactionTags"][0]["name"] == "Travel"


@pytest.mark.asyncio
async def test_get_budgets_defaults_previous_to_next_month(fake_client, monkeypatch):
    fake_client.responses["Common_GetJointPlanningData"] = {"budgetSystem": "flex"}

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 3, 15)

    monkeypatch.setattr(reads, "date", _FixedDate)

    await reads.get_budgets()
    op_name, variables = fake_client.calls[0]
    assert op_name == "Common_GetJointPlanningData"
    assert variables == {"startDate": "2026-02-01", "endDate": "2026-04-01"}


@pytest.mark.asyncio
async def test_get_budgets_respects_explicit_dates(fake_client):
    fake_client.responses["Common_GetJointPlanningData"] = {}
    await reads.get_budgets(start_date="2026-01-01", end_date="2026-02-01")
    assert fake_client.calls == [
        ("Common_GetJointPlanningData", {"startDate": "2026-01-01", "endDate": "2026-02-01"})
    ]


@pytest.mark.asyncio
async def test_get_cashflow_summary(fake_client):
    fake_client.responses["Web_GetTransactionsPage"] = {
        "aggregates": [{"summary": {"sum": -100.0}}]
    }
    result = await reads.get_cashflow_summary()
    assert fake_client.calls == [
        (
            "Web_GetTransactionsPage",
            {"filters": {"transactionVisibility": "non_hidden_transactions_only"}},
        )
    ]
    assert result["aggregates"][0]["summary"]["sum"] == -100.0


@pytest.mark.asyncio
async def test_get_transactions_builds_filters(fake_client):
    fake_client.responses["Web_GetTransactionsList"] = {
        "allTransactions": {"totalCount": 1, "results": [{"id": "x1"}]},
        "transactionRules": [],
    }
    await reads.get_transactions(
        limit=10,
        offset=5,
        start_date="2026-01-01",
        end_date="2026-01-31",
        search="coffee",
        category_ids=["c1"],
        account_ids=["a1"],
        tag_ids=["t1"],
        has_notes=True,
        is_split=False,
    )
    op_name, variables = fake_client.calls[0]
    assert op_name == "Web_GetTransactionsList"
    assert variables == {
        "offset": 5,
        "limit": 10,
        "orderBy": "date",
        "filters": {
            "search": "coffee",
            "categories": ["c1"],
            "accounts": ["a1"],
            "tags": ["t1"],
            "transactionVisibility": "non_hidden_transactions_only",
            "hasNotes": True,
            "isSplit": False,
            "startDate": "2026-01-01",
            "endDate": "2026-01-31",
        },
    }


@pytest.mark.asyncio
async def test_get_transactions_defaults_have_no_optional_filter_keys(fake_client):
    fake_client.responses["Web_GetTransactionsList"] = {
        "allTransactions": {"totalCount": 0, "results": []},
        "transactionRules": [],
    }
    await reads.get_transactions()
    _, variables = fake_client.calls[0]
    assert variables["filters"] == {
        "search": "",
        "categories": [],
        "accounts": [],
        "tags": [],
        "transactionVisibility": "non_hidden_transactions_only",
    }


@pytest.mark.asyncio
async def test_get_transactions_rejects_mismatched_date_range(fake_client):
    with pytest.raises(ValueError, match="must be given together"):
        await reads.get_transactions(start_date="2026-01-01")


@pytest.mark.asyncio
async def test_get_transaction_details(fake_client):
    fake_client.responses["Web_GetTransactionDrawer"] = {
        "getTransaction": {"id": "x1", "amount": -12.5},
        "myHousehold": {"id": "h1"},
    }
    result = await reads.get_transaction_details("x1")
    assert fake_client.calls == [
        (
            "Web_GetTransactionDrawer",
            {"id": "x1", "redirectPosted": True, "debugActivityLogEnabled": False},
        )
    ]
    assert result["getTransaction"]["id"] == "x1"


@pytest.mark.asyncio
async def test_get_recurring_transactions_flattens_status_groups(fake_client, monkeypatch):
    fake_client.responses["Common_GetAggregatedRecurringItems"] = {
        "aggregatedRecurringItems": {
            "groups": [
                {"groupBy": {"status": "due"}, "results": [{"transactionId": "r1"}]},
                {"groupBy": {"status": "paid"}, "results": [{"transactionId": "r2"}]},
            ],
            "aggregatedSummary": {"expense": {"total": 500.0}},
        }
    }

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 6, 10)

    monkeypatch.setattr(reads, "date", _FixedDate)

    result = await reads.get_recurring_transactions()
    op_name, variables = fake_client.calls[0]
    assert op_name == "Common_GetAggregatedRecurringItems"
    assert variables["startDate"] == "2026-06-01"
    assert variables["endDate"] == "2026-06-30"
    assert [r["transactionId"] for r in result["recurring_items"]] == ["r1", "r2"]
    assert result["summary"]["expense"]["total"] == 500.0


@pytest.mark.asyncio
async def test_get_transaction_rules(fake_client):
    fake_client.responses["Web_GetTransactionRules"] = {
        "transactionRules": [{"id": "rule1"}]
    }
    result = await reads.get_transaction_rules()
    assert fake_client.calls == [("Web_GetTransactionRules", {})]
    assert result["transactionRules"][0]["id"] == "rule1"
