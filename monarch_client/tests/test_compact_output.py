"""Compact get_transactions / list_accounts output: projectors, the size
budget, filter mapping and the detail="full" compatibility path. All fixtures
are synthetic."""

from __future__ import annotations

import json

import pytest

from monarch_client import project, reads


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call(self, op_name, variables):
        self.calls.append((op_name, variables))
        return self.responses[op_name]


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient({})
    monkeypatch.setattr(reads, "_client", client)
    return client


def _raw_txn(i: int, bulk: int = 0) -> dict:
    return {
        "id": f"t{i}",
        "amount": -float(i) - 0.5,
        "pending": False,
        "date": "2026-01-15",
        "hideFromReports": False,
        "plaidName": "PLAID NAME " + "x" * bulk,
        "notes": "a note" if i % 2 else "  ",
        "reviewStatus": "needs_review",
        "needsReview": True,
        "isSplitTransaction": False,
        "category": {"id": f"c{i}", "name": f"Cat{i}", "icon": "i", "group": {"id": "g"}},
        "merchant": {"id": f"m{i}", "name": f"Merchant{i}", "logoUrl": "u"},
        "account": {"id": "a1", "displayName": "Checking", "logoUrl": "u"},
        "tags": [{"id": "tag1", "name": "n"}, {"id": "tag2", "name": "n2"}],
    }


def _response(n: int, total: int | None = None, bulk: int = 0) -> dict:
    return {
        "allTransactions": {
            "totalCount": total if total is not None else n,
            "results": [_raw_txn(i, bulk) for i in range(n)],
        },
        "transactionRules": [],
    }


# ---- projector ------------------------------------------------------------


def test_compact_row_fields():
    row = project.transaction_compact_row(_raw_txn(1))
    assert row == {
        "id": "t1", "date": "2026-01-15", "amount": -1.5, "pending": False,
        "merchant": "Merchant1", "merchant_id": "m1",
        "original_statement": "PLAID NAME", "notes": "a note",
        "category": "Cat1", "category_id": "c1",
        "account": "Checking", "account_id": "a1",
        "review_status": "needs_review", "needs_review": True,
        "hide_from_reports": False, "is_split": False,
        "tag_ids": ["tag1", "tag2"],
    }
    assert project.transaction_compact_row(_raw_txn(2))["notes"] is None


def test_compact_row_truncates_statement_and_notes():
    t = _raw_txn(1)
    t["plaidName"] = "TST* " + "A" * 100
    t["notes"] = "n" * 200
    row = project.transaction_compact_row(t)
    assert len(row["original_statement"]) == 60 and row["original_statement"].startswith("TST* A")
    assert len(row["notes"]) == 80 and row["original_statement"].endswith("\u2026")


def test_compact_row_tolerates_missing_keys():
    row = project.transaction_compact_row({"id": "x"})
    assert row["id"] == "x" and row["merchant"] is None and row["tag_ids"] == []
    assert row["notes"] is None and row["original_statement"] is None


def test_envelope_and_next_offset():
    out = project.transactions_compact(_response(10, total=35), offset=20)
    assert (out["returned"], out["offset"], out["total_count"]) == (10, 20, 35)
    assert out["next_offset"] == 30 and out["truncated"] is False
    last = project.transactions_compact(_response(5, total=25), offset=20)
    assert last["next_offset"] is None


# ---- size budget ----------------------------------------------------------


def test_compact_budget_trims_and_pages_correctly():
    out = project.transactions_compact(_response(100, total=500), offset=40, budget=3000)
    assert len(json.dumps(out)) <= 3000
    assert out["truncated"] is True
    assert 0 < out["returned"] < 100
    assert out["next_offset"] == 40 + out["returned"]
    assert [t["id"] for t in out["transactions"]] == [f"t{i}" for i in range(out["returned"])]


def test_default_budget_never_exceeded_with_100_synthetic_rows():
    out = project.transactions_compact(_response(100, total=100), offset=0)
    assert len(json.dumps(out)) < project.TRANSACTIONS_BUDGET_CHARS
    assert project.TRANSACTIONS_BUDGET_CHARS == 40_000


def test_compact_single_oversized_row_raises_instead_of_looping():
    with pytest.raises(ValueError, match="single transaction row"):
        project.transactions_compact(_response(3, total=9), offset=0, budget=50)


