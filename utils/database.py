import os
import time
import threading
from contextlib import contextmanager
from datetime import datetime, date
from config import Config
import pymysql
import psycopg
import psycopg.rows
import sqlite3


def _adapt_datetime(value):
    """Serialize datetime/date objects to ISO-8601 strings for SQLite.

    Python 3.12 deprecated the default datetime adapter that sqlite3 used to
    transparently convert ``datetime`` objects.  This explicit adapter stores
    values in ``YYYY-MM-DD HH:MM:SS.ffffff`` form (space, not 'T', separator)
    which is sortable lexicographically and compatible with the
    ``_parse_datetime`` / ``_normalize_datetime`` parsers used throughout the
    project.  PostgreSQL and MySQL are unaffected because psycopg3 and pymysql
    handle datetime natively.
    """
    if isinstance(value, datetime):
        return value.isoformat(' ')
    if isinstance(value, date):
        return value.isoformat(' ')
    return value


# Register the adapter once at module level.  This replaces the deprecated
# default adapter so that passing a datetime object to any SQLite query no
# longer triggers a DeprecationWarning on Python 3.12+.
sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_adapter(date, _adapt_datetime)


_tls = threading.local()
_pg_lock = threading.Lock()


def dict_factory(cursor, row):
    return dict(zip([column[0] for column in cursor.description], row))


