"""
The master write switch, in its own module so both writes.py (per-tool
checks) and MonarchClient.call (transport-level backstop) can share it
without importing each other.

Unset (or anything but "1") means no mutation-kind vendored op can be sent:
reads and the dry-run preview_transaction_rule are unaffected. Added
2026-09-29 (Opus review M13): unlike sleeper_client (dry_run + sandbox-league
allowlist) and skylight_client (hard gates), monarch_client had no gate at
all, and delete_transaction/delete_tag/delete_transaction_rule act on the
real, bank-synced account.

This is only the coarse, all-or-nothing layer. Within it, each of the 8
destructive tools (delete_transaction, delete_tag, delete_transaction_rule,
delete_category_group, delete_category, delete_savings_goal, delete_account,
mark_stream_as_not_recurring) also requires a per-call `confirm` that must
echo a value taken from a fresh read made inside the tool
(writes._require_confirm; added after the 2026-10-01 review, when all 30
write tools were live globally with only delete_account gated). So do the
wide-blast-radius flags (apply_to_future=True on the budget tools,
apply_to_existing_transactions=True on create/update_transaction_rule). A
confirm proves the caller looked at the target; it is not authentication.
"""

from __future__ import annotations

import os

from .errors import MonarchWriteBlocked

WRITES_ENV = "MONARCH_CLIENT_ENABLE_WRITES"


def writes_enabled() -> bool:
    """True iff MONARCH_CLIENT_ENABLE_WRITES=1 (exactly "1")."""
    return os.environ.get(WRITES_ENV) == "1"


def require_writes(what: str) -> None:
    """Raise MonarchWriteBlocked unless the gate is open. `what` names the
    tool or operation that was refused, for the message."""
    if not writes_enabled():
        raise MonarchWriteBlocked(
            f"{what}: writes are disabled -- set {WRITES_ENV}=1 in the "
            "monarch MCP server's environment to enable",
            gate="enabled",
        )
