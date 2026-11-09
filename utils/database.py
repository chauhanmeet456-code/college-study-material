import os
import time
import threading
import logging
import hashlib
from contextlib import contextmanager
from datetime import datetime, date
from urllib.parse import parse_qs, urlsplit
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
_transaction_logger = logging.getLogger("studyhub.database")


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
                try:
                    row = self._cursor.fetchone()
                    self._lastrowid = row["id"] if row and "id" in row else None
                except Exception:
                    self._lastrowid = None
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
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def cursor(self):
        return self._conn.cursor()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._pool is not None:
            try:
                self._pool.put(self._conn)
            except Exception as exc:
                _transaction_logger.warning(
                    "Could not return PostgreSQL connection to pool (%s)",
                    type(exc).__name__,
                )
                PostgreSQLConnectionPool._discard(self._conn)
        else:
            try:
                self._conn.close()
            except Exception:
                pass

    @property
    def closed(self):
        if self._closed:
            return True
        try:
            return bool(self._conn.closed)
        except Exception:
            return True


class DatabaseConnectionError(psycopg.OperationalError):
    """Raised with clear actionable guidance when database connection fails."""

    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(
        self, message=None, *, original_error=None, host=None, port=None,
        database=None, user=None,
    ):
        error_text = str(original_error or '').lower()
        if 'getaddrinfo' in error_text or '11001' in error_text:
            safe_message = (
                "PostgreSQL DNS name resolution failed ([Errno 11001] getaddrinfo failed). "
                "For local development on Windows, set DEV_DATABASE_URL (or LOCAL_DATABASE_URL) in .env "
                "to your local PostgreSQL instance (e.g. postgresql://postgres:password@localhost:5432/college_study), "
                "or set USE_SQLITE=true if developing offline. In production on Render, DATABASE_URL is used."
            )
        elif 'connection refused' in error_text or '10061' in error_text:
            safe_message = (
                "PostgreSQL connection refused. "
                "Confirm the configured PostgreSQL database server is running and reachable."
            )
        elif 'password authentication failed' in error_text:
            safe_message = (
                "PostgreSQL password authentication failed. "
                "Verify the configured database credentials."
            )
        else:
            safe_message = message or 'Could not connect to the configured PostgreSQL database.'
        super().__init__(safe_message)


def is_database_connection_error(exc):
    """Return whether an exception indicates a database connectivity failure."""
    if isinstance(
        exc,
        (DatabaseConnectionError, psycopg.OperationalError, pymysql.err.OperationalError),
    ):
        return True

    if isinstance(exc, sqlite3.OperationalError):
        message = str(exc).lower()
        return any(text in message for text in (
            'unable to open database file',
            'database is locked',
            'database or disk is full',
            'disk i/o error',
        ))
    return False


def is_database_unavailable_error(exc):
    """Recognize connection failures and missing optional-schema objects."""
    if is_database_connection_error(exc):
        return True

    sqlstate = getattr(exc, 'sqlstate', None)
    if sqlstate in {'42P01', '42703'}:  # PostgreSQL undefined table/column
        return True

    error_code = getattr(exc, 'args', (None,))[0]
    if error_code == 1146:  # MySQL/MariaDB missing table
        return True

    message = str(exc).lower()
    return 'no such table:' in message or 'no such column:' in message


