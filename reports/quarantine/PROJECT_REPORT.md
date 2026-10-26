# StudyHub Project Report

## 1. Project Title and Overview

**StudyHub** (college-study-material) is a Flask-based College Study Material
Management Platform. It provides a student portal for browsing, viewing, and
downloading study materials, and an admin panel for managing subjects,
materials, users, and security. The application supports multiple database
backends (SQLite, PostgreSQL/NeónDB, MySQL) with automatic backend selection.

## 2. Objectives

1. Full project audit (code, database, templates, tests, docs)
2. Verify and fix SQLite-to-NeonDB PostgreSQL migration
3. Fix CSP errors blocking Google Fonts, PDFs, and videos
4. Diagnose and fix slow website loading
5. Fix reproducible application errors
6. Test all existing features
7. Safely identify unnecessary files
8. Update all markdown documentation

## 3. Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, Flask 3.0.0 |
| Database (dev) | SQLite |
| Database (prod) | PostgreSQL / NeónDB (psycopg v3) |
| Frontend | HTML5, CSS3, Bootstrap 5.3.2 (CDN), Font Awesome 6 |
| Auth | Werkzeug PBKDF2, SHA-256 session tokens, OTP |

## 4. Errors Found and Fixed

| # | Error | Root Cause | Fix |
|---|-------|-----------|-----|
| 1 | requirements.txt corrupted | Task description pasted into file | Restored clean package list |
| 2 | BOM character in test file | UTF-8 BOM (U+FEFF) | Removed BOM |
| 3 | Tests fail from non-root dirs | Missing `os.chdir(root)` | Added to test files |
| 4 | 13 unused imports | Accumulated during development | Removed across 8 files |
| 5 | Google Fonts blocked by CSP | Missing fonts origins in `style-src`/`font-src` | Added origins to CSP |
| 6 | PDF iframes blocked | `frame-src` missing `'self'` | Added `'self'` to `frame-src` |
| 7 | Videos not playable | No `Accept-Ranges` header | Added `Accept-Ranges: bytes` |
| 8 | Image uploads blocked | Image extensions missing from `ALLOWED_EXTENSIONS` | Added png, jpg, jpeg, gif, webp |
| 9 | Admin mobile nav broken | Sidebar toggle button missing in template | Added toggle button to header |
| 10 | Bootstrap source maps blocked | `connect-src` missing `cdn.jsdelivr.net` | Added to `connect-src` |
| 11 | Slow website (8-11s) | New DB connection per query | PostgreSQL connection pooling |
| 12 | Per-request settings query | No caching | 60s TTL thread-local cache |
| 13 | Homepage 3 separate queries | Redundant COUNT queries | Combined into 1 query |
| 14 | PostgreSQL `cursor.lastrowid` | psycopg3 lacks `lastrowid` | Added `RETURNING id` support |
| 15 | PostgreSQL `INSERT IGNORE` | MySQL syntax used for PG | Changed to `ON CONFLICT DO NOTHING` |

## 5. Security Review

- `.env` excluded from Git (line 1) ✓
- `instance/` excluded from Git (line 6) ✓
- No credentials in documentation ✓
- Password hashing preserved (PBKDF2) ✓
- All 45 POST forms have CSRF tokens ✓
- CSP restrictive but functional ✓
- Path traversal protection present ✓
- Session validation enforced ✓
- Rate limiting active ✓

## 6. Database Migration Status

- **Connection**: PostgreSQL/NeónDB verified working (~2.7s cold start)
- **Tables**: All 16 tables created in PostgreSQL
- **Data**: 232/244 rows migrated (12 orphaned rows skipped)
- **Idempotent**: Safe to rerun (`ON CONFLICT DO NOTHING`)
- **SQLite database**: Preserved untouched (`instance/college_study.sqlite`)
- **SQLite backup**: Preserved (`instance/college_study.sqlite.bak`)

## 7. Performance Findings

| Metric | Before | After |
|--------|--------|-------|
| GET / (warm) | 8.63-9.42s | 1.63-1.77s (85% faster) |
| GET /about | ~3s | 0.01s (99.7% faster) |
| GET /login | ~3s | 0.04s (98.7% faster) |

**Root causes:** NeónDB cold-start per query, per-request settings query, redundant count queries
**Fixes:** Connection pool, settings cache, query consolidation

## 8. Test Results

| Suite | Tests | Passed | Failed |
|-------|-------|--------|--------|
| tests_project.py | 69 | 69 | 0 |
| tests_session_management.py | 40 | 40 | 0 |
| tests_session_edge_cases.py | 29 | 29 | 0 |
| **Total** | **138** | **138** | **0** |

## 9. Remaining Issues

| Issue | Severity | Recommendation |
|-------|----------|---------------|
| `init_sqlite.py` missing 6 settings rows | Low | Update to include all 11 settings |
| Python 3.12 DeprecationWarning (SQLite) | Low | Test-only issue |
| NeónDB cold-start latency | Low | Use SQLite for local development |
| 34 orphaned guest_views rows | Low | Skipped during migration (FK violations) |
| Duplicate docs in docs/ and root | Low | Future cleanup recommended |

## 10. Commands to Run

```bash
# Install
pip install -r requirements.txt

# Initialize database
python init_sqlite.py      # SQLite
python init_postgres.py   # PostgreSQL

# Run
python app.py
# Visit: http://127.0.0.1:5000

# Tests
python tests/tests_project.py
python tests/tests_session_management.py
python tests/tests_session_edge_cases.py
```
