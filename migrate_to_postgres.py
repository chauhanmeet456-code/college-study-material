"""Migrate existing data from SQLite to PostgreSQL (NeonDB).

This script reads data from `instance/college_study.sqlite` and transfers it
to the PostgreSQL database specified by `DATABASE_URL`. It preserves primary
keys and foreign key relationships.

Usage:
    python migrate_to_postgres.py           # Migrate (skips existing rows)
    python migrate_to_postgres.py --reset   # Wipe target tables first, then migrate

Safety features:
- Never drops tables
- With --reset: deletes all data in target tables (in reverse dependency order)
- Without --reset: uses INSERT ... ON CONFLICT (DO NOTHING) to avoid duplicates
- Resets PostgreSQL sequences after inserting explicit IDs
- Skips rows that would violate foreign key constraints (orphaned references)
- Reports source and destination record counts for verification
"""

import os
import sys
import sqlite3
import psycopg
from config import Config


# Tables to migrate, in dependency order (parent tables first).
TABLES_IN_ORDER = [
    'users',
    'admins',
    'categories',
    'subjects',
    'materials',
    'favorites',
    'contact_messages',
    'announcements',
    'guest_views',
    'guest_downloads',
    'settings',
    'user_sessions',
    'login_history',
    'failed_login_attempts',
    'admin_audit_log',
]


def get_sqlite_conn():
    """Connect to the source SQLite database (read-only)."""
    db_path = Config.SQLITE_DB
    if not os.path.exists(db_path):
        print(f"ERROR: SQLite database not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_postgres_conn():
    """Connect to the PostgreSQL (NeonDB) database."""
    if not Config.USE_POSTGRES:
        print("ERROR: DATABASE_URL is not set in .env")
        print("Configure DATABASE_URL with your NeonDB PostgreSQL connection string.")
        sys.exit(1)

    conn = psycopg.connect(Config.DATABASE_URL)
    return conn


def count_rows(conn, table, is_pg=False):
    """Count rows in a table."""
    cursor = conn.cursor()
    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
    count = cursor.fetchone()[0]
    cursor.close()
    return count


def fetch_all_rows(sqlite_conn, table):
    """Fetch all rows from a SQLite table."""
    cursor = sqlite_conn.cursor()
    cursor.execute(f'SELECT * FROM "{table}"')
    rows = cursor.fetchall()
    return rows, [desc[0] for desc in cursor.description]


def reset_sequence(pg_conn, table):
    """Reset the SERIAL sequence for a table after inserting explicit IDs."""
    cursor = pg_conn.cursor()
    try:
        cursor.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"(SELECT MAX(id) FROM {table}))"
        )
    except psycopg.Error:
        pass  # table might be empty or sequence issues
    cursor.close()


def truncate_pg_tables(pg_conn, should_truncate=False):
    """Check that target tables are empty.

    If should_truncate is True, all data in target tables is deleted (in reverse
    dependency order) before migration starts.
    """
    cursor = pg_conn.cursor()
    warnings = []

    if should_truncate:
        print("-- Resetting target tables (deleting all data) --")
        for table in reversed(TABLES_IN_ORDER):
            try:
                cursor.execute(f'DELETE FROM "{table}"')
            except psycopg.Error:
                pass  # table might not exist
        pg_conn.commit()
        print("-- Tables reset complete --\n")
        cursor.close()
        return

    for table in TABLES_IN_ORDER:
        try:
            ct = count_rows(pg_conn, table, is_pg=True)
            if ct > 0:
                warnings.append(f"  - {table}: {ct} rows (will use ON CONFLICT to skip)")
        except psycopg.Error:
            warnings.append(f"  - {table}: table does not exist yet")

    if warnings:
        print("\nPostgreSQL table status (existing data will NOT be deleted):")
        for w in warnings:
            print(w)
    cursor.close()


