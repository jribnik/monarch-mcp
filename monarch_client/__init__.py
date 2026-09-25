"""
monarch_client: a minimal, self-contained HTTP client for Monarch Money's
real (unofficial) GraphQL API.

Built to replace `monarchmoney-enhanced` (vendored under monarch-mcp's
_audit/, abandoned upstream) with something monarch-mcp fully owns. Three
pieces, kept separate on purpose:

  - auth.py: a pure reader of a small JSON file (cookies/headers) that
    api-recon produces via `recon export-session`, run separately by a
    human -- never a password login of its own, and never a runtime
    dependency on api-recon or the `recon` binary itself.
  - operations/: verbatim query/mutation text vendored from api-recon's
    operation catalog (`recon export-ops`), committed here and reviewed
    like any other dependency -- same "consume a produced artifact, never
    the tool that produced it" relationship as auth.py's file.
  - transport.py: POSTs a named operation with those two ingredients to
    api.monarch.com and turns GraphQL-level errors into typed exceptions.

This package has zero imports from the rest of monarch-mcp (server.py,
config.py) and no runtime dependency on api-recon -- it can be lifted into
its own repo later with a `git mv` if a second consumer ever shows up.

Run `python -m monarch_client.doctor` to check that auth, the network path,
and a vendored operation all work end to end.
"""

from __future__ import annotations

from typing import Any

from . import operations, transport
from .errors import (
    MonarchAuthError,
    MonarchBlockedError,
    MonarchError,
    MonarchGraphQLError,
    MonarchRateLimited,
    MonarchTransportError,
    VendoredOperationError,
)

__all__ = [
    "MonarchClient",
    "MonarchError",
    "MonarchAuthError",
    "MonarchBlockedError",
    "MonarchRateLimited",
    "MonarchTransportError",
    "MonarchGraphQLError",
    "VendoredOperationError",
]


class MonarchClient:
    """
    Calls one vendored Monarch operation by name.

    Thin on purpose: this only glues operations.load() -> transport.execute()
    together and checks the vendored file's integrity first. reads.py and
    writes.py build the variables each MCP tool needs and call
    `MonarchClient().call(op_name, variables)`.
    """

    async def call(self, op_name: str, variables: dict[str, Any]) -> dict[str, Any]:
        operations.verify_integrity(op_name)
        query_text = operations.load(op_name)
        return await transport.execute(op_name, query_text, variables)

    async def aclose(self) -> None:
        await transport.aclose()
