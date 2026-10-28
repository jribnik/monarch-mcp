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
async def test_get_recurring_merchant_reviews_keeps_only_pending(fake_client):
    fake_client.responses["Common_GetRecurringStreams"] = {
        "recurringTransactionStreams": [
            {"stream": {"id": "s1", "reviewStatus": "approved"}},
            {"stream": {"id": "s2", "reviewStatus": "pending"}},
            {"stream": {"id": "s3", "reviewStatus": "approved"}},
        ]
    }
    result = await reads.get_recurring_merchant_reviews()
    assert fake_client.calls == [
        ("Common_GetRecurringStreams", {"includeLiabilities": True})
    ]
    assert result["pending_review_count"] == 1
    assert [s["id"] for s in result["pending_streams"]] == ["s2"]
    assert result["total_streams"] == 3


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


@pytest.mark.asyncio
async def test_get_account_type_options(fake_client):
    fake_client.responses["Common_GetAccountTypeOptions"] = {
        "accountTypeOptions": [
            {"type": {"name": "depository", "possibleSubtypes": [{"name": "checking"}]}}
        ]
    }
    result = await reads.get_account_type_options()
    assert fake_client.calls == [("Common_GetAccountTypeOptions", {})]
    assert result["accountTypeOptions"][0]["type"]["name"] == "depository"


@pytest.mark.asyncio
async def test_get_merchants_compact_projection_and_defaults(fake_client):
    fake_client.responses["Common_SearchMerchantsByName"] = {
        "merchants": [
            {"id": "m1", "name": "Costco", "transactionCount": 12, "__typename": "Merchant"},
            {"id": "m2", "name": "Costco Gas", "transactionCount": 0, "__typename": "Merchant"},
        ]
    }
    result = await reads.get_merchants()
    assert fake_client.calls == [
        ("Common_SearchMerchantsByName", {"limit": 100, "offset": 0})
    ]
    assert result == {
        "merchants": [{"id": "m1", "name": "Costco", "transaction_count": 12}],
        "count": 1,
        "fetched": 2,
        "more_may_exist": False,
        "next_offset": None,
    }


@pytest.mark.asyncio
async def test_get_merchants_search_and_include_zero(fake_client):
    fake_client.responses["Common_SearchMerchantsByName"] = {
        "merchants": [
            {"id": "m1", "name": "Costco", "transactionCount": 12},
            {"id": "m2", "name": "Costco Gas", "transactionCount": 0},
        ]
    }
    result = await reads.get_merchants(search=" cost ", limit=2, include_zero_txn=True)
    assert fake_client.calls == [
        ("Common_SearchMerchantsByName", {"search": "cost", "limit": 2, "offset": 0})
    ]
    assert [m["id"] for m in result["merchants"]] == ["m1", "m2"]
    # a full page (len == limit) means more may exist
    assert result["more_may_exist"] is True and result["count"] == 2
    assert result["next_offset"] == 2 and "truncated" not in result


@pytest.mark.asyncio
async def test_get_merchants_offset_is_sent_and_advances(fake_client):
    fake_client.responses["Common_SearchMerchantsByName"] = {
        "merchants": [{"id": "m1", "name": "A", "transactionCount": 1},
                      {"id": "m2", "name": "B", "transactionCount": 1}]
    }
    result = await reads.get_merchants(limit=2, offset=40)
    assert fake_client.calls == [
        ("Common_SearchMerchantsByName", {"limit": 2, "offset": 40})
    ]
    assert result["next_offset"] == 42
    with pytest.raises(ValueError):
        await reads.get_merchants(offset=-1)


@pytest.mark.asyncio
async def test_get_merchants_truncated_is_computed_before_zero_filter(fake_client):
    fake_client.responses["Common_SearchMerchantsByName"] = {
        "merchants": [
            {"id": "m1", "name": "A", "transactionCount": 0},
            {"id": "m2", "name": "B", "transactionCount": 0},
        ]
    }
    result = await reads.get_merchants(limit=2)
    assert result == {"merchants": [], "count": 0, "fetched": 2,
                      "more_may_exist": True, "next_offset": 2}


@pytest.mark.asyncio
async def test_get_merchants_empty_and_bad_limit(fake_client):
    fake_client.responses["Common_SearchMerchantsByName"] = {"merchants": None}
    assert (await reads.get_merchants())["merchants"] == []
    with pytest.raises(ValueError):
        await reads.get_merchants(limit=0)


# ---- net worth / credit score / household / businesses / goals -------------
# All fixtures below are synthetic (structure only), not captured values.


def _snaps(start: date, days: int):
    from datetime import timedelta

    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "balance": 1000.0 + i,
            "assetsBalance": 1500.0 + i,
            "liabilitiesBalance": -500.0,
            "__typename": "AggregateSnapshot",
        }
        for i in range(days)
    ]


