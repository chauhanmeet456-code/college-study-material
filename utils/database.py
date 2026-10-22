import os
import time
from contextlib import contextmanager
from config import Config
import pymysql
import sqlite3

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
    
    def fetchone(self):
        return self._cursor.fetchone()
    
    def fetchall(self):
        return self._cursor.fetchall()
    
    def close(self):
        return self._cursor.close()
    
    def __getattr__(self, name):
        return getattr(self._cursor, name)

def get_connection():
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
    if not Config.USE_SQLITE:
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

    SQLite does not support row level locking, so a ``BEGIN IMMEDIATE``
    transaction is used to take the write lock up front.  This keeps the
    "check for an existing active session, then create a new one" sequence
    atomic, which is what makes simultaneous login attempts safe.  MySQL uses a
    plain transaction (the surrounding statements use ``FOR UPDATE`` where a
    row lock is required).
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
        _commit_transaction(conn)
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


def _commit_transaction(conn):
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

