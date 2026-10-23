# Database Setup Guide

## Database Selection

The application supports three database backends with automatic selection:

| Backend | Config | Use Case |
|---------|--------|----------|
| **SQLite** | `USE_SQLITE=true` | Local development |
| **PostgreSQL** | `DATABASE_URL=<url>` | Production (NeónDB) |
| **MySQL** | Default (fallback) | Alternative production |

**Priority**: `DATABASE_URL` (PostgreSQL) → `USE_SQLITE=true` (SQLite) → MySQL

## Required Environment Variables

| Variable | Required For | Description |
|----------|-------------|-------------|
| `SECRET_KEY` | All | Flask secret key for session signing |
| `USE_SQLITE` | SQLite | Set to `true` for SQLite development mode |
| `DATABASE_URL` | PostgreSQL | Full PostgreSQL connection string |
| `DB_HOST` | MySQL | MySQL host (default: `localhost`) |
| `DB_PORT` | MySQL | MySQL port (default: `3306`) |
| `DB_NAME` | MySQL | MySQL database name |
| `DB_USER` | MySQL | MySQL username |
| `DB_PASSWORD` | MySQL | MySQL password |
| `MAIL_SERVER` | All | SMTP server for OTP emails (optional) |
| `MAIL_PORT` | All | SMTP port (default: 587) |
| `MAIL_USERNAME` | All | SMTP username (optional) |
| `MAIL_PASSWORD` | All | SMTP password (optional) |

## PostgreSQL / NeónDB Configuration

1. Create a NeónDB project at [https://neon.tech](https://neon.tech)
2. Create a database (default: `neondb`)
3. Obtain the connection string from the NeónDB dashboard
4. Add to `.env`:

```env
USE_SQLITE=false
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DATABASE?sslmode=require
```

**Connection string format**: `postgresql://` scheme, credentials, NeónDB
endpoint, database name, and `sslmode=require`.

Never expose the complete connection string in documentation or logs.

### PostgreSQL Driver

- Library: `psycopg` (v3) — `psycopg[binary]==3.2.6`
- Install: `pip install "psycopg[binary]==3.2.6"`
- Used via unified `get_connection()` API in `utils/database.py`
- `PostgresCursorWrapper` converts `?` placeholders to `%s` transparently

## SQLite Configuration

For local development:

```env
USE_SQLITE=true
```

Database stored at: `instance/college_study.sqlite`
(Excluded from Git via `.gitignore`)

## MySQL Configuration

For MySQL (fallback):

```env
USE_SQLITE=false
DB_HOST=your_mysql_host
DB_PORT=3306
DB_NAME=college_study
DB_USER=your_user
DB_PASSWORD=your_password
```

Uses: `PyMySQL==1.1.0`

## Schema Initialization

### SQLite

```bash
python init_sqlite.py
```

Creates 16 tables with SQLite-compatible DDL, default categories, settings,
and auto-migration columns via `ALTER TABLE` if missing.

### PostgreSQL

```bash
python init_postgres.py
```

Creates 16 tables with PostgreSQL-compatible DDL:
- `SERIAL PRIMARY KEY` (auto-incrementing)
- Triggers as functions for `updated_at` auto-update
- `ON CONFLICT DO NOTHING` for idempotent inserts
- All indexes and foreign key constraints

## SQLite Backup Procedure

Before migrating to PostgreSQL, always backup the SQLite database:

```bash
cp instance/college_study.sqlite instance/college_study.sqlite.bak
```

The migration script also requires the SQLite database and never modifies it.

## Migration Procedure (SQLite → PostgreSQL)

### Run Migration

```bash
python migrate_to_postgres.py
```

For clean re-migration (deletes all PG data first):

```bash
python migrate_to_postgres.py --reset
```

### Migration Safety

- Never drops tables
- Uses `INSERT ... ON CONFLICT DO NOTHING` (idempotent)
- Skips rows with orphaned FK references (reports count)
- Resets PostgreSQL sequences after explicit ID inserts
- Compares source and destination counts
- Never modifies SQLite database
- Safe to rerun

### Migration Verification

After migration, verify counts match between SQLite and PostgreSQL:

```bash
python -c "
from utils.database import get_connection, get_cursor, close_cursor
from config import Config
conn = get_connection()
cursor = get_cursor(conn)
for t in ['users','subjects','materials','categories']:
    cursor.execute(f'SELECT COUNT(*) as c FROM \"{t}\"')
    print(f'{t}: {cursor.fetchone()[\"c\"]}')
close_cursor(cursor)
conn.close()
"
```

## Data Verification Checklist

- [x] All 16 tables created in PostgreSQL
- [x] 15 of 16 table counts match SQLite source
- [x] Password hashes preserved (Werkzeug PBKDF2)
- [x] Primary keys preserved
- [x] Foreign key relationships maintained
- [x] Categories populated (8 default categories)
- [x] Settings populated (11 settings rows)
- [x] Admin account preserved
- [x] Materials with file paths preserved

## Safe Recovery

If PostgreSQL is unavailable or unreachable:

1. **Switch to SQLite**:
```env
USE_SQLITE=true
# Remove or unset DATABASE_URL
```

2. **Re-initialize SQLite**:
```bash
python init_sqlite.py
```

3. **Verify**:
```bash
python app.py
```

SQLite database at `instance/college_study.sqlite` and backup at
`instance/college_study.sqlite.bak` are never modified by PostgreSQL operations.

## Database Troubleshooting

| Problem | Solution |
|---------|----------|
| Connection timeout | Verify DATABASE_URL, check NeónDB IP allowlist, check NeónDB status |
| Slow first connection | Cold start (expected for NeónDB serverless); warm up with `python -c "from utils.database import get_connection; get_connection().close()"` |
| Migration FK errors | Orphaned rows are skipped; rerun migration to verify counts |
| Slow page loads | Connection pooling and settings cache added; use SQLite for local dev |
| Sequence not incrementing | Migration resets sequences automatically |
| Python 3.12 datetime warning | SQLite-only; does not affect PostgreSQL |

## Connection Pooling

PostgreSQL connections use a thread-local pool (`PostgreSQLConnectionPool` in
`utils/database.py`):

- Connections are reused across requests in the same thread
- Validation uses `conn.pgconn.status` (no network round-trip)
- Stale connections are automatically recreated
- No application code changes needed (transparent via `PooledConnection`)

## Performance Notes

- First PostgreSQL connection: ~2.7-3.2s (NeónDB cold start)
- Subsequent queries on warm connection: ~0.2-0.3s each
- SQLite queries: <1ms (local file)
- Settings data cached for 60 seconds (thread-local)