def test_compact_next_offset_when_total_unknown_uses_limit():
    data = _response(5)
    data["allTransactions"]["totalCount"] = None
    assert project.transactions_compact(data, 0, limit=5)["next_offset"] == 5
    assert project.transactions_compact(data, 0, limit=50)["next_offset"] is None


def test_full_detail_under_budget_keeps_raw_shape_plus_envelope():
    data = _response(3)
    out = project.transactions_full(data, offset=0)
    assert out["allTransactions"] == project.transactions(data)["allTransactions"]
    assert out["truncated"] is False and out["next_offset"] is None


def test_full_detail_reports_next_offset_when_more_rows_exist():
    out = project.transactions_full(_response(3, total=10), offset=4)
    assert out["truncated"] is False and out["next_offset"] == 7


def test_full_detail_over_budget_is_trimmed_with_next_offset():
    data = _response(25, total=90, bulk=4000)
    out = project.transactions_full(data, offset=10)
    assert len(json.dumps(out)) < project.TRANSACTIONS_BUDGET_CHARS
    kept = len(out["allTransactions"]["results"])
    assert 0 < kept < 25
    assert out["truncated"] is True and out["next_offset"] == 10 + kept


def test_full_detail_single_oversized_row_raises():
    with pytest.raises(ValueError, match="exceeds"):
        project.transactions_full(_response(1, bulk=50_000), offset=0)


@pytest.mark.asyncio
async def test_get_transactions_full_budget_end_to_end_and_no_silent_clamp(fake_client):
    fake_client.responses["Web_GetTransactionsList"] = _response(25, total=90, bulk=4000)
    out = await reads.get_transactions(limit=25, detail="full")
    assert fake_client.calls[0][1]["limit"] == reads.FULL_DETAIL_MAX_LIMIT == 25
    assert len(json.dumps(out)) < project.TRANSACTIONS_BUDGET_CHARS
    assert out["truncated"] is True
    with pytest.raises(ValueError, match="detail=.compact. or limit<=25"):
        await reads.get_transactions(limit=26, detail="full")
    with pytest.raises(ValueError, match="exceeds the maximum 200"):
        await reads.get_transactions(limit=500)
    assert len(fake_client.calls) == 1


# ---- reads.get_transactions behavior --------------------------------------


@pytest.mark.asyncio
async def test_default_is_compact_and_visible_only(fake_client):
    fake_client.responses["Web_GetTransactionsList"] = _response(2, total=2)
    out = await reads.get_transactions()
    _, v = fake_client.calls[0]
    assert v["limit"] == 50 and v["offset"] == 0 and v["orderBy"] == "date"
    assert v["filters"]["transactionVisibility"] == "non_hidden_transactions_only"
    assert out["transactions"][0]["merchant"] == "Merchant0"
    assert "allTransactions" not in out


@pytest.mark.asyncio
async def test_visibility_all_and_hidden_are_refused_not_guessed(fake_client):
    fake_client.responses["Web_GetTransactionsList"] = _response(0)
    for v in ("all", "hidden"):
        with pytest.raises(ValueError, match="not yet supported.*api-recon"):
            await reads.get_transactions(visibility=v)
    with pytest.raises(ValueError, match="visibility"):
        await reads.get_transactions(visibility="bogus")
    assert fake_client.calls == []
    await reads.get_transactions(visibility="visible")
    assert fake_client.calls[0][1]["filters"]["transactionVisibility"] == (
        "non_hidden_transactions_only")


@pytest.mark.asyncio
async def test_merchant_and_credits_only_filters(fake_client):
    fake_client.responses["Web_GetTransactionsList"] = _response(0)
    await reads.get_transactions(merchant_ids=["m1", "m2"], credits_only=True)
    f = fake_client.calls[0][1]["filters"]
    assert f["merchants"] == ["m1", "m2"] and f["creditsOnly"] is True
    await reads.get_transactions(credits_only=False)
    f = fake_client.calls[1][1]["filters"]
    assert "creditsOnly" not in f and "merchants" not in f  # False sends nothing
    await reads.get_transactions()
    f = fake_client.calls[2][1]["filters"]
    assert "creditsOnly" not in f and "merchants" not in f


@pytest.mark.asyncio
async def test_total_count_only(fake_client):
    fake_client.responses["Web_GetTransactionsList"] = _response(1, total=1234)
    out = await reads.get_transactions(limit=100, total_count_only=True)
    assert out == {"total_count": 1234}
    assert fake_client.calls[0][1]["limit"] == 1


