# OTP Login Flow Fix Report

## Summary

**Problem:** Users sometimes received the OTP email but the login page displayed
"We couldn't send the verification code. Please try again later." instead of
showing the OTP verification screen.

**Root Cause:** `SMTPServerDisconnected` raised during `server.send_message()`
was treated as a definitive delivery failure. Many SMTP providers (e.g. Gmail)
accept the message data and close the connection **before** the client reads
the final `250 OK` reply. In that scenario the email was actually delivered, but
the application treated the exception as a failure and **cleared the pending OTP
from the session**, making the received code unusable.

## Root Cause Analysis

### Why the OTP verification page did not open

1. **OTP generation & storage happened before email delivery** — the OTP was
   correctly generated (`OTPManager.generate_otp()`) and stored in the session
   (`OTPManager.store_otp()`), so the code existed for the user to enter.

2. **`send_email()` returned `False` on `SMTPServerDisconnected`** — the old
   code in `utils/otp.py` had a broad `except Exception` that caught
   `SMTPServerDisconnected` and returned `False`. This was true even when the
   disconnect happened **after** the message data was transmitted to the server.

3. **The login route cleared the OTP on failure** — in `_handle_credential_step()`
   (`routes/auth.py`), `if not sent:` called `_clear_login_state()` and
   `_clear_pending_otp()`, **wiping** the session key that held the OTP. The
   email arrived in the user's inbox, but the matching OTP was gone.

4. **Timing: ~13.5 s before the exception surfaced** — the `MAIL_TIMEOUT`
   (10 s) socket timeout applied to every SMTP operation. When the server
   accepted the `DATA` and then went silent, the client waited the full 10 s
   for a reply that never came. The socket timeout was converted to
   `SMTPServerDisconnected` by `smtplib`'s `getreply()`. Total login latency
   was ~16–17 s (3 s connect timeout + ~10 s send timeout + overhead).

### SMTP error analysis

| Scenario | Old behaviour | New behaviour |
|---|---|---|
| Server sends `250 OK` after `send_message` | Returns `True` | `SUCCESS` |
| `SMTPServerDisconnected` **during** `send_message` (message data already transmitted) | Returns `False` → OTP cleared | `AMBIGUOUS` → OTP preserved, OTP screen shown |
| `SMTPServerDisconnected` during `connect`/`starttls`/`login` | Returns `False` | `FAILURE` → error shown, OTP cleared |
| Any other `Exception` (e.g. `SMTPAuthenticationError`) | Returns `False` | `FAILURE` |
| `server.quit()` after a known disconnect | Attempted, may timeout (extra delay) | Skipped when `data_sent` is True |

### Timeout / delay diagnosis

| Phase | Old timeout | New timeout |
|---|---|---|
| SMTP connect | `MAIL_CONNECT_TIMEOUT` (3 s) — but **ignored** because `smtplib.SMTP(timeout=10)` was passed explicitly | `connect_timeout` (3 s) — actually used for the TCP connection |
| STARTTLS / login | `MAIL_TIMEOUT` (10 s) | `MAIL_TIMEOUT` (10 s) |
| `send_message` data phase | `MAIL_TIMEOUT` (10 s) | `MAIL_SEND_TIMEOUT` (8 s, configurable) |
| `server.quit()` after silent disconnect | Attempted → may block another timeout | Skipped (not called when server already disconnected) |

## Files Changed

