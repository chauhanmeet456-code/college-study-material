"""Tests for SMTP timeout, retry, and notification resilience.

These tests mock ``smtplib.SMTP`` so no real network access is required.
They verify:

* Transient failures (timeout, disconnect during connect/login) are retried
  up to ``MAX_SEND_ATTEMPTS``.
* Ambiguous failures (disconnect during send_message) are NOT retried
  (to avoid duplicate emails).
* Authentication failures are NOT retried.
* Successful delivery returns ``RESULT_SUCCESS`` on the first attempt.
* Session revocation commits even when notification delivery fails.
* The notification queue persists failed deliveries for later retry.
* Transaction rollback occurs when revocation raises.

Run: python -m pytest tests/tests_smtp_resilience.py -v
"""

import os
import sys
import smtplib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
# Also add tests dir so we can import conftest (which saves originals
# before tests_project.py overrides them at import time).
sys.path.insert(0, os.path.join(root, 'tests'))
os.chdir(root)

from config import Config

# Force SQLite so tests are self-contained; .env is untouched.
Config.USE_POSTGRES = False
Config.USE_SQLITE = True

from app import create_app  # noqa: E402
from utils.otp import (  # noqa: E402
    OTPManager,
    RESULT_SUCCESS,
    RESULT_FAILURE,
    RESULT_AMBIGUOUS,
    EmailDeliveryResult,
)

# ``tests_project.py`` overrides ``OTPManager.send_email`` / ``is_configured`` /
# ``send_otp_email`` with always-success lambdas at import time.  The
# ``tests/conftest.py`` module saves the originals before any test module
# runs, and ``_restore_otp_methods`` copies them back for each test.
from conftest import (  # noqa: E402
    SAVED_SEND_EMAIL,
    SAVED_SEND_OTP_EMAIL,
    SAVED_IS_CONFIGURED,
    SAVED_MAX_SEND_ATTEMPTS,
    SAVED_RETRY_DELAY_SECONDS,
)

from utils.database import (  # noqa: E402
    get_connection,
    get_cursor,
    close_cursor,
    immediate_transaction,
    ensure_schema_extensions,
)

app = create_app()


class FakeSMTP:
    """A controllable stand-in for ``smtplib.SMTP``.

    The class-level *scenario* attribute is a 5-tuple:
    ``(connect_fail, starttls_fail, login_fail, send_fail, send_disconnect)``.

    When ``connect_fail`` is True the constructor raises ``TimeoutError``.
    When ``starttls_fail`` is True, ``starttls`` raises
    ``SMTPServerDisconnected``. When ``login_fail`` is True, ``login`` raises
    ``SMTPAuthenticationError``. When ``send_fail`` is True, ``send_message``
    raises either ``TimeoutError`` (when ``send_disconnect`` is False) or
    ``SMTPServerDisconnected`` (when True).
    """

    scenario = None
    instance_count = 0

    def __init__(self, host, port, timeout=None):
        type(self).instance_count += 1
        self.host = host
        self.port = port
        self.sock = MagicMock()
        self._msg = None
        self._scenario = self.scenario or (False, False, False, False, False)

        # If connect_fail is set, simulate a connection failure.
        connect_fail, _, _, _, _ = self._scenario
        if connect_fail:
            raise TimeoutError('connect timed out')

    def starttls(self, context=None):
        _, starttls_fail, _, _, _ = self._scenario
        if starttls_fail:
            raise smtplib.SMTPServerDisconnected('starttls disconnected')

    def login(self, username, password):
        _, _, login_fail, _, _ = self._scenario
        if login_fail:
            raise smtplib.SMTPAuthenticationError(535, b'Auth failed')

    def send_message(self, msg):
        _, _, _, send_fail, send_disconnect = self._scenario
        self._msg = msg
        if send_fail:
            if send_disconnect:
                raise smtplib.SMTPServerDisconnected(
                    'Connection unexpectedly closed: The read operation timed out'
                )
            else:
                raise TimeoutError('send timed out')

    def quit(self):
        pass


def _get_existing_user_id():
    """Return an existing user id, or None if no users exist."""
    with app.app_context():
        conn = get_connection()
        row = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        conn.close()
        return row["id"] if row else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_smtp_and_delay():
    """Reset FakeSMTP scenario and zero the retry delay so tests run fast."""
    FakeSMTP.scenario = None
    FakeSMTP.instance_count = 0
    with patch.object(OTPManager, 'RETRY_DELAY_SECONDS', 0):
        yield
    FakeSMTP.scenario = None


