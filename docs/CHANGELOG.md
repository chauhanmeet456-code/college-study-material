# Changelog

All notable changes to this project are documented in this file.
This entry records the complete audit, error-fixing, performance optimization,
CSP fixes, PDF/video fixes, and documentation work performed in these sessions.

## [2026-10-23] — Complete Audit, Performance Optimization, CSP & Media Fixes

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