class PostgreSQLConnectionPool:
    """A thread-local connection pool for PostgreSQL / NeonDB.

    Each thread keeps a pool of cached connections (up to Config.DB_POOL_MAX_PER_THREAD).
    When the caller invokes ``conn.close()`` on a PooledConnection, it is returned
    to the pool instead of being destroyed, avoiding expensive cold-start and
    handshake latency on every database call.

    Pooled connections are rolled back and health-checked before reuse so
    terminated or transaction-aborted NeonDB sessions are discarded.
    """

    def __init__(self, max_per_thread=None):
        self._max_per_thread = max_per_thread or Config.DB_POOL_MAX_PER_THREAD

    @staticmethod
    def _database_key():
        return hashlib.sha256(
            (Config.DATABASE_URL or '').encode('utf-8')
        ).digest()

    def get(self):
        pool = getattr(_tls, "pg_pool", None)
        database_key = self._database_key()
        while pool:
            conn = pool.pop()
            if (
                getattr(conn, "_studyhub_database_key", database_key) == database_key
                and self._prepare_for_reuse(conn)
            ):
                return conn
            self._discard(conn)

        return self._create_connection()

    def put(self, conn):
        if (
            getattr(conn, "_studyhub_database_key", self._database_key())
            != self._database_key()
            or not self._reset_transaction(conn)
        ):
            self._discard(conn)
            return

        conn._studyhub_last_used = time.time()
        pool = getattr(_tls, "pg_pool", None)
        if pool is None:
            pool = []
            _tls.pg_pool = pool
        if len(pool) < self._max_per_thread:
            pool.append(conn)
        else:
            self._discard(conn)

    @staticmethod
    def _is_open(conn):
        try:
            return (
                not conn.closed
                and hasattr(conn, "pgconn")
                and conn.pgconn.status == psycopg.pq.ConnStatus.OK
            )
        except Exception:
            return False

    @classmethod
    def _transaction_is_idle(cls, conn):
        try:
            return (
                hasattr(conn, "pgconn")
                and conn.pgconn.transaction_status == psycopg.pq.TransactionStatus.IDLE
            )
        except Exception:
            return False

    @classmethod
    def _reset_transaction(cls, conn):
        if not cls._is_open(conn):
            return False
        try:
            if not cls._transaction_is_idle(conn):
                conn.rollback()
            return cls._is_open(conn) and cls._transaction_is_idle(conn)
        except Exception as exc:
            _transaction_logger.warning(
                "PostgreSQL connection reset failed (%s)",
                type(exc).__name__,
            )
            return False

    @classmethod
    def _prepare_for_reuse(cls, conn):
        if not cls._reset_transaction(conn):
            return False
        cursor = None
        try:
            cursor = conn.execute("SELECT 1")
            return_value = cursor.fetchone()
            if return_value is None:
                return False
            cursor.close()
            cursor = None
            if cls._reset_transaction(conn):
                conn._studyhub_last_used = time.time()
                return True
            return False
        except Exception as exc:
            _transaction_logger.info(
                "Discarding unusable PostgreSQL connection (%s)",
                type(exc).__name__,
            )
            return False
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    @staticmethod
    def _discard(conn):
        try:
            conn.close()
        except Exception:
            pass

    @staticmethod
    def _create_connection():
        db_url = Config.DATABASE_URL
        if Config.DATABASE_CONFIG_ERROR:
            raise DatabaseConnectionError(Config.DATABASE_CONFIG_ERROR)
        if not db_url:
            raise DatabaseConnectionError(
                'PostgreSQL configuration error: no PostgreSQL URL was selected. '
                'Set DEV_DATABASE_URL locally or DATABASE_URL on Render.'
            )

        # Parse host only to choose the default SSL mode; never log the URL.
        try:
            parsed = urlsplit(db_url)
            host = parsed.hostname
            port = parsed.port or 5432
        except ValueError:
            raise DatabaseConnectionError(
                'Configured PostgreSQL URL is malformed.'
            ) from None

        connect_kwargs = {
            "row_factory": psycopg.rows.dict_row,
            "connect_timeout": Config.DB_CONNECT_TIMEOUT,
            "application_name": "studyhub",
        }

        # Respect an explicitly configured sslmode. Local PostgreSQL defaults
        # to prefer SSL; managed/remote PostgreSQL requires encrypted transport.
        if not parse_qs(parsed.query).get("sslmode"):
            connect_kwargs["sslmode"] = (
                "prefer"
                if (host or "").lower() in ("localhost", "127.0.0.1", "::1")
                else "require"
            )

        max_attempts = 3
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                conn = psycopg.connect(db_url, **connect_kwargs)
                conn.autocommit = False
                conn._studyhub_last_used = time.time()
                conn._studyhub_database_key = PostgreSQLConnectionPool._database_key()
                return conn
            except psycopg.OperationalError as exc:
                last_exc = exc
                err_str = str(exc).lower()
                # DNS failures and expired connect timeouts won't improve with an
                # immediate retry; fail fast so optional homepage queries can fall back.
                if any(text in err_str for text in (
                    "getaddrinfo", "11001", "timeout", "timed out",
                )):
                    break
                if attempt < max_attempts:
                    time.sleep(0.5 * attempt)

        _transaction_logger.error(
            "PostgreSQL connection failed (%s)",
            type(last_exc).__name__ if last_exc else "unknown error",
        )
        raise DatabaseConnectionError(
            original_error=last_exc,
            host=host,
            port=port,
        ) from None


