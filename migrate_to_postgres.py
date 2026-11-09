"""Safely migrate an existing SQLite database into a PostgreSQL schema.

The default mode validates the source, target connection, and destination
schema without writing data. Use --apply to migrate. Production writes require
--target production and --confirm-production. The source SQLite file is opened
read-only and is never modified.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.types.json import Jsonb

from config import Config, _normalize_postgres_url


TABLES_IN_ORDER = (
    "users",
    "admins",
    "categories",
    "subjects",
    "materials",
    "favorites",
    "contact_messages",
    "announcements",
    "guest_views",
    "guest_downloads",
    "settings",
    "user_sessions",
    "login_history",
    "failed_login_attempts",
    "admin_audit_log",
    "pending_notifications",
    "user_oauth_accounts",
    "user_notifications",
)


def _is_loopback(host):
    return (host or "").lower() in {"localhost", "127.0.0.1", "::1"}


def resolve_target_url(target, environ=None):
    """Return a validated migration target URL without printing its contents."""
    environ = os.environ if environ is None else environ
    is_render = (
        str(environ.get("RENDER", "")).strip().lower() in {"1", "true", "yes", "on"}
        or bool(environ.get("RENDER_SERVICE_ID"))
    )
    if is_render and target != "production":
        return None, "Render migration commands must explicitly select --target production."

    if target == "production":
        value = environ.get("DATABASE_URL")
        if not value:
            return None, "Production migration target requires DATABASE_URL."
    else:
        value = environ.get("DEV_DATABASE_URL") or environ.get("LOCAL_DATABASE_URL")
        if not value:
            return (
                None,
                "Local PostgreSQL migration target requires DEV_DATABASE_URL or "
                "LOCAL_DATABASE_URL; a remote DATABASE_URL is never selected locally.",
            )

    normalized, error = _normalize_postgres_url(value)
    if error:
        return None, error
    if target == "development" and not _is_loopback(urlsplit(normalized).hostname):
        return None, (
            "Development migration target must be a loopback PostgreSQL URL. "
            "Use --target production with explicit confirmation for production."
        )
    return normalized, None


def open_sqlite_readonly(db_path):
    """Open an existing SQLite source without creating or changing it."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("SQLite source database does not exist: {}".format(path))
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def open_postgres(target_url):
    """Connect and validate PostgreSQL before migration work starts."""
    parsed = urlsplit(target_url)
    options = {
        "connect_timeout": Config.DB_CONNECT_TIMEOUT,
        "application_name": "studyhub-migration",
    }
    if not parsed.query or "sslmode=" not in parsed.query.lower():
        options["sslmode"] = "prefer" if _is_loopback(parsed.hostname) else "require"

    try:
        conn = psycopg.connect(target_url, **options)
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise RuntimeError("PostgreSQL validation query returned an unexpected result.")
        conn.rollback()
        return conn
    except psycopg.Error as exc:
        raise RuntimeError(_safe_postgres_error(exc)) from None


def _safe_postgres_error(exc):
    message = str(exc).lower()
    if "getaddrinfo" in message or "name or service not known" in message:
        return "PostgreSQL host name could not be resolved. Check network/DNS and the configured target."
    if "timeout" in message or "timed out" in message:
        return "PostgreSQL connection timed out. Check target availability and network access."
    if "password authentication failed" in message or "authentication failed" in message:
        return "PostgreSQL authentication failed. Verify the target credentials in the selected environment variable."
    if "connection refused" in message or "could not connect" in message:
        return "PostgreSQL refused or could not accept the connection. Verify host, port, and server status."
    return "PostgreSQL connection failed ({}). Check target configuration and server logs.".format(
        type(exc).__name__
    )


def sqlite_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    existing = {row["name"] for row in rows}
    return [table for table in TABLES_IN_ORDER if table in existing], sorted(
        existing.difference(TABLES_IN_ORDER)
    )


