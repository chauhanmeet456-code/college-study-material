# Test Suite — StudyHub

## Test Suites

The project has three standalone test suites (no test framework dependency — pure Python).

### 1. tests/tests_project.py (69 tests)

Covers: app startup, compilation, template parsing, public routes, material pages,
authentication flow, registration, admin authentication, authorization, security,
static assets, database integrity, error handling, contact form.

Run: `python tests/tests_project.py`

| Tests | Passed | Failed |
|-------|--------|--------|
| 69 | 69 | 0 |

### 2. tests/tests_session_management.py (40 tests)

Covers: first login session, multi-device conflict detection, cancel login,
keep existing session, replace session, revoked session blocking, logout,
login history isolation, failed login recording, concurrent logins.

Run: `python tests/tests_session_management.py`

| Tests | Passed | Failed |
|-------|--------|--------|
| 40 | 40 | 0 |

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
```

### Total Results

| Suite | Total | Passed | Failed | Exit Code |
|-------|-------|--------|--------|-----------|
| tests_project.py | 69 | 69 | 0 | 0 |
| tests_session_management.py | 40 | 40 | 0 | 0 |
| tests_session_edge_cases.py | 29 | 29 | 0 | 0 |
| **Total** | **138** | **138** | **0** | — |

## PostgreSQL Verification

- App imports and starts: **PASS**
- PostgreSQL/NeónDB connection: **PASS** (~2.7s cold start)
- All 16 tables created: **PASS**
- Registration with `lastrowid`: **PASS** (verified via smoke test)
- Contact form submission: **PASS** (verified via smoke test)

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