_pg_pool = PostgreSQLConnectionPool()


def get_connection():
    """Return a database connection based on configuration.

    Local priority: explicit DATABASE_BACKEND selection, then a configured
    development PostgreSQL URL, explicit SQLite mode, and configured MySQL.

    A local ``DATABASE_URL`` is never selected. ``DEV_DATABASE_URL`` takes
    precedence over the ``USE_SQLITE`` convenience flag unless the caller
    explicitly selects another backend with ``DATABASE_BACKEND``.

    For PostgreSQL, a thread-local connection pool is used to avoid the
    expensive cold-start latency of NeonDB's serverless compute on every
    database call.  The pooled connection is returned to the pool when
    ``conn.close()`` is called.
    """
    if Config.USE_SQLITE:
        os.makedirs(os.path.dirname(Config.SQLITE_DB), exist_ok=True)
        conn = sqlite3.connect(Config.SQLITE_DB, timeout=30)
        conn.row_factory = dict_factory
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn
    if Config.DATABASE_CONFIG_ERROR:
        raise DatabaseConnectionError(Config.DATABASE_CONFIG_ERROR)
    if Config.USE_POSTGRES:
        conn = _pg_pool.get()
        return PooledConnection(conn, _pg_pool)
    if not any(os.getenv(key) for key in ('DB_HOST', 'DB_NAME', 'DB_USER')):
        raise DatabaseConnectionError(
            'No database is configured. Set DEV_DATABASE_URL locally or '
            'DATABASE_URL on Render.'
        )
    try:
        conn = pymysql.connect(
            host=Config.MYSQL_HOST,
            port=Config.MYSQL_PORT,
            user=Config.MYSQL_USER,
            password=Config.MYSQL_PASSWORD,
            database=Config.MYSQL_DB,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=Config.DB_CONNECT_TIMEOUT,
        )
    except pymysql.err.OperationalError as exc:
        error_code = exc.args[0] if exc.args else None
        if error_code == 1045:
            message = (
                'MySQL authentication failed for the configured local database '
                'account. Verify DB_USER and DB_PASSWORD in .env; no credentials '
                'were logged.'
            )
        elif error_code in (2002, 2003, 2006, 2013):
            message = (
                'The configured MySQL server is unavailable or refused the '
                'connection. Verify DB_HOST, DB_PORT, and that MySQL is running.'
            )
        else:
            message = (
                'MySQL connection failed (error code {}). Verify the local '
                'database settings in .env.'.format(error_code or 'unknown')
            )
        _transaction_logger.error(
            'MySQL connection failed (error code %s)',
            error_code or 'unknown',
        )
        raise DatabaseConnectionError(message) from None
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
        except Exception as cleanup_error:
            _transaction_logger.warning(
                "Transaction rollback failed (%s)",
                type(cleanup_error).__name__,
            )
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
    """Commit only a transaction owned by this context manager."""
    if dialect is None:
        dialect = _dialect(conn)
    if dialect == "sqlite":
        if not started:
            return
        if not conn.in_transaction:
            raise RuntimeError(
                "SQLite transaction ended before its owning context committed"
            )
        conn.execute("COMMIT")
    else:
        conn.commit()