def source_counts(conn, tables):
    counts = {}
    for table in tables:
        counts[table] = conn.execute(
            'SELECT COUNT(*) FROM "{}"'.format(table.replace('"', '""'))
        ).fetchone()[0]
    return counts


def postgres_columns(conn, table):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, udt_name "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        return {
            row[0]: {"data_type": row[1], "udt_name": row[2]}
            for row in cursor.fetchall()
        }


def migration_column_pairs(table, source_columns, destination_columns):
    """Map source columns to compatible destination columns."""
    pairs = []
    for source_column in source_columns:
        if (
            table == "user_oauth_accounts"
            and source_column == "provider_subject"
            and "provider_subject_hash" in source_columns
            and "provider_subject_hash" in destination_columns
            and "provider_subject" not in destination_columns
        ):
            continue
        destination_column = source_column
        if (
            table == "user_oauth_accounts"
            and source_column == "provider_subject"
            and "provider_subject_hash" not in source_columns
            and "provider_subject_hash" in destination_columns
            and "provider_subject" not in destination_columns
        ):
            destination_column = "provider_subject_hash"
        pairs.append((source_column, destination_column))
    return pairs


def _migration_value(table, source_column, destination_column, value, type_info):
    if (
        table == "user_oauth_accounts"
        and source_column == "provider_subject"
        and destination_column == "provider_subject_hash"
        and value is not None
    ):
        value = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return _convert_value(value, type_info)


def validate_destination_schema(sqlite_conn, pg_conn, tables):
    problems = []
    destination_counts = {}
    type_maps = {}
    for table in tables:
        source_columns = {
            row[1]
            for row in sqlite_conn.execute(
                'PRAGMA table_info("{}")'.format(table.replace('"', '""'))
            ).fetchall()
        }
        try:
            dest_columns = postgres_columns(pg_conn, table)
            with pg_conn.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM "{}"'.format(table))
                destination_counts[table] = cursor.fetchone()[0]
        except psycopg.Error:
            pg_conn.rollback()
            problems.append("{}: destination table missing or unreadable".format(table))
            continue
        column_pairs = migration_column_pairs(table, source_columns, dest_columns)
        missing = sorted(
            destination_column
            for _, destination_column in column_pairs
            if destination_column not in dest_columns
        )
        if missing:
            problems.append(
                "{}: destination is missing source columns ({})".format(
                    table, ", ".join(missing)
                )
            )
        type_maps[table] = dest_columns
    return problems, destination_counts, type_maps


def _convert_value(value, type_info):
    if value is None:
        return None
    data_type = type_info["data_type"]
    udt_name = type_info["udt_name"]
    if data_type == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "t", "yes", "on"}
        return bool(value)
    if data_type == "date" and isinstance(value, str):
        return date.fromisoformat(value[:10])
    if data_type.startswith("timestamp") and isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if udt_name in ("json", "jsonb") and isinstance(value, str):
        return Jsonb(json.loads(value))
    if data_type in ("text", "character varying", "character") and isinstance(value, (int, float)):
        return str(value)
    return value


