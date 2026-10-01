"""
python -m monarch_client.doctor [--op OP_NAME]

End-to-end health check for monarch_client, run from monarch-mcp's venv.
This is the tool to run first when a monarch_client-backed MCP tool starts
failing. Never prints cookie/header VALUES -- only cookie names and
expiry -- since this is routinely run by a human pasting terminal output
into a chat.

monarch_client itself never shells out to `recon` (see auth.py's module
docstring) -- so this doctor doesn't check for a `recon` binary either.
If the auth check below fails, it prints the exact `recon export-session`
command to run from api-recon to fix it.

With no arguments: reads (and prints a summary of) the current auth file,
makes one live smoke call (Common_GetMe), and reports whether the write
gate is open. Catalog drift is NOT checked here (removed 2026-09-29, along
with this module's hardcoded ~/src/api-recon path): api-recon's drift-watch
consumes `python -m monarch_client.operations --provenance-json` and does
the comparison against its own catalog.

With --op NAME: calls that one vendored operation with no variables (most
of the read ops need real variables -- see reads.py -- so this is a raw
transport-level check, not a substitute for exercising the actual MCP
tool).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Optional

from . import MonarchClient, auth, operations, writes
from .errors import MonarchError

def _print_write_gate() -> None:
    if writes.writes_status():
        print(f"[info] write gate: OPEN ({writes.WRITES_ENV}=1) -- write tools will execute")
    else:
        print(
            f"[info] write gate: CLOSED -- write tools refuse until "
            f"{writes.WRITES_ENV}=1 is set (reads and dry-run previews unaffected)"
        )


def _check_auth() -> Optional[auth.AuthMaterial]:
    try:
        material = auth.load()
    except MonarchError as e:
        print(f"[FAIL] auth: {e}")
        return None
    print(
        f"[ok]   auth: {len(material.cookies)} cookies for {auth.API_HOST} "
        f"-- names: {sorted(material.cookies.keys())}"
    )
    print(f"       expires_at (informational bound only): {material.expires_at}")
    print(f"       exported_at: {material.exported_at}")

    path = auth.cache_path()
    if path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        print(f"       file age: {age_hours:.1f}h ({path})")
        if age_hours > 24 * 30:
            print(
                "       [info] this file hasn't been refreshed in 30+ days -- "
                "if auth starts failing, re-run: "
                f"recon export-session {auth.site()} --api-host {auth.API_HOST} "
                f"--out {path}"
            )
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
    # Deliberately NOT the email: this output is routinely pasted into chat.
    print(f"[ok]   Common_GetMe: id={me.get('id')!r}")
    return True


def _is_unsafe_op(op_name: str) -> bool:
    """True for ops `--op` must not send: any vendored mutation (checked
    from the query text), plus anything PROVENANCE marks walk_reachable=False
    (a few queries the read-only walk never visits). `--op` sends no
    variables and passes through none of writes.py's safety gates, so a
    mutation here would run unguarded."""
    if operations.is_mutation(op_name):
        return True
    entry = operations.PROVENANCE.get(op_name)
    return bool(entry) and not entry.get("walk_reachable", True)


async def _run_op(op_name: str) -> None:
    if _is_unsafe_op(op_name):
        print(
            f"[FAIL] {op_name}: refusing -- this op is a mutation, or is "
            "marked walk_reachable=False in PROVENANCE (a query the "
            "read-only walk never visits), and --op sends no variables and "
            "passes through none of writes.py's safety gates. Use the real "
            "MCP tool to exercise it."
        )
        sys.exit(1)

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
    args = parser.parse_args()

    label = "REAL account" if auth.site() == auth.DEFAULT_SITE else "NON-DEFAULT site (test account)"
    print(f"Site: {auth.site()!r} ({label}) -- override with MONARCH_CLIENT_SITE")

    if args.op:
        asyncio.run(_run_op(args.op))
        return

    _print_write_gate()
    material = _check_auth()
    ok = material is not None
    if material is not None:
        ok = asyncio.run(_check_smoke_call()) and ok
    print(
        "[info] catalog drift: not checked here -- api-recon's drift-watch reads "
        "`python -m monarch_client.operations --provenance-json` and compares it "
        "against its own live catalog"
    )

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