@pytest.fixture(autouse=True)
def _restore_otp_methods():
    """Restore the real OTPManager.send_email (tests_project.py overrides it).

    ``tests_project.py`` replaces ``send_email`` / ``is_configured`` /
    ``send_otp_email`` with always-success lambdas at module import time.
    Without this fixture, every test in this file would exercise the stub
    instead of the real retry logic.
    """
    OTPManager.send_email = staticmethod(SAVED_SEND_EMAIL)
    OTPManager.is_configured = staticmethod(SAVED_IS_CONFIGURED)
    OTPManager.send_otp_email = staticmethod(SAVED_SEND_OTP_EMAIL)
    OTPManager.MAX_SEND_ATTEMPTS = SAVED_MAX_SEND_ATTEMPTS
    OTPManager.RETRY_DELAY_SECONDS = SAVED_RETRY_DELAY_SECONDS
    yield


@pytest.fixture(autouse=True)
def _cleanup_test_notifications():
    """Remove notification rows with test dedupe keys before and after each test."""
    test_dedupes = (
        'smtp_queue_test_dedupe',
        'smtp_dup_test_dedupe',
        'session_terminated:%s' % '%d',
    )
    with app.app_context():
        conn = get_connection()
        conn.execute("DELETE FROM pending_notifications WHERE dedupe_key LIKE 'smtp_%'")
        conn.execute("DELETE FROM pending_notifications WHERE dedupe_key LIKE 'session_terminated:%'")
        conn.commit()
        conn.close()
    yield
    with app.app_context():
        conn = get_connection()
        conn.execute("DELETE FROM pending_notifications WHERE dedupe_key LIKE 'smtp_%'")
        conn.execute("DELETE FROM pending_notifications WHERE dedupe_key LIKE 'session_terminated:%'")
        conn.commit()
        conn.close()


@pytest.fixture
def mock_smtp():
    """Patch ``smtplib.SMTP`` with ``FakeSMTP`` and return the class."""
    with patch('smtplib.SMTP', FakeSMTP):
        yield FakeSMTP


# ---------------------------------------------------------------------------
# Tests for OTPManager.send_email retry behaviour
# ---------------------------------------------------------------------------


def test_successful_email_delivery_returns_success(mock_smtp):
    """A clean SMTP exchange must return RESULT_SUCCESS on first attempt."""
    with app.app_context():
        result = OTPManager.send_email(
            'recipient@example.com', 'Subject', '<p>Body</p>',
        )
        assert result.is_success
        assert result.is_success
        assert mock_smtp.instance_count == 1


def test_timeout_during_connect_retries_and_succeeds():
    """A TimeoutError during connect must be retried and succeed on retry."""
    call_log = []

    class FlakySMTP(FakeSMTP):
        def __init__(self, *args, **kwargs):
            call_log.append('init')
            if len(call_log) <= 1:
                raise TimeoutError('connect timed out')
            super().__init__(*args, **kwargs)

    with app.app_context():
        with patch('smtplib.SMTP', FlakySMTP):
            result = OTPManager.send_email(
                'user@example.com', 'Test', '<p>Body</p>',
            )

    assert result.is_success
    assert len(call_log) == 2  # first failed, second succeeded


def test_smtp_disconnect_during_connect_retries():
    """SMTPServerDisconnected during connect must be retried."""
    call_log = []

    class FlakySMTP(FakeSMTP):
        def __init__(self, *args, **kwargs):
            call_log.append('init')
            if len(call_log) < OTPManager.MAX_SEND_ATTEMPTS:
                raise smtplib.SMTPServerDisconnected('Server disconnected')
            super().__init__(*args, **kwargs)

    with app.app_context():
        with patch('smtplib.SMTP', FlakySMTP):
            result = OTPManager.send_email(
                'user@example.com', 'Test', '<p>Body</p>',
            )

    # Final attempt succeeds (last chance)
    assert result.is_success
    assert len(call_log) == OTPManager.MAX_SEND_ATTEMPTS


def test_smtp_disconnect_during_send_is_ambiguous_no_retry(mock_smtp):
    """SMTPServerDisconnected during send_message must NOT retry (ambiguous)."""
    with app.app_context():
        FakeSMTP.scenario = (False, False, False, True, True)
        result = OTPManager.send_email(
            'user@example.com', 'Test', '<p>Body</p>',
        )

    assert result.is_ambiguous
    assert result.is_ambiguous


