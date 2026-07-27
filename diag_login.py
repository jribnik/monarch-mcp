#!/usr/bin/env python3
"""
Diagnostic: reproduce the Monarch login POST and print the RAW response so we can
see why it's returning 403 (error_code / detail). Credentials are not stored.

    ~/monarch-mcp/.venv/bin/python ~/monarch-mcp/diag_login.py
"""
from __future__ import annotations

import asyncio
import getpass
import json

from aiohttp import ClientSession

import config
from monarchmoney.monarchmoney import MonarchMoneyEndpoints


async def main() -> None:
    key = config.load_or_create_key()
    mm = config.new_client(key)  # applies api.monarch.com + app.monarch.com

    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")

    payload = {
        "username": email,
        "password": password,
        "trusted_device": True,
        "supports_mfa": True,
        "supports_email_otp": True,
        "supports_recaptcha": True,
    }
    headers = mm._headers.copy()
    headers["Content-Type"] = "application/json"

    url = MonarchMoneyEndpoints.getLoginEndpoint()
    print(f"\nPOST {url}")
    print("Origin:", headers.get("Origin"))
    async with ClientSession() as s:
        async with s.post(url, json=payload, headers=headers) as r:
            body_text = await r.text()
            print("HTTP status:", r.status)
            try:
                body = json.loads(body_text)
                # Redact nothing sensitive is echoed back, but be safe.
                print("Response JSON:", json.dumps(body, indent=2)[:2000])
            except Exception:
                print("Response text:", body_text[:2000])
            print("\nResponse headers of interest:")
            for h in ("cf-mitigated", "cf-ray", "server", "www-authenticate", "set-cookie"):
                if h in r.headers:
                    print(f"  {h}: {r.headers[h][:120]}")


if __name__ == "__main__":
    asyncio.run(main())
