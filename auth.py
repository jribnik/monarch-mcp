#!/usr/bin/env python3
"""
One-time interactive login for the Monarch MCP server.

Run this yourself in a terminal:

    ~/monarch-mcp/.venv/bin/python ~/monarch-mcp/auth.py

You will be prompted for your Monarch email, password, and (if enabled) your
two-factor code. Your credentials are sent only to Monarch's own API and are
NEVER written to disk or echoed. On success, an ENCRYPTED session file is saved
to ~/.monarch-mcp/session.mmsession so the MCP server can reuse the login
without your password.

Nothing here is logged to chat or committed to the repo.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys

from monarchmoney import AuthenticationError, RequireMFAException

import config


async def main() -> int:
    key = config.load_or_create_key()
    mm = config.new_client(key)

    print("Monarch login (credentials go only to Monarch, never stored)\n")
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")

    try:
        # use_saved_session=False: force a fresh login so we know it works.
        await mm.login(
            email=email,
            password=password,
            use_saved_session=False,
            save_session=False,
        )
    except (RequireMFAException, AuthenticationError):
        # Monarch requires a second factor. The login attempt above already
        # asked Monarch to email a one-time code (if email OTP is enabled on the
        # account); authenticator-app users read the code from their app instead.
        # multi_factor_authenticate() re-submits with the code as email_otp (for a
        # 6-digit code) or totp, and returns the auth token.
        print(
            "\nMonarch needs a verification code."
            "\n  • If you use email codes: check your email for a 6-digit code."
            "\n  • If you use an authenticator app: read the current code from it."
        )
        code = input("Verification code: ").strip()
        await mm.multi_factor_authenticate(email, password, code)

    # Persist the token, encrypted with our strong key, outside the repo.
    mm.save_session(str(config.SESSION_FILE))
    os.chmod(config.SESSION_FILE, 0o600)

    # Sanity check: the saved session can actually query the API.
    me = await mm.get_accounts()
    n = len(me.get("accounts", [])) if isinstance(me, dict) else "?"
    print(f"\n✓ Login OK. Encrypted session saved to {config.SESSION_FILE}")
    print(f"  ({n} accounts visible.) You can close this and use the MCP server.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 - surface a clean message, not a stack dump of secrets
        print(f"\n✗ Login failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
