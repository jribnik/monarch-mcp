"""
Per-tool backend selection between the legacy monarchmoney-enhanced library
and monarch_client, so a regression in the new client is isolated to
whichever tool(s) it's flipped onto, and rollback is one env var away.

Env vars:
  MONARCH_MCP_BACKEND: global default -- "library" (default), "client", or
      "dual". "dual" is reads-only; see dispatch()'s docstring.
  MONARCH_MCP_CLIENT_TOOLS: comma-separated tool names forced to "client"
      regardless of the global default.
  MONARCH_MCP_LIBRARY_TOOLS: comma-separated tool names forced to "library"
      regardless of the global default. Takes precedence over
      MONARCH_MCP_CLIENT_TOOLS when a tool is named in both -- falling back
      to the backend that's always worked is the safer choice for a
      conflicting config, not an error.

Flipping one tool: `MONARCH_MCP_CLIENT_TOOLS=get_tags` and restart the MCP
server. Rolling back: unset it, or add the tool name to
MONARCH_MCP_LIBRARY_TOOLS, and restart.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

STATE_DIR = Path(os.environ.get("MONARCH_MCP_HOME", Path.home() / ".monarch-mcp"))
PARITY_LOG_PATH = STATE_DIR / "parity.log"

Backend = str  # "library" | "client" | "dual"


def _tool_set(env_var: str) -> set[str]:
    raw = os.environ.get(env_var, "")
    return {name.strip() for name in raw.split(",") if name.strip()}


def resolve(tool_name: str) -> Backend:
    if tool_name in _tool_set("MONARCH_MCP_LIBRARY_TOOLS"):
        return "library"
    if tool_name in _tool_set("MONARCH_MCP_CLIENT_TOOLS"):
        return "client"
    return os.environ.get("MONARCH_MCP_BACKEND", "library")


def _shape(value: Any, depth: int = 0, max_depth: int = 6) -> Any:
    """Structure-only projection for the parity log: key names and value
    TYPES, never values -- this is real financial data, and the log is
    plain (if 0600) text on disk."""
    if depth > max_depth:
        return "..."
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1, max_depth) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(value[0], depth + 1, max_depth)] if value else []
    return type(value).__name__


def _log_parity(tool_name: str, library_shape: Any, client_shape: Any) -> None:
    if library_shape == client_shape:
        return
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "library_shape": library_shape,
        "client_shape": client_shape,
    }
    with open(PARITY_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    os.chmod(PARITY_LOG_PATH, 0o600)


async def dispatch(
    tool_name: str,
    *,
    library_call: Callable[[], Awaitable[dict[str, Any]]],
    client_call: Callable[[], Awaitable[dict[str, Any]]],
    mutating: bool = False,
) -> dict[str, Any]:
    """
    Run `tool_name` through whichever backend(s) resolve() picks, and
    return the result the caller (an MCP tool) hands back to Claude.

    "dual" is reads-only by construction: it calls BOTH backends, logs a
    structure-only diff (key paths + value types, never values) to
    ~/.monarch-mcp/parity.log when they differ, and always returns the
    LIBRARY's result. `mutating=True` (every write tool must pass this)
    enforces that: if MONARCH_MCP_BACKEND=dual is set globally (meant for
    soaking READ tools) and a write tool's per-tool override doesn't pin it
    to "library" or "client" explicitly, this raises instead of silently
    running the mutation through both backends -- a write tool's config
    must always resolve to exactly one backend, no exceptions.
    """
    resolved = resolve(tool_name)

    if mutating and resolved == "dual":
        raise ValueError(
            f"{tool_name!r} is a write tool and MONARCH_MCP_BACKEND=dual would "
            "run it through BOTH backends -- dual mode is read-only (see this "
            "function's docstring). Pin this tool to one backend explicitly: "
            f"add {tool_name!r} to MONARCH_MCP_LIBRARY_TOOLS or "
            "MONARCH_MCP_CLIENT_TOOLS."
        )

    if resolved == "library":
        return await library_call()

    if resolved == "client":
        return await client_call()

    if resolved == "dual":
        library_result = await library_call()
        client_result = await client_call()
        _log_parity(tool_name, _shape(library_result), _shape(client_result))
        return library_result

    raise ValueError(f"unknown backend {resolved!r} for tool {tool_name!r}")
