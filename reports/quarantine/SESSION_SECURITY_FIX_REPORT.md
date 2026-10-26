# Session Security Fix Report

## Overview

This report documents the root-cause analysis and fixes for the student session-conflict screen and the admin User Security page session-termination controls.

---

## 1. Root Cause of Student Session-Conflict Issues

### Symptom
The student login conflict screen showed a confusing interface with multiple technical details (session IDs, database IDs, IP addresses) and too many action buttons ("End Other Session & Login Now", "Keep Existing Session", "Cancel").

### Root Cause
The template (`templates/auth/login.html`) was rendering raw conflict-session data including internal IDs and IP addresses. Additionally, the date/time fields were rendered using direct `strftime()` calls that broke when the SQLite database returned datetime values as strings (producing `AttributeError` and a 500 error).

### Fix
- Replaced the multi-button interface with two clear actions: **"End Other Session & Login Now"** and **"Cancel"**.
- Removed the "Keep Existing Session" button (it duplicated the Cancel behavior).
- Removed all technical identifiers (session IDs, database IDs, IP addresses) from the student-facing display.
- Added a `format_datetime` Jinja2 filter that safely handles both `datetime` objects and string values, formatting them as readable local dates (e.g., "Last active: 24 Oct 2026, 4:18 PM").
- Added a JavaScript confirmation dialog (`confirm()`) to the "End Other Session" button to prevent accidental clicks.
- Added `data-prevent-double-click` attribute to prevent duplicate form submissions.

### Server-Side Behavior
The conflict screen posts to the existing `/login` endpoint with `replace_session=1`. The server-side flow (`_handle_replace_choice` → `_complete_login`) already:
- Validates the user via `LOGIN_VERIFIED_USER` stored in the Flask session.
- Revokes all existing active sessions atomically within a transaction.
- Creates a new authenticated session.
- Records the event in `LoginHistory`.

OTP verification is completed **before** the conflict screen is shown, so the existing OTP flow is never bypassed.

---

## 2. Why Admin End / End All Controls Failed

### Root Cause Analysis

#### 2.1 Incorrect Redirect Target
The `revoke_user_session` and `revoke_all_user_sessions` routes in `routes/admin.py` redirected to `admin.user_login_history` (a user-specific page) instead of `admin.user_security` (the page the admin was on). This gave the appearance that the buttons "didn't work" — the admin was taken to a different page after clicking.

**Fix:** Changed both redirects to `admin.user_security`.

#### 2.2 Transaction Rollback on Logging Failure
Both revocation routes wrapped the session revocation together with `LoginHistory.create()` and `AdminAuditLog.log()` calls in a single transaction. If either logging call failed, the entire transaction (including the session revocation) was rolled back silently.

**Fix:** Separated the session revocation from the logging calls. The revocation runs in the main transaction; logging calls are wrapped in individual `try/except` blocks so that a logging failure does not undo the revocation.

#### 2.3 Missing Session Ownership Validation
The `revoke_user_session` route checked that the session existed but did not verify that the session belonged to the specified user. This could allow an admin to accidentally revoke a session belonging to a different user.

**Fix:** Added `row.get('user_id') != user_id` check with a clear error message: "Session not found or does not belong to this user."

#### 2.4 Missing "End All" Button on Security Page
The User Security page (`templates/admin/user_security.html`) only had an individual "End" button per session row. There was no "End all sessions for this user" button on this page.

**Fix:** Added an "End all" button alongside each session row's "End" button, posting to the existing `admin.revoke_all_user_sessions` endpoint.

### Verification of Revoked Session Denial
The `enforce_session_validity` before-request handler in `app.py` validates the server-side session on every protected request (except static files and admin endpoints). When a session is revoked:
1. The `enforce_session_validity` handler queries `UserSession.get_by_token_hash()`.
2. If the session status is not `active` (i.e., `revoked`, `logged_out`, or `expired`), the session token is removed from the Flask session and the user is redirected to the login page with an explanatory flash message.
3. This ensures that even if the browser still holds the signed Flask cookie, the server-side session record is authoritative.

