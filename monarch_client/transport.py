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

import time
from email.utils import parsedate_to_datetime
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


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Retry-After is either delta-seconds or an HTTP-date (RFC 9110). Return
    seconds to wait, or None if absent/unparseable -- a malformed header must
    never turn a clean 429 into a raw ValueError."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


async def _post(
    op_name: str, query_text: str, variables: dict[str, Any], material: auth.AuthMaterial
) -> httpx.Response:
    client = _get_http_client()
    # The Cookie header is built from THIS call's auth material and the
    # shared client's jar is cleared afterwards. Using the jar instead (as an
    # earlier version did) leaked cookies across a mid-process
    # MONARCH_CLIENT_SITE switch (site A's cookies kept riding along on site
    # B's requests) and let a server Set-Cookie silently replace the
    # exported session value on later calls. An explicit Cookie header takes
    # precedence over the jar in httpx/urllib.
    # Consequence: ONLY the exported cookies are ever sent. A cookie the
    # server sets later (e.g. a refreshed Cloudflare `__cf_bm`) is dropped
    # with the jar and not carried to the next call, so a cookie missing from
    # the export stays missing. Run `python -m monarch_client.doctor` once
    # after deploying this behavior (and after any re-export) to confirm the
    # exported set alone still gets a smoke call through.
    headers = _build_headers(material)
    headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in material.cookies.items())
    body = {"operationName": op_name, "variables": variables, "query": query_text}
    try:
        return await client.post(GRAPHQL_URL, json=body, headers=headers)
    except httpx.RequestError as e:
        raise MonarchTransportError(f"{op_name}: request failed ({e})") from e
    finally:
        client.cookies.clear()


async def execute(
    op_name: str,
    query_text: str,
    variables: dict[str, Any],
    *,
    vendored_path: Optional[str] = None,
    query_hash: Optional[str] = None,
) -> dict[str, Any]:
    """
    POST one vendored GraphQL operation to Monarch's real API and return its
    parsed `data`.

    Raises MonarchGraphQLError on any `errors[]` in the response, even
    alongside partial `data` -- for financial reads, a silently-incomplete
    result is worse than a loud failure (the partial data is still attached
    to the exception for debugging). On a non-Cloudflare 401/403, raises
    MonarchAuthError immediately -- auth.py is a pure file reader with no
    way to refresh itself, so retrying with the same material would just
    reproduce the same failure; see auth.py's module docstring for the
    actual fix (a human re-exports the session file).

    `vendored_path` / `query_hash` are optional provenance (where the query
    text came from, which catalog hash it was vendored against); they are
    attached to any MonarchGraphQLError so the field-probe ladder can start
    without re-running anything.
    """
    material = auth.load()
    resp = await _post(op_name, query_text, variables, material)

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        raise MonarchRateLimited(
            f"{op_name}: rate limited (429)",
            retry_after=_parse_retry_after(retry_after),
        )

    if _looks_like_cloudflare_block(resp):
        raise MonarchBlockedError(
            f"{op_name}: blocked by Cloudflare (status {resp.status_code}) -- "
            "see this module's docstring for the mitigation ladder"
        )

    if resp.status_code in (401, 403):
        raise MonarchAuthError(
            f"{op_name}: unauthorized (status {resp.status_code}) -- the "
            f"auth file for site {auth.site()!r} is stale or invalid\n"
            f"run: recon export-session {auth.site()} --api-host "
            f"{auth.API_HOST} --out {auth.cache_path()}"
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

    if not isinstance(payload, dict):
        raise MonarchTransportError(
            f"{op_name}: response JSON was {type(payload).__name__}, expected "
            f"an object: {resp.text[:500]!r}"
        )

    errors = payload.get("errors")
    if errors:
        raise MonarchGraphQLError(
            op_name,
            errors,
            vendored_path=vendored_path,
            query_hash=query_hash,
            partial_data=payload.get("data"),
        )

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise MonarchTransportError(
            f"{op_name}: response `data` was {type(data).__name__}, expected an object"
        )
    return data
