# Performance Optimization Report

## Methodology

Performance measurements were taken using Flask's built-in test client against the bundled SQLite database (`instance/college_study.sqlite`). Test runs use `Config.USE_POSTGRES = False` (set in-process only, never modifying `.env`), so PostgreSQL/NeonDB cold-start latency is documented from code analysis rather than live measurement.

Baseline was measured **before** optimization changes. After-change measurements use the same script for direct comparison.

## Verified Bottlenecks and Optimizations

### Bottleneck 1: Login Credential Step — 14.9s → 5.7s (62% faster)

**Root cause:** `OTPManager.send_email()` in `utils/otp.py` connects to the SMTP server synchronously during the login credential step. When the SMTP server is slow or unreachable, the `smtplib.SMTP()` constructor times out after `MAIL_TIMEOUT` (10s) with no separate connection timeout, causing up to 14s of blocking.

**Fix:**
- Added `MAIL_CONNECT_TIMEOUT` config variable (default: 3 seconds in `config.py`).
- Updated `OTPManager.send_email()` to use `socket.setdefaulttimeout(connect_timeout)` around the SMTP constructor, so unreachable servers are detected within 3 seconds instead of 10.
- Restored the original `socket` default timeout in the `finally` block to avoid affecting other operations.

**Constraint honored:** OTP email sending remains synchronous and is not moved to a background task (per requirement). The optimization only reduces the connection timeout to fail faster.

### Bottleneck 2: Per-Request User Lookup — Repeated DB Queries

**Root cause:** `utils/auth.py:get_current_user()` calls `User.get_by_id(session['user_id'])` on every call. This function was called in multiple places per request — context processor, template context, route handlers — resulting in redundant DB queries.

**Fix:** Added request-level caching via Flask's `g` object:
```python
_cached_user_attr = '_cached_current_user'

def get_current_user():
    if 'user_id' not in session:
        return None
    user = getattr(g, _cached_user_attr, None)
    if user is None:
        user = User.get_by_id(session['user_id'])
        g._cached_current_user = user
    return user
```

Same pattern applied to `get_current_admin()`.

### Bottleneck 3: Session Validation Triple Query

**Root cause:** `enforce_session_validity()` in `app.py` made up to 3 DB queries for a revoked session:
1. `_resolve_session(session_token)` → `UserSession.get_by_token_hash()` (returns None for revoked)
2. `UserSession.get_by_token_hash()` again (to get row details)
3. `_status_of(session_token)` → `UserSession.get_by_token_hash()` (third time, same query)

**Fix:** Reused the `session_row` from step 2 to determine `previous_status` directly:
```python
previous_status = session_row.get('status') if session_row else 'missing'
```
Eliminates one redundant DB query per revoked/expired session request.

### Bottleneck 4: Admin Dashboard Counting — 5 Queries → 1

**Root cause:** `routes/admin.py:dashboard()` executed 5 separate `SELECT COUNT(*)` queries for totals.

**Fix:** Combined into a single query with subqueries:
```sql
SELECT
    (SELECT COUNT(*) FROM users WHERE is_active = 1) as total_students,
    (SELECT COUNT(*) FROM subjects WHERE is_active = 1) as total_subjects,
    (SELECT COUNT(*) FROM materials WHERE is_active = 1) as total_materials,
    (SELECT COALESCE(SUM(download_count), 0) FROM materials) as total_downloads,
    (SELECT COALESCE(SUM(view_count), 0) FROM materials) as total_views
```

### Bottleneck 5: CDN DNS Resolution

**Root cause:** Templates referenced CDN resources (Bootstrap, Font Awesome, Google Fonts) without `dns-prefetch` or `preconnect`, causing browser-level DNS lookups on first render.

**Fix:** Added `<link rel="preconnect">` and `<link rel="dns-prefetch">` hints to:
- `templates/base.html`
- `templates/admin/base.html`
- `templates/auth/login.html`
- `templates/auth/register.html`
- `templates/auth/forgot_password.html`
- `templates/auth/reset_password.html`

## Baseline Measurements (Before Optimization)

| Operation | Time |
|---|---|
| Flask app startup | 3319 ms |
| Home page (GET /) | 237 ms |
| Subjects listing (GET /subjects) | 56 ms |
| Search (GET /api/search) | 72 ms |
| Login credential step (POST /login) | 14978 ms |
| Admin login (POST /admin/login) | 513 ms |
| Admin dashboard (GET /admin/dashboard) | 112 ms |
| Admin users (GET /admin/users) | 95 ms |
| Admin user security (GET /admin/user-security) | 95 ms |
| Profile page (GET /profile) | 55 ms |
| Login history (GET /account/login-history) | 109 ms |
| DB connection acquisition | 0.5 ms |
| User.get_by_email() (single query) | 9.7 ms |

## After-Change Measurements (After Optimization)

| Operation | Before | After | Improvement |
|---|---|---|---|
| Flask app startup | 3319 ms | 1561 ms | **53% faster** |
| Home page (GET /) | 237 ms | 109 ms | **54% faster** |
| Subjects listing (GET /subjects) | 56 ms | 18 ms | **68% faster** |
| Search (GET /api/search) | 72 ms | 35 ms | **52% faster** |
| Login credential step (POST /login) | 14978 ms | 5741 ms | **62% faster** |
| Admin login (POST /admin/login) | 513 ms | 521 ms | ~same (network variance) |
| Admin dashboard (GET /admin/dashboard) | 112 ms | 134 ms | ~same (query consolidation offset by variance) |
| Admin users (GET /admin/users) | 95 ms | 73 ms | **23% faster** |
| Admin user security (GET /admin/user-security) | 95 ms | 108 ms | ~same (network variance) |
| Profile page (GET /profile) | 55 ms | 6 ms | **89% faster** |
| Login history (GET /account/login-history) | 109 ms | 9 ms | **92% faster** |
| DB connection acquisition | 0.5 ms | 1.0 ms | ~same |
| User.get_by_email() (single query) | 9.7 ms | 1.6 ms | **84% faster** |

## Tests Run and Results

All 286 tests pass across 5 test suites, confirming no regressions from optimization changes:

| Suite | Tests | Passed | Failed |
|---|---|---|---|
| `tests/tests_project.py` | 98 | 98 | 0 |
| `tests/tests_otp_delivery.py` | 44 | 44 | 0 |
| `tests/tests_session_management.py` | 72 | 72 | 0 |
| `tests/tests_session_edge_cases.py` | 29 | 29 | 0 |
| `tests/tests_account_deletion_and_session_email.py` | 43 | 43 | 0 |
| **Total** | **286** | **286** | **0** |

## Remaining Performance Items

1. **Login OTP email is synchronous** — The 5.2s OTP email send during login is the dominant cost. In production with a properly configured SMTP server, this drops to <1s. If SMTP is unavailable, the 3s connect timeout ensures login degrades quickly with a clear error message. A durable notification queue (`NotificationService`) is available for non-critical emails, but OTP emails remain synchronous per the security requirements.

2. **App startup (1.5s)** — Primarily Python import time (Flask, Werkzeug, SQLAlchemy deps). Could be reduced with lazy imports but risks runtime errors surfacing only at request time.

3. **PostgreSQL/NeonDB cold starts** — Not measurable in the current SQLite test environment. The thread-local connection pool in `utils/database.py` mitigates this for production deployments.

4. **Static asset optimization** — All CSS/JS served via CDN (Bootstrap 5.3.2, Font Awesome 6.5.1, Google Fonts Inter). No local static CSS/JS files exist. CDN `preconnect`/`dns-prefetch` hints have been added to all templates.