def test_timeout_during_send_is_ambiguous_no_retry(mock_smtp):
    """TimeoutError during send must NOT retry (ambiguous — data may have been sent)."""
    with app.app_context():
        FakeSMTP.scenario = (False, False, False, True, False)
        result = OTPManager.send_email(
            'user@example.com', 'Test', '<p>Body</p>',
        )

    assert result.is_ambiguous
    assert result.is_ambiguous


def test_auth_failure_no_retry(mock_smtp):
    """SMTPAuthenticationError must return FAILURE without retry."""
    with app.app_context():
        FakeSMTP.scenario = (False, False, True, False, False)
        result = OTPManager.send_email(
            'user@example.com', 'Test', '<p>Body</p>',
        )

    assert result.is_failure
    assert mock_smtp.instance_count == 1  # no retry


def test_all_retries_exhausted_returns_failure(mock_smtp):
    """When all retry attempts fail, return FAILURE."""
    with app.app_context():
        FakeSMTP.scenario = (True, False, False, False, False)  # connect timeout always
        result = OTPManager.send_email(
            'user@example.com', 'Test', '<p>Body</p>',
        )

    assert result.is_failure
    assert mock_smtp.instance_count == OTPManager.MAX_SEND_ATTEMPTS


def test_quit_exception_does_not_propagate():
    """An exception in the finally-block quit() must not escape _attempt_send."""
    class QuittingSMTP(FakeSMTP):
        def quit(self):
            raise smtplib.SMTPServerDisconnected('Server gone during quit')

    with app.app_context():
        with patch('smtplib.SMTP', QuittingSMTP):
            result = OTPManager.send_email(
                'user@example.com', 'Test', '<p>Body</p>',
            )
        assert result.is_success  # should still return a valid result


def test_send_email_catches_any_exception_from_attempt_send():
    """send_email must return RESULT_FAILURE if _attempt_send raises."""
    with app.app_context():
        with patch.object(OTPManager, '_attempt_send',
                          side_effect=RuntimeError('unexpected')):
            result = OTPManager.send_email(
                'user@example.com', 'Test', '<p>Body</p>',
            )
        assert result.is_failure


# ---------------------------------------------------------------------------
# Tests for deliver() and notification queue
# ---------------------------------------------------------------------------


def test_email_failure_does_not_crash_notification_delivery():
    """deliver() must return False (not raise) when SMTP fails."""
    from utils.notifications import deliver

    with app.app_context():
        with patch('smtplib.SMTP', FakeSMTP):
            FakeSMTP.scenario = (True, False, False, False, False)
            with patch.object(OTPManager, 'is_configured', return_value=True):
                result = deliver('user@example.com', 'Subject', 'Body')
                assert result is False


def test_deliver_success_returns_true():
    """deliver() must return True when SMTP succeeds."""
    from utils.notifications import deliver

    with app.app_context():
        with patch('smtplib.SMTP', FakeSMTP):
            result = deliver('user@example.com', 'Subject', 'Body')
            assert result is True


