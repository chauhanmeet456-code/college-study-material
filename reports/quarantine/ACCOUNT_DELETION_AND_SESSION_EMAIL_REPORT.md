# Account Deletion & Session Email Report

## 1. Account-Deletion Routes and Behavior Inspected

### User Self-Deletion
- **Route:** `POST /profile/delete-account` and `GET/POST /profile/confirm-delete-account`
- **File:** `routes/user.py:101-179`
- **Flow:**
  1. `delete_account()` verifies the user's password via `User.verify_password()`.
  2. Generates an OTP with `OTPManager.generate_otp()` and stores it via `OTPManager.store_otp(email, otp, 'delete_account')`.
  3. Sends the OTP email via `OTPManager.send_otp_email()` (only when `Config.MAIL_USERNAME` and `Config.MAIL_PASSWORD` are set).
  4. Stores the email in `session['delete_account_email']` so it survives the CSRF token and OTP exchange.
  5. Redirects to `confirm_delete_account()`.
  6. `confirm_delete_account()` verifies the OTP via `OTPManager.verify_otp(email, otp, 'delete_account')`.
  7. On success: calls `session.clear()`, then `User.delete(user_id)` (soft delete — sets `is_active=0, deleted_by_user=1, deletion_requested_at=NOW`).
  8. Sends the confirmation email via `send_template_notification('account_deleted_self', ...)` **only if** `User.delete()` returns `True`.
  9. If the email fails to send but deletion succeeds, a warning flash message is shown to the user.

### Admin Deletion
- **Route:** `POST /admin/users/delete/<int:user_id>`
- **File:** `routes/admin.py:451-483`
- **Flow:**
  1. Protected by `@admin_required` and `@csrf_protect`.
  2. Captures `user['email']` **before** any deletion.
  3. If the user was already self-deleted (`deleted_by_user=1` and `deletion_processed IS NOT NULL`), calls `User.permanent_delete()` (hard delete).
  4. Otherwise, calls `User.mark_deletion_processed()` then `User.delete()` (soft delete).
  5. Sends `send_template_notification('account_deleted_by_admin', ...)` **only if** `deleted` is truthy.
  6. Uses dedupe key `account_deleted_by_admin:<user_id>` to prevent duplicate notifications.

### Email Templates
- **File:** `utils/notifications.py:44-80`
- Three templates defined:
  - `account_deleted_self`: subject "Your StudyHub Account Has Been Deleted"
  - `account_deleted_by_admin`: subject "Your StudyHub Account Was Deleted by an Administrator"
  - `session_terminated`: subject "Your StudyHub Session Was Ended"
- Templates are plain HTML messages with no passwords, OTPs, session tokens, or internal IDs.

### Email Delivery Infrastructure
- **File:** `utils/otp.py:273-282` — `OTPManager.send_notification_email()` reuses the same SMTP transport as OTP emails.
- **File:** `utils/notifications.py` — `NotificationService` with:
  - `send_or_enqueue()`: Idempotent delivery with dedupe-key protection.
  - `deliver()`: Wraps `OTPManager.send_email()` with safe exception handling (no secrets in logs).
  - `process_pending()`: Bounded retry (max 5 attempts) for failed notifications.
  - `send_template_notification()`: Convenience wrapper that pulls subject/body from `TEMPLATES`.
- `.env` is loaded via `load_dotenv()` in `config.py:4` using a project-root-relative path. No secrets are printed, overwritten, or recreated.

## 2. Verified Reason for the Admin Session-Termination Message

### Root Cause
When an administrator ends a user's session via `POST /admin/users/<int:user_id>/revoke-session/<int:session_id>`:

1. **`routes/admin.py:revoke_user_session()`** sets `UserSession.revoke()` with `ended_by_admin=True` and `termination_reason = 'Session revoked by administrator (...)'`.
2. On the affected user's next request to a protected route, **`app.py:enforce_session_validity()`** (the `before_request` handler) detects the revoked session.
3. Because `ended_by_admin == 1`, the handler sets the flash message:
   > "Your session was ended by a StudyHub administrator. Please sign in again."
4. The user is redirected to `/login` where the flash message is rendered.

**Previously the message was generic** ("Your session has expired. Please log in again.") because the `ended_by_admin` column was not being checked in `app.py:enforce_session_validity()`. The fix added a branch that inspects `ended_by_admin` and `termination_reason` to distinguish administrator-terminated sessions from ordinary expirations, then displays the specific message via `flash(message, 'warning')`.

### Verification
- TEST 17 in `tests/tests_session_management.py` confirms the specific admin-ended message appears.
- TEST 10 in `tests/tests_account_deletion_and_session_email.py` confirms the flash message is rendered on the login page after redirect.
- TEST E in `tests/tests_session_edge_cases.py` confirms the audit log records the admin's identity.

## 3. Email Templates and Delivery Behavior Implemented

