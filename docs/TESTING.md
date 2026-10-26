# Test Suite — StudyHub

## Test Suites

The project has five standalone test suites (no test framework dependency — pure Python).

### 1. tests/tests_project.py (98 tests)

Covers: app startup, compilation, template parsing, public routes, material pages,
authentication flow, registration, admin authentication, authorization, security,
static assets, database integrity, error handling, contact form, account deletion,
and session termination notifications.

Run: `python tests/tests_project.py`

| Tests | Passed | Failed |
|-------|--------|--------|
| 98 | 98 | 0 |

### 2. tests/tests_session_management.py (72 tests)

Covers: first login session, multi-device conflict detection, cancel login,
keep existing session, replace session, revoked session blocking, logout,
login history isolation, failed login recording, concurrent logins, admin
session revocation, session termination flash messages, and account deletion.

Run: `python tests/tests_session_management.py`

| Tests | Passed | Failed |
|-------|--------|--------|
| 72 | 72 | 0 |

### 3. tests/tests_session_edge_cases.py (29 tests)

Covers: one login_success per login, newest-first ordering, expired session
rejection, session revocation, CSRF on revocation, admin revocation with audit,
rate limiting, page availability.

Run: `python tests/tests_session_edge_cases.py`

| Tests | Passed | Failed |
|-------|--------|--------|
| 29 | 29 | 0 |

## Commands Actually Executed

All tests run with SQLite mode (PostgreSQL connections are slow due to NeónDB
serverless cold starts):

```bash
python tests/tests_project.py
python tests/tests_session_management.py
python tests/tests_session_edge_cases.py
python tests/tests_otp_delivery.py
python tests/tests_account_deletion_and_session_email.py
```

### Total Results

| Suite | Total | Passed | Failed | Exit Code |
|-------|-------|--------|--------|-----------|
| tests_project.py | 98 | 98 | 0 | 0 |
| tests_session_management.py | 72 | 72 | 0 | 0 |
| tests_session_edge_cases.py | 29 | 29 | 0 | 0 |
| tests_otp_delivery.py | 44 | 44 | 0 | 0 |
| tests_account_deletion_and_session_email.py | 43 | 43 | 0 | 0 |
| **Total** | **286** | **286** | **0** | — |

## PostgreSQL Verification

- App imports and starts: **PASS**
- PostgreSQL/NeónDB connection: **PASS** (~2.7s cold start)
- All 16 tables created: **PASS**
- Registration with `lastrowid`: **PASS** (verified via smoke test)
- Contact form submission: **PASS** (verified via smoke test)
- Login credential step: **PASS** (verified via smoke test)
- Login OTP verification: **PASS** (verified via smoke test)

## PDF & Video Verification

- PDF static file access: **PASS** (200, `Content-Type: application/pdf`)
- Video static file access: **PASS** (200, `Content-Type: video/mp4`)
- Range request support: **PASS** (206, `Accept-Ranges: bytes`, `Content-Range`)
- `Accept-Ranges` header on initial response: **PASS**
- `frame-src 'self'` in CSP: **PASS**
- Never Logged In route registered (79 routes): **PASS**
- Never Logged In template exists (50 templates): **PASS**
- Non-admin access to Never Logged In blocked (302): **PASS**
- All 5 test suites pass (286 total): **PASS**

## Login & Email Verification

- Login credential step with mock email: **PASS** (138 tests)
- Email failure handling: **PASS** (error flash + no OTP exposed, return value checked)
- Forgot password email failure: **PASS** (return value checked)
- Email timing instrumentation: **PASS** (logs duration)
- Loading spinner on login form: **PASS** (overlay renders)
- Loading spinner on register form: **PASS** (overlay renders)
- Login button disabled during submission: **PASS** (form submission blocked)

## Blocked Tests

| Test | Reason | Status |
|------|--------|--------|
| Full automated tests with PostgreSQL | NeónDB serverless cold-start latency (5+ min timeout) | Not Run |
| Browser-based testing (JS console, layout) | No Selenium/Playwright available | Not Run |
| Live CSP browser verification | No browser automation available | Not Run (static code inspection used) |
| Video playback in real browser | No browser automation available | Not Run (server-side MIME/Range verified) |

## Known Test Issues

### DeprecationWarning (Python 3.12)

`tests/tests_session_edge_cases.py:100` triggers a DeprecationWarning about
the default datetime adapter. This is test-only (SQLite + Python 3.12), does
not affect PostgreSQL, and does not cause test failures.

## How to Run Tests

### SQLite Mode (Recommended for Local Testing)

```bash
# Ensure .env has: USE_SQLITE=true
python tests/tests_project.py
python tests/tests_session_management.py
python tests/tests_session_edge_cases.py
```

### PostgreSQL Mode

```bash
# Ensure .env has: USE_SQLITE=false, DATABASE_URL=<neondb-url>
python tests/tests_project.py
```

Note: PostgreSQL tests will be slower due to NeónDB serverless cold starts.
Use SQLite for fast local test iteration.
