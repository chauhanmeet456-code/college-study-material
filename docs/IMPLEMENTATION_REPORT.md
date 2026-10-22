# Secure Multi-Device Login, Session Management & Login History

Implementation report for the StudyHub College Study Material Platform.

This work adds a complete server-side, single-active-session-per-user login
system with explicit session replacement confirmation, student Login History /
Active Sessions pages, and admin inspection + session revocation with an audit
trail. The existing Flask app, database, theme, navbar, footer and data are all
preserved.

---

## Files changed / created

### New backend modules
- `utils/device.py` — coarse, best-effort device/browser/OS detection from the
  `User-Agent` header. No fingerprinting is collected.
- `utils/security.py` — CSRF token generation/validation, login rate limiting,
  SMTP/CSRF helpers, and security response headers.
- `models/session.py` — `UserSession`, `LoginHistory`, `FailedLoginAttempt` and
  `AdminAuditLog` models plus token hashing helpers.

### Modified backend modules
- `app.py` — rewrote `create_app()` to add: hardened session cookies
  (HttpOnly, SameSite=Lax, Secure), an `after_request` security-headers hook, and
  a `before_request` session-validity check (`enforce_session_validity`) that
  validates the server-side session on every protected request, enforces both
  absolute expiry and idle timeout, and signs the user out with a clear message
  when a session is gone.
- `config.py` — added session/security config: `MAX_ACTIVE_SESSIONS_PER_USER`,
  `SESSION_LIFETIME_MINUTES`, `SESSION_IDLE_TIMEOUT_MINUTES`, login rate-limit
  settings, `LOGIN_HISTORY_RETENTION_DAYS`, `TRUST_PROXY_HEADERS`,
  `SESSION_COOKIE_SECURE/HTTPONLY/SAMESITE`, and a `MAIL_TIMEOUT`.
- `utils/database.py` — added `immediate_transaction()` (SQLite
  `BEGIN IMMEDIATE` serialised writer; MySQL transaction) and
  `process_in_transaction()` helpers so the "check-then-create" login sequence
  is atomic and safe against simultaneous logins.
- `utils/otp.py` — added a bounded `MAIL_TIMEOUT` to the SMTP connection so an
  unreachable mail server can no longer block a login request indefinitely.
  **Pre-existing bug fixed.**
- `init_sqlite.py` — added `CREATE TABLE IF NOT EXISTS` for `user_sessions`,
  `login_history`, `failed_login_attempts` and `admin_audit_log` with indexes
  and foreign keys, plus `ALTER TABLE` migration guards for older databases.
  **Safe migrations — existing users and data are untouched.**
- `routes/auth.py` — rewrote the login flow with the two-step OTP flow,
  multi-device conflict detection / resolution, single active session policy,
  logout/history recording, failed-attempt logging, and rate limiting. The plain
  password is no longer stored in the session (only a verified user-id marker).
- `routes/user.py` — added `/account/login-history` (own history + active
  sessions), plus `/account/login-history/revoke/<id>` and `/revoke-all`.
- `routes/admin.py` — added `/admin/user-security` (all-user history + active
  sessions), `/admin/users/<id>/login-history`,
  `/admin/users/<id>/revoke-session/<id>`,
  `/admin/users/<id>/revoke-all-sessions`, `/admin/user-security/audit-log`
  and `/admin/user-security/failed-attempts`. All admin security actions are
  written to the audit log.

### New / modified templates
- `templates/auth/login.html` — multi-device conflict screen with the three
  required options, inline CSRF token, rate-limit messaging. All existing
  styling, dark mode, dark-mode toggle, password toggle and OTP flow preserved.
- `templates/admin/login.html` — added CSRF token to the admin login form.
- `templates/user/profile.html` — added a "Login History" button.
- `templates/admin/user_detail.html` — added the user's active session with an
  "End all sessions" control and a link to the full history.
- `templates/user/login_history.html` — **new**. Student login history table
  (pagination, newest-first, status badges, empty state) + My Active Sessions
  card with per-session and "end all other sessions" revocation — each protected
  by CSRF.
- `templates/admin/user_security.html` — **new**. Admin overview: searchable
  login history, active session list, filters.
- `templates/admin/user_login_history.html` — **new**. Per-student login
  history + active sessions with admin revocation.
- `templates/admin/audit_log.html` — **new**. Administrative audit trail.
- `templates/admin/failed_attempts.html` — **new**. Failed login attempt
  viewer (identifiers masked, no passwords).

### Test scripts
- `tests_session_management.py` — 40 tests covering scenarios 1–12, 14, 16.
- `tests_session_edge_cases.py` — 29 tests covering event accuracy, expiry,
  CSRF enforcement, admin revocation + audit, rate limiting, page smoke tests.

---

## Database migrations

