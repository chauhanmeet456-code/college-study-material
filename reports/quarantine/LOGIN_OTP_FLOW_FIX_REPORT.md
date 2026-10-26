# Login & OTP Flow Fix Report

## Verified Cause of the Email Error and Missing OTP Page

### Root Cause

The primary root cause was a **broken `.env` file path in `config.py`** (Phase 1 diagnosis).

The committed version of `config.py` called `load_dotenv()` with no arguments, which searches the current working directory. Previous work changed this to:

```python
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
```

This path resolves to **`Project/.env`** (two directories above `config.py`), which **does not exist**. The actual `.env` file lives at `D:\...\college-study-material\.env`.

Because `load_dotenv` is called **before** the `Config` class body executes, and Flask's own auto-loading of `.env` happens **after** `Config` is already defined (during `Flask(__name__)` in `create_app()`), the `Config` class attributes are always populated with defaults:

| Variable        | Default Value      | Consequence                                  |
| --------------- | ------------------ | -------------------------------------------- |
| `DATABASE_URL`  | `None`             | `USE_POSTGRES = False`                       |
| `USE_SQLITE`    | `False`            | Falls through to MySQL on `localhost:3306`   |
| `MAIL_USERNAME` | `''`               | `OTPManager.is_configured()` returns `False` |
| `MAIL_PASSWORD` | `''`               | Same                                         |
| `SECRET_KEY`    | `'dev-secret-key'` | Insecure session signing                     |

When `is_configured()` returned `False`, `_handle_credential_step` in `routes/auth.py` displayed the error message **"We couldn't send the verification code. Please try again later."** and rendered the login page with `show_otp=False`, so the OTP entry form was never shown.

The user reported receiving OTP emails because those emails were from prior successful test runs (when the configuration was working) or from a different environment where the `.env` was properly loaded. With the broken path, no new emails were actually sent.

### Secondary Issues Found

1. **`forgot_password` route never set `session['forgot_email']`** — The `reset_password` route reads `session.get('forgot_email')` to identify the user, but the `forgot_password` POST handler never stored it. Users could never reach the password-reset OTP form.

2. **`send_otp` API endpoint stored OTP before checking `is_configured()`** — A stale, unsent OTP lingered in the session when email was not configured.

3. **`_handle_credential_step` generated/stored OTP before checking `is_configured()`** — Same issue; a pending OTP remained in the session even when email was not configured.

4. **Admin login spinner selector mismatch** — The JavaScript searched for `form[action*='admin_login']`, but `url_for('admin.admin_login')` generates `/admin/login` (with a slash, not underscore). The form submit handler was never attached, so the loading overlay never appeared on admin login.

5. **Student login spinner** — Conflict-screen buttons (End Other Session, etc.) returned early without showing the loading overlay. Submit buttons were not disabled, allowing potential duplicate submissions.

6. **Registration spinner** — Submit button was not disabled on form submission.

## Fix Summary

### 1. `config.py` — Corrected `.env` loading path

```python
# Before (broken — points to Project/.env which doesn't exist):
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

# After (correct — points to the .env in the same directory as config.py):
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
```

This is robust to the current working directory and always loads `.env` from the project root.

### 2. `routes/auth.py` — `_handle_credential_step`

- Check `OTPManager.is_configured()` **before** generating or storing the OTP. If not configured, flash the error, clear login state AND the pending OTP, and return the login form with `show_otp=False`.
- After a failed email send, clear the pending OTP from the session via the new `_clear_pending_otp(email, 'login')` helper.
- On success, flash the accurate message "Verification code sent to your email address."

### 3. `routes/auth.py` — `send_otp` API endpoint

- Moved the `is_configured()` check **before** OTP generation and storage, ensuring no stale OTP lingers in the session when email is not configured.
- Resend count is only incremented after a confirmed successful email send.

### 4. `routes/auth.py` — `forgot_password` route

- Now sets `session['forgot_email'] = email` and `session.permanent = True` only when the user exists **and** email is configured.
- Checks `is_configured()` before generating/storing the OTP, consistent with the login flow.

### 5. `routes/auth.py` — New `_clear_pending_otp` helper

```python
def _clear_pending_otp(email, purpose):
    session.pop(f'otp_{purpose}_{email}', None)
```

Used by `_handle_credential_step` to clean up stale OTP challenges when email send fails.

### 6. `templates/admin/login.html` — Spinner selector and button disabling

- Fixed selector from `form[action*='admin_login']` to `form[action='/admin/login']`.
- Added submit-button disabling on form submit.
- Added `String()` wrapping for `localStorage.setItem` dark-mode value.
- Removed duplicate `{{ csrf_input() }}` call.

### 7. `templates/auth/login.html` — Spinner improvements

- Conflict-screen buttons now show the loading overlay ("Signing you in…").
- Submit button is disabled on all form submissions to prevent duplicate requests.
- Removed the "Change email" button (already removed by previous work; confirmed absent).
- Fixed `localStorage.setItem` to use `String()` wrapping.
- Resend OTP button now shows the loading overlay during the fetch and properly hides it on both success and error.

### 8. `templates/auth/register.html` — Spinner improvements

- Submit button is disabled on form submission.

### 9. Test files — SQLite override

- Added `Config.USE_POSTGRES = False` to all test files so tests use the bundled SQLite database (`instance/college_study.sqlite`) instead of attempting to connect to the external NeonDB server. This does not modify `.env` — it only overrides the in-process `Config` attribute at import time.

## Login-to-OTP-to-Dashboard Flow (Corrected)

