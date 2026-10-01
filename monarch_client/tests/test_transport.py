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
    monkeypatch.setattr(auth, "load", lambda: material)
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
async def test_auth_failure_raises_immediately_without_retry(monkeypatch):
    """auth.py is a pure file reader with no way to refresh itself, so a
    401/403 must raise on the first call -- retrying would just re-read
    the identical file and reproduce the identical failure."""
    calls = {"n": 0, "loads": 0}

    def fake_load():
        calls["loads"] += 1
        return FAKE_MATERIAL

    monkeypatch.setattr(auth, "load", fake_load)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"errors": [{"message": "unauthorized"}]})

    transport._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(MonarchAuthError, match="recon export-session"):
        await transport.execute("Common_GetMe", "query {}", {})
    assert calls["n"] == 1
    assert calls["loads"] == 1


@pytest.mark.asyncio
async def test_persistent_auth_failure_raises_auth_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"message": "unauthorized"}]})

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchAuthError, match="recon export-session monarch"):
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


@pytest.mark.asyncio
async def test_graphql_error_carries_vendored_provenance(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "boom"}], "data": None})

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchGraphQLError) as exc_info:
        await transport.execute(
            "Common_GetMe",
            "query {}",
            {},
            vendored_path="monarch_client/operations/Common_GetMe.graphql",
            query_hash="abcdef1234567890",
        )
    err = exc_info.value
    assert err.vendored_path == "monarch_client/operations/Common_GetMe.graphql"
    assert err.query_hash == "abcdef1234567890"
    assert "abcdef123456" in str(err)


@pytest.mark.asyncio
async def test_client_call_passes_provenance_to_graphql_error(monkeypatch):
    from monarch_client import MonarchClient, operations

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "boom"}], "data": None})

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchGraphQLError) as exc_info:
        await MonarchClient().call("Common_GetMe", {})
    err = exc_info.value
    assert err.vendored_path == "monarch_client/operations/Common_GetMe.graphql"
    assert err.query_hash == operations.PROVENANCE["Common_GetMe"].get(
        "catalog_query_hash"
    )


@pytest.mark.asyncio
async def test_rate_limit_retry_after_http_date(monkeypatch):
    """Retry-After may be an HTTP-date; that must not raise a raw ValueError."""
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    when = datetime.now(timezone.utc) + timedelta(seconds=120)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": format_datetime(when, usegmt=True)})

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchRateLimited) as exc_info:
        await transport.execute("Common_GetMe", "query {}", {})
    assert 60 < exc_info.value.retry_after <= 120


@pytest.mark.asyncio
async def test_rate_limit_garbage_retry_after_is_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "soon-ish"})

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchRateLimited) as exc_info:
        await transport.execute("Common_GetMe", "query {}", {})
    assert exc_info.value.retry_after is None


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["[1, 2]", '"str"', "5", "null"])
async def test_non_object_json_body_raises_transport_error(monkeypatch, body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchTransportError, match="expected an object"):
        await transport.execute("Common_GetMe", "query {}", {})


@pytest.mark.asyncio
async def test_non_object_data_raises_transport_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [1]})

    _install_mock(monkeypatch, handler)

    with pytest.raises(MonarchTransportError, match="`data`"):
        await transport.execute("Common_GetMe", "query {}", {})


@pytest.mark.asyncio
async def test_cookies_do_not_leak_across_calls_or_sites(monkeypatch):
    """Each request sends exactly its own call's auth cookies: nothing from a
    previous site's material, and a server Set-Cookie must not replace the
    exported session value on the next call."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie", ""))
        return httpx.Response(
            200, json={"data": {}}, headers={"Set-Cookie": "session_id=from-server; Path=/"}
        )

    material_a = auth.AuthMaterial(
        cookies={"session_id": "A", "only_a": "1"},
        headers={"User-Agent": "ua"}, expires_at=None, exported_at=None,
    )
    material_b = auth.AuthMaterial(
        cookies={"session_id": "B"},
        headers={"User-Agent": "ua"}, expires_at=None, exported_at=None,
    )
    transport._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    for material in (material_a, material_a, material_b):
        monkeypatch.setattr(auth, "load", lambda m=material: m)
        await transport.execute("Common_GetMe", "query {}", {})

    assert seen[0] == "session_id=A; only_a=1"
    assert seen[1] == "session_id=A; only_a=1"  # not "from-server"
    assert seen[2] == "session_id=B"  # no only_a from the previous site