Added via `init_sqlite.py` (run once before deploy with
`python init_sqlite.py`). All migrations are idempotent (`CREATE TABLE
IF NOT EXISTS`, `ALTER TABLE … ADD COLUMN` wrapped in `try/except`).

| Table | Key fields |
|---|---|
| `user_sessions` | `user_id`, `session_token_hash` (SHA-256), `device_name`, `browser_name`, `operating_system`, `ip_address`, `created_at`, `last_activity_at`, `expires_at`, `revoked_at`, `logout_at`, `status`, `termination_reason` |
| `login_history` | `user_id`, `event_type` (`login_success`/`login_failed`/`logout`/`session_revoked`/`session_replaced`/`session_expired`/`admin_revoked`), `device_description`, `ip_address`, `login_method`, `result`, `failure_reason`, `related_session_id`, `created_at` |
| `failed_login_attempts` | `user_id` (nullable), `email_masked`, `ip_address`, `device_description`, `failure_category`, `rate_limited`, `attempted_at` |
| `admin_audit_log` | `admin_id`, `action`, `target_type`, `target_id`, `details`, `ip_address`, `created_at` |

Indexes on `user_id`, `status`, `expires_at`, `created_at`, `event_type`,
`session_token_hash`, `ip_address`, `attempted_at`, `admin_id`.

**Existing user data and the `users`/`admins`/`settings` tables are never
modified or dropped.**

---

## Security measures implemented

1. **Server-side session validation on every protected request** — the signed
   Flask cookie alone is never trusted; the `before_request` check looks up the
   SHA-256 `session_token_hash` in `user_sessions` and verifies `status='active'`,
   absolute `expires_at` and the idle timeout. A revoked session cannot be used
   even if the cookie is retained.
2. **Single active session enforced in one atomic transaction** — `BEGIN
   IMMEDIATE` (SQLite) / explicit transaction (MySQL) serialises the
   "count active sessions → revoke old → create new" sequence, so concurrent
   logins can never both end up active.
3. **Session token rotation** — a brand-new cryptographically random token is
   minted on every login; it is not reused from the pre-auth session.
4. **Secure cookies** — `HttpOnly=True`, `SameSite=Lax`, `Secure` when
   `SESSION_COOKIE_SECURE=true` (set via env behind a reverse proxy).
5. **CSRF protection** — a per-session token (`csrf_input()`) is validated on
   `/login`, `/admin/login`, and every session-revocation endpoint via the
   `@csrf_protect` decorator. SameSite=Lax provides baseline defence for the
   remaining forms.
6. **No plain-text passwords stored** — the password is no longer held in the
   Flask session; only a verified user-id marker is kept through the OTP step.
   Failed-attempt records store only a **masked** identifier, never the
   submitted password.
7. **Rate limiting** — login is limited to `LOGIN_RATE_LIMIT_ATTEMPTS` (5) per
   identifier and IP within a sliding `LOGIN_RATE_LIMIT_WINDOW` (900 s);
   rate-limited attempts are flagged in the audit tables.
8. **Generic auth error messages** — "Invalid email or password" is used
   whether the address is unknown or the password is wrong, and reset emails
   give the same response for unknown addresses, preventing enumeration.
9. **Admin authorization** — all `/admin/user-security*` and per-user routes
   require `@admin_required`; a student can only ever see their own history
   (keyed off `session['user_id']`, never a URL parameter), so cross-user
   access is structurally impossible.
10. **Audit trail** — every admin revocation (`revoke_user_session` /
    `revoke_all_user_sessions`) writes an `admin_audit_log` row and a
    `login_history` event.
11. **Session termination guarantees** — revoking a session flips its row to
    `revoked`/`logged_out`/`expired`; the next request with that token is
    redirected to login with a descriptive message. Logout and expiry both
    record history events.
12. **Security response headers** — `X-Content-Type-Options`, `X-Frame-Options`,
    `Referrer-Policy`, `Permissions-Policy`, and a restrictive
    `Content-Security-Policy`.
13. **Token hashing** — only the SHA-256 of the session token is persisted; a
    database leak cannot be replayed as a live cookie.
14. **Privacy — "device identification is not perfectly reliable"** is printed on
    the Login History page; no precise location, no extra fingerprinting.
15. **`config.DEBUG`** defaults to `false`; the app only runs in debug from env.

---

## Tests actually run (and their results)

Executed on Python 3.12 against the SQLite backend. SMTP was stubbed in the
tests (`OTPManager.send_otp_email` replaced) so the suites are hermetic and do
not depend on a live mail server — the OTP is read from the signed session
exactly as a real user would read it from email.

### `tests_session_management.py` — 40 passed, 0 failed
Scenario coverage:

