"""Every @mcp.tool() in server.py is invoked through FastMCP (call_tool) so the
output-schema validation that real clients hit is exercised. Regression for
the 9367e80 bug where wrappers were annotated `-> str` but returned dicts:
the write succeeded, then FastMCP raised ToolError on the result.

Underlying client functions are stubbed to return realistic dicts (no
network). The stubs enforce the REAL function's signature (a wrong keyword,
a missing required argument or too many positionals fails the call) and
record exactly what each wrapper passed, so test_wrapper_passes_each_argument_
under_its_own_name can prove no argument is mis-routed. Three wrappers are
additionally driven end to end through the real writes_splits_rules functions
with a fake client."""

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

# The complete tool surface: 11 read-side tools (incl. the preview_transaction_rule
# dry-run) + 30 write tools. Registering or dropping a tool must be a conscious
# edit here (this is an exact-equality check, not a subset check).
EXPECTED_READ_TOOLS = {
    "list_accounts", "get_categories", "get_tags", "get_budgets",
    "get_cashflow_summary", "get_transactions", "get_transaction_details",
    "get_recurring_transactions", "get_transaction_rules",
    "get_account_type_options", "preview_transaction_rule",
}
EXPECTED_WRITE_TOOLS = {
    "recategorize_transaction", "update_transaction", "set_transaction_tags",
    "create_tag", "update_tag", "delete_tag",
    "create_transaction_rule", "update_transaction_rule", "delete_transaction_rule",
    "mark_stream_as_not_recurring", "create_transaction", "delete_transaction",
    "create_manual_account", "update_account", "delete_account",
    "create_category_group", "update_category_group", "delete_category_group",
    "create_category", "update_category", "delete_category", "update_merchant",
    "set_budget_amount", "set_flex_budget_amount",
    "create_savings_goal", "update_savings_goal",
    "set_savings_goal_budget_amount", "delete_savings_goal",
    "split_transaction", "unsplit_transaction",
}
EXPECTED_TOOLS = EXPECTED_READ_TOOLS | EXPECTED_WRITE_TOOLS

# Tools whose `confirm` / `confirm_name` argument is REQUIRED (review H6).
CONFIRM_REQUIRED = {
    "delete_transaction": "confirm", "delete_tag": "confirm",
    "delete_transaction_rule": "confirm", "delete_category_group": "confirm",
    "delete_category": "confirm",
    "delete_savings_goal": "confirm", "mark_stream_as_not_recurring": "confirm",
    "delete_account": "confirm_name",
}
# Tools with an OPTIONAL confirm that is required only for a wide-blast flag.
CONFIRM_OPTIONAL = {
    "set_budget_amount", "set_flex_budget_amount",
    "set_savings_goal_budget_amount", "create_transaction_rule",
    "update_transaction_rule",
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
    """Replace every public client coroutine with a strict-signature stub.
    `calls` lists the stubbed names hit; `received` maps name -> the bound
    arguments the wrapper actually passed."""
    class Calls(list):
        received: dict[str, dict[str, Any]]

    calls = Calls()
    received: dict[str, dict[str, Any]] = {}
    calls.received = received
    for mod in CLIENT_MODULES:
        for name, fn in inspect.getmembers(mod, inspect.iscoroutinefunction):
            if name.startswith("_") or fn.__module__ != mod.__name__:
                continue

            def make(n, sig):
                async def stub(*a, **k):
                    bound = sig.bind(*a, **k)  # TypeError on any signature mismatch
                    calls.append(n)
                    received[n] = dict(bound.arguments)
                    return dict(_STUB_RESULT)

                return stub

            monkeypatch.setattr(mod, name, make(name, inspect.signature(fn)))
    return calls


def _tools():
    import asyncio

    return asyncio.run(server.mcp.list_tools())


def test_server_registers_exactly_the_expected_tools():
    names = [t.name for t in _tools()]
    assert len(names) == len(set(names)), "duplicate tool registration"
    assert set(names) == EXPECTED_TOOLS
    assert len(EXPECTED_READ_TOOLS) == 11 and len(EXPECTED_WRITE_TOOLS) == 30


@pytest.mark.parametrize("tool", sorted(CONFIRM_REQUIRED))
def test_destructive_tools_require_confirm(tool):
    spec = {t.name: t for t in _tools()}[tool].inputSchema
    assert CONFIRM_REQUIRED[tool] in spec["required"]


@pytest.mark.parametrize("tool", sorted(CONFIRM_OPTIONAL))
def test_wide_blast_tools_expose_optional_confirm(tool):
    spec = {t.name: t for t in _tools()}[tool].inputSchema
    assert "confirm" in spec["properties"] and "confirm" not in spec.get("required", [])


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


def _sentinel(name: str, schema: dict[str, Any], idx: int) -> Any:
    """A distinctive, schema-valid value for one parameter, so a mis-routed
    argument shows up as the wrong value under the wrong name."""
    if "anyOf" in schema:
        schema = [s for s in schema["anyOf"] if s.get("type") != "null"][0]
    t = schema.get("type")
    if t == "string":
        return f"val-{name}"
    if t == "number":
        return 100.25 + idx
    if t == "integer":
        return 50 + idx
    if t == "boolean":
        return not schema.get("default", False)
    if t == "array":
        item = schema.get("items", {})
        return [{"k": name}] if item.get("type") == "object" else [f"val-{name}"]
    if t == "object":
        return {"k": name}
    return f"val-{name}"


def _mapping_cases():
    cases = []
    for tool in _tools():
        props = tool.inputSchema.get("properties", {})
        required = set(tool.inputSchema.get("required", []))
        for param in props:
            cases.append((tool, param, required))
    return cases


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,param,required", _mapping_cases(), ids=lambda v: getattr(v, "name", None) or None
)
async def test_wrapper_passes_each_argument_under_its_own_name(stubbed, tool, param, required):
    """Send the required args plus ONE optional param, each with a distinctive
    value, and assert the client function received exactly those values under
    exactly those names -- so positional mis-routing in a wrapper (a swapped or
    shifted argument) can't hide behind a permissive stub."""
    props = tool.inputSchema["properties"]
    sent = {
        n: _sentinel(n, props[n], i)
        for i, n in enumerate(props)
        if n in required or n == param
    }
    await server.mcp.call_tool(tool.name, sent)
    got = stubbed.received[tool.name]
    for name, value in sent.items():
        assert name in got, f"{tool.name}: {name} was not passed to the client function"
        assert got[name] == value, f"{tool.name}: {name} arrived as {got[name]!r}, sent {value!r}"
    # nothing extra was invented by the wrapper, except values it left unset
    assert set(got) >= set(sent)


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