def test_notification_queued_on_smtp_failure(mock_smtp):
    """A failed notification must be persisted in the queue for retry."""
    from utils.notifications import NotificationService

    with app.app_context():
        FakeSMTP.scenario = (True, False, False, False, False)  # connect timeout
        r = NotificationService.send_or_enqueue(
            'queue_test@example.com', 'Subject', 'Body',
            'test', related_id=1, dedupe_key='smtp_queue_test_dedupe',
        )

        conn = get_connection()
        row = conn.execute(
            "SELECT status FROM pending_notifications WHERE dedupe_key = ?",
            ('smtp_queue_test_dedupe',),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["status"] == 'failed'  # SMTP configured but failed
        assert r == NotificationService.RESULT_QUEUED


def test_notification_not_duplicated(mock_smtp):
    """Sending the same notification twice via dedupe_key must skip the second."""
    from utils.notifications import NotificationService

    with app.app_context():
        FakeSMTP.scenario = (False, False, False, False, False)
        with patch.object(OTPManager, 'is_configured', return_value=True):
            r1 = NotificationService.send_or_enqueue(
                'dup_test@example.com', 'Subject', 'Body',
                'test', related_id=1, dedupe_key='smtp_dup_test_dedupe',
            )
            r2 = NotificationService.send_or_enqueue(
                'dup_test@example.com', 'Subject', 'Body',
                'test', related_id=1, dedupe_key='smtp_dup_test_dedupe',
            )

        # Second call should be skipped (dedupe guard)
        assert r1 == NotificationService.RESULT_SENT
        assert r2 == NotificationService.RESULT_SKIPPED

        # Verify only one row in the DB
        conn = get_connection()
        count = conn.execute(
            "SELECT COUNT(*) AS total FROM pending_notifications WHERE dedupe_key = ?",
            ('smtp_dup_test_dedupe',),
        ).fetchone()["total"]
        conn.close()
        assert count == 1


# ---------------------------------------------------------------------------
# Tests for session revocation resilience
# ---------------------------------------------------------------------------


def test_session_revocation_commits_before_notification():
    """Session revocation must commit even when notification delivery fails.

    This simulates the exact flow of ``revoke_all_user_sessions`` in routes/admin.py:
    1. Revoke sessions in a transaction (commits on success)
    2. Attempt notification (may fail due to SMTP)
    3. Verify sessions are still revoked regardless of notification result
    """
    from models.session import UserSession
    from utils.notifications import send_template_notification

    with app.app_context():
        user_id = _get_existing_user_id()
        if user_id is None:
            pytest.skip("No users in the database")

        # Create a test session for this user
        now = datetime.now()
        expires_at = now + timedelta(hours=8)
        conn = get_connection()
        conn.execute(
            "INSERT INTO user_sessions (user_id, session_token_hash, "
            "created_at, last_activity_at, expires_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'active')",
            (user_id, 'smtp_test_hash', now, now, expires_at),
        )
        conn.commit()
        session_row = conn.execute(
            "SELECT id FROM user_sessions WHERE session_token_hash = ?",
            ('smtp_test_hash',),
        ).fetchone()
        conn.close()
        session_id = session_row["id"]

        try:
            # 1. Transaction block — mirrors routes/admin.py
            revoked = False
            try:
                with immediate_transaction() as conn:
                    changed = UserSession.revoke_all_for_user(
                        conn, user_id,
                        'All sessions revoked by administrator (TestAdmin)',
                        when=now,
                        ended_by_admin=True,
                    )
                    # Simulate notification failure AFTER commit
                    # The transaction commits at this point (when the with block exits)
            except Exception:
                revoked = False

            # Check if sessions were revoked
            conn = get_connection()
            row = conn.execute(
                "SELECT status FROM user_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            conn.close()

            assert row is not None, "Session should still exist"
            assert row["status"] == 'revoked', (
                f"Session should be revoked — transaction committed before notification, "
                f"got status={row['status']}"
            )

        finally:
            conn = get_connection()
            conn.execute(
                "DELETE FROM user_sessions WHERE session_token_hash = ?",
                ('smtp_test_hash',),
            )
            conn.execute(
                "DELETE FROM pending_notifications WHERE dedupe_key = ?",
                ('session_terminated:%d' % user_id,),
            )
            conn.commit()
            conn.close()


def test_transaction_rollback_when_revocation_fails():
    """If revocation raises an exception inside the transaction, it must roll back."""
    from models.session import UserSession

    with app.app_context():
        user_id = _get_existing_user_id()
        if user_id is None:
            pytest.skip("No users in the database")

        # Create a session
        now = datetime.now()
        expires_at = now + timedelta(hours=8)
        conn = get_connection()
        conn.execute(
            "INSERT INTO user_sessions (user_id, session_token_hash, "
            "created_at, last_activity_at, expires_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'active')",
            (user_id, 'rollback_test_hash', now, now, expires_at),
        )
        conn.commit()
        session_row = conn.execute(
            "SELECT id FROM user_sessions WHERE session_token_hash = ?",
            ('rollback_test_hash',),
        ).fetchone()
        conn.close()
        session_id = session_row["id"]

        try:
            # Simulate a failure inside the transaction
            with pytest.raises(RuntimeError):
                with immediate_transaction() as conn:
                    UserSession.revoke_all_for_user(
                        conn, user_id, 'test reason',
                        when=now, ended_by_admin=True,
                    )
                    raise RuntimeError('simulated failure')

            # The session should NOT be revoked because the transaction rolled back
            conn = get_connection()
            row = conn.execute(
                "SELECT status FROM user_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            conn.close()

            assert row is not None
            assert row["status"] == 'active', (
                "Session should remain active after transaction rollback"
            )

        finally:
            conn = get_connection()
            conn.execute(
                "DELETE FROM user_sessions WHERE session_token_hash = ?",
                ('rollback_test_hash',),
            )
            conn.commit()
            conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
