Confirming this on a fresh install, with a couple of extra details that may help.

On my account Monarch uses **email OTP** (a 6-digit code mailed on each new-device
login), and the interactive flow breaks in two places:

1. **Wrong exception type / error surfacing.** `login()` never raises
   `RequireMFAException` for an email-OTP account. The server returns
   `403 {"error_code": "EMAIL_OTP_REQUIRED", "detail": "Retrieve the code from your
   email to continue login."}`, and because `error_code` isn't `MFA_REQUIRED`,
   the 403 handler in `authentication_service` falls straight through to
   `raise AuthenticationError("Login failed with status 403")`. So callers
   catching `RequireMFAException` (like `interactive_login()`) never get a chance
   to prompt for the code. As @kadimgh noted, the TOTP path raises
   `MFARequiredError`, which `interactive_login` also doesn't catch — same root
   cause, different code.

2. **6-digit ≠ always email OTP.** `_multi_factor_authenticate` decides between
   `email_otp` and `totp` purely by `len(code) == 6 and code.isdigit()`. TOTP
   codes are *also* 6 digits, so authenticator users get their code sent in the
   wrong field. This needs the caller to disambiguate (or the client to try one,
   then fall back to the other on failure).

**Workaround** that's working for me without patching the library: attempt
`login(...)` (that request is what makes Monarch email the code), catch
`AuthenticationError`, prompt for the code, then call
`multi_factor_authenticate(email, password, code)`. The initial failed login is
the trigger for the OTP email, and codes expire quickly, so use the newest one.

A clean fix would probably: (a) raise a catchable, dedicated exception (e.g.
`EmailOTPRequired`) from `login()` on `EMAIL_OTP_REQUIRED` and keep
`RequireMFAException`/`MFARequiredError` consistent for the TOTP case, and
(b) let `multi_factor_authenticate` take an explicit factor type instead of
guessing from code length. Happy to open a PR along those lines if it'd be
welcome.

(Separately: login and all GraphQL queries are also broken on `main` right now
due to the api.monarch.com domain move and a bad `connector` kwarg in the pooled
transport — I've opened a PR for those.)
