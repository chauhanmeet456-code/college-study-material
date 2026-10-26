# Changelog

All notable changes to this project are documented in this file.
This entry records the complete audit, error-fixing, performance optimization,
CSP fixes, PDF/video fixes, and documentation work performed in these sessions.

## [2026-10-26] — OTP Email Delivery Ambiguous-SMTP Handling and Login Flow Fix

### Root Cause
`SMTPServerDisconnected` raised during `server.send_message()` was treated as a
definitive failure.  Some SMTP providers (e.g. Gmail) accept the message data
and close the connection before sending the final `250 OK`, causing
`socket.timeout` → `SMTPServerDisconnected`.  The old code caught this and
returned `False`, which caused `_handle_credential_step()` to **clear the
pending OTP from the session** — so the email arrived but the user could never
enter the code.

### Fixes

- **`utils/otp.py`** — Added `EmailDeliveryResult` class (SUCCESS / FAILURE /
  AMBIGUOUS).  `send_email()` now tracks the SMTP phase and returns:
  - `SUCCESS` — server accepted the message (250 reply received)
  - `AMBIGUOUS` — `SMTPServerDisconnected` during `send_message()` (message
    data was transmitted; server disconnected before final 250 reply)
  - `FAILURE` — connection/TLS/auth error or server disconnect before DATA
  Server-side disconnect during `quit()` is now skipped when data was sent,
  avoiding an extra timeout delay.
- **`config.py`** — Added `MAIL_SEND_TIMEOUT` (default 8s) applied to the
  socket only during the message-send phase, reducing worst-case delay from
  ~13s to ~10s on silent disconnects.
- **`routes/auth.py`** — `_handle_credential_step()` now preserves the pending
  OTP and login state on `AMBIGUOUS` delivery, showing the OTP verification
  screen with a truthful "may have been sent" message instead of clearing the
  session.  Added `OTPManager.is_otp_valid()` check to prevent duplicate OTP
  generation from double-submissions.  `send_otp()` API endpoint returns
  `ambiguous: true` with HTTP 202 and does **not** increment the resend counter.
- **`templates/auth/login.html`** — Added `login_submitted` hidden field for
  double-submit prevention; JS now disables the submit button, shows loading
  overlay, and restores button state on error.  Resend button is disabled
  during AJAX requests and handles `ambiguous` responses.
- **`utils/otp.py`** — Added `is_otp_valid()` static method (no side effects).

### Tests
- Added TEST 14: Ambiguous SMTP disconnect preserves OTP state and shows OTP screen
- Added TEST 15: Ambiguous API endpoint does not increment resend counter
- Added TEST 16: Duplicate login submissions do not create multiple OTP challenges
- Added TEST 17: No OTP exposed in logs on ambiguous delivery
- Updated all 5 test suites to use `EmailDeliveryResult` return values
- All 303 tests pass (61 + 72 + 29 + 43 + 98)

### Documentation
- Quarantined `docs/ARCHITECTURE.md` (outdated: references non-existent files `IMPLEMENTATION_REPORT.md`, `NO_ERROR_REPORT.md`; content superseded by `Design.md`)
- Quarantined `docs/TASKS.md` (stale task tracking with outdated checkbox states)
- Quarantined `docs/MEMORY.md` (content consolidated into `docs/SETUP.md`, `docs/README.md`, `docs/RULES.md`)
- Created `docs/README.md` as a documentation index with quick-start and project structure
- Created `docs/SETUP.md` as a quick-start setup guide
- Created `docs/CLEANUP_INVENTORY.md` (Phase 1 file audit and classification)
- Created `docs/SECURITY_AUDIT_REPORT.md` (consolidated 7 security fix reports)
- Created `docs/PERFORMANCE_OPTIMIZATION_REPORT.md` (consolidated performance reports)
- Created `docs/CLEANUP_REPORT.md` (docs cleanup inventory)
- Added `.pytest_cache/` to `.gitignore`

### Performance Optimizations
- Added `MAIL_CONNECT_TIMEOUT` config (default 3s) for faster SMTP connection failure
- `OTPManager.send_email()` uses `socket.setdefaulttimeout()` to detect unreachable SMTP servers in 3s instead of 10s
- Per-request caching of `get_current_user()`/`get_current_admin()` via Flask's `g` object (eliminates redundant DB queries within a request)
- Eliminated redundant 3rd DB query in `enforce_session_validity()` for revoked sessions
- Combined 5 admin dashboard count queries into 1
- Added DNS prefetch and preconnect hints to all 6 template files (base, admin base, login, register, forgot_password, reset_password)

### Code Fixes
- Fixed `enforce_session_validity()` to reuse `session_row` for status determination (was making 3 queries instead of 2 for revoked sessions)

## [2026-10-23] — Complete Audit, Performance Optimization, CSP & Media Fixes

## [2026-10-24] — Login Email Timeout Fix, Loading Spinner, Never Logged In Feature

### Login & Email Fixes

- **`routes/auth.py`** — Fixed login credential step: now checks
  `send_otp_email()` return value. On failure, shows error message with
  development OTP instead of false "OTP sent" success message.
- **`routes/auth.py`** — Fixed forgot password flow: same return-value
  check and error handling.
- **`utils/otp.py`** — Added timing instrumentation to `send_email()`:
  logs duration and error type (not credentials). Added `flush=True` for
  immediate log visibility.
- **`app.py`** — Added `before_request`/`after_request` timing hooks:
  logs requests taking >1 second with `[TIMING]` prefix.
- **`utils/security.py`** — Added `Accept-Ranges: bytes` header to all
  responses (fixes video seeking — browsers need this on initial response).

### Loading Spinner

- **`templates/auth/login.html`** — Added centered circular spinner overlay
  with "Logging you in..." / "Verifying your code..." messages.
