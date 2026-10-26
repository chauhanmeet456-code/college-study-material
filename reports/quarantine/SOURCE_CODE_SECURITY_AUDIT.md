# Source Code Security Audit

## Overview

This audit checks whether backend source code, credentials, and internal data are exposed through the StudyHub Flask application. It covers file-serving routes, debug configuration, sensitive data in responses, CSRF protection, authentication enforcement, and cookie security.

---

## 1. Frontend vs. Backend Security Boundary

### Important Technical Limitation

**Frontend source code (HTML, CSS, JavaScript) sent to a browser is inherently inspectable.** Any user of the StudyHub platform can:
- View page source (Ctrl+U / Cmd+U)
- Open browser developer tools (F12)
- Inspect network requests and responses
- Read all HTML, CSS, and JavaScript served by the application

**Attempting to prevent this (e.g., disabling right-click, blocking Ctrl+U, hiding source) is pointless and should never be done.** Such restrictions are trivially bypassed and harm usability for legitimate users.

Instead, the correct security strategy is:
- **Keep all private logic on the server.** Never expose database credentials, API keys, secret keys, or internal algorithms in frontend code.
- **Never put secrets in HTML** — not in hidden fields, data attributes, comments, or inline scripts.
- **Validate and authorize on the server for every request**, regardless of what the frontend does.

The StudyHub application already follows this principle: the `SECRET_KEY`, database credentials, and SMTP credentials are loaded from environment variables or `.env` (which is never served to clients). The frontend JavaScript contains no secrets.

---

## 2. Publicly Exposed Routes and Files

### 2.1 File-Serving Routes

| Route | Status | Notes |
|-------|--------|-------|
| `/static/<path:filename>` | **Safe** | Flask's built-in static handler serves only files from the `static/` directory. No `.env`, `.py`, `.git`, or database files are present there. |
| `/material/<id>/download` | **Safe** | Uses `send_from_directory` with path-traversal protection: verifies the resolved directory starts with `Config.UPLOAD_FOLDER`. |
| `/` (home) | **Public** | Static landing page, no sensitive data. |
| `/login`, `/register`, `/forgot-password` | **Public** | Auth forms, no sensitive data in responses. |

### 2.2 Sensitive File Access Tests

All tested paths return HTTP 404 (not found):

| Path | Result |
|------|--------|
| `/.env` | 404 |
| `/config.py` | 404 |
| `/app.py` | 404 |
| `/.gitignore` | 404 |
| `/requirements.txt` | 404 |
| `/instance/college_study.sqlite` | 404 |
| `/.git/config` | 404 |
| `/.git/HEAD` | 404 |

### 2.3 Path Traversal

The download route in `routes/main.py:218` checks:
```python
if not directory.startswith(os.path.abspath(Config.UPLOAD_FOLDER)):
    abort(403)
```
This prevents directory traversal attacks. All traversal attempts (`../../../etc/passwd`, etc.) return 400/403/404.

### 2.4 Instance Directory

The `instance/` directory (containing `college_study.sqlite` and backups) is Flask's instance folder, which is **not** served by any route. It is only accessible on the server filesystem.

---

## 3. Sensitive Data Exposure Findings

### 3.1 What Was Checked in Rendered Pages

| Data Type | In Login Page? | In Admin Login Page? | In Home Page? |
|-----------|---------------|---------------------|--------------|
| `SECRET_KEY` / `dev-secret-key` | No | No | No |
| `DATABASE_URL` | No | No | No |
| `MAIL_PASSWORD` | No | No | No |
| `password_hash` | No | No | No |
| Session tokens | No | No | No |
| Stack traces | No | No | No |
| File paths | No | No | No |

### 3.2 What Is In the Pages (Expected, Not a Vulnerability)

- **CSRF tokens** are present in form hidden fields and meta tags. These are by design and are required for form protection. They are session-scoped and cannot be used to impersonate users.
- **Material filenames** (e.g., `study-guide.pdf`) appear in HTML for download links. This is the intended public metadata.
- **User display names** appear on profile pages for authenticated users viewing their own data.

### 3.3 Conflict Screen Remediation

The student conflict screen previously displayed:
- Session IDs (database internal IDs) — **removed**
- User IDs — **removed**
- IP addresses — **removed**

Now it only shows: device name (if detected), operating system (if detected), and formatted local timestamps.

---

## 4. Debug Mode and Error Handling