| File | Change |
|---|---|
| `utils/otp.py` | Added `EmailDeliveryResult` class with `SUCCESS` / `FAILURE` / `AMBIGUOUS` states and `is_success` / `is_failure` / `is_ambiguous` properties. Modified `send_email()` to track SMTP phase, distinguish `SMTPServerDisconnected` during `send_message` (AMBIGUOUS) from pre-send (FAILURE), and skip `quit()` when data was sent. Added `is_otp_valid()` helper. Updated `send_notification_email()` docstring. |
| `config.py` | Added `MAIL_SEND_TIMEOUT` config (default 8 s) for the message-send phase. |
| `routes/auth.py` | `_handle_credential_step()`: added duplicate-submission guard via `OTPManager.is_otp_valid()`; three-way branching on `EmailDeliveryResult` (SUCCESS → OTP screen; AMBIGUOUS → OTP screen with info message, OTP preserved; FAILURE → error, OTP cleared). `send_otp()` API endpoint: returns `ambiguous: True` with HTTP 202 and does **not** increment resend counter. `forgot_password()`: handles AMBIGUOUS by showing generic message. |
| `templates/auth/login.html` | Added `login_submitted` hidden field for double-submit prevention. Credential-step button has `data-prevent-double-click`. JS guards against duplicate submissions, disables the submit button during form submission, and restores button state on re-render. Resend button is disabled during AJAX request and handles `ambiguous` responses. |
| `tests/tests_otp_delivery.py` | Updated all mocks to return `EmailDeliveryResult` singletons. Added TEST 14 (ambiguous OTP preserved), TEST 15 (ambiguous API, no resend increment), TEST 16 (duplicate prevention), TEST 17 (no OTP in logs). |
| `tests/tests_session_management.py` | Updated mocks to use `EmailDeliveryResult` / `RESULT_SUCCESS`. |
| `tests/tests_session_edge_cases.py` | Updated mocks to use `EmailDeliveryResult` / `RESULT_SUCCESS`. |
| `tests/tests_project.py` | Updated mocks to use `EmailDeliveryResult` / `RESULT_SUCCESS`. |
| `tests/tests_account_deletion_and_session_email.py` | Updated mocks to use `EmailDeliveryResult` / `RESULT_SUCCESS`. |
| `docs/CHANGELOG.md` | Added changelog entry for the OTP flow fix. |
| `docs/SECURITY_AUDIT_REPORT.md` | Updated audit table with `EmailDeliveryResult` note. |

## Login-to-OTP Flow Changes

### Before (broken)

```
User POSTs /login
  → credentials valid
  → OTP generated & stored in session
  → send_otp_email() → send_email() → SMTPServerDisconnected
  → send_email() returns False
  → _handle_credential_step() sees sent=False
  → clears PENDING_LOGIN_EMAIL, clears pending OTP from session
  → renders login page with "We couldn't send the verification code"
  → User has the email but can't enter the OTP (it was deleted)
```

### After (fixed)

```
User POSTs /login
  → credentials valid
  → is_otp_valid() check → no existing OTP → proceed
  → OTP generated & stored in session
  → send_otp_email() → send_email() → SMTPServerDisconnected during send
  → send_email() returns AMBIGUOUS (phase was 'send')
  → _handle_credential_step() sees result.is_ambiguous
  → PRESERVES PENDING_LOGIN_EMAIL and pending OTP
  → renders login.html with show_otp=True + "may have been sent" message
  → User enters OTP they received → verification succeeds → session created
```

### Duplicate submission prevention

1. **Frontend**: Hidden `login_submitted` field is set to `'1'` on first form
   submit. JS checks this field and blocks a second submit client-side.
2. **Backend**: `OTPManager.is_otp_valid()` checks if a non-expired OTP
   already exists for the email. If so, the existing OTP is preserved and the
   OTP screen is shown — no new OTP is generated, no second email is sent.

## Security Measures Preserved

| Measure | Status |
|---|---|
| OTP never appears in HTML, flash messages, JSON responses, or logs | Verified by TEST 4, TEST 17 |
| OTP never appears in terminal logs (`send_email` logs only timing + exception type) | Verified |
| No passwords, session cookies, or SMTP credentials logged | Verified by `tests_project.py` security checks |
| OTP expiration (5 min default) | Unchanged |
| OTP single-use verification | Unchanged |
| Max 5 verification attempts per OTP | Unchanged |
| Max 3 resends per OTP challenge | Unchanged |
| Resend cooldown (30 s default) | Unchanged |
| CSRF protection on all POST endpoints | Unchanged |
| Login rate limiting (5 attempts / 15 min) | Unchanged |
| Session token hashing (SHA-256) | Unchanged |
| No OTP bypass on ambiguous delivery | The OTP is required for login regardless of delivery status |
| Ambiguous delivery does not increment resend counter | Implemented |