- **`templates/auth/register.html`** — Added spinner overlay with
  "Creating your account..." message.
- Both overlays: CSS-animated spinner (no external dependency), smooth fade,
  `role="status"` for accessibility, bypasses for conflict-resolution buttons.

### Never Logged In Feature

- **`models/user.py`** — Added `get_never_logged_in_users()` method:
  queries users where `last_login IS NULL`, with search, status filter, pagination.
- **`routes/admin.py`** — Added `/users/never-logged-in` route (protected by
  `@admin_required`), reuses existing auth pattern.
- **`templates/admin/users.html`** — Added "Never Logged In" button next to
  Deletion Requests and Deleted Users.
- **`templates/admin/never_logged_in.html`** — New page with user table
  (ID, Name, Email, Registration Date, Status, Last Login), search, filter,
  pagination, and empty state.
- `last_login` field already exists and is set only after successful OTP
  verification (not on credential entry or failed attempts).

### Test Results

| Suite | Tests | Passed | Failed |
|-------|-------|--------|--------|
| tests_project.py | 69 | 69 | 0 |
| tests_session_management.py | 40 | 40 | 0 |
| tests_session_edge_cases.py | 29 | 29 | 0 |
| **Total** | **138** | **138** | **0** |

### Performance Optimizations

- **`utils/database.py`** — Added `PostgreSQLConnectionPool` class and
  `PooledConnection` wrapper for thread-local connection pooling:
  - Reuses PostgreSQL connections instead of creating new ones per call
  - Zero-network validation using `conn.pgconn.status` (no `SELECT 1` round-trip)
  - Eliminates NeónDB serverless cold-start on repeated connections
  - Homepage warm load time: 8-11s → 1.6s (85% improvement)
- **`models/settings.py`** — Added thread-local settings cache (60s TTL):
  - `Settings.get_all()` caches results, avoiding DB query on every page load
  - `Settings.set()` invalidates cache immediately
  - Simple routes (/about, /login) now load in <0.05s
- **`routes/main.py`** — Combined 3 count queries into 1, removed redundant import

### Security (CSP)

- **`utils/security.py`** — Fixed Content-Security-Policy:
  - Added `https://fonts.googleapis.com` to `style-src` (Google Fonts CSS was blocked)
  - Added `https://fonts.gstatic.com` to `font-src` (Google Fonts font files were blocked)
  - Added `'self'` to `frame-src` (PDF/video iframes were blocked)
  - Added `https://cdn.jsdelivr.net` to `connect-src` (Bootstrap source-map requests blocked)

### PDF & Video Fixes

- **PDF files blocked** — `frame-src` lacked `'self'`, blocking same-origin iframe
  used by the PDF viewer template
- **Video playback issues** — Added `Accept-Ranges: bytes` header to responses,
  enabling browser Range requests for video seeking
- **Image uploads blocked** — `ALLOWED_EXTENSIONS` missing image extensions
  (png, jpg, jpeg, gif, webp)
- **Admin mobile navigation** — Sidebar toggle button was missing from the
  template despite CSS/JS support existing

### Added

- `init_postgres.py` — PostgreSQL schema initialization (16 tables, triggers, indexes)
- `migrate_to_postgres.py` — SQLite-to-PostgreSQL migration (idempotent, FK-safe)
- `PDF_VIDEO_FIX_REPORT.md` — PDF/video diagnosis and fix report
- `DATABASE_SETUP.md` — Database configuration guide
- `TESTING.md` — Test documentation with results
- `PROJECT_REPORT.md` — Complete project status report
- `PERFORMANCE_REPORT.md` — Performance analysis and optimization report
- `reports/cleanup_candidates.md` — File cleanup analysis

### Changed

- `config.py` — Added `DATABASE_URL`/`USE_POSTGRES`; added image extensions to `ALLOWED_EXTENSIONS`
- `utils/database.py` — Added PostgreSQL support, connection pooling, `lastrowid` compatibility
- `utils/security.py` — Fixed CSP directives, added `Accept-Ranges: bytes`
- `templates/admin/base.html` — Added sidebar toggle button for mobile
- `routes/admin.py` — Updated flash messages to include images
- `models/material.py` — Fixed PostgreSQL INSERT syntax for `Favorite.add()`
- `requirements.txt` — Restored from corruption, added `psycopg[binary]`
- `tests/*.py` — Fixed `os.chdir(root)`, removed BOM, removed unused imports
- `README.md` — Rewritten with PostgreSQL/mobile/responsive documentation
- `docs/MEMORY.md`, `docs/NO_ERROR_REPORT.md` — Updated with latest findings

### Fixed

- **requirements.txt corrupted** — Full task description pasted into file
- **BOM character** in `tests/tests_session_management.py`
- **Test working directory** — Added `os.chdir(root)` to test files
- **13 unused imports** removed across 8 files
- **CSP misconfiguration** — Google Fonts and same-origin iframes blocked
- **Slow website loading** — 8-11s → 1.6s after connection pooling
- **PostgreSQL `cursor.lastrowid`** missing — Added `RETURNING id` support
- **PostgreSQL `INSERT IGNORE`** syntax error — Changed to `ON CONFLICT DO NOTHING`
- **Image upload blocked** — Added image extensions to `ALLOWED_EXTENSIONS`
- **Admin mobile nav broken** — Added missing sidebar toggle button

## Test Results

| Suite | Tests | Passed | Failed |
|-------|-------|--------|--------|
| `tests_project.py` | 69 | 69 | 0 |
| `tests_session_management.py` | 40 | 40 | 0 |
| `tests_session_edge_cases.py` | 29 | 29 | 0 |
| **Total** | **138** | **138** | **0** |
