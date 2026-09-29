from __future__ import annotations

import pytest

from monarch_client import doctor, operations


def _walk_unreachable_op() -> str:
    return next(
        name
        for name, entry in sorted(operations.PROVENANCE.items())
        if entry.get("walk_reachable", True) is False
    )


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