@pytest.mark.asyncio
async def test_net_worth_auto_short_span_is_daily(fake_client):
    fake_client.responses["Common_GetAggregateSnapshots"] = {
        "aggregateSnapshots": _snaps(date(2026, 3, 1), 10)
    }
    result = await reads.get_net_worth_history("2026-03-01", "2026-03-10")
    assert fake_client.calls == [
        (
            "Common_GetAggregateSnapshots",
            {"filters": {"startDate": "2026-03-01", "endDate": "2026-03-10",
                         "useAdaptiveGranularity": True}},
        )
    ]
    assert result["interval"] == "day" and result["count"] == 10
    assert result["points"][0] == {
        "date": "2026-03-01", "net_worth": 1000.0, "assets": 1500.0, "liabilities": -500.0
    }


@pytest.mark.asyncio
async def test_net_worth_auto_medium_span_is_weekly_last_of_week(fake_client):
    # 2026-03-02 is a Monday: 14 days -> 2 ISO weeks
    fake_client.responses["Common_GetAggregateSnapshots"] = {
        "aggregateSnapshots": _snaps(date(2026, 3, 2), 14)
    }
    result = await reads.get_net_worth_history("2026-03-02", "2026-07-01")
    assert result["interval"] == "week"
    assert [p["date"] for p in result["points"]] == ["2026-03-08", "2026-03-15"]
    assert result["points"][0]["net_worth"] == 1006.0  # last snapshot of the week


@pytest.mark.asyncio
async def test_net_worth_auto_long_span_is_monthly_and_unsorted_input_ok(fake_client):
    snaps = _snaps(date(2026, 1, 30), 5)  # Jan 30, 31, Feb 1, 2, 3
    snaps.reverse()
    fake_client.responses["Common_GetAggregateSnapshots"] = {"aggregateSnapshots": snaps}
    result = await reads.get_net_worth_history("2024-01-01", "2026-02-03")
    assert result["interval"] == "month"
    assert [p["date"] for p in result["points"]] == ["2026-01-31", "2026-02-03"]


@pytest.mark.asyncio
async def test_net_worth_explicit_interval_default_end_and_validation(fake_client, monkeypatch):
    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 3, 15)

    monkeypatch.setattr(reads, "date", _FixedDate)
    fake_client.responses["Common_GetAggregateSnapshots"] = {"aggregateSnapshots": None}
    result = await reads.get_net_worth_history("2026-03-01", interval="month")
    # defaulted end_date -> endDate is null (what the app sends)
    assert fake_client.calls[0][1]["filters"]["endDate"] is None
    assert result == {"interval": "month", "points": [], "count": 0}
    # explicit end_date equal to today -> also null
    await reads.get_net_worth_history("2026-03-01", "2026-03-15")
    assert fake_client.calls[1][1]["filters"]["endDate"] is None
    for kwargs in (
        {"start_date": "03/01/2026"},
        {"start_date": "2026-03-01", "interval": "hour"},
        {"start_date": "2026-03-20"},  # after the default end
    ):
        with pytest.raises(ValueError):
            await reads.get_net_worth_history(**kwargs)


@pytest.mark.asyncio
async def test_credit_score_history_projects_only_date_score_name(fake_client):
    fake_client.responses["Common_GetSpinwheelCreditScoreSnapshots"] = {
        "me": {"id": "u1"},
        "myHousehold": {
            "id": "h",
            "users": [
                {"id": "u1", "name": "A Person", "displayName": "Ay",
                 "profilePictureUrl": "https://example.invalid/a.png"},
                {"id": "u2", "name": "b@example.invalid", "displayName": "Bee",
                 "profilePictureUrl": None},
            ],
        },
        "spinwheelUser": {
            "id": "sw", "onboardingStatus": "DONE", "onboardingErrorMessage": None,
            "spinwheelUserId": "SECRET", "creditScoreRefreshSubscriptionId": "SECRET2",
            "user": {"id": "u1", "name": "A Person", "displayName": "Ay"},
        },
        "creditScoreSnapshots": [
            {"reportedDate": "2026-02-01", "score": 700, "user": {"id": "u1"}},
            {"reportedDate": "2026-01-01", "score": 690, "user": {"id": "u1"}},
            {"reportedDate": "2026-02-01", "score": 650, "user": {"id": "u2"}},
            {"reportedDate": "2025-12-31", "score": 600, "user": {"id": "u1"}},
            {"reportedDate": "2026-03-02", "score": 710, "user": {"id": "u1"}},
        ],
    }
    result = await reads.get_credit_score_history("2026-01-01", "2026-03-01")
    assert fake_client.calls == [("Common_GetSpinwheelCreditScoreSnapshots", {})]
    assert result == {
        "scores": [
            {"date": "2026-01-01", "score": 690, "user": "Ay"},
            {"date": "2026-02-01", "score": 700, "user": "Ay"},
            {"date": "2026-02-01", "score": 650, "user": "Bee"},
        ],
        "count": 3,
    }
    flat = repr(result)
    for leaked in ("SECRET", "A Person", "example.invalid", "onboarding", "sw"):
        assert leaked not in flat
    with pytest.raises(ValueError):
        await reads.get_credit_score_history("2026-03-01", "2026-01-01")


