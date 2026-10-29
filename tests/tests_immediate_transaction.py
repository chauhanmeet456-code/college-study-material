"""Regression tests for transaction dialect and ownership handling.

Account deletion previously passed its transaction connection into notification
enqueueing, but the queue helper committed that caller-owned connection before
the context manager exited. The final ``COMMIT`` then raised::

    sqlite3.OperationalError: cannot commit - no transaction is active

The model and queue helpers now leave commit/rollback to the connection owner.
The dialect test also guards against sending SQLite-only transaction syntax to
a connection of another backend.

Run: python -m pytest tests/tests_immediate_transaction.py -q
"""

import os
import sys
import sqlite3
import uuid

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
    _commit_transaction,
)
from models.user import User

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
        cursor.execute(
            "SELECT COUNT(*) AS total FROM notification_queue_test WHERE dummy=?",
            ("x",),
        )
        assert cursor.fetchone()["total"] == 1
        close_cursor(cursor)
        conn.close()

        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute(
            "DELETE FROM notification_queue_test WHERE dummy=?",
            ("x",),
        )
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
        cursor.execute(
            "SELECT COUNT(*) AS total FROM notification_queue_test WHERE dummy=?",
            ("y",),
        )
        assert cursor.fetchone()["total"] == 0
        cursor.execute("DELETE FROM notification_queue_test WHERE dummy=?", ("y",))
        conn.commit()
        close_cursor(cursor)
        conn.close()


def test_sqlite_commit_is_not_attempted_without_owned_transaction():
    class ConnectionWithoutTransaction:
        in_transaction = False

        def execute(self, query):
            raise AssertionError("COMMIT must not run without an owned transaction")

    _commit_transaction(ConnectionWithoutTransaction(), False, "sqlite")


def test_sqlite_early_commit_is_reported_instead_of_suppressed():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.commit()
        with pytest.raises(RuntimeError, match="transaction ended"):
            _commit_transaction(conn, True, "sqlite")
    finally:
        conn.close()


def test_user_delete_respects_parent_transaction_ownership():
    email = "transaction-%s@example.com" % uuid.uuid4().hex
    user_id = User.create("Transaction Test", email, "TestPass123!")
    try:
        with pytest.raises(RuntimeError, match="rollback test"):
            with immediate_transaction() as conn:
                assert User.delete(user_id, conn=conn)
                assert conn.in_transaction
                raise RuntimeError("rollback test")
        assert User.get_by_id(user_id) is not None
    finally:
        User.permanent_delete(user_id)


def test_deletion_and_notification_enqueue_rollback_together():
    from utils.notifications import queue_template_notification

    email = "queue-transaction-%s@example.com" % uuid.uuid4().hex
    user_id = User.create("Queue Transaction Test", email, "TestPass123!")
    dedupe_key = "transaction-test:%s" % uuid.uuid4().hex
    try:
        with pytest.raises(RuntimeError, match="rollback test"):
            with immediate_transaction() as conn:
                assert User.permanent_delete(user_id, conn=conn)
                queue_template_notification(
                    "account_deleted_self",
                    email,
                    related_id=user_id,
                    dedupe_key=dedupe_key,
                    conn=conn,
                )
                raise RuntimeError("rollback test")

        assert User.get_by_id(user_id) is not None
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute(
            "SELECT COUNT(*) AS total FROM pending_notifications "
            "WHERE dedupe_key = ?",
            (dedupe_key,),
        )
        assert cursor.fetchone()["total"] == 0
        close_cursor(cursor)
        conn.close()
    finally:
        User.permanent_delete(user_id)


def test_deletion_and_notification_enqueue_commit_once():
    from utils.notifications import queue_template_notification

    email = "queue-commit-%s@example.com" % uuid.uuid4().hex
    user_id = User.create("Queue Commit Test", email, "TestPass123!")
    dedupe_key = "transaction-commit-test:%s" % uuid.uuid4().hex
    try:
        with immediate_transaction() as conn:
            assert User.permanent_delete(user_id, conn=conn)
            assert queue_template_notification(
                "account_deleted_self",
                email,
                related_id=user_id,
                dedupe_key=dedupe_key,
                conn=conn,
            ) == "queued"

        assert User.get_by_id(user_id) is None
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute(
            "SELECT status FROM pending_notifications WHERE dedupe_key = ?",
            (dedupe_key,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["status"] == "pending"
        close_cursor(cursor)
        cursor = get_cursor(conn)
        cursor.execute(
            "DELETE FROM pending_notifications WHERE dedupe_key = ?",
            (dedupe_key,),
        )
        conn.commit()
        close_cursor(cursor)
        conn.close()
    finally:
        User.permanent_delete(user_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
