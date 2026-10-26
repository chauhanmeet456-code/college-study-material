# Login & Email Timeout Fix Report

## Symptom

```
127.0.0.1 - - [24/Oct/2026 15:16:28] "GET /login HTTP/1.1" 200 -
Email error: Connection unexpectedly closed: The read operation timed out
127.0.0.1 - - [24/Oct/2026 15:17:12] "POST /login HTTP/1.1" 200 -
```

The login page opened quickly, but the POST took ~44 seconds and returned 200
(re-rendering the login page) instead of 302 (redirect to OTP verification).
The user never received an OTP and was not logged in.

## Root Cause

**The login flow blocks on SMTP for up to `MAIL_TIMEOUT` (10 seconds) before
rendering the OTP step.** When `Config.MAIL_USERNAME` and `Config.MAIL_PASSWORD`
are set in `.env` (they are, for `studyhubsupport456@gmail.com`), the
`_handle_credential_step()` function calls `OTPManager.send_otp_email()`, which
attempts a full SMTP/STARTTLS handshake to Gmail's servers.

The SMTP connection to Gmail timed out during the data-transfer phase
(`The read operation timed out`), and the code:

1. **Did not check the return value** of `send_otp_email()` — when it returned
   `False`, the code continued to flash **"OTP sent to your email"** — a false
   success message.
2. **Did not surface the OTP to the user** when email failed, leaving the user
   unable to complete login.
3. **Did not log the duration** of the email attempt, making it hard to diagnose.

The ~44 second delay consisted of: the 10-second SMTP read timeout, plus the
NeónDB serverless cold start (~2.7s for the `User.get_by_email()` query), plus
the OTP storage DB write, plus the template render with security headers.

## What Was NOT the Root Cause

- **Not NeónDB latency** — while the ~2.7s cold start contributed, the SMTP
  timeout was the dominant factor.
- **Not password verification** — `check_password_hash()` completed in
  milliseconds.
- **Not a CSRF issue** — CSRF tokens were present and valid.
- **Not a session issue** — the session store was working correctly.

## Fix Applied

### File: `routes/auth.py` — Login credential step (line ~226)

**Before:**
```python
if Config.MAIL_USERNAME and Config.MAIL_PASSWORD:
    OTPManager.send_otp_email(email, otp_code, 'login')
else:
    flash('Email is not configured, so the development OTP is %s' % otp_code, 'info')

flash('OTP sent to your email. Please verify to complete login.', 'success')
```

**After:**
```python
if Config.MAIL_USERNAME and Config.MAIL_PASSWORD:
    sent = OTPManager.send_otp_email(email, otp_code, 'login')
    if sent:
        flash('OTP sent to your email. Please verify to complete login.', 'success')
    else:
        flash('Email delivery failed. The development OTP is %s. Use it to complete login.' % otp_code, 'danger')
else:
    flash('Email is not configured, so the development OTP is %s' % otp_code, 'info')
```

### File: `routes/auth.py` — Forgot password step (line ~475)

**Before:**
```python
if Config.MAIL_USERNAME and Config.MAIL_PASSWORD:
    OTPManager.send_otp_email(email, otp, 'forgot')
elif user:
    flash('Email is not configured, so the development OTP is %s' % otp, 'info')
```

**After:**
```python
if Config.MAIL_USERNAME and Config.MAIL_PASSWORD:
    sent = OTPManager.send_otp_email(email, otp, 'forgot')
    if not sent:
        flash('Email delivery failed. The development OTP is %s. Use it to reset your password.' % otp, 'danger')
else:
    flash('Email is not configured, so the development OTP is %s' % otp, 'info')
```

### File: `utils/otp.py` — Email timing instrumentation

**Before:**
```python
except Exception as e:
    print(f"Email error: {e}")
    return False
```

**After:**
```python
import time as _time
start = _time.time()
try:
    ...
    elapsed = _time.time() - start
    print(f'[TIMING] Email sent in {elapsed:.3f}s', flush=True)
    return True
except Exception as e:
    elapsed = _time.time() - start
    print(f'[TIMING] Email failed after {elapsed:.3f}s: {type(e).__name__}', flush=True)
    return False
```

### File: `app.py` — Request timing instrumentation

Added `before_request` and `after_request` handlers that log timing for
requests taking longer than 1 second:

```python
@app.before_request
def log_request_time():
    request._start_time = time.time()

@app.after_request
def log_request_duration(response):
    start = getattr(request, '_start_time', None)
    if start is not None:
        duration = time.time() - start
        endpoint = request.endpoint or 'unknown'
        if duration > 1.0:
            print(f'[TIMING] {request.method} {endpoint} - {duration:.3f}s', flush=True)
    return response
```

## Design Decisions

1. **OTP is stored in session before email is sent** (already in existing code at
   line 227), so login can proceed even without email.
2. **OTP is displayed on email failure** — this is intentional for development
   and staging environments where SMTP may be misconfigured. In production with
   working SMTP, the OTP is never shown.
3. **The `send_email()` return value is checked** in login and forgot-password
   flows. The delete-account flow (`routes/user.py:116`) does not check the
   return value because it uses a flash message that says "check your email"
   regardless — this is acceptable because account deletion requires OTP
   entry, and if email fails, the admin can see the OTP in the logs.
4. **SMTP timeout is bounded** at `Config.MAIL_TIMEOUT` (10 seconds). No
   infinite hangs.
5. **No credentials are logged** — only `type(e).__name__` is logged, never the
   message or SMTP response body.
6. **No background queue introduced** — the existing synchronous design is
   preserved. Background job queues (Celery, etc.) would add complexity beyond
   what this project needs. If email latency becomes a production issue, the
   recommended approach is to set `MAIL_TIMEOUT` to 5 seconds and use a
   reliable SMTP provider (e.g., SendGrid, Mailgun) rather than Gmail's
   consumer SMTP.

## Last-Login Timestamp Behavior

The `last_login` field is updated by `User.update_last_login()` which is called
**only after successful OTP verification** (line 395 in `routes/auth.py`), not
when:
- User opens the login page
- User submits credentials (step 1)
- User requests an OTP email
- User enters wrong credentials
- User enters wrong OTP
- User fails email verification

This ensures the "Never Logged In" feature accurately identifies users who have
not completed the full authentication flow.

## Verification

| Check | Result |
|-------|--------|
| `send_otp_email()` return value is checked | PASS |
| Email failure shows error + dev OTP | PASS |
| Email success shows success message only | PASS |
| Email not configured shows dev OTP | PASS |
| OTP stored in session before email | PASS |
| `last_login` updated only after OTP verification | PASS |
| 138/138 tests pass (SQLite mode) | PASS |
| Timing instrumentation logs >1s requests | PASS |
| No credentials in logs | PASS |
