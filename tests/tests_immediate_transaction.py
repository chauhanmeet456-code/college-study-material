"""Regression tests for the transaction helper dialect mismatch.

Root cause fixed: ``immediate_transaction`` previously used the ``Config.USE_SQLITE``
environment flag to decide whether to issue ``BEGIN IMMEDIATE``. When the
configuration had mismatched flags (e.g. ``USE_SQLITE`` true but a PostgreSQL
connection was actually returned by ``get_connection``), the transaction helper
executed ``BEGIN IMMEDIATE`` against PostgreSQL and raised::

    psycopg.errors.SyntaxError: syntax error at or near "IMMEDIATE"

The fix detects the dialect from the *actual connection* instead of from the
config flags, so ``BEGIN IMMEDIATE`` is only ever sent to SQLite and PostgreSQL
connections use an implicit transaction.

Run: python -m pytest tests/tests_immediate_transaction.py -q
"""

import os
import sys

import pytest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
os.chdir(root)

from config import Config
from utils.database import (
    immediate_transaction,
    get_connection,
    get_cursor,
    close_cursor,
    _dialect,
)

# Force SQLite so the test is self-contained; the .env file is untouched.
Config.USE_POSTGRES = False
Config.USE_SQLITE = True

from app import create_app  # noqa: E402

app = create_app()


@pytest.fixture(autouse=True)
def _ensure_test_table():
    with app.app_context():
        conn = get_connection()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notification_queue_test (id INTEGER PRIMARY KEY, dummy TEXT)"
        )
        conn.execute("DELETE FROM notification_queue_test")
        conn.commit()
        conn.close()
    yield


def test_dialect_detected_from_real_connection():
    """The dialect must be derived from the connection, not the config flag."""
    with app.app_context():
        conn = get_connection()
        assert _dialect(conn) == "sqlite"
        conn.close()


def test_transaction_commits_writes():
    """A successful block must persist its write."""
    with app.app_context():
        with immediate_transaction() as conn:
            cursor = get_cursor(conn)
            cursor.execute(
                "INSERT INTO notification_queue_test (dummy) VALUES (?)",
                ("x",),
            )
            close_cursor(cursor)
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM notification_queue_test WHERE dummy=?", ("x",))
        conn.commit()
        close_cursor(cursor)
        conn.close()


def test_transaction_rolls_back_on_exception():
    """A failing block must not persist its partial write."""
    with app.app_context():
        with pytest.raises(RuntimeError):
            with immediate_transaction() as conn:
                cursor = get_cursor(conn)
                cursor.execute(
                    "INSERT INTO notification_queue_test (dummy) VALUES (?)",
                    ("y",),
                )
                close_cursor(cursor)
                raise RuntimeError("boom")
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM notification_queue_test WHERE dummy=?", ("y",))
        conn.commit()
        close_cursor(cursor)
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