def _rollback_transaction(conn, started, dialect=None):
    if dialect is None:
        dialect = _dialect(conn)
    if dialect == "sqlite":
        if not started or not conn.in_transaction:
            return
        conn.execute("ROLLBACK")
    else:
        conn.rollback()


def process_in_transaction(callback):
    """Execute ``callback(conn)`` inside a serialised transaction."""
    with immediate_transaction() as conn:
        return callback(conn)


# ---------------------------------------------------------------------------
# Idempotent schema extensions
# ---------------------------------------------------------------------------
# These migrations are strictly additive. They add a session status column,
# durable notification queues, and external OAuth identity storage.
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
    if dialect == "postgres":
        query = (
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = %s AND column_name = %s LIMIT 1"
        )
    else:
        query = (
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = DATABASE() "
            "AND table_name = %s AND column_name = %s LIMIT 1"
        )
    cur = conn.execute(query, (table, column))
    return cur.fetchone() is not None


def _has_table(conn, table):
    dialect = _dialect(conn)
    if dialect == "sqlite":
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        return cur.fetchone() is not None
    if dialect == "postgres":
        query = (
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = %s LIMIT 1"
        )
    else:
        query = (
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s LIMIT 1"
        )
    cur = conn.execute(query, (table,))
    return cur.fetchone() is not None


def _dialect(conn):
    if isinstance(conn, sqlite3.Connection):
        return "sqlite"
    if isinstance(conn, PooledConnection):
        return "postgres"
    if isinstance(conn, pymysql.connections.Connection):
        return "mysql"
    try:
        if Config.USE_POSTGRES:
            return "postgres"
        if Config.USE_SQLITE:
            return "sqlite"
        return "mysql"
    except Exception:
        return "sqlite"


