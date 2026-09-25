"""
Exception hierarchy for monarch_client.

Kept deliberately specific: a caller (server.py's backend dispatch, or a
human running `doctor`) needs to tell "your login expired, go run `recon
login monarch`" apart from "Cloudflare blocked this request" apart from
"the query is wrong" apart from "the network is down" -- each has a
completely different fix, and collapsing them into one generic exception is
exactly what made monarchmoney-enhanced's errors useless (see monarch-mcp's
README, "When the API drifts").
"""

from __future__ import annotations

from typing import Any, Optional


class MonarchError(Exception):
    """Base class for every error monarch_client raises."""


class MonarchAuthError(MonarchError):
    """
    The session is missing, expired, or rejected by Monarch (401, or a 403
    that looks like a CSRF/login redirect rather than a bot-block).

    There is no automated fix for this -- monarch_client never attempts a
    password login (see monarch_client/auth.py). The message always names
    the human action that actually resolves it.
    """

    def __init__(self, message: str = "run: recon login monarch"):
        super().__init__(message)


class MonarchBlockedError(MonarchError):
    """
    Cloudflare (or similar bot-management) rejected the request -- distinct
    from an auth failure because no amount of re-authenticating fixes it.
    Usually a User-Agent/fingerprint mismatch with the browser that
    originally solved the challenge; see transport.py's module docstring
    for the mitigation ladder.
    """


class MonarchRateLimited(MonarchError):
    """HTTP 429. Carries the server's Retry-After header, if present."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class MonarchTransportError(MonarchError):
    """The HTTP request itself failed (DNS, timeout, connection reset, a
    non-2xx status not otherwise classified above)."""


class VendoredOperationError(MonarchError):
    """A vendored .graphql file is missing, unreadable, or fails its
    provenance/hash check (see operations/__init__.py)."""


class MonarchGraphQLError(MonarchError):
    """
    The HTTP call succeeded but the GraphQL response carried an `errors`
    array -- the server understood the request enough to respond, but
    rejected some or all of it (a classic sign of API drift: a field or
    argument the vendored query used no longer exists).

    Carries everything needed to start the field-probe ladder in
    monarch-mcp's README without re-running anything: which operation, the
    server's own error messages/locations/extension codes, where the
    vendored query text came from, and (deliberately) any partial `data`
    the server still returned -- attached for debugging only. Callers must
    not treat partial data as a usable result: for financial reads, a
    silently-incomplete answer is worse than a loud failure.
    """

    def __init__(
        self,
        op_name: str,
        errors: list[dict[str, Any]],
        *,
        vendored_path: Optional[str] = None,
        query_hash: Optional[str] = None,
        partial_data: Optional[dict[str, Any]] = None,
    ):
        self.op_name = op_name
        self.errors = errors
        self.vendored_path = vendored_path
        self.query_hash = query_hash
        self.partial_data = partial_data

        messages = "; ".join(e.get("message", str(e)) for e in errors)
        locations = [
            loc
            for e in errors
            for loc in e.get("locations", [])
            if loc
        ]
        detail = f"{op_name}: {messages}"
        if locations:
            detail += f" at {locations}"
        if vendored_path:
            detail += f" (vendored: {vendored_path}"
            if query_hash:
                detail += f", catalog {query_hash[:12]}…"
            detail += ")"
        super().__init__(detail)
