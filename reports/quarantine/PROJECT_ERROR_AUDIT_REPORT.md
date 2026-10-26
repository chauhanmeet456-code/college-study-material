# Project Error Audit Report

## Errors Discovered, Root Causes, and Fixes

### 1. Broken `.env` loading path (CRITICAL)

**File**: `config.py:4`

**Root cause**: The `load_dotenv` call used `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` as the base directory, which resolves to `Project/` (two levels up from `config.py`), not the project root where `.env` lives. This caused all environment variables to fall back to defaults:
- `MAIL_USERNAME=''` → `OTPManager.is_configured()` returned `False`
- `DATABASE_URL=None` → `USE_POSTGRES=False`, app fell through to unconfigured MySQL
- `SECRET_KEY='dev-secret-key'` → insecure session signing

**Fix**: Changed to `os.path.dirname(os.path.abspath(__file__))` so `.env` is loaded from the same directory as `config.py`.

**Status**: Fixed and verified. Env vars now load correctly.

### 2. `forgot_password` route never set `session['forgot_email']`

**File**: `routes/auth.py`, `forgot_password()` function

**Root cause**: The `reset_password` route reads `session.get('forgot_email')` to identify the user, but the `forgot_password` POST handler never stored the email in the session. After submitting the forgot-password form, users were redirected to `/reset-password`, which immediately redirected them back to `/forgot-password` with the message "Please request a password reset first." — an infinite loop preventing password resets.

**Fix**: Added `session['forgot_email'] = email` and `session.permanent = True` when the user exists and email is configured. Also reordered to check `is_configured()` before generating/storing the OTP.

**Status**: Fixed and verified (TEST 13 passes).

### 3. Admin login spinner never appeared

**File**: `templates/admin/login.html`

**Root cause**: The JavaScript selector `form[action*='admin_login']` looked for the string "admin_login" (with underscore, matching the Flask endpoint name), but `url_for('admin.admin_login')` generates the URL `/admin/login` (with a slash). The selector never matched the form, so the loading overlay was never shown on admin login submission.

**Fix**: Changed selector to `form[action='/admin/login']`.

**Status**: Fixed.

### 4. Student login spinner didn't show for conflict-screen actions

**File**: `templates/auth/login.html`

**Root cause**: When a user clicked "End Other Session" or "Keep Existing Session" (conflict resolution buttons), the submit handler returned early without showing the loading overlay. Additionally, submit buttons were not disabled, allowing potential duplicate submissions.

**Fix**: Conflict buttons now trigger `showLoading('Signing you in…')`. All form submissions disable the submit button via `e.submitter || loginForm.querySelector('button[type="submit"]').disabled = true`.

**Status**: Fixed.

### 5. Registration spinner didn't disable submit button

**File**: `templates/auth/register.html`

**Root cause**: The submit handler showed the loading overlay but did not disable the submit button, allowing click-spam.

**Fix**: Added `submitBtn.disabled = true` in the registration submit handler.

**Status**: Fixed.

### 6. `_handle_credential_step` generated OTP before checking email configuration

**File**: `routes/auth.py`, `_handle_credential_step()`

**Root cause**: The OTP was generated and stored in the session (step 3 in the required flow) **before** checking whether email was configured (step 5). When email was not configured, a stale OTP remained in the session with no way for the user to retrieve it.

**Fix**: Reordered to check `OTPManager.is_configured()` before generating/storing the OTP. Added `_clear_pending_otp()` helper to clean up OTP state on failure.

**Status**: Fixed.

### 7. `send_otp` API endpoint stored OTP before checking email configuration

**File**: `routes/auth.py`, `send_otp()`

**Root cause**: Same pattern as issue #6 — OTP was generated and stored before `is_configured()` was checked, leaving a stale, unsent OTP in the session.

**Fix**: Moved `is_configured()` check before OTP generation and storage.

**Status**: Fixed.

### 8. `.env.example` missing OTP and session configuration entries

**File**: `.env.example`

**Root cause**: The `.env.example` was missing `OTP_EXPIRY_SECONDS`, `OTP_MAX_RESENDS`, `OTP_RESEND_COOLDOWN_SECONDS`, `OTP_MAX_VERIFICATION_ATTEMPTS`, `SESSION_COOKIE_SECURE`, and other configuration entries that exist in the `Config` class.