## Tests Executed

| Test Suite | Tests | Passed | Failed |
|---|---|---|---|
| `tests/tests_otp_delivery.py` | 61 | 61 | 0 |
| `tests/tests_session_management.py` | 72 | 72 | 0 |
| `tests/tests_session_edge_cases.py` | 29 | 29 | 0 |
| `tests/tests_account_deletion_and_session_email.py` | 43 | 43 | 0 |
| `tests/tests_project.py` | 98 | 98 | 0 |
| **Total** | **303** | **303** | **0** |

### New tests added (TEST 14–17)

**TEST 14 — Ambiguous SMTP disconnect preserves OTP state**
- Mocks `send_otp_email` to return `RESULT_AMBIGUOUS`
- Verifies OTP screen (`show_otp=True`) is rendered, not the error page
- Verifies `email_error` is not set
- Verifies OTP remains in session and is verifiable
- Verifies login completes successfully after entering the OTP

**TEST 15 — Ambiguous API endpoint does not increment resend counter**
- Mocks `send_otp_email` to return `RESULT_AMBIGUOUS`
- POSTs to `/api/send-otp`
- Verifies response has `ambiguous: True` and HTTP 202
- Verifies resend counter remains at 0 (not incremented)

**TEST 16 — Duplicate login submissions do not create multiple OTPs**
- Submits the login form twice with the same credentials
- Verifies only one OTP email is sent (via `send_otp_email` call count)
- Verifies the OTP value is the same after both submissions (not overwritten)
- Verifies the OTP screen is shown on the duplicate submission

**TEST 17 — No OTP exposed in logs on ambiguous delivery**
- Captures stdout/stderr during login
- Verifies the OTP value does not appear in any captured output
- Verifies `MAIL_PASSWORD` does not appear in any captured output

## Timing Comparison

| Operation | Before | After |
|---|---|---|
| SMTP connect timeout | 10 s (MAIL_TIMEOUT, not MAIL_CONNECT_TIMEOUT) | 3 s (connect_timeout actually used) |
| `send_message()` silent-disconnect wait | 10 s | 8 s (MAIL_SEND_TIMEOUT) |
| `server.quit()` on dead socket | Attempted → potential extra timeout | Skipped (no delay) |
| Worst-case login latency | ~16–17 s | ~11–12 s (estimated) |
| User sees OTP screen on ambiguous | No (sees error, OTP is lost) | Yes (can enter received OTP) |

**Note:** The actual SMTP connection and message transmission times are
provider-dependent and not controlled by the application. The 8 s send-phase
timeout reduces the worst-case silent-disconnect wait from 10 s to 8 s. The
primary user-experience improvement is that on an ambiguous disconnect, the
user now sees the OTP verification screen instead of an error message.

## Remaining Issues Requiring SMTP Provider Verification

1. **`MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`**: The fix
   works with any SMTP provider, but the timing improvement depends on the
   provider's behaviour. If the provider consistently disconnects silently
   after accepting the message, users will see the OTP screen within ~11 s
   instead of ~17 s. If the provider responds quickly with `250 OK`, login
   is fast in both versions.

2. **Gmail SMTP**: Gmail is known to sometimes not send the final `250` reply
   within the timeout window. The `AMBIGUOUS` handling addresses this, but
   the user should verify that received OTPs can actually be entered (they
   can — the OTP is preserved in the session).

3. **`MAIL_SEND_TIMEOUT`**: Defaults to 8 s. If the SMTP provider is known to
   take longer than 8 s to accept the `DATA` command reply, increase
   `MAIL_SEND_TIMEOUT` in `.env`. Conversely, if the provider is consistently
   fast, it can be lowered to reduce the worst-case delay.

4. **No `.env` file exists or SMTP not configured**: When
   `OTPManager.is_configured()` returns `False`, the system falls back to
   storing the OTP in the session (no email sent). This is a development-only
   behaviour and is not affected by this fix.
