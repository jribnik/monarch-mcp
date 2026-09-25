"""
backend.py lives at the monarch-mcp repo root (not inside monarch_client --
it's server.py's own dispatch mechanism, not part of the client package),
but its tests live here since this is where the venv's pytest config
already finds monarch_client's tests. conftest.py adds the repo root to
sys.path so `import backend` works.
"""

from __future__ import annotations

import json

import pytest

import backend


@pytest.fixture(autouse=True)
def isolated_parity_log(tmp_path, monkeypatch):
    log_path = tmp_path / "parity.log"
    monkeypatch.setattr(backend, "STATE_DIR", tmp_path)
    monkeypatch.setattr(backend, "PARITY_LOG_PATH", log_path)
    return log_path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("MONARCH_MCP_BACKEND", "MONARCH_MCP_CLIENT_TOOLS", "MONARCH_MCP_LIBRARY_TOOLS"):
        monkeypatch.delenv(var, raising=False)


class TestResolve:
    def test_defaults_to_library(self):
        assert backend.resolve("get_tags") == "library"

    def test_global_backend_override(self, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_BACKEND", "client")
        assert backend.resolve("get_tags") == "client"

    def test_per_tool_client_override(self, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_CLIENT_TOOLS", "get_tags,get_categories")
        assert backend.resolve("get_tags") == "client"
        assert backend.resolve("get_categories") == "client"
        assert backend.resolve("list_accounts") == "library"

    def test_library_override_wins_over_client_override(self, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_BACKEND", "client")
        monkeypatch.setenv("MONARCH_MCP_LIBRARY_TOOLS", "get_tags")
        assert backend.resolve("get_tags") == "library"
        assert backend.resolve("list_accounts") == "client"

    def test_library_override_wins_when_tool_named_in_both(self, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_CLIENT_TOOLS", "get_tags")
        monkeypatch.setenv("MONARCH_MCP_LIBRARY_TOOLS", "get_tags")
        assert backend.resolve("get_tags") == "library"


class TestDispatch:
    @pytest.mark.asyncio
    async def test_library_backend_calls_only_library(self):
        calls = []

        async def lib():
            calls.append("library")
            return {"a": 1}

        async def client():
            calls.append("client")
            return {"a": 1}

        result = await backend.dispatch("t", library_call=lib, client_call=client)
        assert result == {"a": 1}
        assert calls == ["library"]

    @pytest.mark.asyncio
    async def test_client_backend_calls_only_client(self, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_BACKEND", "client")
        calls = []

        async def lib():
            calls.append("library")
            return {"a": 1}

        async def client():
            calls.append("client")
            return {"a": 2}

        result = await backend.dispatch("t", library_call=lib, client_call=client)
        assert result == {"a": 2}
        assert calls == ["client"]

    @pytest.mark.asyncio
    async def test_dual_calls_both_and_returns_library_result(self, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_BACKEND", "dual")
        calls = []

        async def lib():
            calls.append("library")
            return {"a": 1}

        async def client():
            calls.append("client")
            return {"a": 2}

        result = await backend.dispatch("t", library_call=lib, client_call=client)
        assert result == {"a": 1}
        assert set(calls) == {"library", "client"}

    @pytest.mark.asyncio
    async def test_dual_logs_when_shapes_differ(self, isolated_parity_log):
        import os

        os.environ["MONARCH_MCP_BACKEND"] = "dual"
        try:
            async def lib():
                return {"a": 1, "b": "extra"}

            async def client():
                return {"a": 1}

            await backend.dispatch("my_tool", library_call=lib, client_call=client)
        finally:
            del os.environ["MONARCH_MCP_BACKEND"]

        assert isolated_parity_log.exists()
        entry = json.loads(isolated_parity_log.read_text().strip())
        assert entry["tool"] == "my_tool"
        assert entry["library_shape"] == {"a": "int", "b": "str"}
        assert entry["client_shape"] == {"a": "int"}
        # Never log actual values.
        assert "1" not in json.dumps(entry["library_shape"])

    @pytest.mark.asyncio
    async def test_dual_does_not_log_when_shapes_match(self, isolated_parity_log, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_BACKEND", "dual")

        async def lib():
            return {"a": 1}

        async def client():
            return {"a": 999}  # different value, same shape

        await backend.dispatch("t", library_call=lib, client_call=client)
        assert not isolated_parity_log.exists()

    @pytest.mark.asyncio
    async def test_mutating_tool_rejects_global_dual(self, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_BACKEND", "dual")
        calls = []

        async def lib():
            calls.append("library")
            return {}

        async def client():
            calls.append("client")
            return {}

        with pytest.raises(ValueError, match="dual mode is read-only"):
            await backend.dispatch(
                "create_tag", library_call=lib, client_call=client, mutating=True
            )
        assert calls == []  # neither backend was called

    @pytest.mark.asyncio
    async def test_mutating_tool_with_explicit_override_is_fine(self, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_BACKEND", "dual")
        monkeypatch.setenv("MONARCH_MCP_CLIENT_TOOLS", "create_tag")

        async def lib():
            return {"a": "library"}

        async def client():
            return {"a": "client"}

        result = await backend.dispatch(
            "create_tag", library_call=lib, client_call=client, mutating=True
        )
        assert result == {"a": "client"}

    @pytest.mark.asyncio
    async def test_non_mutating_tool_still_allows_dual(self, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_BACKEND", "dual")

        async def lib():
            return {"a": 1}

        async def client():
            return {"a": 1}

        # Should not raise -- mutating defaults to False.
        result = await backend.dispatch("get_tags", library_call=lib, client_call=client)
        assert result == {"a": 1}

    @pytest.mark.asyncio
    async def test_unknown_backend_raises(self, monkeypatch):
        monkeypatch.setenv("MONARCH_MCP_BACKEND", "bogus")

        async def lib():
            return {}

        async def client():
            return {}

        with pytest.raises(ValueError, match="unknown backend"):
            await backend.dispatch("t", library_call=lib, client_call=client)


class TestShape:
    def test_scalars(self):
        assert backend._shape(1) == "int"
        assert backend._shape("x") == "str"
        assert backend._shape(None) == "NoneType"

    def test_dict_recurses_on_keys_not_values(self):
        assert backend._shape({"amount": -42.5, "id": "abc"}) == {
            "amount": "float",
            "id": "str",
        }

    def test_list_uses_first_element_only(self):
        assert backend._shape([{"id": "a"}, {"id": "b"}]) == [{"id": "str"}]

    def test_empty_list(self):
        assert backend._shape([]) == []

    def test_depth_limit(self):
        nested = {"a": {"b": {"c": {"d": "leaf"}}}}
        assert backend._shape(nested, max_depth=1) == {"a": {"b": "..."}}