def migrate_table(sqlite_conn, pg_conn, table, column_types):
    cursor = sqlite_conn.execute('SELECT * FROM "{}"'.format(table.replace('"', '""')))
    source_columns = [description[0] for description in cursor.description]
    column_pairs = migration_column_pairs(table, source_columns, column_types)
    rows = cursor.fetchall()
    if not rows:
        return {"source": 0, "inserted": 0, "conflicts": 0, "fk_skipped": 0, "errors": 0}

    destination_columns = [destination for _, destination in column_pairs]
    quoted_columns = ", ".join(
        '"{}"'.format(name.replace('"', '""')) for name in destination_columns
    )
    placeholders = ", ".join(["%s"] * len(destination_columns))
    query = (
        'INSERT INTO "{}" ({}) VALUES ({}) ON CONFLICT DO NOTHING'
        .format(table, quoted_columns, placeholders)
    )
    counts = {
        "source": len(rows),
        "inserted": 0,
        "conflicts": 0,
        "fk_skipped": 0,
        "errors": 0,
    }

    try:
        with pg_conn.cursor() as pg_cursor:
            for row_number, row in enumerate(rows, 1):
                values = tuple(
                    _migration_value(
                        table,
                        source_column,
                        destination_column,
                        row[source_column],
                        column_types[destination_column],
                    )
                    for source_column, destination_column in column_pairs
                )
                pg_cursor.execute("SAVEPOINT studyhub_migration_row")
                try:
                    pg_cursor.execute(query, values)
                    if pg_cursor.rowcount:
                        counts["inserted"] += 1
                    else:
                        counts["conflicts"] += 1
                    pg_cursor.execute("RELEASE SAVEPOINT studyhub_migration_row")
                except psycopg.Error as exc:
                    pg_cursor.execute("ROLLBACK TO SAVEPOINT studyhub_migration_row")
                    pg_cursor.execute("RELEASE SAVEPOINT studyhub_migration_row")
                    if exc.sqlstate == "23503":
                        counts["fk_skipped"] += 1
                    else:
                        counts["errors"] += 1
                        print(
                            "  {} row {} failed ({}).".format(
                                table, row_number, exc.sqlstate or type(exc).__name__
                            )
                        )
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise

    if "id" in destination_columns and counts["inserted"]:
        with pg_conn.cursor() as pg_cursor:
            pg_cursor.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                "COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM \"{}\"".format(table),
                (table,),
            )
        pg_conn.commit()
    return counts


def reset_destination(pg_conn, tables):
    """Delete only the selected destination tables inside one transaction."""
    try:
        with pg_conn.cursor() as cursor:
            for table in reversed(tables):
                cursor.execute('DELETE FROM "{}"'.format(table))
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise


