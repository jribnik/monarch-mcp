"""
Auth material for monarch_client, sourced from api-recon's encrypted browser
session -- never a password login of our own.

api-recon (~/src/api-recon) owns the one real login: a human completes it in
a real, headful browser (`recon login monarch`), including MFA/CAPTCHA, and
the resulting session is encrypted at rest under ~/.api-recon. This module
never re-implements that -- it only asks api-recon, via its `recon
export-session` CLI command, to turn that saved session into plain HTTP
auth material (cookies + a couple of headers) for api.monarch.com, and
caches the result here so we're not shelling out on every call.

This is deliberately the ONLY session api-recon and monarch-mcp share. It
does not create a second, divergent session system -- see the module
docstring in monarch-mcp's own config.py/auth.py, which this is meant to
eventually replace entirely.

What "refresh" does and doesn't mean: `recon export-session` re-reads
api-recon's already-saved session file; it does not renegotiate anything
with Monarch. So refreshing here only helps if that underlying session
changed (i.e. after a `recon login monarch`). If Monarch has actually
invalidated the session, no amount of refreshing here fixes it -- only a
human re-login does, which is why MonarchAuthError's message always says so.

Which ACCOUNT this hits is controlled by `MONARCH_CLIENT_SITE` (default:
"monarch", the real account), not hardcoded -- api-recon also registers a
"monarch-sandbox" site (see adapters/__init__.py there) backed by a
dedicated, disposable Monarch account with no real financial data, meant
for exactly this: verifying a new write operation actually works before
trusting it against the real one. Each site gets its own cache file, so a
real-account session and a sandbox session never collide or overwrite each
other. THIS DISTINCTION MATTERS: an earlier mistake in this project ran a
live `create_tag` mutation against the real account while intending to
test against the sandbox, because the code had no sandbox awareness at all
and silently reused a warm real-account auth cache. `load()` prints which
site it resolved to (once per process) specifically so that mistake is
visible before a mutation runs, not after.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .errors import MonarchAuthError

DEFAULT_SITE = "monarch"
API_HOST = "api.monarch.com"

# Same default state directory monarch-mcp's own config.py uses -- not
# imported from there (this package has zero monarch-mcp imports, so it can
# be lifted into its own repo later with a `git mv`), just the same
# sensible default location for this machine's Monarch-related state.
STATE_DIR = Path(os.environ.get("MONARCH_CLIENT_HOME", Path.home() / ".monarch-mcp"))

# A freshness heuristic, not a real expiry check -- see module docstring.
# 12h keeps a long-running MCP server process from re-shelling out on every
# single tool call while still picking up a same-day `recon login` fairly
# quickly.
AUTH_CACHE_MAX_AGE_SECONDS = 12 * 60 * 60

_RECON_EXPORT_TIMEOUT_SECONDS = 30

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
    """Which api-recon site this client authenticates against. Override
    with MONARCH_CLIENT_SITE -- e.g. "monarch-sandbox" while verifying a
    write operation. Defaults to "monarch", the real account."""
    return os.environ.get("MONARCH_CLIENT_SITE", DEFAULT_SITE)


def cache_path() -> Path:
    """Auth cache file for the CURRENT site (see site()). Distinct per
    site so a real-account cache and a sandbox cache never collide."""
    return STATE_DIR / f"api-auth.{site()}.json"


def _resolve_recon_bin() -> str:
    """
    Find the `recon` CLI, in order: an explicit override, PATH, then the
    known dev location for this machine. Raising here (rather than letting
    a bare FileNotFoundError surface from subprocess) means the error
    message can point at all three fixes at once.
    """
    env_bin = os.environ.get("MONARCH_RECON_BIN")
    if env_bin:
        return env_bin

    which = shutil.which("recon")
    if which:
        return which

    fallback = Path.home() / "src" / "api-recon" / ".venv" / "bin" / "recon"
    if fallback.exists():
        return str(fallback)

    raise MonarchAuthError(
        "could not find the `recon` CLI -- set MONARCH_RECON_BIN, put it on "
        "PATH, or confirm ~/src/api-recon/.venv/bin/recon exists\n"
        f"run: recon login {site()}"
    )


def _run_recon_export() -> None:
    current_site = site()
    recon_bin = _resolve_recon_bin()
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)

    out_path = cache_path()
    try:
        result = subprocess.run(
            [
                recon_bin,
                "export-session",
                current_site,
                "--api-host",
                API_HOST,
                "--out",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=_RECON_EXPORT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise MonarchAuthError(
            f"`recon export-session` timed out after "
            f"{_RECON_EXPORT_TIMEOUT_SECONDS}s\nrun: recon login {current_site}"
        ) from e
    except OSError as e:
        raise MonarchAuthError(
            f"failed to run `recon export-session` ({e})\n"
            f"run: recon login {current_site}"
        ) from e

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise MonarchAuthError(
            f"`recon export-session` failed (exit {result.returncode}): "
            f"{stderr}\nrun: recon login {current_site}"
        )

    # export-session already writes 0600; enforce it regardless of umask.
    if out_path.exists():
        os.chmod(out_path, 0o600)


def _cache_is_fresh() -> bool:
    path = cache_path()
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < AUTH_CACHE_MAX_AGE_SECONDS


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_cache() -> AuthMaterial:
    path = cache_path()
    current_site = site()
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise MonarchAuthError(
            f"auth cache at {path} is missing or unreadable ({e})\n"
            f"run: recon login {current_site}"
        ) from e

    try:
        return AuthMaterial(
            cookies=dict(payload["cookies"]),
            headers=dict(payload["headers"]),
            expires_at=_parse_dt(payload.get("expires_at")),
            exported_at=_parse_dt(payload.get("exported_at")),
        )
    except KeyError as e:
        raise MonarchAuthError(
            f"auth cache at {path} is malformed (missing {e}) -- "
            f"delete it and retry\nrun: recon login {current_site}"
        ) from e


def _announce_site() -> None:
    """Print which site this process is about to authenticate against,
    once per site per process -- see module docstring for why this exists
    (a prior mistake ran a live mutation against the real account while a
    test script intended to hit the sandbox, with nothing making that
    visible beforehand)."""
    global _announced_site
    current_site = site()
    if _announced_site == current_site:
        return
    _announced_site = current_site
    label = "REAL account" if current_site == DEFAULT_SITE else "sandbox/test account"
    print(f"monarch_client: authenticating against site {current_site!r} ({label})", file=sys.stderr)


def load(*, force_refresh: bool = False) -> AuthMaterial:
    """
    Return current HTTP auth material for api.monarch.com, refreshing from
    api-recon's saved session if the cache is missing, stale, or
    `force_refresh` is set. Raises MonarchAuthError (naming the human fix)
    if no usable session can be produced at all. Which account this
    targets is controlled by MONARCH_CLIENT_SITE -- see site().
    """
    _announce_site()

    if not force_refresh and _cache_is_fresh():
        try:
            return _read_cache()
        except MonarchAuthError:
            pass  # cache is corrupt -- fall through and regenerate it

    _run_recon_export()
    return _read_cache()
