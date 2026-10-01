from __future__ import annotations

import re
from pathlib import Path

import pytest

from monarch_client import doctor, operations


def _walk_unreachable_op() -> str:
    return next(
        name
        for name, entry in sorted(operations.PROVENANCE.items())
        if entry.get("walk_reachable", True) is False
    )


NULL_HASH_MUTATIONS = (
    "Common_DeleteTransactionMutation",
    "Common_DeleteTransactionRule",
    "Common_MarkAsNotRecurring",
)


def _vendored_ops() -> list[str]:
    ops_dir = Path(operations.__file__).parent
    return sorted(p.stem for p in ops_dir.glob("*.graphql"))


def _is_mutation_text(op_name: str) -> bool:
    return re.match(r"\s*mutation\b", operations.load(op_name)) is not None


def test_every_vendored_mutation_is_refused():
    mutations = [op for op in _vendored_ops() if _is_mutation_text(op)]
    assert mutations, "expected at least one vendored mutation"
    for op in mutations:
        assert doctor._is_unsafe_op(op), f"{op} is a mutation but --op would send it"
    # the hand-recovered mutations have no walk_reachable key in PROVENANCE,
    # so only the query-text check catches them
    for op in NULL_HASH_MUTATIONS:
        assert op in mutations
        assert "walk_reachable" not in operations.PROVENANCE[op]
        assert doctor._is_unsafe_op(op)


@pytest.mark.asyncio
@pytest.mark.parametrize("op", NULL_HASH_MUTATIONS)
async def test_run_op_refuses_null_hash_mutations(monkeypatch, op):
    called = {"n": 0}

    class Boom:
        def __init__(self, *a, **k):
            called["n"] += 1

    monkeypatch.setattr(doctor, "MonarchClient", Boom)
    with pytest.raises(SystemExit) as exc_info:
        await doctor._run_op(op)
    assert exc_info.value.code == 1
    assert called["n"] == 0


def test_unsafe_op_detection():
    assert doctor._is_unsafe_op(_walk_unreachable_op())
    assert not doctor._is_unsafe_op("Common_GetMe")
    assert not doctor._is_unsafe_op("Not_A_Vendored_Op")


@pytest.mark.asyncio
async def test_run_op_refuses_walk_unreachable_ops(monkeypatch, capsys):
    called = {"n": 0}

    class Boom:
        def __init__(self, *a, **k):
            called["n"] += 1

    monkeypatch.setattr(doctor, "MonarchClient", Boom)

    with pytest.raises(SystemExit) as exc_info:
        await doctor._run_op(_walk_unreachable_op())
    assert exc_info.value.code == 1
    assert called["n"] == 0  # never even built a client, so nothing was sent
    assert "refusing" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_smoke_call_never_prints_email(monkeypatch, capsys):
    """doctor output is pasted into chat, so the account email must not appear."""

    class FakeClient:
        async def call(self, op, variables):
            return {"me": {"id": "u1", "email": "someone@example.com"}}

        async def aclose(self):
            pass

    monkeypatch.setattr(doctor, "MonarchClient", FakeClient)
    assert await doctor._check_smoke_call() is True
    out = capsys.readouterr().out
    assert "u1" in out
    assert "someone@example.com" not in out