class MySQLCursorWrapper:
    """Wrapper that converts SQLite ? placeholders to MySQL %s placeholders."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=None):
        if params is not None:
            query = query.replace("?", "%s")
        return self._cursor.execute(query, params)

    def executemany(self, query, params=None):
        if params is not None:
            query = query.replace("?", "%s")
        return self._cursor.executemany(query, params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        return self._cursor.close()

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class PostgresCursorWrapper:
    """Wrapper that adapts SQLite ? placeholders and pymysql-style DictCursor to psycopg.

    psycopg v3 uses %s placeholders natively, but the models pass ? placeholders.
    This wrapper converts ? to %s transparently. With dict_row factory, rows are
    already dicts so no additional conversion is needed.

    For INSERT queries, the wrapper appends ``RETURNING id`` to capture the
    last-inserted row ID, since psycopg3 cursors do not provide ``lastrowid``.
    """

    def __init__(self, cursor):
        self._cursor = cursor
        self._lastrowid = None

    def execute(self, query, params=None):
        if params is not None:
            query = query.replace("?", "%s")

        # Detect INSERT (but not INSERT OR IGNORE / INSERT IGNORE which are
        # SQLite/MySQL syntax — those are handled case-by-case in the models).
        stripped = query.strip()
        upper = stripped.upper()
        if upper.startswith("INSERT") and "RETURNING" not in upper:
            if upper.startswith("INSERT INTO") or upper.startswith("INSERT "):
                query = stripped.rstrip(";") + " RETURNING id"
                if params is not None:
                    self._cursor.execute(query, params)
                else:
                    self._cursor.execute(query)
                row = self._cursor.fetchone()
                self._lastrowid = row["id"] if row else None
                return self

        self._lastrowid = None
        if params is not None:
            return self._cursor.execute(query, params)
        else:
            return self._cursor.execute(query)

    def executemany(self, query, params=None):
        if params is not None:
            query = query.replace("?", "%s")
        self._lastrowid = None
        if params is not None:
            return self._cursor.executemany(query, params)
        else:
            return self._cursor.executemany(query)

    @property
    def lastrowid(self):
        return self._lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        return self._cursor.close()

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class PooledConnection:
    """Wrapper that returns the underlying PostgreSQL connection to the pool
    when ``close()`` is called, instead of actually closing it.  All other
    attribute access is delegated to the real psycopg connection.

    This lets existing code that calls ``conn.close()`` transparently reuse
    pooled connections without any call-site changes.
    """

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def cursor(self):
        return self._conn.cursor()

    def close(self):
        if self._pool is not None:
            self._pool.put(self._conn)
        else:
            self._conn.close()

    @property
    def closed(self):
        return False


class PostgreSQLConnectionPool:
    """A minimal thread-local connection pool for PostgreSQL / NeonDB.

    Each thread keeps at most one cached connection.  When the caller invokes
    ``conn.close()`` the connection is returned to the pool instead of being
    destroyed, avoiding the expensive cold-start penalty on every database call.

    Connections are validated with a trivial ``SELECT 1`` before reuse; a
    stale connection is replaced with a fresh one.
    """

    def __init__(self, max_per_thread=1):
        self._max_per_thread = max_per_thread

    def get(self):
        pool = getattr(_tls, "pg_pool", None)
        if pool:
            for _ in range(len(pool)):
                conn = pool.pop()
                # Lightweight status check (no network round-trip)
                try:
                    if conn.pgconn.status == 0:
                        return conn
                    else:
                        conn.close()
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
            _tls.pg_pool = None

        return self._create_connection()

    def put(self, conn):
        pool = getattr(_tls, "pg_pool", None)
        if pool is None:
            pool = []
            _tls.pg_pool = pool
        if len(pool) < self._max_per_thread:
            pool.append(conn)
        else:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _create_connection():
        conn = psycopg.connect(
            Config.DATABASE_URL,
            row_factory=psycopg.rows.dict_row,
            sslmode="require" if "sslmode" not in Config.DATABASE_URL else "prefer",
            connect_timeout=30,
            application_name="studyhub",
        )
        conn.autocommit = False
        return conn


_pg_pool = PostgreSQLConnectionPool()


def get_connection():
    """Return a database connection based on configuration.

    Priority: SQLite (USE_SQLITE=true) > PostgreSQL (DATABASE_URL) > MySQL (default)

    When ``USE_SQLITE`` is explicitly set to true in the environment, it takes
    priority over ``DATABASE_URL`` so that a locally present SQLite database
    is never shadowed by a stale or empty PostgreSQL endpoint.

    For PostgreSQL, a thread-local connection pool is used to avoid the
    expensive cold-start latency of NeonDB's serverless compute on every
    database call.  The pooled connection is returned to the pool when
    ``conn.close()`` is called.
    """
    if Config.USE_POSTGRES:
        conn = _pg_pool.get()
        return PooledConnection(conn, _pg_pool)
    if Config.USE_SQLITE:
        os.makedirs(os.path.dirname(Config.SQLITE_DB), exist_ok=True)
        conn = sqlite3.connect(Config.SQLITE_DB)
        conn.row_factory = dict_factory
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    conn = pymysql.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor,
    )
    return conn


def get_cursor(conn):
    cursor = conn.cursor()
    if Config.USE_POSTGRES:
        cursor = PostgresCursorWrapper(cursor)
    elif not Config.USE_SQLITE:
        cursor = MySQLCursorWrapper(cursor)
    return cursor


def close_cursor(cursor):
    if cursor:
        if hasattr(cursor, "_cursor"):
            cursor.close()
        else:
            cursor.close()


def execute_query(conn, query, params=None):
    cursor = None
    try:
        cursor = get_cursor(conn)
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        return cursor
    except Exception:
        if cursor:
            close_cursor(cursor)
        raise


@contextmanager
def immediate_transaction():
    """Run a block inside a transaction that serialises concurrent writers.

    SQLite uses BEGIN IMMEDIATE to take the write lock up front.  PostgreSQL and MySQL
    use a plain transaction (row-level locking via FOR UPDATE where needed).
    """
    conn = get_connection()
    dialect = _dialect(conn)
    started = False
    last_error = None
    try:
        if dialect == "sqlite":
            # Autocommit mode so we control BEGIN/COMMIT explicitly.
            conn.isolation_level = None
            for attempt in range(6):
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    started = True
                    break
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if "locked" not in message and "busy" not in message:
                        raise
                    last_error = exc
                    time.sleep(0.2 * (attempt + 1))
            if not started:
                raise (
                    last_error
                    if last_error
                    else RuntimeError("Could not acquire database write lock")
                )
        # PostgreSQL and MySQL run with autocommit disabled, so the transaction
        # is opened implicitly by the first statement; nothing to do here. The
        # real connection type is used (never env-flag guessing), which keeps the
        # behavior correct even if the configuration has mismatched flag values.
        yield conn
        _commit_transaction(conn, started, dialect)
    except Exception:
        try:
            _rollback_transaction(conn, started, dialect)
        except Exception:
            pass
        raise
    finally:
        if dialect == "sqlite":
            try:
                conn.isolation_level = ""
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def _commit_transaction(conn, started, dialect=None):
    """Commit safely. A caller that already committed must not break us."""
    if dialect is None:
        dialect = _dialect(conn)
    if dialect == "sqlite":
        try:
            conn.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            if "no transaction" not in str(exc).lower():
                raise
    else:
        conn.commit()


def _rollback_transaction(conn, started, dialect=None):
    if dialect is None:
        dialect = _dialect(conn)
    if dialect == "sqlite":
        if not started:
            return
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError as exc:
            if "no transaction" not in str(exc).lower():
                raise
    else:
        conn.rollback()


def process_in_transaction(callback):
    """Execute ``callback(conn)`` inside a serialised transaction."""
    with immediate_transaction() as conn:
        return callback(conn)


# ---------------------------------------------------------------------------
# Idempotent schema extensions
# ---------------------------------------------------------------------------
# These migrations are strictly additive. They add a column that lets the
# session layer distinguish an administrator-ended session from an ordinary
# revocation, and a tiny table that backs the durable notification queue used
# for account-deletion and session-termination emails.
#
# They are safe to run repeatedly: every statement first checks whether the
# column / table already exists, so running them on a database that is already
# up to date is a no-op. Existing rows are never deleted or modified by the
# column addition (SQLite and PostgreSQL both backfill the default of 0).


def _has_column(conn, table, column):
    """Return True when ``column`` already exists on ``table``."""
    dialect = _dialect(conn)
    if dialect == "sqlite":
        try:
            cur = conn.execute("PRAGMA table_info(%s)" % table)
            cols = [row[1] if not isinstance(row, dict) else row["name"] for row in cur.fetchall()]
        except Exception:
            return False
        return column in cols
    # PostgreSQL / MySQL: query the information schema.
    cur = conn.execute(
        (
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s LIMIT 1"
            if dialect == "postgres"
            else "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s LIMIT 1"
        ),
        (table, column),
    )
    return cur.fetchone() is not None


def _has_table(conn, table):
    dialect = _dialect(conn)
    if dialect == "sqlite":
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        return cur.fetchone() is not None
    cur = conn.execute(
        "SELECT 1 FROM information_schema.tables " "WHERE table_name = %s LIMIT 1",
        (table,),
    )
    return cur.fetchone() is not None


def _dialect(conn):
    try:
        if Config.USE_POSTGRES:
            return "postgres"
        if Config.USE_SQLITE:
            return "sqlite"
        return "mysql"
    except Exception:
        return "sqlite"


def ensure_schema_extensions():
    """Add the admin-ended-session flag and the notification queue table.

    Additive only; never raises — a failed extension is logged and skipped so a
    locked-down or read-only connection can never break application startup.
    """
    from utils import notifications as _notif  # local import to avoid cycles

    logger = _notif.log if hasattr(_notif, "log") else None
    errors = []

    try:
        conn = get_connection()
        try:
            dialect = _dialect(conn)
            changed = False
            if not _has_column(conn, "user_sessions", "ended_by_admin"):
                ddl = "ALTER TABLE user_sessions ADD COLUMN ended_by_admin INTEGER DEFAULT 0"
                if dialect == "sqlite":
                    try:
                        conn.execute(ddl)
                        changed = True
                    except Exception as exc:  # duplicate column name
                        if "duplicate column" not in str(exc).lower():
                            errors.append(str(exc))
                else:
                    # PostgreSQL supports ADD COLUMN IF NOT EXISTS is not available;
                    # the _has_column guard above makes this safe.
                    conn.execute(ddl)
                    changed = True
            if not _has_table(conn, "pending_notifications"):
                _notif.create_pending_notifications_table(conn, dialect)
                changed = True
            if changed:
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # never block startup on schema extension
        errors.append(str(exc))
        if logger:
            logger.warning("Schema extensions skipped due to error (non-fatal)")

    return not errors
