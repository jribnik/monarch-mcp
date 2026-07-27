# Fix login and GraphQL against the current Monarch API

## Summary

On a clean install from `main`, **login and every GraphQL query currently fail.**
Two independent breakages:

### 1. API domain migration (login → HTTP 405)

Monarch moved its API from `api.monarchmoney.com` to `api.monarch.com` (and the
web app to `app.monarch.com`). The old host now **301-redirects** the login
`POST /auth/login/` to the new host. `aiohttp` follows a 301 by downgrading the
request to `GET`, and the new host answers `GET /auth/login/` with **405 Method
Not Allowed** — surfaced as `AuthenticationError: Login failed with status 405`.

Fix: point `MonarchMoneyEndpoints.BASE_URL` at `https://api.monarch.com` and the
`Origin` header at `https://app.monarch.com`.

### 2. Pooled GraphQL transport: bad `connector` kwarg + no TLS verification

`GraphQLClient._get_client()` constructs:

```python
AIOHTTPTransport(..., connector=connector)
```

but gql's `AIOHTTPTransport` has no top-level `connector` parameter — a custom
connector goes through `client_session_args`. So **every** query raises:

```
TypeError: AIOHTTPTransport.__init__() got an unexpected keyword argument 'connector'
```

(reproducible on any gql 3.4+; gql 4.x fails even earlier). Separately, gql's
`AIOHTTPTransport` **defaults to not verifying TLS certificates** — every request
here carries the auth token, so this is a real MITM exposure.

Fix: pass the connector via `client_session_args={"connector": connector}` and
set `ssl=True` (matching the three other `AIOHTTPTransport` call sites in
`monarchmoney.py`, which already pin `ssl=True`).

## Diff

- `monarchmoney/monarchmoney.py` — `BASE_URL` + `Origin` → `*.monarch.com`
- `monarchmoney/services/graphql_client.py` — `client_session_args` + `ssl=True`

Two files, +10/-3. No behavior change beyond making requests succeed and be
TLS-verified.

## Testing

Against a live account (email-OTP login), with these changes and no other
patches: login succeeds and `get_accounts()` / `get_transaction_categories()`
return real data (23 accounts, 71 categories). Before the changes, login fails
with 405; after fixing only the domain, every query fails with the `connector`
`TypeError`.

## Notes

- This does **not** touch the interactive-MFA/email-OTP issue (#40); that's a
  separate concern — see my comment there.
