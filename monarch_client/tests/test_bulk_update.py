"""bulk_update_transactions: dry-run (no mutation, no gate), cap, confirm
gate, partial-failure reporting and field->mutation mapping. Synthetic data."""

from __future__ import annotations

import pytest

from monarch_client import errors, reads, writes


class _FakeClient:
    def __init__(self, txns, fail=None):
        self.txns = txns
        self.fail = fail or {}
        self.calls = []

    async def call(self, op_name, variables):
        self.calls.append((op_name, variables))
        if op_name == "Web_GetTransactionDrawer":
            return {"getTransaction": self.txns.get(variables["id"]), "myHousehold": {}}
        tid = (variables["input"].get("id") or variables["input"].get("transactionId"))
        if tid in self.fail:
            raise RuntimeError(self.fail[tid])
        if op_name == "Web_TransactionDrawerUpdateTransaction":
            return {"updateTransaction": {"transaction": {"id": tid}, "errors": None}}
        if op_name == "Web_SetTransactionTags":
            return {"setTransactionTags": {"transaction": {"id": tid}, "errors": None}}
        raise AssertionError(op_name)

    def mutations(self):
        return [c for c in self.calls if c[0] != "Web_GetTransactionDrawer"]


def _txn(i, cat="c0", tags=(), hide=False, review=None, needs=True, notes=""):
    return {
        "id": f"t{i}", "amount": -10.0 - i, "merchant": {"name": f"M{i}"},
        "category": {"id": cat, "name": f"name-{cat}"},
        "tags": [{"id": t} for t in tags], "hideFromReports": hide,
        "reviewStatus": review, "needsReview": needs, "notes": notes,
    }


@pytest.fixture
def fake(monkeypatch):
    client = _FakeClient({f"t{i}": _txn(i) for i in range(5)})
    monkeypatch.setattr(writes, "_client", client)
    monkeypatch.setattr(reads, "_client", client)
    monkeypatch.setenv(writes.WRITES_ENV, "1")
    return client


@pytest.mark.asyncio
async def test_dry_run_is_default_sends_no_mutation_and_needs_no_gate(fake, monkeypatch):
    monkeypatch.delenv(writes.WRITES_ENV, raising=False)
    out = await writes.bulk_update_transactions(["t0", "t1", "nope"], category_id="c9")
    assert out["dry_run"] is True
    assert out["would_affect_count"] == 2 and out["missing_ids"] == ["nope"]
    assert out["diffs"][0] == {
        "id": "t0", "merchant": "M0", "amount": -10.0,
        "before": {"category_id": "c0"}, "after": {"category_id": "c9"},
    }
    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_unchanged_transactions_are_not_counted(fake):
    fake.txns["t1"] = _txn(1, cat="c9")
    out = await writes.bulk_update_transactions(["t0", "t1"], category_id="c9")
    assert out["would_affect_count"] == 1 and out["unchanged_ids"] == ["t1"]


@pytest.mark.asyncio
async def test_validation_errors(fake):
    f = writes.bulk_update_transactions
    with pytest.raises(ValueError, match="nothing to change"):
        await f(["t0"])
    with pytest.raises(ValueError, match="not both"):
        await f(["t0"], reviewed=True, needs_review=False)
    with pytest.raises(ValueError, match="only supports True"):
        await f(["t0"], reviewed=False)
    with pytest.raises(ValueError, match="must not be empty"):
        await f([], notes="x")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_cap_of_50_ids(fake):
    ids = [f"x{i}" for i in range(51)]
    with pytest.raises(ValueError, match="max is 50"):
        await writes.bulk_update_transactions(ids, notes="n")
    assert fake.calls == []
    ok = await writes.bulk_update_transactions([f"x{i}" for i in range(50)], notes="n")
    assert len(ok["missing_ids"]) == 50


