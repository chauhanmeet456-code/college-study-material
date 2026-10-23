import os
import time
import threading
from contextlib import contextmanager
from config import Config
import pymysql
import psycopg
import psycopg.rows
import sqlite3

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
            query = query.replace('?', '%s')
        return self._cursor.execute(query, params)

    def executemany(self, query, params=None):
        if params is not None:
            query = query.replace('?', '%s')
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
            query = query.replace('?', '%s')

        # Detect INSERT (but not INSERT OR IGNORE / INSERT IGNORE which are
        # SQLite/MySQL syntax — those are handled case-by-case in the models).
        stripped = query.strip()
        upper = stripped.upper()
        if upper.startswith('INSERT') and 'RETURNING' not in upper:
            if upper.startswith('INSERT INTO') or upper.startswith('INSERT '):
                query = stripped.rstrip(';') + ' RETURNING id'
                if params is not None:
                    self._cursor.execute(query, params)
                else:
                    self._cursor.execute(query)
                row = self._cursor.fetchone()
                self._lastrowid = row['id'] if row else None
                return self

        self._lastrowid = None
        if params is not None:
            return self._cursor.execute(query, params)
        else:
            return self._cursor.execute(query)

    def executemany(self, query, params=None):
        if params is not None:
            query = query.replace('?', '%s')
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
        pool = getattr(_tls, 'pg_pool', None)
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
        pool = getattr(_tls, 'pg_pool', None)
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
            sslmode='require' if 'sslmode' not in Config.DATABASE_URL else 'prefer',
            connect_timeout=30,
            application_name='studyhub',
        )
        conn.autocommit = False
        return conn


_pg_pool = PostgreSQLConnectionPool()


def get_connection():
    """Return a database connection based on configuration.

    Priority: PostgreSQL (DATABASE_URL) > SQLite (USE_SQLITE=true) > MySQL (default)

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
        conn.execute('PRAGMA foreign_keys = ON')
        return conn
    conn = pymysql.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor
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
        if hasattr(cursor, '_cursor'):
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
    started = False
    last_error = None
    try:
        if Config.USE_SQLITE:
            # Autocommit mode so we control BEGIN/COMMIT explicitly.
            conn.isolation_level = None
            for attempt in range(6):
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    started = True
                    break
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if 'locked' not in message and 'busy' not in message:
                        raise
                    last_error = exc
                    time.sleep(0.2 * (attempt + 1))
            if not started:
                raise last_error if last_error else RuntimeError('Could not acquire database write lock')
        yield conn
        _commit_transaction(conn, started)
    except Exception:
        try:
            _rollback_transaction(conn, started)
        except Exception:
            pass
        raise
    finally:
        try:
            if Config.USE_SQLITE:
                conn.isolation_level = ''
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _commit_transaction(conn, started):
    """Commit safely. A caller that already committed must not break us."""
    if Config.USE_SQLITE:
        try:
            conn.execute('COMMIT')
        except sqlite3.OperationalError as exc:
            if 'no transaction' not in str(exc).lower():
                raise
    else:
        conn.commit()


def _rollback_transaction(conn, started):
    if Config.USE_SQLITE:
        if not started:
            return
        try:
            conn.execute('ROLLBACK')
        except sqlite3.OperationalError as exc:
            if 'no transaction' not in str(exc).lower():
                raise
    else:
        conn.rollback()


def process_in_transaction(callback):
    """Execute ``callback(conn)`` inside a serialised transaction."""
    with immediate_transaction() as conn:
        return callback(conn)
