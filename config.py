"""
Shared configuration for the Monarch MCP server.

Security posture (see README):
  - The Monarch session is persisted ENCRYPTED with a strong, randomly generated
    key -- never the library's weak $HOME-derived default, and never plain JSON.
  - The session file and the key file live OUTSIDE this repo, under
    ~/.monarch-mcp, each with 0600 permissions.
  - Credentials (email/password/MFA) are never stored; only the resulting
    Monarch auth token lives (encrypted) in the session file.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

# Everything lives outside the repo, in the user's home, locked to the user.
STATE_DIR = Path(os.environ.get("MONARCH_MCP_HOME", Path.home() / ".monarch-mcp"))
SESSION_FILE = STATE_DIR / "session.mmsession"
KEY_FILE = STATE_DIR / "session.key"


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Tighten in case it pre-existed with looser perms.
    os.chmod(STATE_DIR, 0o700)


def load_or_create_key() -> str:
    """
    Return the strong session-encryption password.

    Precedence:
      1. MONARCH_SESSION_KEY env var (user-supplied), if set.
      2. A random 256-bit key persisted at ~/.monarch-mcp/session.key (0600),
         generated on first use.

    This replaces the library's default password (derived from $HOME), which is
    guessable by anyone who can read the session file.
    """
    env_key = os.environ.get("MONARCH_SESSION_KEY")
    if env_key:
        return env_key

    _ensure_state_dir()
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()

    key = secrets.token_urlsafe(32)
    # Create with 0600 from the start (umask-independent).
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key


def session_exists() -> bool:
    return SESSION_FILE.exists()


# --------------------------------------------------------------------------- #
# Domain migration
#
# Monarch moved its API from api.monarchmoney.com -> api.monarch.com (and the web
# app to app.monarch.com). The pinned/audited library still hardcodes the old
# host, which now 301-redirects the login POST; aiohttp follows the redirect as a
# GET and the new host answers 405 -> "Login failed with status 405".
#
# We point the client directly at the new hosts. These are Monarch's own
# domains; this introduces no new third-party endpoints and stays within the
# audited network surface.
# --------------------------------------------------------------------------- #

API_BASE_URL = "https://api.monarch.com"
APP_ORIGIN = "https://app.monarch.com"


def _patch_gql_connector() -> None:
    """
    Make the library's GraphQL transport work with modern gql.

    monarchmoney-enhanced constructs `AIOHTTPTransport(..., connector=connector)`,
    but gql's AIOHTTPTransport takes a custom connector via `client_session_args`,
    not a top-level `connector` kwarg -- so every GraphQL query raises
    "unexpected keyword argument 'connector'". We rebind the transport class (only
    in the library's modules) to a subclass that accepts `connector` and forwards
    it correctly. No-ops if a future gql supports `connector` natively.
    """
    import inspect

    from gql.transport.aiohttp import AIOHTTPTransport as _Base

    if "connector" in inspect.signature(_Base.__init__).parameters:
        return  # native support; nothing to do

    class _CompatTransport(_Base):  # type: ignore[misc, valid-type]
        def __init__(self, *args, connector=None, client_session_args=None, **kwargs):
            csa = dict(client_session_args or {})
            if connector is not None:
                csa["connector"] = connector
            # gql's AIOHTTPTransport defaults to NOT verifying TLS certs. We carry
            # a Monarch auth token on every query, so force verification on.
            kwargs.setdefault("ssl", True)
            super().__init__(*args, client_session_args=csa or None, **kwargs)

    import monarchmoney.monarchmoney as _mm
    import monarchmoney.services.graphql_client as _gc

    _gc.AIOHTTPTransport = _CompatTransport
    _mm.AIOHTTPTransport = _CompatTransport


def new_client(session_password: str):
    """Construct a MonarchMoney client pointed at the current Monarch domains."""
    from monarchmoney import MonarchMoney
    from monarchmoney.monarchmoney import MonarchMoneyEndpoints

    _patch_gql_connector()
    MonarchMoneyEndpoints.BASE_URL = API_BASE_URL

    mm = MonarchMoney(
        session_file=str(SESSION_FILE),
        session_password=session_password,
        use_encryption=True,  # strong key, never plain JSON
    )
    mm._headers["Origin"] = APP_ORIGIN
    return mm