### Admin Session Isolation
Admin sessions use Flask's built-in session mechanism (storing `admin_id` and `admin_name` directly in the signed cookie), **not** the custom `UserSession` model. The `enforce_session_validity` handler skips admin endpoints (`_is_admin_endpoint()`). This means admin session revocation works through a different mechanism (`admin_logout`), and the admin's own session is always preserved when revoking student sessions.

---

## 3. Database Schema and Transaction Safety

### Schema (No Changes Required)
The `user_sessions` table uses **soft deletes** — sessions are marked with `status = 'revoked'` (or `logged_out`, `expired`) rather than being deleted. This preserves audit history and allows the `enforce_session_validity` handler to distinguish between different termination reasons.

Indexes exist on:
- `session_token_hash` — used by token lookup on every request
- `user_id` — used by active-session queries
- `status` — used by revocation and active-session queries
- `expires_at` — used by expiration checks

No schema changes were needed.

### Transaction Correctness
All session mutations use `immediate_transaction()`, which ensures atomicity. The `_complete_login` function revokes old sessions and creates the new session in a single transaction, preventing race conditions between concurrent logins.

---

## 4. Tests Added

### Student Session-Conflict Screen (tests_session_management.py)
- **TEST 13:** Conflict screen displays explanation text, "End Other Session" button, "Cancel" button, and does NOT show "Keep Existing Session" or session IDs.
- **TEST 4 (updated):** Cancel preserves the existing session.
- **TEST 5 (updated):** "End Other Session & Login Now" revokes the old session, creates a new one, and revokes the old session on the next request.

### Admin Session Controls (tests_session_management.py)
- **TEST 14:** Admin can terminate one selected active session → session marked `revoked` in DB → revoked session blocked from `/profile`.
- **TEST 15:** Admin "End All" revokes only sessions belonging to the selected user → zero active sessions after.
- **TEST 16:** Non-admin blocked from session endpoints; missing CSRF token rejected without revoking the session.

### Template Tests (tests_project.py)
- Footer shows current numeric year (e.g., `© 2026`).
- Broken Python attribute expression absent from rendered HTML.
- Sensitive files (`.env`, `.py`, `.git`, database files, logs) not served.
- Path traversal attempts blocked.
- No sensitive credentials in rendered pages.
- No debug tracebacks in 404/500 error pages.

### Test Results
```
tests_project.py:        98 passed, 0 failed
tests_otp_delivery.py:   44 passed, 0 failed
tests_session_management.py: 57 passed, 0 failed
tests_session_edge_cases.py: 29 passed, 0 failed
Total: 228 passed, 0 failed
```

---

## 5. Files Changed

| File | Change |
|------|--------|
| `app.py` | Added `format_datetime` Jinja2 template filter |
| `routes/admin.py` | Fixed redirect targets; separated logging from revocation; added session ownership validation |
| `templates/auth/login.html` | Simplified conflict screen; removed sensitive info; fixed date formatting; added confirmation + double-click prevention |
| `templates/admin/user_security.html` | Added "End all sessions" button alongside individual "End" |
| `tests/tests_session_management.py` | Updated assertions; added TEST 13-16 |
| `tests/tests_project.py` | Added security and footer-year tests |

## 6. Manual Verification Steps

1. **Student conflict screen:** Log in on two browsers; the second login should show a clean conflict screen with device name, OS, and login/last-active dates.
2. **Admin End single session:** Log in as admin, go to User Security, click "End" on any session — the session should be revoked and disappear from the active list.
3. **Admin End all:** Click "End all" next to any user's session — all that user's sessions should be revoked.
4. **Revoked session denial:** After revoking a session, try accessing `/profile` from that browser — it should redirect to login.
5. **Footer year:** Visit any auth page footer — it should show `© 2026 StudyHub. All rights reserved.`

No manual database migration is required. All changes are application-layer only.