def ensure_schema_extensions():
    """Apply additive session, notification, and announcement schema updates.

    Database connectivity or missing-schema failures are logged and skipped;
    unexpected programming and DDL errors propagate to the caller.
    """
    from utils import notifications as _notif  # local import to avoid cycles

    errors = []

    try:
        conn = get_connection()
        try:
            dialect = _dialect(conn)
            changed = False
            if _has_table(conn, "user_sessions") and not _has_column(conn, "user_sessions", "ended_by_admin"):
                ddl = "ALTER TABLE user_sessions ADD COLUMN ended_by_admin INTEGER DEFAULT 0"
                if dialect == "sqlite":
                    try:
                        conn.execute(ddl)
                        changed = True
                    except Exception as exc:  # duplicate column name
                        if "duplicate column" not in str(exc).lower():
                            errors.append(type(exc).__name__)
                else:
                    # PostgreSQL supports ADD COLUMN IF NOT EXISTS is not available;
                    # the _has_column guard above makes this safe.
                    conn.execute(ddl)
                    changed = True
            if not _has_table(conn, "pending_notifications"):
                _notif.create_pending_notifications_table(conn, dialect)
                changed = True
            if _has_table(conn, "users") and not _has_table(conn, "user_oauth_accounts"):
                oauth_id_column = (
                    "id INTEGER PRIMARY KEY AUTOINCREMENT"
                    if dialect == "sqlite"
                    else "id SERIAL PRIMARY KEY"
                    if dialect == "postgres"
                    else "id BIGINT AUTO_INCREMENT PRIMARY KEY"
                )
                conn.execute(
                    "CREATE TABLE user_oauth_accounts ("
                    + oauth_id_column + ", "
                    "provider VARCHAR(32) NOT NULL, "
                    "provider_subject_hash VARCHAR(64) NOT NULL, "
                    "user_id INTEGER NOT NULL, "
                    "linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                    "UNIQUE (provider, provider_subject_hash), "
                    "UNIQUE (user_id, provider), "
                    "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)"
                )
                changed = True
            queue_columns = (
                ("retryable", "INTEGER DEFAULT 1"),
                ("next_attempt_at", "TIMESTAMP DEFAULT NULL"),
                ("claimed_at", "TIMESTAMP DEFAULT NULL"),
                ("last_error", "VARCHAR(100) DEFAULT NULL"),
            )
            for column, definition in queue_columns:
                if not _has_column(conn, "pending_notifications", column):
                    try:
                        conn.execute(
                            "ALTER TABLE pending_notifications ADD COLUMN "
                            + column + " " + definition
                        )
                        changed = True
                    except Exception as exc:
                        if "duplicate column" not in str(exc).lower():
                            errors.append(type(exc).__name__)
            if changed:
                conn.commit()

            if _has_table(conn, "announcements"):
                announcement_columns = (
                    ("published_at", "TIMESTAMP DEFAULT NULL"),
                    ("link", "VARCHAR(500) DEFAULT NULL"),
                    ("audience", "VARCHAR(20) DEFAULT 'active'"),
                )
                for column, definition in announcement_columns:
                    if not _has_column(conn, "announcements", column):
                        conn.execute(
                            "ALTER TABLE announcements ADD COLUMN "
                            + column + " " + definition
                        )
                        changed = True

            if _has_table(conn, "users") and not _has_table(conn, "user_notifications"):
                if dialect == "sqlite":
                    conn.execute(
                        "CREATE TABLE user_notifications ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "user_id INTEGER NOT NULL, "
                        "title VARCHAR(255) NOT NULL, "
                        "message TEXT NOT NULL, "
                        "link VARCHAR(500) DEFAULT NULL, "
                        "dedupe_key VARCHAR(180) DEFAULT NULL, "
                        "is_read INTEGER NOT NULL DEFAULT 0, "
                        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                        "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, "
                        "UNIQUE(user_id, dedupe_key))"
                    )
                elif dialect == "postgres":
                    conn.execute(
                        "CREATE TABLE user_notifications ("
                        "id SERIAL PRIMARY KEY, "
                        "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                        "title VARCHAR(255) NOT NULL, "
                        "message TEXT NOT NULL, "
                        "link VARCHAR(500) DEFAULT NULL, "
                        "dedupe_key VARCHAR(180) DEFAULT NULL, "
                        "is_read INTEGER NOT NULL DEFAULT 0, "
                        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                        "UNIQUE(user_id, dedupe_key))"
                    )
                else:
                    conn.execute(
                        "CREATE TABLE user_notifications ("
                        "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
                        "user_id BIGINT NOT NULL, "
                        "title VARCHAR(255) NOT NULL, "
                        "message TEXT NOT NULL, "
                        "link VARCHAR(500) DEFAULT NULL, "
                        "dedupe_key VARCHAR(180) DEFAULT NULL, "
                        "is_read INTEGER NOT NULL DEFAULT 0, "
                        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                        "UNIQUE KEY uq_user_notification_dedupe(user_id, dedupe_key), "
                        "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)"
                    )
                changed = True

            # Add performance indexes if tables exist
            if _has_table(conn, "materials"):
                for idx_stmt in (
                    "CREATE INDEX IF NOT EXISTS idx_materials_active ON materials(is_active)",
                    "CREATE INDEX IF NOT EXISTS idx_materials_featured ON materials(is_featured)",
                    "CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(category_id)",
                    "CREATE INDEX IF NOT EXISTS idx_materials_subject ON materials(subject_id)",
                ):
                    try:
                        conn.execute(idx_stmt)
                        changed = True
                    except Exception:
                        pass
            if _has_table(conn, "announcements"):
                try:
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_announcements_active ON announcements(is_active)")
                    changed = True
                except Exception:
                    pass

            if changed:
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # database outages must not prevent service startup
        if not is_database_unavailable_error(exc):
            raise
        errors.append(type(exc).__name__)
        _transaction_logger.error(
            "Database schema extension initialization failed (%s)",
            type(exc).__name__,
        )

    return not errors