**Fix**: Updated `.env.example` with all configuration entries, documented the `DATABASE_URL` priority, and noted that `.env` is git-ignored.

**Status**: Fixed.

## Code Audit Findings (Not All Fixed)

### A. Homepage silently swallows database errors (`routes/main.py:99-100`)

The `index()` route wraps dashboard statistics queries in `try/except Exception: pass`. If the database is unreachable, the homepage renders with all stats showing zero, with no error visible to the user or logged. This is a pre-existing design decision for graceful degradation.

**Recommendation**: Log the exception at warning level so operators are alerted. Not changed to avoid altering behavior.

### B. SECRET_KEY is a placeholder in `.env`

The `.env` file contains `SECRET_KEY=your_secret_key_here_change_in_production`. This is a known placeholder that must be replaced before production use. Replacing it would invalidate all existing sessions and session tokens.

**Recommendation**: Generate a secure random key (`python -c "import secrets; print(secrets.token_hex(32))"`) and update `.env`. This requires a maintenance window as all users will be logged out.

### C. `main.py` path handling is Windows-specific (`routes/main.py:194`)

The `view_material` route checks `raw_path.startswith('static\\')` (backslash). This is Windows-specific and would fail on Linux deployments where paths use forward slashes. The code does have an `elif raw_path.startswith('static/')` fallback, but the primary check is platform-dependent.

**Recommendation**: Use `os.path.sep` or normalize paths with `os.path.normpath` before comparison. Not changed to avoid unintended behavior changes.

### D. `main.py` subjects route doesn't close cursor on exception (`routes/main.py:112-123`)

The `subjects()` route opens a cursor but doesn't use try/finally to clean up. If an exception occurs, the cursor and connection may leak.

**Recommendation**: Wrap in try/finally. Not changed to avoid altering behavior.

### E. PostgreSQL connection pool uses `pgconn.status` check (`utils/database.py:162`)

The `PostgreSQLConnectionPool.get()` method checks `conn.pgconn.status == 0` to determine if a connection is alive. A status of 0 means `CONNECTION_OK` in libpq. However, this check doesn't actually verify the connection is usable (e.g., it doesn't catch a connection that was closed by the server but not yet detected). A more robust approach would be to issue a `SELECT 1` before reusing the connection.

**Recommendation**: Add a lightweight `SELECT 1` ping before returning pooled connections. The current `PooledConnection.cursor()` method delegates to the underlying connection, so a ping could be added there. Not changed to avoid performance impact.

### F. `migrate_to_postgres.py` uses `sqlite3` directly (expected)

The migration script directly uses `sqlite3` to read from the SQLite database and transfer data to PostgreSQL. This is intentional — it's a one-time migration tool, not runtime code. All runtime code uses the abstracted `get_connection()` interface.

**Status**: No action needed.

## Tests Passed/Failed

### Tests Passed: 182 / 182

| Suite | Passed | Failed |
|-------|--------|--------|
| `tests/tests_project.py` | 69 | 0 |
| `tests/tests_otp_delivery.py` | 44 | 0 |
| `tests/tests_session_management.py` | 40 | 0 |
| `tests/tests_session_edge_cases.py` | 29 | 0 |

### Tests Failed: 0

## Remaining Issues That Could Not Be Verified

1. **PostgreSQL/NeonDB connectivity**: The environment where tests run does not have access to the PostgreSQL/NeonDB server. Tests run against SQLite (`instance/college_study.sqlite`) via a `Config.USE_POSTGRES = False` override in test files. The PostgreSQL code path (`utils/database.py` PostgreSQL pool, `PostgresCursorWrapper`) cannot be verified in this environment. The `.env` file contains a `DATABASE_URL` for NeonDB, but the connection could not be tested.

2. **SMTP delivery**: Email sending is mocked in all tests. The actual SMTP configuration in `.env` (`smtp.gmail.com:587`) cannot be tested without sending real emails. The `.env` credentials appear valid (Gmail SMTP), but connectivity and authentication could not be verified.

3. **Production SECRET_KEY**: The `.env` file uses a placeholder `SECRET_KEY`. This works for development but must be replaced before production. All session tokens, CSRF tokens, and OTP challenges depend on this key.

4. **Cross-platform path handling**: The application was developed on Windows and uses Windows-style path separators in some places (`main.py:194`). Linux deployment would need verification.
