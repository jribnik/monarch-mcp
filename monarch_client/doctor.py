"""
python -m monarch_client.doctor [--op OP_NAME] [--refresh-auth]

End-to-end health check for monarch_client, run from monarch-mcp's venv.
This is the tool to run first when a monarch_client-backed MCP tool starts
failing. Never prints cookie/header VALUES -- only cookie names and
expiry -- since this is routinely run by a human pasting terminal output
into a chat.

With no arguments: checks the `recon` binary is findable, loads (and prints
a summary of) current auth material, makes one live smoke call
(Common_GetMe), and -- if api-recon's catalog is reachable on this
machine -- flags any vendored operation whose catalog hash has drifted
since it was vendored.

With --op NAME: calls that one vendored operation with no variables (most
of the 9 read ops need real variables -- see reads.py -- so this is a raw
transport-level check, not a substitute for exercising the actual MCP
tool).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from . import MonarchClient, auth, operations
from .errors import MonarchError

def _api_recon_catalog_path() -> Path:
    """The catalog for whichever site auth.site() currently resolves to --
    NOT hardcoded to the real 'monarch' site, so running doctor against
    MONARCH_CLIENT_SITE=monarch-sandbox checks drift against the sandbox's
    own catalog, not the real account's."""
    return (
        Path.home() / "src" / "api-recon" / "artifacts" / auth.site() / "catalog" / "current.json"
    )


def _check_recon_bin() -> bool:
    try:
        path = auth._resolve_recon_bin()
    except MonarchError as e:
        print(f"[FAIL] recon binary: {e}")
        return False
    print(f"[ok]   recon binary: {path}")
    return True


def _check_auth(*, force_refresh: bool) -> Optional[auth.AuthMaterial]:
    try:
        material = auth.load(force_refresh=force_refresh)
    except MonarchError as e:
        print(f"[FAIL] auth: {e}")
        return None
    print(
        f"[ok]   auth: {len(material.cookies)} cookies for {auth.API_HOST} "
        f"-- names: {sorted(material.cookies.keys())}"
    )
    print(f"       expires_at (informational bound only): {material.expires_at}")
    print(f"       exported_at: {material.exported_at}")
    return material


async def _check_smoke_call() -> bool:
    client = MonarchClient()
    try:
        data = await client.call("Common_GetMe", {})
    except MonarchError as e:
        print(f"[FAIL] Common_GetMe smoke call: {e}")
        return False
    finally:
        await client.aclose()

    me = data.get("me") or {}
    print(f"[ok]   Common_GetMe: id={me.get('id')!r} email={me.get('email')!r}")
    return True


def _check_catalog_drift() -> None:
    catalog_path = _api_recon_catalog_path()
    if not catalog_path.exists():
        print(f"[skip] catalog drift check: {catalog_path} not reachable")
        return

    try:
        catalog = json.loads(catalog_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[skip] catalog drift check: couldn't read catalog ({e})")
        return

    catalog_ops = catalog.get("operations", {})
    for op_name, entry in sorted(operations.PROVENANCE.items()):
        catalog_entry = catalog_ops.get(op_name)
        if catalog_entry is None:
            print(f"[warn] {op_name}: not in current catalog (removed or renamed?)")
            continue
        live_hash = catalog_entry.get("query_hash")
        vendored_hash = entry.get("catalog_query_hash")
        if live_hash == vendored_hash:
            print(f"[ok]   {op_name}: catalog hash unchanged since vendoring")
        else:
            vendored_short = vendored_hash[:12] if vendored_hash else None
            live_short = live_hash[:12] if live_hash else None
            print(
                f"[warn] {op_name}: catalog hash changed since vendoring "
                f"({vendored_short}… -> {live_short}…) -- consider "
                "re-running the vendoring checklist (operations/README.md)"
            )


async def _run_op(op_name: str) -> None:
    client = MonarchClient()
    try:
        data = await client.call(op_name, {})
    except MonarchError as e:
        print(f"[FAIL] {op_name}: {e}")
        sys.exit(1)
    finally:
        await client.aclose()
    raw = json.dumps(data)
    print(f"[ok]   {op_name}: {len(raw)} bytes of JSON")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--op", help="Execute one vendored op with no variables; print response size."
    )
    parser.add_argument(
        "--refresh-auth",
        action="store_true",
        help="Force a fresh `recon export-session` before checking.",
    )
    args = parser.parse_args()

    label = "REAL account" if auth.site() == auth.DEFAULT_SITE else "sandbox/test account"
    print(f"Site: {auth.site()!r} ({label}) -- override with MONARCH_CLIENT_SITE")

    if args.op:
        asyncio.run(_run_op(args.op))
        return

    ok = _check_recon_bin()
    material = _check_auth(force_refresh=args.refresh_auth)
    ok = ok and material is not None
    if material is not None:
        ok = asyncio.run(_check_smoke_call()) and ok
    _check_catalog_drift()

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