def initialize_destination_schema(pg_conn):
    """Run the existing additive PostgreSQL schema initializer and app extensions."""
    from init_postgres import SCHEMA_SQL

    try:
        with pg_conn.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS user_oauth_accounts ("
                "id SERIAL PRIMARY KEY, "
                "provider VARCHAR(32) NOT NULL, "
                "provider_subject_hash VARCHAR(64) NOT NULL, "
                "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                "linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "UNIQUE(provider, provider_subject_hash), UNIQUE(user_id, provider))"
            )
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS user_notifications ("
                "id SERIAL PRIMARY KEY, "
                "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                "title VARCHAR(255) NOT NULL, message TEXT NOT NULL, "
                "link VARCHAR(500) DEFAULT NULL, dedupe_key VARCHAR(180) DEFAULT NULL, "
                "is_read INTEGER NOT NULL DEFAULT 0, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "UNIQUE(user_id, dedupe_key))"
            )
            for column, definition in (
                ("published_at", "TIMESTAMP DEFAULT NULL"),
                ("link", "VARCHAR(500) DEFAULT NULL"),
                ("audience", "VARCHAR(20) DEFAULT 'active'"),
            ):
                cursor.execute(
                    'ALTER TABLE announcements ADD COLUMN IF NOT EXISTS "{}" {}'.format(
                        column, definition
                    )
                )
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=Config.SQLITE_DB,
        help="SQLite source path (default: SQLITE_DB/SQLITE_SOURCE_DB or project default)",
    )
    parser.add_argument(
        "--target",
        choices=("development", "production"),
        required=True,
        help="Explicit PostgreSQL target environment",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate source, target connection, and destination schema without writing",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the migration; without this option the command is read-only",
    )
    parser.add_argument(
        "--initialize-schema",
        action="store_true",
        help="Create missing PostgreSQL tables/indexes using the existing schema initializer",
    )
    parser.add_argument(
        "--confirm-production",
        action="store_true",
        help="Explicitly authorize writes when --target production is selected",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete rows from selected destination tables before migration",
    )
    parser.add_argument(
        "--confirm-reset",
        action="store_true",
        help="Also type RESET STUDYHUB TARGET to confirm destructive --reset",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.target == "production" and args.apply and not args.confirm_production:
        print("ERROR: production writes require --confirm-production.", file=sys.stderr)
        return 2
    if args.reset and (not args.apply or not args.confirm_reset):
        print(
            "ERROR: --reset requires --apply and --confirm-reset.",
            file=sys.stderr,
        )
        return 2
    if args.target == "production" and args.reset:
        print("ERROR: destructive reset is disabled for production targets.", file=sys.stderr)
        return 2
    if args.initialize_schema and not args.apply:
        print(
            "ERROR: --initialize-schema requires --apply; schema creation writes to the target.",
            file=sys.stderr,
        )
        return 2
    if args.reset:
        confirmation = input("Type RESET STUDYHUB TARGET to delete destination rows: ")
        if confirmation != "RESET STUDYHUB TARGET":
            print("Reset cancelled.")
            return 2

    source_conn = None
    target_conn = None
    summary = {"source": {}, "destination_before": {}, "tables": {}, "errors": []}
    try:
        source_conn = open_sqlite_readonly(args.source)
        tables, ignored_tables = sqlite_tables(source_conn)
        if not tables:
            raise RuntimeError("SQLite source contains no recognized StudyHub tables.")

        target_url, target_error = resolve_target_url(args.target)
        if target_error:
            raise RuntimeError(target_error)
        target_conn = open_postgres(target_url)

        if args.initialize_schema:
            initialize_destination_schema(target_conn)

        summary["source"] = source_counts(source_conn, tables)
        summary["ignored_source_tables"] = ignored_tables
        schema_errors, destination_counts, type_maps = validate_destination_schema(
            source_conn, target_conn, tables
        )
        summary["destination_before"] = destination_counts
        if schema_errors:
            summary["errors"].extend(schema_errors)
            raise RuntimeError(
                "Destination schema validation failed: {}".format(
                    "; ".join(schema_errors)
                )
            )

        print("Source SQLite: {}".format(Path(args.source).resolve()))
        print("Target PostgreSQL: validated ({})".format(args.target))
        print("Source tables: {}".format(len(tables)))
        print("Mode: {}".format("write" if args.apply else "read-only validation"))

        if args.check_config or not args.apply:
            print("Configuration and schema validation passed; no data was written.")
            return 0

        if args.reset:
            reset_destination(target_conn, tables)
            destination_counts = {table: 0 for table in tables}

        for table in tables:
            result = migrate_table(
                source_conn, target_conn, table, type_maps[table]
            )
            with target_conn.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM "{}"'.format(table))
                result["destination_after"] = cursor.fetchone()[0]
            summary["tables"][table] = result
            print(
                "{}: source={source}, inserted={inserted}, conflicts={conflicts}, "
                "foreign-key skipped={fk_skipped}, errors={errors}, destination={destination_after}"
                .format(table, **result)
            )

        print("Migration summary:")
        print(json.dumps(summary, indent=2, default=str))
        return 1 if any(row["errors"] or row["fk_skipped"] for row in summary["tables"].values()) else 0
    except (
        OSError,
        sqlite3.Error,
        RuntimeError,
        psycopg.Error,
        ValueError,
        TypeError,
    ) as exc:
        if target_conn:
            target_conn.rollback()
        if isinstance(exc, psycopg.Error):
            detail = _safe_postgres_error(exc)
        elif isinstance(exc, RuntimeError):
            detail = str(exc)
        else:
            detail = "Migration failed ({}). No credentials or row values were logged.".format(
                type(exc).__name__
            )
        print("ERROR: {}".format(detail), file=sys.stderr)
        if summary["source"]:
            summary["errors"].append(detail)
            print("Partial migration summary:")
            print(json.dumps(summary, indent=2, default=str))
        return 1
    finally:
        if source_conn:
            source_conn.close()
        if target_conn:
            target_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