### Account Self-Deletion Email
- Sent via `send_template_notification('account_deleted_self', user['email'], ...)` at `routes/user.py:149-158`.
- Only sent after `User.delete()` returns `True`.
- If delivery fails, the user sees: "Your account has been deleted successfully. However, we could not send the confirmation email..."
- Dedupe key: `account_deleted_self:<user_id>`.

### Admin Deletion Email
- Sent via `send_template_notification('account_deleted_by_admin', user_email, ...)` at `routes/admin.py:472-480`.
- Email address is captured **before** the database record is modified.
- Only sent after the deletion is successful.
- Dedupe key: `account_deleted_by_admin:<user_id>`.

### Session Termination Email (Optional)
- Sent via `send_template_notification('session_terminated', user['email'], ...)` at `routes/admin.py:799-806` (single session) and `routes/admin.py:854-861` (all sessions).
- **Only sent when the revocation succeeds** (guarded by `revoked` flag set inside the `try` block).
- Dedupe keys: `session_terminated:<session_id>` and `session_terminated_all:<user_id>`.

### Reliability Features
- Deduplicated via `dedupe_key` — duplicate requests return `RESULT_SKIPPED`.
- Failed sends are queued for retry via `process_pending()` with a maximum of 5 attempts.
- `deliver()` catches SMTP exceptions and logs only the exception type and masked email address.
- No real emails are sent during tests (all `OTPManager.send_email` calls are mocked).

## 4. Files Changed

| File | Change |
|------|--------|
| `routes/admin.py` | Session-termination emails only sent on successful revocation (moved inside `try` with `revoked` guard) |
| `app.py` | `enforce_session_validity()` distinguishes admin-ended sessions via `ended_by_admin` column |
| `utils/notifications.py` | New module: `NotificationService` with `send_or_enqueue`, `deliver`, `process_pending`, `send_template_notification`, and `TEMPLATES` |
| `utils/otp.py` | Added `send_notification_email()` method to `OTPManager` |
| `models/user.py` | `User.delete()` and `User.permanent_delete()` now return boolean success indicator |
| `routes/user.py` | `confirm_delete_account()` sends notification email after successful deletion; handles email failure gracefully |
| `tests/tests_account_deletion_and_session_email.py` | Added 12 tests covering all notification and session-termination scenarios |
| `tests/tests_session_management.py` | TEST 17 added for specific admin-ended session message |
| `config.py` | `load_dotenv()` path uses project root (ensures `.env` is found regardless of CWD) |

## 5. Tests Executed and Results

### `tests/tests_session_management.py` — 72 passed, 0 failed
- TEST 1-7: Login session creation, conflict detection, replacement, revocation, logout
- TEST 8-9: Failed login recording, concurrent login safety
- TEST 10-13: Conflict screen UI, admin session revocation, CSRF enforcement
- **TEST 17**: Admin-ended session shows specific flash message ✓
- **TEST 18**: User self-deletion sends confirmation email ✓
- **TEST 19**: Admin deletion sends notification email ✓

### `tests/tests_session_edge_cases.py` — 29 passed, 0 failed
- Session expiry handling, user self-revocation, admin audit logging, rate limiting

### `tests/tests_otp_delivery.py` — 44 passed, 0 failed
- OTP email delivery, resend limits, verification rate limiting

### `tests/tests_project.py` — 98 passed, 0 failed
- Application startup, route registration, template parsing, public routes, security checks

### `tests/tests_account_deletion_and_session_email.py` — 43 passed, 0 failed
- TEST 1-7: Template keys, persistence, deduplication, retry, max attempts, template notification
- **TEST 8**: Admin session revoke sets `ended_by_admin` and sends `session_terminated` email ✓
- **TEST 9**: Admin revoke-all sends notification ✓
- **TEST 10**: Admin-ended session shows correct flash message ✓
- **TEST 11**: User self-deletion sends `account_deleted_self` email ✓
- **TEST 12**: Admin deletion sends `account_deleted_by_admin` email ✓

**Total: 286 tests passed, 0 failed.**

## 6. Remaining SMTP Configuration / Manual Steps

1. **`.env` file**: Must contain valid SMTP credentials (`MAIL_USERNAME`, `MAIL_PASSWORD`) for real email delivery. The application does not set default SMTP credentials.
2. **Email-not-configured path**: When SMTP is not configured, `OTPManager.is_configured()` returns `False`, and `send_otp_email()` emails are skipped (OTP is stored in the Flask session for test access). The `NotificationService.deliver()` function returns `False` when SMTP is not configured, and notifications are queued with `status='pending'` for later retry.
3. **Notification retry sweep**: `NotificationService.process_pending()` must be invoked periodically (e.g., via a cron job or background worker) to retry failed notifications. This is not automated in the current codebase.
4. **Admin credentials**: The default admin account (`admin` / `admin@123`) must be changed in production via the admin dashboard or database.