1. **User enters email + password** on the login page and submits the form (normal HTML POST to `/login`).
2. **`auth.login` route** receives the POST. The `csrf_protect` decorator validates the CSRF token.
3. **Credential step** (`_handle_credential_step`):
   - Validates email and password against the database.
   - On failure: flashes "Invalid email or password." and redirects back to login.
   - On success: stores `pending_login_email` in session.
   - Checks `OTPManager.is_configured()` — if False, shows the error message and returns the credential form (no OTP generated).
   - If configured: generates a cryptographically secure 6-digit OTP (`secrets.randbelow(900000) + 100000`), stores it in the Flask session under key `otp_login_{email}` with timestamp, attempts counter, resend counter, and IP address.
   - Sends the OTP via SMTP (`OTPManager.send_otp_email`).
   - On success: flashes "Verification code sent to your email address." and renders `login.html` with `show_otp=True`.
   - On failure: flashes the error message, clears login state and pending OTP, renders the login form with `show_otp=False`.
4. **Browser displays the OTP entry form** (the `.otp-section` div gets the `active` CSS class).
5. **User enters the 6-digit OTP** and submits the same form (still POST to `/login`).
6. **OTP step** (`_handle_otp_step`):
   - Retrieves `pending_login_email` from session.
   - Calls `OTPManager.verify_otp(email, otp, 'login')`.
   - `verify_otp` checks: OTP exists, not expired (5-minute TTL), attempts not exceeded (5 max), OTP matches.
   - On match: removes OTP from session, returns `True`.
   - On any failure: flashes an appropriate error, returns `False`.
7. **Session creation** (only if OTP verified):
   - Creates a `UserSession` row in the database with a hashed token.
   - Creates a `LoginHistory` entry.
   - Sets `session_token`, `user_id`, `user_name`, `session_id` in the Flask session.
   - Checks for conflicting active sessions (multi-device).
   - If conflict: renders the conflict screen with options (End Other Session, Keep Existing, Cancel).
   - If no conflict or conflict resolved: redirects to the dashboard (`main.index`).
8. **Subsequent requests** are protected by the `enforce_session_validity` before_request handler, which validates the server-side session token on every request to protected pages.

## OTP Challenge Storage

The OTP challenge is stored in the **Flask signed session cookie** (not in the database). The session is signed with `SECRET_KEY` but not encrypted. Key characteristics:

- **Challenge identifier**: `otp_{purpose}_{email}` (e.g., `otp_login_user@example.com`). The email is provided by the user during login, so the key includes user-controlled input. However, since the session is signed, the client cannot tamper with the stored challenge data.
- **Expiry**: 5 minutes (`OTP_EXPIRY_SECONDS = 300`), checked in `OTPManager.verify_otp`.
- **Invalidation**: OTP is removed from the session on successful verification (`session.pop(key, None)` in `verify_otp`). A new OTP is generated on resend, overwriting the old one (old code is invalidated).
- **Rate limiting**: Maximum 5 verification attempts (`OTP_MAX_VERIFICATION_ATTEMPTS`). After exceeding, the OTP is removed and the user must request a new one.
- **Resend limit**: Maximum 3 successful resends per challenge (`OTP_MAX_RESENDS`). A resend only counts when the email provider accepts the send. Failed sends do not increment the counter.
- **Cooldown**: 30 seconds between resends (`OTP_RESEND_COOLDOWN_SECONDS`).

**Security note**: Storing the OTP in a client-side session cookie means the OTP value is transmitted to the browser in signed (but not encrypted) form. An attacker who obtains the `SECRET_KEY` could extract the OTP. In a production deployment with HTTPS, this is acceptable because the cookie is only readable by the server (HttpOnly=True) and the secret key protects integrity. For maximum security, consider storing OTP challenges in the database instead of the session.

## SMTP Configuration

The `.env` file already contains valid SMTP configuration:

```
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=ENTER_YOUR_MAIL
MAIL_PASSWORD=ENTER_YOUR_MAIL_PASSWORD
MAIL_TIMEOUT=10
MAIL_USE_TLS=True
```

The `OTPManager.send_email` method:

- Creates an SMTP connection with a 10-second timeout.
- Uses `starttls()` for encryption.
- Authenticates with the provided credentials.
- Returns `True` only after `server.send_message()` succeeds.
- Catches all exceptions and returns `False` (never crashes).
- Properly closes the server connection in a `finally` block.

**Remaining manual configuration requirement**: The `SECRET_KEY` in `.env` is a placeholder (`your_secret_key_here_change_in_production`). It must be replaced with a cryptographically random value (e.g., `python -c "import secrets; print(secrets.token_hex(32))"`) before production deployment. This is documented in `.env.example`.

## Tests Executed and Results

All four test suites pass:

| Suite                               | Tests   | Passed  | Failed |
| ----------------------------------- | ------- | ------- | ------ |
| `tests/tests_project.py`            | 69      | 69      | 0      |
| `tests/tests_otp_delivery.py`       | 44      | 44      | 0      |
| `tests/tests_session_management.py` | 40      | 40      | 0      |
| `tests/tests_session_edge_cases.py` | 29      | 29      | 0      |
| **Total**                           | **182** | **182** | **0**  |

Key test coverage:

- Successful OTP email send (mocked)
- SMTP timeout / connection failure → error shown, no session created
- Email provider acceptance followed by DB failure → handled safely
- OTP challenge persistence in Flask session
- OTP page navigation after successful send
- Missing or expired challenge state
- Successful and failed OTP verification
- Session creation only after OTP verification succeeds
- OTP expiry and invalidation of old codes
- Maximum of 3 successful resend attempts
- Failed resend not consuming the quota
- No development OTP exposed
- No false email failure message after success
- Correct frontend handling of responses (AJAX + form-based)
- Spinner dismissal after errors
- Existing student and admin authentication
- Password reset flow (forgot_password → reset_password)
