"""
Auth material for monarch_client -- a pure reader of a static file, exactly
like operations/ is a pure reader of static .graphql files. This module
contains NO knowledge that api-recon or the `recon` CLI exist: no
subprocess calls, no binary resolution, nothing. That's deliberate --
api-recon is a tool that PRODUCES an artifact (here, a small JSON file of
cookies/headers); monarch_client CONSUMES that artifact. It must not become
a runtime dependency of a long-running MCP server process the way an
earlier version of this module made it: that version auto-refreshed by
shelling out to `recon export-session` whenever its cache aged past 12h,
which meant the server needed a working api-recon checkout, venv, and
`recon` binary indefinitely just to keep serving reads.

Producing/refreshing the file this module reads is an explicit, separate,
human-or-cron-triggered action, run from api-recon:

    recon export-session <site> --api-host api.monarch.com --out <path>

(see cache_path() for the exact expected path) -- typically once, right
after `recon login <site>` in a real browser. That login is the one real
credential-handling event in this whole system; nothing here re-implements
it or ever will. Auto-refreshing on staleness never actually helped much
anyway: `recon export-session` only re-reads api-recon's already-saved
session, so refreshing here was only ever useful in the narrow window
right after a human had already re-run `recon login` -- a rare,
human-triggered event, not something worth an inline subprocess call on
monarch_client's hot path.

Which ACCOUNT this reads for is controlled by `MONARCH_CLIENT_SITE`
(default: "monarch", the real account), not hardcoded. The mechanism exists
so a non-real account can be pointed at for testing: api-recon registered a
"monarch-sandbox" site backed by a dedicated, disposable Monarch account.
That Monarch ACCOUNT has since been DELETED, but api-recon still registers a
`monarch-sandbox` adapter site name (src/recon/adapters/__init__.py), so the
name still resolves there while no live account sits behind it; any other
site name only works if you have a real session for it in api-recon. Each site gets its own
cache file, so a real-account file and a test-account file never collide or
overwrite each other. THIS DISTINCTION MATTERS: an earlier mistake in this
project ran a live `create_tag` mutation against the real account while
intending to test against the sandbox, because the code had no site
awareness at all and silently reused a warm real-account auth cache.
`load()` prints which site it resolved to (once per process) specifically so
that mistake is visible before a mutation runs, not after.

The exported file holds cookies plus a few headers (User-Agent, CSRF token) --
there is no bearer/access token; Monarch's web API authenticates by session
cookie.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .errors import MonarchAuthError

DEFAULT_SITE = "monarch"
API_HOST = "api.monarch.com"

# Default state directory for this machine's Monarch-related state. Owned
# here rather than imported from monarch-mcp (this package has zero
# monarch-mcp imports, so it can be lifted into its own repo later with a
# `git mv`).
STATE_DIR = Path(os.environ.get("MONARCH_CLIENT_HOME", Path.home() / ".monarch-mcp"))

# Set to the last-announced site once load() has printed it, so a
# long-running process (an MCP server) sees the notice exactly once at
# startup rather than on every tool call, but a MID-PROCESS change (e.g. a
# test script that flips MONARCH_CLIENT_SITE) still gets re-announced.
_announced_site: Optional[str] = None


@dataclass(frozen=True)
class AuthMaterial:
    cookies: dict[str, str]
    headers: dict[str, str]
    expires_at: Optional[datetime]
    exported_at: Optional[datetime]


def site() -> str:
    """Which api-recon site this client reads auth material for. Override
    with MONARCH_CLIENT_SITE (any site api-recon has a session for; the old
    "monarch-sandbox" Monarch account no longer exists, though api-recon still
    registers that site name). Defaults to "monarch", the
    real account."""
    return os.environ.get("MONARCH_CLIENT_SITE", DEFAULT_SITE)


def cache_path() -> Path:
    """Auth file for the CURRENT site (see site()) -- the exact path
    `recon export-session <site> --api-host api.monarch.com --out <this
    path>` should be pointed at. Distinct per site so a real-account file
    and a test-account file never collide."""
    return STATE_DIR / f"api-auth.{site()}.json"


def _export_command(current_site: str) -> str:
    return (
        f"recon export-session {current_site} --api-host {API_HOST} "
        f"--out {cache_path()}"
    )


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError) as e:
        raise MonarchAuthError(f"unparseable timestamp {value!r}") from e
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_cache() -> AuthMaterial:
    path = cache_path()
    current_site = site()
    export_cmd = _export_command(current_site)

    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as e:
        raise MonarchAuthError(
            f"no auth file for site {current_site!r} at {path}\n"
            f"run: {export_cmd}\n"
            f"(after `recon login {current_site}` first, if you haven't logged in yet)"
        ) from e
    except (OSError, json.JSONDecodeError) as e:
        raise MonarchAuthError(
            f"auth file at {path} is unreadable ({e})\nrun: {export_cmd}"
        ) from e

    try:
        return AuthMaterial(
            cookies=dict(payload["cookies"]),
            headers=dict(payload["headers"]),
            expires_at=_parse_dt(payload.get("expires_at")),
            exported_at=_parse_dt(payload.get("exported_at")),
        )
    except (KeyError, TypeError, ValueError, MonarchAuthError) as e:
        # KeyError: a required field is missing; TypeError/ValueError: the
        # JSON has the wrong shape (e.g. cookies is a list); MonarchAuthError:
        # _parse_dt rejected a timestamp. All mean "re-export the file".
        what = f"missing {e}" if isinstance(e, KeyError) else str(e)
        raise MonarchAuthError(
            f"auth file at {path} is malformed ({what}) -- delete it "
            f"and re-run\nrun: {export_cmd}"
        ) from e


def _announce_site() -> None:
    """Print which site this process is about to authenticate against,
    once per site per process -- see module docstring for why this exists
    (a prior mistake ran a live mutation against the real account while a
    test script intended to hit a test account, with nothing making that
    visible beforehand)."""
    global _announced_site
    current_site = site()
    if _announced_site == current_site:
        return
    _announced_site = current_site
    label = "REAL account" if current_site == DEFAULT_SITE else "NON-DEFAULT site (test account)"
    print(f"monarch_client: authenticating against site {current_site!r} ({label})", file=sys.stderr)


def load() -> AuthMaterial:
    """
    Return current HTTP auth material for api.monarch.com by reading the
    exported auth file for the current site (see site()). Pure file read --
    never shells out to `recon` or anything else. Raises MonarchAuthError,
    naming the exact `recon export-session` command to run, if no usable
    file exists.
    """
    _announce_site()
    return _read_cache()
