"""
Tests for the MONARCH_CLIENT_ENABLE_WRITES master gate (writes.py).

The disabled-refuses test discovers write functions by introspection, so a
future write added to writes.py without _require_writes() fails here
instead of silently shipping ungated.
"""

from __future__ import annotations

import inspect
import typing

import pytest

from monarch_client import doctor, errors, operations, reads, writes

# Public coroutine functions in writes.py that are deliberately NOT gated:
# preview_transaction_rule is a dry-run (a query that sends nothing that
# mutates), classified as a "read" at the tool level.
UNGATED = {"preview_transaction_rule"}

EXPECTED_GATED = {
    "create_tag",
    "delete_tag",
    "create_transaction_rule",
    "delete_transaction_rule",
    "create_transaction",
    "delete_transaction",
    "create_manual_account",
    "recategorize_transaction",
    "update_transaction",
    "set_transaction_tags",
    "mark_stream_as_not_recurring",
}


class _FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call(self, op_name, variables):
        self.calls.append((op_name, variables))
        return {}


def _write_functions() -> dict[str, typing.Callable]:
    return {
        name: fn
        for name, fn in inspect.getmembers(writes, inspect.iscoroutinefunction)
        if not name.startswith("_") and fn.__module__ == writes.__name__
    }


def _dummy_args(fn) -> dict:
    """Fill every REQUIRED parameter with a type-appropriate dummy; the
    gate must refuse before any argument is looked at."""
    hints = typing.get_type_hints(fn)
    out = {}
    for pname, p in inspect.signature(fn).parameters.items():
        if p.default is not inspect.Parameter.empty:
            continue
        t = hints.get(pname, str)
        if t is float:
            out[pname] = 1.0
        elif t is bool:
            out[pname] = False
        elif typing.get_origin(t) is list:
            out[pname] = ["x"]
        else:
            out[pname] = "x"
    return out


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(writes, "_client", client)
    monkeypatch.setattr(reads, "_client", client)
    return client


def test_gate_covers_exactly_the_eleven_write_tools():
    gated = set(_write_functions()) - UNGATED
    assert gated == EXPECTED_GATED


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", "2"])
async def test_disabled_refuses_every_write_and_sends_nothing(
    monkeypatch, fake_client, value
):
    if value is None:
        monkeypatch.delenv(writes.WRITES_ENV, raising=False)
    else:
        monkeypatch.setenv(writes.WRITES_ENV, value)
    assert writes.writes_status() is False

    for name, fn in sorted(_write_functions().items()):
        if name in UNGATED:
            continue
        with pytest.raises(errors.MonarchWriteBlocked) as exc:
            await fn(**_dummy_args(fn))
        assert exc.value.gate == "enabled"
        assert name in str(exc.value)
        assert writes.WRITES_ENV in str(exc.value)

    assert fake_client.calls == [], "a refused write must not reach the network"


@pytest.mark.asyncio
async def test_enabled_lets_a_write_through(monkeypatch, fake_client):
    monkeypatch.setenv(writes.WRITES_ENV, "1")
    assert writes.writes_status() is True
    fake_client.call = _canned(
        {
            "Common_CreateTransactionTag": {
                "createTransactionTag": {
                    "tag": {"id": "t1", "name": "n", "color": "#000000", "order": 0},
                    "errors": None,
                }
            }
        },
        fake_client,
    )
    result = await writes.create_tag(name="n", color="#000000")
    assert result  # projected result returned
    assert [op for op, _ in fake_client.calls] == ["Common_CreateTransactionTag"]


def _canned(responses, client):
    async def call(op_name, variables):
        client.calls.append((op_name, variables))
        return responses[op_name]

    return call


@pytest.mark.asyncio
async def test_preview_is_allowed_while_gate_closed(monkeypatch, fake_client):
    monkeypatch.delenv(writes.WRITES_ENV, raising=False)
    fake_client.call = _canned(
        {
            "Common_PreviewTransactionRule": {
                "transactionRulePreview": {
                    "totalCount": 0,
                    "results": [],
                    "errors": None,
                }
            }
        },
        fake_client,
    )
    # Whatever the projection returns, the point is: no MonarchWriteBlocked,
    # and the preview op was actually sent.
    try:
        await writes.preview_transaction_rule(
            merchant_name_criteria=[{"operator": "eq", "value": "x"}]
        )
    except errors.MonarchWriteBlocked:
        pytest.fail("dry-run preview must not be gated")
    except Exception:
        pass  # projection of the canned shape is not what this test covers
    assert [op for op, _ in fake_client.calls] == ["Common_PreviewTransactionRule"]


@pytest.mark.asyncio
async def test_call_layer_blocks_any_mutation_op_when_closed(monkeypatch, fake_client):
    """Defense in depth: even bypassing the per-tool check, _call refuses
    every vendored mutation-kind op while the gate is closed."""
    monkeypatch.delenv(writes.WRITES_ENV, raising=False)
    mutations = [n for n in operations.PROVENANCE if operations.is_mutation(n)]
    # 11 write tools, 10 distinct mutation ops: recategorize_transaction and
    # update_transaction share Web_TransactionDrawerUpdateTransaction.
    assert len(mutations) >= 10, mutations
    for op in mutations:
        with pytest.raises(errors.MonarchWriteBlocked):
            await writes._call(op, {})
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_call_layer_allows_queries_when_closed(monkeypatch, fake_client):
    monkeypatch.delenv(writes.WRITES_ENV, raising=False)
    fake_client.call = _canned({"Common_GetMe": {"me": {}}}, fake_client)
    await writes._call("Common_GetMe", {})
    assert [op for op, _ in fake_client.calls] == ["Common_GetMe"]


def test_doctor_reports_gate_state(monkeypatch, capsys):
    monkeypatch.delenv(writes.WRITES_ENV, raising=False)
    doctor._print_write_gate()
    assert "CLOSED" in capsys.readouterr().out
    monkeypatch.setenv(writes.WRITES_ENV, "1")
    doctor._print_write_gate()
    assert "OPEN" in capsys.readouterr().out
