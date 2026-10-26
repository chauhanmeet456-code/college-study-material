# Security Audit Report

## Overview

This report consolidates all security findings and remediation status for the StudyHub Flask application. It covers source-code exposure, session management, OTP/email delivery, authentication flows, and error handling.

## 1. Frontend vs. Backend Security Boundary

**Frontend source code (HTML, CSS, JavaScript) sent to a browser is inherently inspectable.** Any user can view page source, open dev tools, or inspect network requests. The correct strategy is to keep all private logic on the server and validate/authorize every request server-side. The StudyHub application follows this principle: `SECRET_KEY`, database credentials, and SMTP credentials are loaded from environment variables (`.env` is never served). Frontend JavaScript contains no secrets.

## 2. Publicly Exposed Routes and Files

| Route | Status | Notes |
|-------|--------|-------|
| `/static/<path:filename>` | **Safe** | Flask serves only from `static/`. No `.env`, `.py`, `.git`, or DB files present. |
| `/material/<id>/download` | **Safe** | Uses `send_from_directory` with path-traversal protection. |
| `/` (home) | **Public** | Static landing page, no sensitive data. |
| `/login`, `/register`, `/forgot-password` | **Public** | Auth forms, no sensitive data in responses. |

All sensitive paths (`/.env`, `/config.py`, `/app.py`, `/.gitignore`, etc.) return HTTP 404.

## 3. Session Management Security

### 3.1 Session Conflict Screen (Student)
- **Issue:** Conflict screen displayed internal session IDs, database IDs, and IP addresses. Date/time fields used `strftime()` directly, causing `AttributeError` on SQLite string datetime values (500 error).
- **Fix:** Removed all technical identifiers. Added `format_datetime` Jinja2 filter that safely handles both `datetime` objects and string values. Removed "Keep Existing Session" button. Added JS confirmation and double-click prevention.

### 3.2 Admin Session Revocation
- **Issue 1:** `revoke_user_session` and `revoke_all_user_sessions` redirected to `admin.user_login_history` instead of `admin.user_security` — appeared broken.
- **Fix:** Changed both redirects to `admin.user_security`.
- **Issue 2:** Session revocation was wrapped in the same transaction as logging calls (`LoginHistory.create`, `AdminAuditLog.log`). A logging failure rolled back the revocation.
- **Fix:** Separated revocation from logging. Logging uses individual `try/except` blocks.
- **Issue 3:** No session ownership validation — admins could revoke sessions belonging to other users.
- **Fix:** Added `row.get('user_id') != user_id` check.
- **Issue 4:** No "End all sessions" button on the User Security page.
- **Fix:** Added button posting to existing `admin.revoke_all_user_sessions` endpoint.

### 3.3 Admin Session Termination Message
- **Root cause:** The `enforce_session_validity` before-request handler in `app.py` used a generic "session expired" message for every kind of authentication failure, including admin-ended sessions.
- **Fix:** Added logic to inspect `ended_by_admin` column and `termination_reason` field. Admin-ended sessions now show: "Your session was ended by a StudyHub administrator. Please sign in again."
- Session termination emails are only sent after successful revocation (guarded by `revoked` flag inside `try` block).

### 3.4 Server-Side Session Validation
- `enforce_session_validity` runs on every protected request, querying `UserSession.get_by_token_hash()` to validate the session token against the database.
- Admin endpoints bypass this check (`_is_admin_endpoint()`); admin sessions use Flask's signed cookies with `admin_id`.
- Revoked/expired sessions are denied access even if the browser retains the signed cookie.
- **Optimization:** Eliminated a redundant third DB query per revoked-session check by reusing the already-fetched `session_row` instead of calling `_status_of()` separately.

## 4. OTP and Email Security

### 4.1 Removed Development OTP Disclosure
All paths that exposed OTP values in user-facing responses have been removed:

| Location | Before | After |
|---|---|---|
| `_handle_credential_step()` (login) | `flash('Email is not configured, so the development OTP is %s')` | `flash("We couldn't send the verification code. Please try again later.")` |
| `_handle_credential_step()` (login send failure) | `flash('Email delivery failed. The development OTP is %s...')` | Same neutral message |
| `forgot_password()` (not configured / send failure) | Same OTP disclosure | Same neutral message |
| `send_otp()` API (email not configured) | `{'otp': otp}` in JSON | `{'success': False, 'message': "We couldn't send..."}` (HTTP 503) |
| `send_email()` logs | (none exposed) | Logs only `type(e).__name__` and SMTP phase, not exception message, credentials, or OTP |
| `_handle_credential_step()` (ambiguous SMTP disconnect) | OTP cleared from session on disconnect | OTP preserved; user sees OTP screen with "may have been sent" message |