@pytest.mark.asyncio
async def test_argument_validation(fake_client):
    with pytest.raises(ValueError, match="detail"):
        await reads.get_transactions(detail="tiny")
    with pytest.raises(ValueError, match="order_by"):
        await reads.get_transactions(order_by="amount")
    with pytest.raises(ValueError, match="limit"):
        await reads.get_transactions(limit=0)
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_fetch_transactions_raw_keeps_the_old_shape(fake_client):
    fake_client.responses["Web_GetTransactionsList"] = _response(1)
    out = await reads.fetch_transactions_raw(search="x", limit=100)
    assert set(out) == {"allTransactions", "transactionRules"}
    assert fake_client.calls[0][1]["limit"] == 100


# ---- list_accounts --------------------------------------------------------


def _acct(id_, name, type_, signed, asset, *, hidden=False, nw=True, cred=True):
    return {
        "id": id_, "displayName": name, "signedBalance": signed, "isAsset": asset,
        "isHidden": hidden, "includeInNetWorth": nw, "updatedAt": "2026-01-01T00:00:00Z",
        "type": {"name": type_, "display": type_.title()},
        "subtype": {"display": "Sub " + name},
        "institution": {"name": "Bank"} if cred else None,
        "credential": {"id": "c", "institution": {"name": "Bank"}} if cred else None,
        "notes": "private", "mask": "1234",
    }


ACCOUNTS = {
    "accountTypeSummaries": [
        {"type": {"name": "depository"}, "accounts": [
            _acct("a1", "Checking", "depository", 1000.50, True),
            _acct("a2", "Old", "depository", 50.0, True, hidden=True),
            _acct("a3", "Cash", "depository", 20.0, True, cred=False),
        ]},
        {"type": {"name": "credit"}, "accounts": [
            _acct("a4", "Card", "credit", -300.25, False),
            _acct("a5", "Excluded loan", "loan", -999.0, False, nw=False),
        ]},
    ]
}


@pytest.mark.asyncio
async def test_list_accounts_compact_rows_and_totals(fake_client):
    fake_client.responses["Web_GetAccountsPage"] = ACCOUNTS
    out = await reads.list_accounts()
    assert [a["id"] for a in out["accounts"]] == ["a1", "a3", "a4", "a5"]
    assert out["accounts"][0] == {
        "id": "a1", "name": "Checking", "type": "depository", "subtype": "Sub Checking",
        "balance": 1000.5, "institution": "Bank", "has_credential": True,
        "include_in_net_worth": True, "is_hidden": False,
        "updated_at": "2026-01-01T00:00:00Z",
    }
    assert out["accounts"][1]["has_credential"] is False
    assert out["totals"] == {"assets": 1020.5, "liabilities": 300.25, "net_worth": 720.25}
    assert "notes" not in out["accounts"][0]


@pytest.mark.asyncio
async def test_list_accounts_credit_balance_card_reduces_liabilities(fake_client):
    # a card with a POSITIVE signedBalance (overpaid) must reduce liabilities
    data = {"accountTypeSummaries": [{"type": {"name": "credit"}, "accounts": [
        _acct("c1", "Owed", "credit", -300.0, False),
        _acct("c2", "Overpaid", "credit", 50.0, False),
        _acct("a1", "Checking", "depository", 1000.0, True),
    ]}]}
    fake_client.responses["Web_GetAccountsPage"] = data
    out = await reads.list_accounts()
    assert out["totals"] == {"assets": 1000.0, "liabilities": 250.0, "net_worth": 750.0}


@pytest.mark.asyncio
async def test_list_accounts_include_hidden_and_type_filter(fake_client):
    fake_client.responses["Web_GetAccountsPage"] = ACCOUNTS
    out = await reads.list_accounts(include_hidden=True)
    assert "a2" in [a["id"] for a in out["accounts"]]
    assert out["totals"]["assets"] == 1070.5
    only = await reads.list_accounts(account_types=["CREDIT"])
    assert [a["id"] for a in only["accounts"]] == ["a4"]
    assert only["totals"] == {"assets": 0.0, "liabilities": 300.25, "net_worth": -300.25}


@pytest.mark.asyncio
async def test_list_accounts_full_is_unchanged_raw(fake_client):
    fake_client.responses["Web_GetAccountsPage"] = ACCOUNTS
    out = await reads.list_accounts(detail="full", include_hidden=False)
    assert out == project.accounts(ACCOUNTS)
    assert len(out["accounts"]) == 5
    with pytest.raises(ValueError, match="detail"):
        await reads.list_accounts(detail="x")