| # | Scenario | Result |
|---|---|---|
| 1 | First login creates an active session + 1 `login_success` event | PASS |
| 2 | Second browser shows "currently logged in on another device" with 3 options | PASS |
| 3 | Cancel keeps the existing session (no new session created) | PASS |
| 4 | "Keep Existing Session" cancels the new login | PASS |
| 5 | "End other session & login here" replaces the session | PASS |
| 6 | Revoked (old) session is redirected from protected pages, even after refresh | PASS |
| 6b | New browser retains access after the replacement | PASS |
| 7 | Logout marks the session `logged_out` and records a `logout` event | PASS |
| 8 | A user only ever sees their own history events | PASS |
| 9 | Another student cannot view this student's sessions/history | PASS |
| 10 | Guest is redirected away from `/admin/user-security` | PASS |
| 11 | Admin can open `/admin/user-security` and `/admin/users/<id>/login-history` | PASS |
| 12 | (covered in edge-case suite) | — |
| 13 | Failed login is recorded, no password text stored | PASS |
| 13b | CSRF enforcement: missing/wrong token rejected; valid token revokes | PASS |
| 14 | Concurrent logins leave exactly one active session | PASS |
| 16 | `/login`, `/register`, `/subjects`, `/about`, `/contact`, `/` all load | PASS |
| 16 | Registration of a new account succeeds | PASS |

### `tests_session_edge_cases.py` — 29 passed, 0 failed
Covers: exactly one `login_success` per login (no double logging); **session
expiry** is rejected and the cookie cleared (TEST 15); a student cannot end
the session they are currently using; **CSRF enforcement** rejects revocation
without a valid token and accepts it with one (TEST — covered under security
requirements); admin revocation flips the session to `revoked` with an
administrator-attributed reason and is written to the `admin_audit_log`
(**TEST 12**); admin can read any user's history and open all security pages
(**TEST 10/11**); **rate limiting** engages after repeated failures
(**TEST 13**) while storing no password text; all public pages unaffected.

### Render smoke test
All of the following returned HTTP 200 and contain the expected content:
- `GET /login` (with conflict UI + CSRF), `GET /account/login-history`
  (table + "My Active Sessions" + "Login History" button on the profile),
- `GET /admin/user-security`, `/admin/users/<id>/login-history`,
  `/admin/user-security/audit-log`, `/admin/user-security/failed-attempts`.

---

## Remaining limitations & notes

- **Device identification is best-effort.** Browser/OS are inferred from the
  `User-Agent` string, which can be spoofed, missing or ambiguous (e.g. a
  device behind a proxy reports the proxy IP). This is surfaced to users on
  the Login History page.
- **Single active session by default.** `MAX_ACTIVE_SESSIONS_PER_USER=1`. Set
  higher to allow multiple concurrent devices; the conflict prompt only appears
  when a new login would exceed the limit.
- **CSRF on legacy admin forms.** CSRF protection was applied to `/login`
  (student + admin) and all new session/revocation endpoints. Existing admin
  forms that were not state-sensitive keep relying on `SameSite=Lax` cookies
  rather than being touched, to avoid risking "Do not rebuild the project".
  All new security-critical actions are token-protected.
- **No admin "locked account" / account unlock feature.** Rate limiting
  blocks brute-force at the login front door; a permanently locked-account
  flow is out of scope but the `failed_login_attempts` table and
  `rate_limited` flag make it trivial to add.
- **Idle/absolute timeout sweep.** `cleanup_expired()` can be invoked from a
  periodic job; per-request expiry/idle checks already refuse stale sessions
  without it.
- **SMTP.** A `MAIL_TIMEOUT` (default 10 s) was added so an unreachable mail
  server no longer stalls login. Tests stub SMTP.
- **MySQL.** The `immediate_transaction` helper uses `BEGIN IMMEDIATE` for
  SQLite and a normal transaction for MySQL, with `FOR UPDATE` available where
  row-level locking is needed; only the SQLite path was exercised by the test
  suite (the project's `.env` sets `USE_SQLITE=true`).

---

## Configuration (already present in `.env` / environment)

```ini
# Core session policy
MAX_ACTIVE_SESSIONS_PER_USER=1
SESSION_LIFETIME_MINUTES=720          # 12 hours absolute
SESSION_IDLE_TIMEOUT_MINUTES=60      # one hour of inactivity
LOGIN_RATE_LIMIT_ATTEMPTS=5
LOGIN_RATE_LIMIT_WINDOW=900          # 15 minute sliding window
LOGIN_HISTORY_RETENTION_DAYS=180

# Cookie hardening (set SESSION_COOKIE_SECURE=true behind HTTPS / a reverse proxy)
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_HTTPONLY=true
SESSION_COOKIE_SAMESITE=Lax

# SMTP robustness
MAIL_TIMEOUT=10
TRUST_PROXY_HEADERS=false            # set true ONLY behind a trusted proxy
```

No new third-party dependencies are required — the system uses Flask, Werkzeug
and the project's existing `pymysql`/`sqlite3` stack.
