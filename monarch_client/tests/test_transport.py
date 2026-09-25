from __future__ import annotations

import httpx
import pytest

from monarch_client import auth, transport
from monarch_client.errors import (
    MonarchAuthError,
    MonarchBlockedError,
    MonarchGraphQLError,
    MonarchRateLimited,
    MonarchTransportError,
)

FAKE_MATERIAL = auth.AuthMaterial(
    cookies={"session_id": "abc", "csrftoken": "xyz"},
    headers={"User-Agent": "test-ua", "X-CSRFToken": "xyz"},
    expires_at=None,
    exported_at=None,
)


@pytest.fixture(autouse=True)
def reset_http_client():
    """Never leak a mock client into another test or the real doctor run."""
    yield
    transport._http_client = None


def _install_mock(monkeypatch, handler, *, material=FAKE_MATERIAL):
    monkeypatch.setattr(auth, "load", lambda **kwargs: material)
    transport._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )


@pytest.mark.asyncio
async def test_execute_returns_data_on_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Origin"] == "https://app.monarch.com"
        assert request.headers["client-platform"] == "web"
        assert "session_id=abc" in request.headers["cookie"]
        return httpx.Response(200, json={"data": {"me": {"id": "1"}}})

    _install_mock(monkeypatch, handler)

    data = await transport.execute("Common_GetMe", "query Common_GetMe { me { id } }", {})
    assert data == {"me": {"id": "1"}}


@pytest.mark.asyncio
async def test_graphql_errors_raise_even_with_partial_data(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {"me": None},
                "errors": [{"message": "field 'foo' not found", "locations": [{"line": 1, "column": 2}]}],
            },
        )

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchGraphQLError) as exc_info:
        await transport.execute("Common_GetMe", "query {}", {})
    assert "field 'foo' not found" in str(exc_info.value)
    assert exc_info.value.partial_data == {"me": None}


@pytest.mark.asyncio
async def test_auth_failure_refreshes_and_retries_once(monkeypatch):
    calls = {"n": 0, "loads": 0}

    def fake_load(**kwargs):
        calls["loads"] += 1
        return FAKE_MATERIAL

    monkeypatch.setattr(auth, "load", fake_load)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={"errors": [{"message": "unauthorized"}]})
        return httpx.Response(200, json={"data": {"me": {"id": "1"}}})

    transport._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    data = await transport.execute("Common_GetMe", "query {}", {})
    assert data == {"me": {"id": "1"}}
    assert calls["n"] == 2
    assert calls["loads"] == 2  # initial + forced refresh


@pytest.mark.asyncio
async def test_persistent_auth_failure_raises_auth_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"message": "unauthorized"}]})

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchAuthError, match="recon login monarch"):
        await transport.execute("Common_GetMe", "query {}", {})


@pytest.mark.asyncio
async def test_cloudflare_block_raises_blocked_not_auth_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, headers={"content-type": "text/html"}, text="<html>blocked</html>"
        )

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchBlockedError):
        await transport.execute("Common_GetMe", "query {}", {})


@pytest.mark.asyncio
async def test_rate_limit_raises_with_retry_after(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "5"})

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchRateLimited) as exc_info:
        await transport.execute("Common_GetMe", "query {}", {})
    assert exc_info.value.retry_after == 5.0


@pytest.mark.asyncio
async def test_network_error_raises_transport_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchTransportError):
        await transport.execute("Common_GetMe", "query {}", {})


@pytest.mark.asyncio
async def test_non_json_response_raises_transport_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchTransportError):
        await transport.execute("Common_GetMe", "query {}", {})
