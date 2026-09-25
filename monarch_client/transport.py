"""
Plain HTTP transport for Monarch's real GraphQL API.

Sends a vendored operation's query text verbatim, with the auth material
auth.py provides, via a single reused httpx.AsyncClient. Deliberately not
sent, and why:

  - `monarch-client-version` (the app build id, e.g. "v1.0.4867"): this goes
    stale the moment the real app ships a new build, at which point sending
    it is an active lie about which client we are. The one verified-live
    call (api-recon commit 6732d76) succeeded without it.
  - `device-uuid`: lives in an `app.monarch.com`-scoped cookie
    (`monarchDeviceUUID`), which the `api.monarch.com` session export
    deliberately excludes (it's not relevant to that host). Also not needed
    by the verified-live call.

If Cloudflare starts blocking requests, the first suspect is a User-Agent
mismatch: the `cf_clearance` cookie is bound to whatever browser fingerprint
solved the original challenge, and auth.py's exported User-Agent may not
match it exactly (see handoff.py in api-recon). Try the real capture
browser's UA before reaching for `device-uuid`. If a specific GraphQL
operation rejects the call despite auth otherwise working, `device-uuid` is
the next thing to try -- but that requires an api-recon-side change to
export an app.monarch.com-scoped cookie as well, so don't build it
speculatively.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from . import auth
from .errors import (
    MonarchAuthError,
    MonarchBlockedError,
    MonarchGraphQLError,
    MonarchRateLimited,
    MonarchTransportError,
)

GRAPHQL_URL = f"https://{auth.API_HOST}/graphql"
ORIGIN = "https://app.monarch.com"
_REQUEST_TIMEOUT_SECONDS = 30.0

_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)
    return _http_client


async def aclose() -> None:
    """Close the shared HTTP client. Call on server shutdown; harmless if
    never called (a fresh client is created lazily on next use)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _build_headers(material: auth.AuthMaterial) -> dict[str, str]:
    return {
        **material.headers,  # User-Agent, X-CSRFToken (see handoff.py)
        "Origin": ORIGIN,
        "Content-Type": "application/json",
        "Accept": "*/*",
        "client-platform": "web",
    }


def _looks_like_cloudflare_block(resp: httpx.Response) -> bool:
    """A bot-management block renders an HTML challenge page; a real auth
    rejection from Monarch's own app is JSON. Distinguishing the two matters
    because only one of them is fixed by re-authenticating."""
    return "text/html" in resp.headers.get("content-type", "") and resp.status_code in (
        403,
        503,
    )


async def _post(
    op_name: str, query_text: str, variables: dict[str, Any], material: auth.AuthMaterial
) -> httpx.Response:
    client = _get_http_client()
    # httpx deprecates per-request `cookies=`; set them on the client instance
    # instead. Harmless to re-set on every call (auth.load() is either the
    # same cached material or a full refresh, never a partial delta).
    client.cookies.update(material.cookies)
    body = {"operationName": op_name, "variables": variables, "query": query_text}
    try:
        return await client.post(
            GRAPHQL_URL,
            json=body,
            headers=_build_headers(material),
        )
    except httpx.RequestError as e:
        raise MonarchTransportError(f"{op_name}: request failed ({e})") from e


async def execute(
    op_name: str, query_text: str, variables: dict[str, Any]
) -> dict[str, Any]:
    """
    POST one vendored GraphQL operation to Monarch's real API and return its
    parsed `data`.

    Raises MonarchGraphQLError on any `errors[]` in the response, even
    alongside partial `data` -- for financial reads, a silently-incomplete
    result is worse than a loud failure (the partial data is still attached
    to the exception for debugging). On a non-Cloudflare 401/403, the cached
    auth material is refreshed exactly once and the request retried exactly
    once before raising MonarchAuthError.
    """
    material = auth.load()
    resp = await _post(op_name, query_text, variables, material)

    if resp.status_code in (401, 403) and not _looks_like_cloudflare_block(resp):
        material = auth.load(force_refresh=True)
        resp = await _post(op_name, query_text, variables, material)

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        raise MonarchRateLimited(
            f"{op_name}: rate limited (429)",
            retry_after=float(retry_after) if retry_after else None,
        )

    if _looks_like_cloudflare_block(resp):
        raise MonarchBlockedError(
            f"{op_name}: blocked by Cloudflare (status {resp.status_code}) -- "
            "see this module's docstring for the mitigation ladder"
        )

    if resp.status_code in (401, 403):
        raise MonarchAuthError(
            f"{op_name}: still unauthorized after a refresh (status "
            f"{resp.status_code})\nrun: recon login monarch"
        )

    if resp.status_code >= 400:
        raise MonarchTransportError(
            f"{op_name}: HTTP {resp.status_code} from Monarch's API: "
            f"{resp.text[:500]!r}"
        )

    try:
        payload = resp.json()
    except ValueError as e:
        raise MonarchTransportError(
            f"{op_name}: response was not valid JSON: {resp.text[:500]!r}"
        ) from e

    errors = payload.get("errors")
    if errors:
        raise MonarchGraphQLError(op_name, errors, partial_data=payload.get("data"))

    return payload.get("data") or {}
