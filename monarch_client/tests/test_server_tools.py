"""Every @mcp.tool() in server.py is invoked through FastMCP (call_tool) so the
output-schema validation that real clients hit is exercised. Regression for
the 9367e80 bug where wrappers were annotated `-> str` but returned dicts:
the write succeeded, then FastMCP raised ToolError on the result.

Underlying client functions are stubbed to return realistic dicts (no
network); three wrappers are additionally driven end to end through the real
writes_splits_rules functions with a fake client."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from monarch_client import reads, writes, writes_splits_rules  # noqa: E402

CLIENT_MODULES = [
    server.client_reads,
    server.client_writes,
    server.client_writes_accounts,
    server.client_writes_categories,
    server.client_writes_splits_rules,
]

# Added in 9367e80 (split/unsplit/rule update + categories/accounts/goals/budgets).
ADDED_IN_9367E80 = {
    "create_category_group", "update_category_group", "delete_category_group",
    "create_category", "update_category", "delete_category", "update_tag",
    "update_merchant", "update_account", "delete_account", "set_budget_amount",
    "set_flex_budget_amount", "create_savings_goal", "update_savings_goal",
    "set_savings_goal_budget_amount", "delete_savings_goal",
    "split_transaction", "unsplit_transaction", "update_transaction_rule",
}

_STUB_RESULT = {"ok": True, "items": [{"id": "x", "name": "y"}], "errors": None}


def _dummy(schema: dict[str, Any]) -> Any:
    if "anyOf" in schema:
        non_null = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return _dummy(non_null[0])
    t = schema.get("type")
    if t == "string":
        return "2026-09-01" if "date" in schema.get("title", "").lower() or \
            "month" in schema.get("title", "").lower() else "x"
    if t == "number":
        return 1.5
    if t == "integer":
        return 1
    if t == "boolean":
        return False
    if t == "array":
        item = schema.get("items", {})
        return [_dummy(item)] if item else []
    if t == "object":
        return {}
    return "x"


def _args_for(tool) -> dict[str, Any]:
    props = tool.inputSchema.get("properties", {})
    return {n: _dummy(props[n]) for n in tool.inputSchema.get("required", [])}


@pytest.fixture
def stubbed(monkeypatch):
    calls: list[str] = []
    for mod in CLIENT_MODULES:
        for name, fn in inspect.getmembers(mod, inspect.iscoroutinefunction):
            if name.startswith("_") or fn.__module__ != mod.__name__:
                continue

            def make(n):
                async def stub(*a, **k):
                    calls.append(n)
                    return dict(_STUB_RESULT)

                return stub

            monkeypatch.setattr(mod, name, make(name))
    return calls


def _tools():
    import asyncio

    return asyncio.run(server.mcp.list_tools())


def test_server_registers_the_expected_tools():
    names = {t.name for t in _tools()}
    assert ADDED_IN_9367E80 <= names
    assert len(names) == len(_tools())


def test_no_tool_is_annotated_str():
    for name, tool in server.mcp._tool_manager._tools.items():
        ann = inspect.signature(tool.fn).return_annotation
        assert ann not in (str, "str"), f"{name} annotated -> str"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _tools(), ids=lambda t: t.name)
async def test_every_tool_returns_through_fastmcp_without_toolerror(stubbed, tool):
    try:
        result = await server.mcp.call_tool(tool.name, _args_for(tool))
    except ToolError as e:  # pragma: no cover - failure path
        pytest.fail(f"{tool.name} raised ToolError: {e}")
    assert result is not None
    assert tool.name in stubbed, f"{tool.name} did not reach a stubbed client function"


# ---- end to end through the real writes_splits_rules functions -------------


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def call(self, op_name, variables):
        self.calls.append((op_name, variables))
        return self.responses[op_name]


@pytest.fixture
def real_fake(monkeypatch):
    client = _FakeClient({})
    monkeypatch.setenv(writes.WRITES_ENV, "1")
    monkeypatch.setattr(writes, "_client", client)
    monkeypatch.setattr(reads, "_client", client)
    return client


@pytest.mark.asyncio
async def test_split_unsplit_update_rule_end_to_end_through_fastmcp(real_fake):
    real_fake.responses["Web_GetTransactionDrawer"] = {
        "getTransaction": {"id": "t1", "amount": -10, "hideFromReports": False}
    }
    real_fake.responses["Common_SplitTransactionMutation"] = {
        "updateTransactionSplit": {"errors": None, "transaction": {"id": "t1"}}
    }
    real_fake.responses["Web_GetTransactionRules"] = {
        "transactionRules": [
            {
                "id": "r1",
                "merchantNameCriteria": [{"operator": "contains", "value": "a"}],
                "setCategoryAction": {"id": "c1", "name": "n"},
            }
        ]
    }
    real_fake.responses["Common_UpdateTransactionRuleMutationV2"] = {
        "updateTransactionRuleV2": {"errors": None}
    }
    r1 = await server.mcp.call_tool(
        "split_transaction",
        {"transaction_id": "t1", "splits": [{"amount": -6}, {"amount": -4}]},
    )
    r2 = await server.mcp.call_tool("unsplit_transaction", {"transaction_id": "t1"})
    r3 = await server.mcp.call_tool(
        "update_transaction_rule", {"rule_id": "r1", "set_hide_from_reports": True}
    )
    for r in (r1, r2, r3):
        assert r is not None
    ops = [op for op, _ in real_fake.calls]
    assert ops.count("Common_SplitTransactionMutation") == 2
    assert "Common_UpdateTransactionRuleMutationV2" in ops