@pytest.mark.asyncio
async def test_get_household_members_drops_avatars_and_flags_me(fake_client):
    fake_client.responses["Common_GetHouseholdMembers"] = {
        "me": {"id": "u2"},
        "myHousehold": {
            "id": "h",
            "users": [
                {"id": "u1", "name": "One", "displayName": "1", "householdRole": "member",
                 "profilePictureUrl": "https://example.invalid/x", "avatarColor": "red"},
                {"id": "u2", "name": "Two", "displayName": "2", "householdRole": "admin",
                 "profilePictureUrl": None, "avatarColor": "blue"},
            ],
        },
    }
    result = await reads.get_household_members()
    assert fake_client.calls == [("Common_GetHouseholdMembers", {})]
    assert result == {
        "members": [
            {"id": "u1", "name": "One", "display_name": "1", "role": "member", "is_me": False},
            {"id": "u2", "name": "Two", "display_name": "2", "role": "admin", "is_me": True},
        ]
    }


@pytest.mark.asyncio
async def test_get_businesses_compact(fake_client):
    fake_client.responses["Common_GetBusinessEntities"] = {
        "businessEntities": [
            {
                "id": "b1", "name": "Acme LLC", "description": "side gig",
                "logoUrl": "https://example.invalid/l", "color": "#fff", "notes": "private",
                "structure": "llc", "createdAt": "x", "updatedAt": "y",
                "accounts": [{"id": "a1", "logoUrl": "u", "displayName": "Biz Checking"}],
                "accountsCount": 1, "transactionsCount": 42,
            }
        ]
    }
    result = await reads.get_businesses()
    assert fake_client.calls == [("Common_GetBusinessEntities", {})]
    assert result == {
        "businesses": [
            {
                "id": "b1", "name": "Acme LLC", "description": "side gig", "structure": "llc",
                "accounts": [{"id": "a1", "name": "Biz Checking"}],
                "accounts_count": 1, "transactions_count": 42,
            }
        ]
    }
    fake_client.responses["Common_GetBusinessEntities"] = {"businessEntities": None}
    assert await reads.get_businesses() == {"businesses": []}


@pytest.mark.asyncio
async def test_get_goals_compact(fake_client):
    fake_client.responses["Common_SavingsGoalsWithThisMonthBalances"] = {
        "savingsGoals": [
            {
                "id": "g1", "type": "savings", "name": "Trip", "createdAt": "c",
                "archivedAt": None, "imageStorageProvider": "p", "imageStorageProviderId": "i",
                "status": "on_track", "progress": 0.5, "currentBalance": 500.0,
                "targetDate": "2027-01-01", "targetAmount": 1000.0,
                "plannedMonthlyContribution": 100.0, "balanceThisMonth": 25.0,
                "estimatedMonthsUntilCompletion": 5, "forecastedCompletionDate": "2027-01-01",
                "isSinkingFund": False, "priority": 1, "spendingTotal": 0.0,
                "allocationAmountsByAccount": [
                    {"goalId": "g1", "totalAmount": 500.0,
                     "account": {"displayName": "Savings", "logoUrl": "x"}},
                ],
            },
            {"id": "g2", "name": "Old", "archivedAt": "2026-01-01"},
        ],
        "goalsBalanceThisMonth": 25.0,
        "currentTotalBalanceForGoals": 500.0,
    }
    default = await reads.get_goals()
    assert fake_client.calls == [("Common_SavingsGoalsWithThisMonthBalances", {})]
    assert [g["id"] for g in default["goals"]] == ["g1"]
    result = await reads.get_goals(include_archived=True)
    g1, g2 = result["goals"]
    assert g1["name"] == "Trip" and g1["target_amount"] == 1000.0
    assert g1["allocations"] == [{"account": "Savings", "amount": 500.0}]
    assert g1["archived"] is False and g2["archived"] is True
    assert "imageStorageProvider" not in g1 and "createdAt" not in g1
    assert result["goals_balance_this_month"] == 25.0
    assert result["current_total_balance_for_goals"] == 500.0
