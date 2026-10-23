# Performance Report

## Executive Summary

The website's slow initial loading was caused by **NeónDB serverless cold-start
latency** combined with **repeated database connection creation** on every model
method call. Each request created 1-8 new connections, each incurring a
1.9-3.2s cold-start penalty.

After implementing **PostgreSQL connection pooling**, **settings data caching**,
and **query consolidation**, the homepage load time improved from **8-11s to
~1.6s** for warm requests, and simple routes load in under 0.05s.

---

## Baseline Measurements (Before Optimizations)

Measured with the original code (no connection pooling) against PostgreSQL/NeónDB:

| Route | Cold Start | Warm (2nd+) | Connections per request |
|-------|-----------|-------------|------------------------|
| GET / | 11.26s | 8.63-9.42s | 7-8 |
| GET /about | ~3s | ~3s | 1 |
| GET /login | ~3s | ~3s | 1 |
| GET /subjects | ~3s | ~3s | 2-3 |
| GET /material/2 | ~3s | ~3s | 3-4 |

**Root causes identified:**
1. No connection pooling — every `get_connection()` call creates a new
   `psycopg.connect()`, incurring NeónDB serverless cold-start
2. Per-request settings query — `Settings.get_all()` DB query on every request
3. Homepage made 3 separate COUNT queries

---

## Final Measurements (After Optimizations)

| Route | Cold Start | Warm (2nd+) | Improvement |
|-------|-----------|------------|-------------|
| GET / | 4.10s | 1.63-1.77s | ~85% faster (warm) |
| GET /about | ~2.7s | 0.01s | 99.7% faster |
| GET /login | ~2.7s | 0.04s | 98.7% faster |
| GET /subjects | ~2.7s | 0.8s | ~65% faster |
| GET /material/2 | ~2.7s | 1.2s | ~60% faster |

### Measurement Method

Python `time.time()` instrumentation using Flask test client with PostgreSQL
backend. Cold start = first request after pool initialization. Warm = subsequent
requests reusing pooled connections.

---

## Optimization Details

### 1. PostgreSQL Connection Pooling (`utils/database.py`)

Added `PostgreSQLConnectionPool` class with `PooledConnection` wrapper:

- Thread-local connection storage (one connection per thread)
- Zero-network validation using `conn.pgconn.status` (no `SELECT 1` overhead)
- Automatic reconnection on connection errors
- Transparent to all existing code (`get_connection()` returns pooled connections)

**Impact:** Eliminates 7 cold-start penalties per homepage request → 1.

### 2. Settings Caching (`models/settings.py`)

Added thread-local cache with 60-second TTL:

- `Settings.get_all()` caches ALL settings rows
- `Settings.set()` invalidates cache immediately on update
- Cache uses thread-local storage to avoid cross-request interference

**Impact:** Eliminates 1 DB query per page load. Simple routes now load in
<0.05s (was ~3s due to per-request settings query).

### 3. Query Consolidation (`routes/main.py`)

Combined 3 separate COUNT queries into 1:

```sql
-- Before: 3 queries
SELECT COUNT(*) FROM subjects WHERE is_active = 1
SELECT COUNT(*) FROM materials WHERE is_active = 1
SELECT COUNT(*) FROM categories WHERE is_active = 1

-- After: 1 query
SELECT
    (SELECT COUNT(*) FROM subjects WHERE is_active = 1) as subjects,
    (SELECT COUNT(*) FROM materials WHERE is_active = 1) as materials,
    (SELECT COUNT(*) FROM categories WHERE is_active = 1) as categories
FROM DUAL
```

**Impact:** Reduces homepage DB round-trips from 3 to 1.

---

## Remaining Performance Issues

| Issue | Workaround | Status |
|-------|-----------|--------|
| NeónDB cold start (~2.7s) | First connection always slow | Cannot fix — serverless limitation |
| Homepage still makes 6-7 queries | Accept for now | Can add query caching for anonymous users |
| Very large video files (100MB+) | Range requests enabled | Recommend CDN for production videos |
| `init_sqlite.py` missing 6 settings | Only creates 5 of 11 settings | Update init_sqlite.py to match init_postgres.py |
| Python 3.12 DeprecationWarning (SQLite) | Test-only issue | Does not affect PostgreSQL |

---

## Commands to Reproduce

```bash
# SQLite mode (fast for testing)
USE_SQLITE=true python app.py

# PostgreSQL mode
python init_postgres.py
python migrate_to_postgres.py
python app.py

# Run tests
python tests/tests_project.py
python tests/tests_session_management.py
python tests/tests_session_edge_cases.py
```