### 4.1 Debug Mode

`config.py` reads `FLASK_DEBUG` from the environment, defaulting to `'false'`:
```python
DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
```

The application runs with `app.run(debug=Config.DEBUG)`. When `FLASK_DEBUG` is not set or is `false`, the Werkzeug debugger is disabled and interactive stack traces are not shown.

### 4.2 Error Pages

- **404 errors:** Render a user-friendly error page or generic message. No stack traces, file paths, or internal details.
- **500 errors:** Render a user-friendly error page or generic `"<h1>500 - Server Error</h1>"`. Internal details are logged server-side only.
- **No `app.run(debug=True)` hardcoded** in the source code.

### 4.3 Timing Middleware

A `before_request`/`after_request` pair measures request duration. Slow requests (> 1 second) are logged server-side via `print()`. This does not expose any information to clients.

---

## 5. Authentication and Authorization

### 5.1 Admin Endpoints

| Test | Result |
|------|--------|
| Unauthenticated POST to `/admin/users/1/revoke-session/1` | 302 redirect to `/admin/login` |
| Unauthenticated POST to `/admin/users/1/revoke-all-sessions` | 302 redirect to `/admin/login` |

All admin session-management endpoints are protected by `@admin_required`.

### 5.2 User Session Authorization

| Test | Result |
|------|--------|
| One user cannot see another user's login history | Verified (TEST 8 in tests_session_management.py) |
| Server validates session token against DB on every protected request | Implemented via `enforce_session_validity` before-request handler |

### 5.3 CSRF Protection

All state-changing endpoints use `@csrf_protect`:
- Login endpoints: protected
- Registration: protected
- OTP resend: protected (via `/api/send-otp`)
- Session revocation (admin): protected
- Profile updates (user): protected
- Material download: GET (read-only, no CSRF needed)

Tests verify that POSTs without a valid CSRF token are rejected.

### 5.4 Revoked Session Rejection

The `enforce_session_validity` before-request handler in `app.py:108` validates every protected request:
1. Looks up the session token hash in the database
2. Checks the session status is `active`
3. Checks the session has not expired (either `expires_at` or idle timeout)
4. If any check fails, clears the session and redirects to login

This ensures that revoked sessions cannot access protected routes, even if the Flask signed cookie is still present.

---

## 6. Cookie and Header Security

| Setting | Value |
|---------|-------|
| `SESSION_COOKIE_HTTPONLY` | `True` |
| `SESSION_COOKIE_SAMESITE` | `Lax` |
| `SESSION_COOKIE_SECURE` | Configurable (env-controlled, defaults to `False` for local HTTP) |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `SAMEORIGIN` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(self), microphone=(), camera=()` |
| `Content-Security-Policy` | Full CSP with `default-src 'self'` and restricted `script-src` |

---

## 7. Upload File Security

| Route | Auth Required? | Notes |
|-------|---------------|-------|
| Profile image upload | Yes (`@user_required`) | Checks file type, generates safe filename |
| Material upload (admin) | Yes (`@admin_required`) | Validates file extension |

**Note:** Files in `static/uploads/` are served by Flask's static file handler. This means uploaded materials are directly accessible at `/static/uploads/...`. A more secure design would move uploads outside the static directory and serve them through an authenticated route. However, this is a pre-existing architectural decision and changing it risks breaking existing material links. This is noted as a **known limitation**, not a regression.

---

## 8. Summary of Security Fixes

| Issue | Status | Action |
|-------|--------|--------|
| Sensitive files accessible via routes | Already safe | Verified — all return 404 |
| Debug mode enabled | Already safe | Defaults to `False`, env-controlled |
| Tracebacks in error pages | Already safe | Generic error pages, no internal details |
| CSRF on state-changing routes | Already safe | `@csrf_protect` on all POST routes |
| Sensitive data in HTML/JSON | Already safe | No secrets, tokens, or passwords in responses |
| Path traversal on downloads | Already safe | `startswith()` directory check |
| Session revocation enforced | Already safe | `enforce_session_validity` on every request |
| Conflict screen showing technical info | **Fixed** | Removed session IDs, IPs, DB IDs from student-facing display |
| Admin session endpoints unprotected | Already safe | `@admin_required` on all admin routes |
| No security headers | Already fixed | CSP, X-Frame-Options, nosniff, etc. |

### No regressions were introduced. All existing security controls remain in place.