def migrate_table(sqlite_conn, pg_conn, table):
    """Migrate a single table from SQLite to PostgreSQL using batch insert.

    Uses executemany for performance. Skips rows that would violate foreign
    key constraints (orphaned references).
    """
    rows, columns = fetch_all_rows(sqlite_conn, table)
    source_count = len(rows)

    if source_count == 0:
        print(f"  {table}: 0 rows (skipped)")
        return 0, 0

    col_names = ', '.join(f'"{c}"' for c in columns)
    placeholders = ', '.join(['%s'] * len(columns))

    # Prepare all values
    all_values = []
    for row in rows:
        values = []
        for col in columns:
            val = row[col]
            # Convert sqlite3.Row to native Python types
            if hasattr(val, 'decode'):
                val = val.decode('utf-8', errors='replace')
            values.append(val)
        all_values.append(tuple(values))

    cursor = pg_conn.cursor()
    inserted = 0
    skipped_fk = 0

    # First try bulk insert with ON CONFLICT
    try:
        cursor.executemany(
            f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders}) '
            f'ON CONFLICT DO NOTHING',
            all_values
        )
        inserted = cursor.rowcount
    except psycopg.Error as e:
        pg_conn.rollback()
        cursor.close()
        # If bulk insert fails, fall back to row-by-row with FK handling
        cursor = pg_conn.cursor()
        for values in all_values:
            try:
                cursor.execute(
                    f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders}) '
                    f'ON CONFLICT DO NOTHING',
                    values
                )
                if cursor.rowcount > 0:
                    inserted += 1
            except psycopg.Error as e2:
                if 'violates foreign key constraint' in str(e2):
                    skipped_fk += 1
                    pg_conn.rollback()
                else:
                    pg_conn.rollback()
        pg_conn.commit()

    pg_conn.commit()
    cursor.close()

    if 'id' in columns and inserted > 0:
        reset_sequence(pg_conn, table)

    if skipped_fk:
        print(f"  ({skipped_fk} rows skipped due to missing FKs)")

    return source_count, inserted


def verify_migration(sqlite_conn, pg_conn):
    """Verify that source and destination record counts match."""
    print("\n--- Verification ---")
    all_match = True
    for table in TABLES_IN_ORDER:
        source = count_rows(sqlite_conn, table)
        try:
            dest = count_rows(pg_conn, table, is_pg=True)
        except psycopg.Error:
            dest = -1
        match = "MATCH" if source == dest else "MISMATCH"
        if source != dest:
            all_match = False
        print(f"  {table}: source={source}, dest={dest} [{match}]")
    return all_match


def main():
    print("=" * 70)
    print("StudyHub SQLite to PostgreSQL Migration Script")
    print("=" * 70)

    # Safety: ensure the source SQLite file is not the same as PG
    print(f"\nSource SQLite: {Config.SQLITE_DB}")
    if not os.path.exists(Config.SQLITE_DB):
        print(f"ERROR: SQLite database not found at {Config.SQLITE_DB}")
        sys.exit(1)

    sqlite_conn = get_sqlite_conn()
    pg_conn = get_postgres_conn()

    print(f"PostgreSQL: connected to DB successfully")

    # Check for --reset flag to allow cleaning partial migrations
    should_reset = '--reset' in sys.argv

    # Check current PG state
    truncate_pg_tables(pg_conn, should_reset)

    print("\n--- Starting Migration ---")

    total_source = 0
    total_inserted = 0

    for table in TABLES_IN_ORDER:
        source_count, inserted = migrate_table(sqlite_conn, pg_conn, table)
        total_source += source_count
        total_inserted += inserted
        pg_conn.commit()
        print(f"  {table}: {source_count} source rows, {inserted} inserted")

    print(f"\n--- Migration Summary ---")
    print(f"Total rows transferred: {total_inserted}")
    print(f"Total source rows: {total_source}")

    # Verify
    if verify_migration(sqlite_conn, pg_conn):
        print("\nAll table counts match!")
    else:
        print("\nSome table counts do not match - review above output")

    sqlite_conn.close()
    pg_conn.close()

    print("\nMigration complete. The SQLite database was not modified.")
    print("Review the verification results before switching your application to PostgreSQL.")


if __name__ == '__main__':
    main()
