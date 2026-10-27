"""Tests for datetime serialization with the Python 3.12 SQLite adapter fix.

Python 3.12 deprecated the default datetime adapter in sqlite3.  The project
registers an explicit adapter (in ``utils/database.py``) so that datetime
objects are stored as ISO-8601 strings (``YYYY-MM-DD HH:MM:SS.ffffff``).
These tests verify:

* Datetime objects are stored without triggering a DeprecationWarning.
* Stored values round-trip through the ``_parse_datetime`` / ``_normalize_datetime``
  parsers used by the session and auth layers.
* Session expiry (``expires_at``) and lastlogin timestamps survive a write/read
  cycle with correct values.

Run: python -m pytest tests/tests_datetime_serialization.py -v
"""

import os
import sys
import warnings
from datetime import datetime, timedelta

import pytest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
os.chdir(root)

from config import Config

# Force SQLite so the test is self-contained; the .env file is untouched.
Config.USE_POSTGRES = False
Config.USE_SQLITE = True

from app import create_app, _parse_datetime, _is_expired  # noqa: E402
from utils.database import (  # noqa: E402
    get_connection,
    get_cursor,
    close_cursor,
    immediate_transaction,
    ensure_schema_extensions,
)

app = create_app()


@pytest.fixture(autouse=True)
def _datetime_test_table():
    """Create a temporary table for round-trip tests, cleaned up each run."""
    with app.app_context():
        conn = get_connection()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS datetime_test (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   label TEXT,
                   ts DATETIME,
                   ts_with_default DATETIME
               )"""
        )
        conn.execute("DELETE FROM datetime_test")
        conn.commit()
        conn.close()
    yield
    with app.app_context():
        conn = get_connection()
        conn.execute("DELETE FROM datetime_test")
        conn.commit()
        conn.close()


def _get_existing_user_id():
    """Return an existing user id, or None if no users exist."""
    with app.app_context():
        conn = get_connection()
        row = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        conn.close()
        return row["id"] if row else None


def _get_existing_admin_id():
    """Return an existing admin id, or None if no admins exist."""
    with app.app_context():
        conn = get_connection()
        row = conn.execute("SELECT id FROM admins LIMIT 1").fetchone()
        conn.close()
        return row["id"] if row else None


def test_datetime_stored_as_iso_string_no_warning():
    """Storing a datetime must not raise a DeprecationWarning."""
    with app.app_context():
        now = datetime.now()
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            conn = get_connection()
            conn.execute(
                "INSERT INTO datetime_test (label, ts) VALUES (?, ?)",
                ("test", now),
            )
            conn.commit()
            conn.close()


def test_datetime_round_trip_parses_correctly():
    """A stored datetime must round-trip through _parse_datetime."""
    with app.app_context():
        original = datetime(2026, 10, 27, 10, 30, 0, 123456)
        conn = get_connection()
        conn.execute(
            "INSERT INTO datetime_test (label, ts) VALUES (?, ?)",
            ("round_trip", original),
        )
        conn.commit()
        row = conn.execute(
            "SELECT ts FROM datetime_test WHERE label = ?", ("round_trip",)
        ).fetchone()
        conn.close()

        stored = row["ts"]
        assert isinstance(stored, str), f"Expected string, got {type(stored)}"
        parsed = _parse_datetime(stored)
        assert parsed is not None, f"Could not parse stored value: {stored!r}"
        assert parsed.year == original.year
        assert parsed.month == original.month
        assert parsed.day == original.day
        assert parsed.hour == original.hour
        assert parsed.minute == original.minute
        assert parsed.second == original.second


def test_datetime_without_microseconds_round_trip():
    """A datetime with zero microseconds must still round-trip."""
    with app.app_context():
        original = datetime(2026, 10, 27, 10, 30, 0, 0)
        conn = get_connection()
        conn.execute(
            "INSERT INTO datetime_test (label, ts) VALUES (?, ?)",
            ("no_micros", original),
        )
        conn.commit()
        row = conn.execute(
            "SELECT ts FROM datetime_test WHERE label = ?", ("no_micros",)
        ).fetchone()
        conn.close()

        parsed = _parse_datetime(row["ts"])
        assert parsed is not None
        assert parsed == original


def test_session_expiry_datetime_storage():
    """expires_at must be stored and readable for session enforcement."""
    user_id = _get_existing_user_id()
    if user_id is None:
        pytest.skip("No users in the database")

    with app.app_context():
        conn = get_connection()
        expires = datetime.now() + timedelta(hours=8)
        now = datetime.now()
        try:
            conn.execute(
                """INSERT INTO user_sessions
                   (user_id, session_token_hash, created_at, last_activity_at,
                    expires_at, status)
                   VALUES (?, ?, ?, ?, ?, 'active')""",
                (user_id, "hash_test", now, now, expires),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM user_sessions WHERE session_token_hash = ?",
                ("hash_test",),
            ).fetchone()
            session_id = row["id"] if row else None
            conn.close()

            assert session_id is not None, "Session should have been inserted"

            conn = get_connection()
            row = conn.execute(
                "SELECT expires_at, created_at, last_activity_at FROM user_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            conn.close()

            parsed_expires = _parse_datetime(row["expires_at"])
            parsed_created = _parse_datetime(row["created_at"])
            parsed_activity = _parse_datetime(row["last_activity_at"])

            assert parsed_expires is not None, "expires_at should parse"
            assert parsed_created is not None, "created_at should parse"
            assert parsed_activity is not None, "last_activity_at should parse"

            # Session with 8h future expiry should NOT be expired
            assert _is_expired(row) is False, "Session with future expiry should not be expired"
        finally:
            conn = get_connection()
            conn.execute("DELETE FROM user_sessions WHERE session_token_hash = ?", ("hash_test",))
            conn.commit()
            conn.close()


def test_last_login_timestamp_storage():
    """User.update_last_login must store last_login correctly."""
    from models.user import User

    user_id = _get_existing_user_id()
    if user_id is None:
        pytest.skip("No users in the database")

    with app.app_context():
        User.update_last_login(user_id)

        conn = get_connection()
        row = conn.execute(
            "SELECT last_login FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        conn.close()

        assert row["last_login"] is not None, "last_login should be set"
        assert isinstance(row["last_login"], str), "last_login should be a string"

        parsed = _parse_datetime(row["last_login"])
        assert parsed is not None, "last_login should parse as datetime"
        now = datetime.now()
        diff = now - parsed
        assert diff.total_seconds() < 60, f"last_login should be recent, got {parsed}"


def test_admin_last_login_timestamp_storage():
    """Admin.update_last_login must store last_login correctly."""
    from models.admin import Admin

    admin_id = _get_existing_admin_id()
    if admin_id is None:
        pytest.skip("No admins in the database")

    with app.app_context():
        Admin.update_last_login(admin_id)

        conn = get_connection()
        row = conn.execute(
            "SELECT last_login FROM admins WHERE id = ?", (admin_id,)
        ).fetchone()
        conn.close()

        assert row["last_login"] is not None
        assert isinstance(row["last_login"], str)
        parsed = _parse_datetime(row["last_login"])
        assert parsed is not None
        now = datetime.now()
        diff = now - parsed
        assert diff.total_seconds() < 60


def test_login_history_timestamp_storage():
    """LoginHistory.create must store created_at without warnings."""
    from models.session import LoginHistory

    user_id = _get_existing_user_id()
    if user_id is None:
        pytest.skip("No users in the database")

    with app.app_context():
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            LoginHistory.create_standalone(
                user_id=user_id,
                event_type="login_success",
                result="success",
            )


def test_failed_login_attempt_timestamp_storage():
    """FailedLoginAttempt.record must store attempted_at without warnings."""
    from models.session import FailedLoginAttempt

    with app.app_context():
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            FailedLoginAttempt.record(
                email="test_warning@example.com",
                ip_address="127.0.0.1",
                user_id=None,
            )


def test_admin_audit_log_timestamp_storage():
    """AdminAuditLog.log must store created_at without warnings."""
    from models.session import AdminAuditLog

    admin_id = _get_existing_admin_id()
    if admin_id is None:
        pytest.skip("No admins in the database")

    with app.app_context():
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            AdminAuditLog.log(
                conn=None,
                admin_id=admin_id,
                action="test_datetime_warning",
                ip_address="127.0.0.1",
            )


def test_session_touch_timestamp_storage():
    """UserSession.touch must store last_activity_at without warnings."""
    from models.session import UserSession

    user_id = _get_existing_user_id()
    if user_id is None:
        pytest.skip("No users in the database")

    with app.app_context():
        conn = get_connection()
        expires = datetime.now() + timedelta(hours=8)
        now = datetime.now()
        try:
            conn.execute(
                """INSERT INTO user_sessions
                   (user_id, session_token_hash, created_at, last_activity_at,
                    expires_at, status)
                   VALUES (?, ?, ?, ?, ?, 'active')""",
                (user_id, "touch_test_hash", now, now, expires),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM user_sessions WHERE session_token_hash = ?",
                ("touch_test_hash",),
            ).fetchone()
            session_id = row["id"]
            conn.close()

            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                with immediate_transaction() as conn:
                    UserSession.touch(conn, session_id)

            conn = get_connection()
            row = conn.execute(
                "SELECT last_activity_at FROM user_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            conn.close()

            assert row["last_activity_at"] is not None
            parsed = _parse_datetime(row["last_activity_at"])
            assert parsed is not None
        finally:
            conn = get_connection()
            conn.execute("DELETE FROM user_sessions WHERE session_token_hash = ?", ("touch_test_hash",))
            conn.commit()
            conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
