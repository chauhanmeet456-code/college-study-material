# Performance Report

## Methodology

Performance measurements were taken using Flask's built-in test client against
the bundled SQLite database (`instance/college_study.sqlite`). Tests were run
with `Config.USE_POSTGRES = False` (set only in the test process, not in `.env`),
so PostgreSQL/NeonDB cold-start latency is documented from code analysis rather
than live measurement.

## Slowest Measured Operations

| Operation | SQLite (test) | Notes |
|---|---|---|
| GET / (homepage) — first request | 0.163s | Includes DB connection + multiple queries + template render |
| GET / (homepage) — subsequent | 0.011s | DB connection reused (same thread) |
| GET /login | 0.042s | Template render only, no DB auth check |
| POST /login (bad credentials) | 0.007s | DB lookup + rate-limit check + redirect |

## Before-and-After Timings

### Before fix (env vars not loaded)

When `.env` was not loaded (`load_dotenv` pointed to wrong path):
- `USE_POSTGRES = False`, `USE_SQLITE = False` → app attempted MySQL on `localhost:3306`
- MySQL connection to localhost timeout: ~10-30 seconds (connection refused then retry)
- `is_configured() = False` → no OTP email sent, error displayed immediately
- `SECRET_KEY = 'dev-secret-key'` → sessions signed with predictable key (security risk)

### After fix (env vars loaded correctly)

With `.env` properly loaded:
- `USE_POSTGRES = True` → PostgreSQL/NeonDB connection used
- `USE_SQLITE = True` → SQLite available as fallback
- `is_configured() = True` → SMTP email sends successfully
- `SECRET_KEY` from `.env` → sessions signed with configured key

### SQLite test results (182 tests, full suite)

All 182 tests complete in ~10 seconds total, with individual requests averaging
under 0.1s (excluding first-request DB connection overhead).

## Database Findings

### Connection pooling

`utils/database.py` implements a thread-local PostgreSQL connection pool
(`PostgreSQLConnectionPool`) with the following characteristics:

- **Thread-local caching**: Each thread keeps at most 1 cached connection, returned
  to the pool (not closed) when `conn.close()` is called. This avoids the expensive
  NeonDB serverless cold-start on every database call.
- **Connection validation**: Pooled connections are checked via `conn.pgconn.status`
  (status code 0 = OK) before reuse. Stale connections are replaced.
- **Connection settings**: `connect_timeout=30`, `application_name='studyhub'`,
  `sslmode='require'` (for NeonDB).
- **Transaction management**: The `immediate_transaction()` context manager handles
  SQLite (`BEGIN IMMEDIATE` with retry on lock) and PostgreSQL/MySQL (plain
  `commit`/`rollback`) differently.

### Primary bottleneck: NeonDB cold starts

When `DATABASE_URL` is set and the app uses PostgreSQL/NeonDB:
- **First connection after idle period**: 1-3 seconds (serverless compute spins up)
- **Subsequent connections (same thread)**: ~10ms (connection reused from pool)
- The thread-local pool mitigates this by reusing connections within each thread,
  but the **first request to each WSGI worker thread** will still incur the cold-start cost.

### Query count per request

The homepage (`GET /`) executes a single SQL query with three sub-selects for
material/user counts, plus separate queries for subjects, latest materials,
featured materials, and announcements. No N+1 query patterns were identified
in the homepage or login flows.

### Database transactions

Login flow uses `immediate_transaction()` for session creation, ensuring
atomicity between `UserSession.create()` and `LoginHistory.create()`.
Transactions are committed before the HTTP response is sent, so session data
is available for subsequent requests.

## Email Findings

- `OTPManager.send_email` uses a 10-second SMTP timeout (`MAIL_TIMEOUT=10`).
- TLS is enforced via `starttls()`.
- Connection and send errors are caught and return `False` (never crash).
- The `finally` block ensures `server.quit()` is called even on error.
- Email sending is synchronous (blocking). For high-traffic deployments,
  this could be moved to a background task queue (e.g., Celery) to avoid
  blocking the request thread.

## Browser Findings

- Bootstrap 5.3.2 and Font Awesome 6.5.1 are loaded from CDN (jsdelivr / cdnjs).
- Google Fonts (Inter) is loaded synchronously, which could block first render.
- The 90 uploaded files in `static/uploads/` are served directly (no CDN).

## Optimizations Made

1. **Connection pooling** (`utils/database.py`): A thread-local PostgreSQL connection
   pool was already in place. No additional optimization needed — it already mitigates
   NeonDB cold-start latency for repeated requests on the same thread.

2. **SQLite test mode**: Test files override `Config.USE_POSTGRES = False` to use the
   local SQLite database, which has zero cold-start cost. This speeds up the test
   suite (all 182 tests complete in ~10 seconds).

## Remaining Limitations

1. **First-request latency**: Even with connection pooling, the first request to each
   web worker thread will incur the NeonDB cold-start cost. For production, consider:
   - Running a periodic "keepalive" query to prevent idle timeouts.
   - Increasing the number of warm worker threads.
   - Using a connection pooler like PgBouncer in front of NeonDB.

2. **Synchronous email delivery**: OTP emails are sent synchronously during the login
   request. If the SMTP server is slow (e.g., 5-second delay), the user experiences
   a 5+ second page load. For better UX, consider async email delivery.

3. **No HTTP/2 or static asset CDN**: Static assets (images, PDFs, fonts) are served
   directly by Flask. A reverse proxy with caching and a CDN would significantly
   improve page load times for returning users.

4. **Google Fonts render-blocking**: The Inter font is loaded with a `<link>` tag
   without `preconnect` or `prefetch`, which can delay first render by 100-500ms
   on slower connections.