### 4.2 OTP Challenge Storage
- Stored in Flask signed session cookie under key `otp_{purpose}_{email}`.
- Expiry: 5 minutes (`OTP_EXPIRY_SECONDS = 300`).
- Maximum 5 verification attempts (`OTP_MAX_VERIFICATION_ATTEMPTS`).
- Resend limit: maximum 3 successful resends per challenge (`OTP_MAX_RESENDS`).
- Cooldown: 30 seconds between resends (`OTP_RESEND_COOLDOWN_SECONDS`).
- **Security note:** OTP is stored in signed (not encrypted) session cookie. For maximum security, consider storing in the database instead.

### 4.3 Email Delivery
- Uses SMTP with `starttls()` encryption, configurable timeout.
- **Optimization:** Added `MAIL_CONNECT_TIMEOUT` (default 3s) to detect unreachable SMTP servers faster. Previously, connection timeouts could block login for up to 10s.
- No OTP values, session tokens, passwords, or internal IDs included in email templates.
- `NotificationService` provides idempotent delivery with dedupe keys and bounded retries (max 5 attempts).

### 4.4 Environment Configuration Fix
- **Issue:** `config.py` called `load_dotenv()` with a path pointing to `Project/.env` (two directories above), which didn't exist. This caused all env vars to fall back to insecure defaults (`SECRET_KEY = 'dev-secret-key'`, `USE_SQLITE = False` → MySQL fallback, `MAIL_USERNAME = ''`).
- **Fix:** Corrected to `load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))`.

## 5. Authentication and Authorization

- Login, registration, password reset, and OTP verification all preserved.
- OTP verification is required before authenticated session creation.
- CSRF protection enforced on all POST routes via custom `csrf_protect` decorator.
- `@user_required` and `@admin_required` decorators enforce authentication on protected routes.
- Non-admin users cannot access admin endpoints or terminate other users' sessions.
- Password hashing uses `werkzeug.security` with secure defaults.
- Rate limiting on login attempts (config-driven).

## 6. CSRF Protection
- Custom `csrf_protect` decorator validates a session-stored `_csrf_token` on all POST/PUT/PATCH/DELETE requests.
- Token is regenerated per session and embedded in forms as a hidden field.
- Login and registration templates already include submit-button disabling on form submission to prevent duplicate requests.

## 7. Cookie Security
- `SESSION_COOKIE_HTTPONLY = True` — blocks JavaScript access.
- `SESSION_COOKIE_SAMESITE = 'Lax'` — baseline CSRF defense.
- `SESSION_COOKIE_SECURE = Config.SESSION_COOKIE_SECURE` — enabled for HTTPS deployments.
- `PERMANENT_SESSION_LIFETIME` — configurable session duration.

## 8. Account Deletion and Notification Security

### User Self-Deletion
- Requires password verification + OTP confirmation.
- Email captured from the user record **before** calling `User.delete()`.
- `User.delete()` performs a soft delete (`is_active=0, deleted_by_user=1`); email remains available for notification.
- Notification email sent via `NotificationService.send_or_enqueue()` with dedupe key — prevents duplicates from double-clicks.
- If email delivery fails, user sees: "Your account has been deleted successfully. However, we could not send the confirmation email..."

### Admin Deletion
- Protected by `@admin_required` and `@csrf_protect`.
- Email captured **before** database modification.
- `permanent_delete()` used for already-self-deleted users; `delete()` for fresh deletions.
- Only sends notification email after successful DB commit.
- Cannot be invoked by non-admin users (enforced by decorator).

### Session Termination Notifications
- Admin-revoked sessions send `session_terminated` email via `NotificationService` with dedupe key `session_terminated:<session_id>`.
- Email is **only** sent when the revocation succeeds (guarded by `revoked` flag).
- Session ID is not included in the email body — only a friendly message.

## 9. Security Tests
All security tests pass:
- No sensitive files served (`.env`, `.py`, `.git`, DB files return 404).
- Path traversal blocked on download routes.
- No debug tracebacks in 404/500 error pages.
- No credentials in rendered HTML.
- CSRF tokens required for all state-changing operations.
- Non-admin users blocked from admin endpoints.
- Session revocation actually revokes (DB status checked).
- Admin session termination distinguished from ordinary expiry.

## 10. Remaining Security Items
1. **SECRET_KEY** — Must be replaced with a cryptographically random value in production: `python -c "import secrets; print(secrets.token_hex(32))"`.
2. **Gmail SMTP** — `MAIL_PASSWORD` must be an App Password (not regular password) if 2FA is enabled.
3. **Notification retry sweep** — `NotificationService.process_pending()` must be called periodically (cron/background worker) to retry failed notifications. Not automated in the current codebase.
4. **OTP in session cookie** — For maximum security, consider moving OTP challenges to the database with row-level locking instead of the Flask session.
