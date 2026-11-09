# Database Setup Guide

## Database Selection

The application supports three backends, selected explicitly by environment:

| Backend | Config | Use Case |
|---------|--------|----------|
| **SQLite** | `USE_SQLITE=true` | Local development |
| **PostgreSQL (local)** | `DEV_DATABASE_URL=<url>` or `LOCAL_DATABASE_URL=<url>` | Local development |
| **PostgreSQL (Render)** | `DATABASE_URL=<url>` | Production (NeonDB) |
| **MySQL** | `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Explicitly configured |

**Priority:** Render requires production `DATABASE_URL` and ignores the local
`.env`. Locally, `DATABASE_BACKEND` can explicitly select a backend; otherwise
`DEV_DATABASE_URL` / `LOCAL_DATABASE_URL` selects PostgreSQL when configured,
then `USE_SQLITE=true` selects SQLite, then configured `DB_*` values retain
MySQL support. Local `DATABASE_URL` is never used as a fallback. Set
`DATABASE_BACKEND=postgresql` to require a local PostgreSQL URL and report a
clear configuration error when it is absent.

## Required Environment Variables

| Variable | Required For | Description |
|----------|-------------|-------------|
| `SECRET_KEY` | All | Flask secret key for session signing |
| `USE_SQLITE` | SQLite | Set to `true` for SQLite development mode |
| `DATABASE_BACKEND` | Optional local override | Set to `postgresql`, `sqlite`, or `mysql`; `postgresql` requires a local development URL |
| `DATABASE_URL` | Render PostgreSQL | Full production PostgreSQL connection string |
| `DEV_DATABASE_URL` | Local PostgreSQL | Full local-development PostgreSQL connection string |
| `LOCAL_DATABASE_URL` | Local PostgreSQL | Alternate local PostgreSQL connection string |
| `SQLITE_DB` | SQLite / migration | Optional explicit SQLite database file path |
| `DB_HOST` | MySQL | MySQL host (default: `localhost`) |
| `DB_PORT` | MySQL | MySQL port (default: `3306`) |
| `DB_NAME` | MySQL | MySQL database name |
| `DB_USER` | MySQL | MySQL username |
| `DB_PASSWORD` | MySQL | MySQL password |
| `MAIL_SERVER` | All | SMTP server for OTP emails (optional) |
| `MAIL_PORT` | All | SMTP port (default: 587) |
| `MAIL_USERNAME` | All | SMTP username (optional) |
| `MAIL_PASSWORD` | All | SMTP password (optional) |

## PostgreSQL Configuration

For local development, set `DEV_DATABASE_URL` to a local PostgreSQL instance:

```env
USE_SQLITE=false
DEV_DATABASE_URL=postgresql://postgres@localhost:5432/college_study
```

For Render, set the production connection string as `DATABASE_URL` in the
Render environment. Do not copy the production URL into the local `.env`.
Development migration targets must use a loopback host. Use
`DATABASE_BACKEND=postgresql` to make the application require local PostgreSQL
instead of selecting a configured SQLite or MySQL backend.

Never expose the complete connection string in documentation or logs.

### PostgreSQL Driver

- Library: `psycopg` (v3) — `psycopg[binary]==3.2.6`
- Install: `pip install "psycopg[binary]==3.2.6"`
- Used via unified `get_connection()` API in `utils/database.py`
- `PostgresCursorWrapper` converts `?` placeholders to `%s` transparently

## SQLite Configuration

SQLite is opt-in and disabled on Render. Set `DATABASE_BACKEND=sqlite` (or
`USE_SQLITE=true` when no development PostgreSQL URL is configured) in the
local `.env` before running the initializer; it refuses to initialize otherwise.

```env
DATABASE_BACKEND=sqlite
USE_SQLITE=true
```

Run `python init_sqlite.py` only after explicitly enabling SQLite. The database
path defaults to `instance/college_study.sqlite`; override it with `SQLITE_DB`.

## MySQL Configuration

For an explicitly selected local MySQL database:

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

Set `DATABASE_BACKEND=sqlite` in the local `.env` first; SQLite is never
selected automatically and is disabled on Render.

```bash
python init_sqlite.py
```

Creates 16 base tables with SQLite-compatible DDL, default categories,
settings, and auto-migration columns via `ALTER TABLE` if missing.

### PostgreSQL

```bash
python init_postgres.py
```

Creates 16 base tables with PostgreSQL-compatible DDL:
- `SERIAL PRIMARY KEY` (auto-incrementing)
- Triggers as functions for `updated_at` auto-update
- `ON CONFLICT DO NOTHING` for idempotent inserts
- All indexes and foreign key constraints

## SQLite Backup Procedure

Before migrating to PostgreSQL, always backup the SQLite database:

```powershell
Copy-Item instance\college_study.sqlite instance\college_study.sqlite.bak
```

The migration script also requires the SQLite database and never modifies it.

## Migration Procedure (SQLite → PostgreSQL)

The migration opens SQLite read-only and defaults to validation only. The
development target uses `DEV_DATABASE_URL` / `LOCAL_DATABASE_URL`; production
uses `DATABASE_URL` only when `--target production` is explicit.

```bash
python migrate_to_postgres.py --target development --check-config
python migrate_to_postgres.py --target development --source instance/college_study.sqlite --apply
```

If the PostgreSQL schema does not exist, schema creation is an explicit write:

```bash
python migrate_to_postgres.py --target development --apply --initialize-schema
```

Production migration requires explicit authorization:

```bash
python migrate_to_postgres.py --target production --check-config
python migrate_to_postgres.py --target production --apply --confirm-production
```

`--reset` is destructive, requires `--apply --confirm-reset`, and is disabled
for production. The migration summary reports source and destination counts, inserted rows,
conflicts, skipped foreign keys, and row errors. IDs are preserved and
PostgreSQL sequences are advanced. Legacy OAuth subject identifiers are
SHA-256 hashed to match the current PostgreSQL schema. The SQLite source is
never modified. Check the printed summary before relying on a migration.

### Migration Verification

After migration, compare source, inserted, conflicting, skipped, and
destination counts. Destination counts can include rows that existed before
migration, so they are not always expected to equal source counts:

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

## Data Verification Status

No live PostgreSQL migration or destination count verification is claimed by
this guide. An operator must run the migration against the intended target and
review its summary before treating data as migrated.

## Safe Recovery

Database selection is explicit. If the selected database is unavailable,
StudyHub reports the connectivity error instead of silently switching to
another database. For optional local SQLite development, set `USE_SQLITE=true`
and run `python init_sqlite.py` only if you intend to create/initialize that
database.

The configured SQLite source (default `instance/college_study.sqlite`) is
opened read-only and is never modified by PostgreSQL migration operations.

## Database Troubleshooting

| Problem | Solution |
|---------|----------|
| Connection timeout | Verify the selected database URL/host and database provider status; do not put production `DATABASE_URL` in local `.env` |
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