@pytest.mark.asyncio
async def test_commit_refused_when_gate_closed_but_nothing_read(fake, monkeypatch):
    monkeypatch.delenv(writes.WRITES_ENV, raising=False)
    with pytest.raises(errors.MonarchWriteBlocked):
        await writes.bulk_update_transactions(
            ["t0"], category_id="c9", dry_run=False, confirm="1"
        )
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [None, "", "1", "3", "two"])
async def test_confirm_mismatch_refuses(fake, confirm):
    with pytest.raises(ValueError, match="does not exactly match"):
        await writes.bulk_update_transactions(
            ["t0", "t1"], category_id="c9", dry_run=False, confirm=confirm
        )
    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_confirm_is_recomputed_not_trusted(fake):
    fake.txns["t1"] = _txn(1, cat="c9")  # already up to date -> count is 1
    with pytest.raises(ValueError, match="does not exactly match"):
        await writes.bulk_update_transactions(
            ["t0", "t1"], category_id="c9", dry_run=False, confirm="2"
        )
    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_field_to_mutation_mapping(fake):
    out = await writes.bulk_update_transactions(
        ["t0", "t1"], category_id="c9", tag_ids=["g1", "g2"],
        hide_from_reports=True, reviewed=True, notes="hello",
        dry_run=False, confirm="2",
    )
    assert out["applied_count"] == 2 and out["failed_count"] == 0
    assert out["results"][0] == {
        "id": "t0", "ok": True,
        "applied": ["category_id", "hide_from_reports", "review_status", "notes", "tag_ids"],
    }
    muts = fake.mutations()
    assert muts[0] == (
        "Web_TransactionDrawerUpdateTransaction",
        {"debugActivityLogEnabled": False, "input": {
            "id": "t0", "category": "c9", "hideFromReports": True,
            "reviewed": True, "notes": "hello"}},
    )
    assert muts[1] == (
        "Web_SetTransactionTags",
        {"debugActivityLogEnabled": False,
         "input": {"transactionId": "t0", "tagIds": ["g1", "g2"]}},
    )
    assert len(muts) == 4


@pytest.mark.asyncio
async def test_only_changed_fields_are_sent_and_needs_review_maps(fake):
    fake.txns["t0"] = _txn(0, cat="c9", needs=True)
    await writes.bulk_update_transactions(
        ["t0"], category_id="c9", needs_review=False, dry_run=False, confirm="1"
    )
    (op, v), = fake.mutations()
    assert v["input"] == {"id": "t0", "needsReview": False}


@pytest.mark.asyncio
async def test_tags_only_uses_only_set_tags_and_empty_list_clears(fake):
    fake.txns["t0"] = _txn(0, tags=("g1",))
    await writes.bulk_update_transactions(["t0"], tag_ids=[], dry_run=False, confirm="1")
    (op, v), = fake.mutations()
    assert op == "Web_SetTransactionTags" and v["input"]["tagIds"] == []


@pytest.mark.asyncio
async def test_partial_failure_is_reported_and_batch_continues(fake):
    fake.fail["t1"] = "boom"
    out = await writes.bulk_update_transactions(
        ["t0", "t1", "t2"], category_id="c9", dry_run=False, confirm="3"
    )
    assert out["applied_count"] == 2 and out["failed_count"] == 1
    assert out["failed_ids"] == ["t1"]
    assert [r["ok"] for r in out["results"]] == [True, False, True]
    assert "boom" in out["results"][1]["error"]
    assert len(fake.mutations()) == 3  # t2 still attempted after t1 failed


@pytest.mark.asyncio
async def test_api_level_errors_count_as_failures_and_partial_apply_is_visible(fake):
    orig = fake.call

    async def call(op, variables):
        if op == "Web_SetTransactionTags":
            fake.calls.append((op, variables))
            return {"setTransactionTags": {"transaction": None, "errors": {"message": "bad tag"}}}
        return await orig(op, variables)

    fake.call = call
    out = await writes.bulk_update_transactions(
        ["t0"], category_id="c9", tag_ids=["g1"], dry_run=False, confirm="1"
    )
    r, = out["results"]
    assert r["ok"] is False and r["applied"] == ["category_id"] and "bad tag" in r["error"]
