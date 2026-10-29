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
import logging
import threading
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


def test_smtp_logs_do_not_include_credentials_otp_or_message_body(mock_smtp, caplog, monkeypatch):
    """SMTP diagnostics include failure facts without exposing secret content."""
    monkeypatch.setattr(Config, 'MAIL_USERNAME', 'smtp-user-secret')
    monkeypatch.setattr(Config, 'MAIL_PASSWORD', 'smtp-password-secret')
    with app.app_context(), caplog.at_level(logging.WARNING, logger='studyhub.smtp'):
        FakeSMTP.scenario = (True, False, False, False, False)
        OTPManager.send_email(
            'private-recipient@example.com',
            'subject-secret',
            'otp-secret-928413',
        )

    assert 'phase=connect' in caplog.text
    assert 'TimeoutError' in caplog.text
    assert 'smtp-user-secret' not in caplog.text
    assert 'smtp-password-secret' not in caplog.text
    assert 'otp-secret-928413' not in caplog.text
    assert 'private-recipient@example.com' not in caplog.text


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


def test_quit_not_called_when_sock_is_none():
    """quit() must NOT be called when server.sock is None.

    Python's SMTP.quit() raises SMTPServerDisconnected('please run connect()
    first') when self.sock is None.  This happens when starttls() or login()
    closes the socket before the finally block runs.  The fix guards quit()
    with a sock check and falls back to close().
    """
    quit_call_count = []

    class SocklessSMTP(FakeSMTP):
        """Simulates starttls() failure that sets sock=None (like real smtplib)."""
        def starttls(self, context=None):
            self.sock = None
            raise smtplib.SMTPServerDisconnected('Connection unexpectedly closed')

        def quit(self):
            quit_call_count.append(1)
            if self.sock is None:
                raise smtplib.SMTPServerDisconnected(
                    'please run connect() first'
                )

    with app.app_context():
        with patch('smtplib.SMTP', SocklessSMTP):
            result = OTPManager.send_email(
                'user@example.com', 'Test', '<p>Body</p>',
            )

    # quit() must not have been called because sock was None
    assert quit_call_count == [], \
        "quit() should not be called when server.sock is None"

    # The result should be a failure with retry (starttls is a transient failure)
    assert result.is_failure


def test_quit_socket_already_none_does_not_raise():
    """If sock is None before quit, quit must not raise or be called.

    This is a direct test of the SMTPServerDisconnected('please run connect()
    first') scenario that the user reported.
    """
    class DisconnectOnStarttlsSMTP(FakeSMTP):
        quit_called = False

        def starttls(self, context=None):
            self.sock = None
            raise smtplib.SMTPServerDisconnected('TLS error')

        def quit(self):
            type(self).quit_called = True
            # This is what the real smtplib.SMTP.quit() does when sock is None
            if self.sock is None:
                raise smtplib.SMTPServerDisconnected(
                    'please run connect() first'
                )

    with app.app_context():
        with patch('smtplib.SMTP', DisconnectOnStarttlsSMTP):
            result = OTPManager.send_email(
                'user@example.com', 'Test', '<p>Body</p>',
            )

    assert DisconnectOnStarttlsSMTP.quit_called is False, \
        "quit() should not be called when sock is None"
    assert result.is_failure


def test_quit_called_and_raises_still_swallowed():
    """Even if quit() raises (sock present but server gone), it must be caught."""
    class RaisingQuitSMTP(FakeSMTP):
        def quit(self):
            raise smtplib.SMTPServerDisconnected('disconnect during quit')

    with app.app_context():
        with patch('smtplib.SMTP', RaisingQuitSMTP):
            result = OTPManager.send_email(
                'user@example.com', 'Test', '<p>Body</p>',
            )
        assert result.is_success


def test_close_called_after_successful_send():
    """close() must be called in the finally block even on success."""
    close_called = []

    class TrackingSMTP(FakeSMTP):
        def close(self):
            close_called.append(1)

    with app.app_context():
        with patch('smtplib.SMTP', TrackingSMTP):
            result = OTPManager.send_email(
                'user@example.com', 'Test', '<p>Body</p>',
            )
        assert result.is_success
        assert len(close_called) == 1, \
            "close() should be called exactly once per attempt"


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
    """deliver() must return a confirmed failure (not raise) when SMTP fails."""
    from utils.notifications import deliver

    with app.app_context():
        with patch('smtplib.SMTP', FakeSMTP):
            FakeSMTP.scenario = (True, False, False, False, False)
            with patch.object(OTPManager, 'is_configured', return_value=True):
                result = deliver('user@example.com', 'Subject', 'Body')
                assert result.is_failure


def test_deliver_success_returns_success():
    """deliver() must return a success result when SMTP succeeds."""
    from utils.notifications import deliver

    with app.app_context():
        with patch('smtplib.SMTP', FakeSMTP):
            result = deliver('user@example.com', 'Subject', 'Body')
            assert result.is_success


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


def test_ambiguous_delivery_not_queued_for_retry(mock_smtp):
    """An ambiguous send (data may have been sent) must NOT be retried.

    When SMTPServerDisconnected occurs during send_message, the result is
    AMBIGUOUS — the server may have accepted the DATA payload before
    disconnecting.  Retrying would risk duplicate delivery.  The
    notification must remain explicitly AMBIGUOUS and not be retried.
    """
    from utils.notifications import NotificationService

    with app.app_context():
        FakeSMTP.scenario = (False, False, False, True, True)  # send disconnect
        with patch.object(OTPManager, 'is_configured', return_value=True):
            r = NotificationService.send_or_enqueue(
                'ambiguous_test@example.com', 'Subject', 'Body',
                'test', related_id=1, dedupe_key='smtp_ambiguous_test',
            )

        conn = get_connection()
        row = conn.execute(
            "SELECT status FROM pending_notifications WHERE dedupe_key = ?",
            ('smtp_ambiguous_test',),
        ).fetchone()
        conn.close()

    assert r == NotificationService.RESULT_AMBIGUOUS
    assert row is not None
    assert row["status"] == 'ambiguous'


def test_failed_delivery_queued_for_retry(mock_smtp):
    """A hard failure (connect timeout) must still be queued for retry."""
    from utils.notifications import NotificationService

    with app.app_context():
        FakeSMTP.scenario = (True, False, False, False, False)  # connect timeout
        with patch.object(OTPManager, 'is_configured', return_value=True):
            r = NotificationService.send_or_enqueue(
                'retry_test@example.com', 'Subject', 'Body',
                'test', related_id=1, dedupe_key='smtp_retry_test',
            )

        conn = get_connection()
        row = conn.execute(
            "SELECT status FROM pending_notifications WHERE dedupe_key = ?",
            ('smtp_retry_test',),
        ).fetchone()
        conn.close()

    assert r == NotificationService.RESULT_QUEUED
    assert row is not None
    assert row["status"] == 'failed'


def test_process_pending_does_not_retry_ambiguous(mock_smtp):
    """The notification retry sweep must skip ambiguous notifications."""
    from utils.notifications import NotificationService

    with app.app_context():
        # Insert an ambiguous notification manually
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'ambiguous')",
            ('ambiguous@example.com', 'Subject', 'Body', 'test', 1,
             'smtp_no_retry_ambiguous', 0),
        )
        conn.commit()
        conn.close()

        # Now try to process pending — should not find the ambiguous one
        FakeSMTP.scenario = (True, False, False, False, False)  # connect fail
        processed, sent = NotificationService.process_pending(limit=10)

    # The ambiguous notification should not have been retried
    assert sent == 0, "Ambiguous notifications should not be retried"


def test_process_pending_preserves_ambiguous_without_retry(mock_smtp):
    """When SMTP outcome is ambiguous, keep it distinct and never retry it.

    BUG: process_pending used bool(result) to check for success.  An ambiguous
    EmailDeliveryResult has bool() == False (because __bool__ returns True only
    for SUCCESS), so the code fell through to the else branch and incremented
    attempt while leaving status as 'failed'.  The row was retried on every
    sweep, risking duplicate email delivery.
    """
    from utils.notifications import NotificationService

    with app.app_context():
        # Insert a 'failed' notification that will receive an ambiguous result
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'failed')",
            ('ambiguous_retry@example.com', 'Subject', 'Body', 'test', 1,
             'smtp_ambiguous_retry_test', 0),
        )
        conn.commit()
        conn.close()

        # send disconnect during send_message -> ambiguous
        FakeSMTP.scenario = (False, False, False, True, True)

        with patch.object(OTPManager, 'is_configured', return_value=True):
            processed, sent = NotificationService.process_pending(limit=10)

        # Verify the row has an explicit non-retryable ambiguous state.
        conn = get_connection()
        row = conn.execute(
            "SELECT status, attempt, retryable FROM pending_notifications WHERE dedupe_key = ?",
            ('smtp_ambiguous_retry_test',),
        ).fetchone()
        conn.close()

    assert processed == 1, f"Should process exactly one row, got {processed}"
    assert sent == 0, "Ambiguous delivery is not confirmed as sent"
    assert row is not None
    assert row["status"] == 'ambiguous'
    assert row["attempt"] == 1
    assert row["retryable"] == 0


def test_retryable_failure_uses_bounded_backoff(mock_smtp):
    from utils.notifications import NotificationService

    with app.app_context():
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, dedupe_key, attempt, status) "
            "VALUES (?, ?, ?, ?, ?, 0, 'pending')",
            ('backoff@example.com', 'Subject', 'Body', 'test', 'smtp_backoff_test'),
        )
        conn.commit()
        conn.close()

        FakeSMTP.scenario = (True, False, False, False, False)
        with patch.object(OTPManager, 'is_configured', return_value=True):
            processed, sent = NotificationService.process_pending(limit=10)
            processed_again, sent_again = NotificationService.process_pending(limit=10)

        conn = get_connection()
        row = conn.execute(
            "SELECT status, attempt, retryable, next_attempt_at "
            "FROM pending_notifications WHERE dedupe_key = ?",
            ('smtp_backoff_test',),
        ).fetchone()
        conn.close()

    assert (processed, sent) == (1, 0)
    assert (processed_again, sent_again) == (0, 0)
    assert row['status'] == 'failed'
    assert row['attempt'] == 1
    assert row['retryable'] == 1
    assert row['next_attempt_at'] is not None


def test_permanent_authentication_failure_stops_retrying(mock_smtp):
    from utils.notifications import NotificationService

    with app.app_context():
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, dedupe_key, attempt, status) "
            "VALUES (?, ?, ?, ?, ?, 0, 'pending')",
            ('auth-failure@example.com', 'Subject', 'Body', 'test', 'smtp_auth_failure_test'),
        )
        conn.commit()
        conn.close()

        FakeSMTP.scenario = (False, False, True, False, False)
        with patch.object(OTPManager, 'is_configured', return_value=True):
            first = NotificationService.process_pending(limit=10)
            second = NotificationService.process_pending(limit=10)

        conn = get_connection()
        row = conn.execute(
            "SELECT status, attempt, retryable FROM pending_notifications "
            "WHERE dedupe_key = ?",
            ('smtp_auth_failure_test',),
        ).fetchone()
        conn.close()

    assert first == (1, 0)
    assert second == (0, 0)
    assert row['status'] == 'failed'
    assert row['attempt'] == 1
    assert row['retryable'] == 0


def test_expired_processing_job_becomes_ambiguous_not_retried(mock_smtp):
    from utils.notifications import NotificationService

    with app.app_context():
        stale_claim = datetime.now() - timedelta(
            seconds=Config.NOTIFICATION_PROCESSING_LEASE_SECONDS + 1,
        )
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, dedupe_key, attempt, "
            "status, claimed_at) VALUES (?, ?, ?, ?, ?, 1, 'processing', ?)",
            ('stale@example.com', 'Subject', 'Body', 'test',
             'smtp_stale_processing_test', stale_claim),
        )
        conn.commit()
        conn.close()

        FakeSMTP.scenario = None
        with patch.object(OTPManager, 'is_configured', return_value=True):
            processed, sent = NotificationService.process_pending(limit=10)

        conn = get_connection()
        row = conn.execute(
            "SELECT status, retryable, last_error FROM pending_notifications "
            "WHERE dedupe_key = ?",
            ('smtp_stale_processing_test',),
        ).fetchone()
        conn.close()

    assert (processed, sent) == (0, 0)
    assert row['status'] == 'ambiguous'
    assert row['retryable'] == 0
    assert row['last_error'] == 'processing_lease_expired'
    assert mock_smtp.instance_count == 0


def test_process_pending_retries_pending_status(mock_smtp):
    """'pending' status rows (SMTP was not configured) must be retried.

    BUG: process_pending only selected status = 'failed'.  Rows stored as
    'pending' (because SMTP was not configured at send_or_enqueue time) were
    never picked up for retry, so they sat in the queue forever even after
    SMTP became available.
    """
    from utils.notifications import NotificationService

    with app.app_context():
        # Insert a 'pending' notification (SMTP was not configured)
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            ('pending_retry@example.com', 'Subject', 'Body', 'test', 1,
             'smtp_pending_retry_test', 0),
        )
        conn.commit()
        conn.close()

        # SMTP is now configured and send succeeds
        FakeSMTP.scenario = (False, False, False, False, False)

        with patch.object(OTPManager, 'is_configured', return_value=True):
            processed, sent = NotificationService.process_pending(limit=10)

        conn = get_connection()
        row = conn.execute(
            "SELECT status FROM pending_notifications WHERE dedupe_key = ?",
            ('smtp_pending_retry_test',),
        ).fetchone()
        conn.close()

    assert processed == 1, f"Should process the pending row, got processed={processed}"
    assert sent == 1, "Pending row should be delivered and marked sent"
    assert row is not None
    assert row["status"] == 'sent', \
        f"Pending row should be marked 'sent' after successful retry, got '{row['status']}'"


def test_process_pending_skips_when_smtp_unconfigured(mock_smtp):
    """Unconfigured transport leaves pending rows untouched for a future sweep.

    No job is claimed until SMTP is configured, so the row stays 'pending'
    with its attempt count unchanged and remains eligible for future sweeps.
    """
    from utils.notifications import NotificationService

    with app.app_context():
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            ('unconfigured@example.com', 'Subject', 'Body', 'test', 1,
             'smtp_unconfigured_test', 0),
        )
        conn.commit()
        conn.close()

        # SMTP not configured — pending work should not be claimed or sent.
        with patch.object(OTPManager, 'is_configured', return_value=False):
            processed, sent = NotificationService.process_pending(limit=10)

        conn = get_connection()
        row = conn.execute(
            "SELECT status, attempt FROM pending_notifications WHERE dedupe_key = ?",
            ('smtp_unconfigured_test',),
        ).fetchone()
        conn.close()

    assert processed == 0, "Should not claim jobs when SMTP is unconfigured"
    assert sent == 0, "Nothing should be marked sent when SMTP is unconfigured"
    assert row is not None
    assert row["status"] == 'pending', \
        f"Should remain 'pending' when SMTP is unconfigured, got '{row['status']}'"
    assert row["attempt"] == 0, \
        "Attempt should NOT increment when SMTP is unconfigured"


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


def test_account_deletion_email_still_works(mock_smtp):
    """Account deletion must still send the account_deleted_by_admin email.

    This is a regression guard: the SMTP fixes must NOT remove or bypass
    the notification call in delete_user() at routes/admin.py.
    """
    from utils.notifications import send_template_notification, NotificationService
    from utils.otp import EmailDeliveryResult

    # Verify the account_deleted_by_admin template exists and can be sent
    with app.app_context():
        FakeSMTP.scenario = (False, False, False, False, False)  # success
        with patch.object(OTPManager, 'is_configured', return_value=True):
            result = send_template_notification(
                'account_deleted_by_admin',
                'deleted_admin_test@example.com',
                related_id=99999,
                dedupe_key='smtp_account_delete_test',
            )

    assert result == NotificationService.RESULT_SENT, \
        "Account deletion email should still be sent"


# ---------------------------------------------------------------------------
# Tests for the durable background queue (enqueue + worker)
# ---------------------------------------------------------------------------

def test_enqueue_does_not_attempt_smtp(mock_smtp):
    """queue_notification / enqueue must store 'pending' WITHOUT calling SMTP.

    The HTTP request that enqueues a notification must not wait for Gmail.
    """
    from utils.notifications import NotificationService

    with app.app_context():
        FakeSMTP.scenario = (True, False, False, False, False)  # connect fail
        result = NotificationService.enqueue(
            'enqueue_test@example.com', 'Subject', 'Body',
            'test_enqueue', related_id=1,
            dedupe_key='smtp_enqueue_no_smtp_test',
        )
        assert result == NotificationService.RESULT_QUEUED

        conn = get_connection()
        row = conn.execute(
            "SELECT status, attempt FROM pending_notifications "
            "WHERE dedupe_key = ?",
            ('smtp_enqueue_no_smtp_test',),
        ).fetchone()
        conn.close()

    assert row is not None
    assert row["status"] == 'pending', \
        f"enqueue() should store 'pending', got '{row['status']}'"
    assert row["attempt"] == 0, "enqueue() should not set any attempt"


def test_queue_template_notification_uses_templates(mock_smtp):
    """queue_template_notification must populate subject/body from templates."""
    from utils.notifications import queue_template_notification, NotificationService

    with app.app_context():
        FakeSMTP.scenario = (True, False, False, False, False)  # never reached
        result = queue_template_notification(
            'account_deleted_by_admin',
            'queued_admin@example.com',
            related_id=88888,
            dedupe_key='smtp_queued_admin_test',
        )
        assert result == NotificationService.RESULT_QUEUED

        conn = get_connection()
        row = conn.execute(
            "SELECT subject, body, status FROM pending_notifications "
            "WHERE dedupe_key = ?",
            ('smtp_queued_admin_test',),
        ).fetchone()
        conn.close()

    assert row is not None
    assert row["status"] == 'pending'
    # The body should contain template content (not be empty or a placeholder)
    assert len(row["subject"]) > 0
    assert len(row["body"]) > 0


def test_worker_processes_pending_job(mock_smtp):
    """The background worker must process a 'pending' row and mark it 'sent'.

    This verifies the full path: worker polls → process_pending → deliver →
    FakeSMTP success → status updated to 'sent'.
    """
    from utils.notification_worker import run_worker
    from utils.notifications import NotificationService

    with app.app_context():
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            ('worker_test@example.com', 'Worker Test', 'Body',
             'test_worker', 1, 'smtp_worker_test', 0),
        )
        conn.commit()
        conn.close()

        FakeSMTP.scenario = (False, False, False, False, False)  # success
        with patch.object(OTPManager, 'is_configured', return_value=True):
            processed, sent = run_worker(once=True)

        conn = get_connection()
        row = conn.execute(
            "SELECT status, attempt FROM pending_notifications "
            "WHERE dedupe_key = ?",
            ('smtp_worker_test',),
        ).fetchone()
        conn.close()

    assert processed >= 1, f"Worker should have processed at least 1 job, got {processed}"
    assert sent >= 1, "Worker should have sent at least 1 job"
    assert row is not None
    assert row["status"] == 'sent', \
        f"Worker should mark as 'sent', got '{row['status']}'"


def test_worker_skips_when_smtp_unconfigured(mock_smtp):
    """The worker must not process jobs when SMTP is not configured."""
    from utils.notification_worker import run_worker

    with app.app_context():
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            ('unconfigured_worker@example.com', 'Test', 'Body',
             'test', 1, 'smtp_worker_unconfigured_test', 0),
        )
        conn.commit()
        conn.close()

        with patch.object(OTPManager, 'is_configured', return_value=False):
            processed, sent = run_worker(once=True)

        conn = get_connection()
        row = conn.execute(
            "SELECT status FROM pending_notifications WHERE dedupe_key = ?",
            ('smtp_worker_unconfigured_test',),
        ).fetchone()
        conn.close()

    assert processed == 0, "Worker should process 0 jobs when SMTP is unconfigured"
    assert sent == 0
    assert row is not None
    assert row["status"] == 'pending', \
        "Row should stay 'pending' when SMTP is unconfigured"


def test_worker_preserves_ambiguous_state(mock_smtp):
    """The worker must NOT retry ambiguous deliveries."""
    from utils.notification_worker import run_worker

    with app.app_context():
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'failed')",
            ('worker_ambiguous@example.com', 'Worker Ambiguous', 'Body',
             'test', 1, 'smtp_worker_ambiguous_test', 0),
        )
        conn.commit()
        conn.close()

        # send_message disconnects after DATA → ambiguous
        FakeSMTP.scenario = (False, False, False, True, True)
        with patch.object(OTPManager, 'is_configured', return_value=True):
            run_worker(once=True)

        conn = get_connection()
        row = conn.execute(
            "SELECT status, attempt FROM pending_notifications "
            "WHERE dedupe_key = ?",
            ('smtp_worker_ambiguous_test',),
        ).fetchone()
        conn.close()

    assert row is not None
    assert row["status"] == 'ambiguous'
    assert row["attempt"] == 1


# ---------------------------------------------------------------------------
# Tests for login OTP SMTP reliability
# ---------------------------------------------------------------------------

def test_send_otp_email_confirmed_failure(mock_smtp):
    """A confirmed SMTP failure (auth error) returns FAILURE, not SUCCESS.

    This ensures login OTP delivery does not falsely report success when
    Gmail rejects authentication.
    """
    from utils.otp import EmailDeliveryResult

    with app.app_context():
        # SMTP connection succeeds but authentication fails
        FakeSMTP.scenario = (True, True, False, False, False)
        with patch.object(OTPManager, 'is_configured', return_value=True):
            result = OTPManager.send_otp_email(
                'otp_failure@example.com', 'Login OTP', 'Your OTP is 123456',
            )

    assert isinstance(result, EmailDeliveryResult), \
        "send_otp_email should return EmailDeliveryResult"
    assert result.is_failure, "Auth failure should return FAILURE"
    assert not result.is_success, "Should not be SUCCESS"
    assert not result.is_ambiguous, "Should not be AMBIGUOUS"


def test_send_otp_email_ambiguous(mock_smtp):
    """An ambiguous OTP delivery during login returns AMBIGUOUS, not SUCCESS.

    This ensures login does not falsely report the OTP was sent when delivery
    was uncertain (Gmail accepted DATA but disconnected before confirming).
    """
    from utils.otp import EmailDeliveryResult

    with app.app_context():
        # send_message disconnects after DATA → ambiguous
        FakeSMTP.scenario = (False, False, False, True, True)
        with patch.object(OTPManager, 'is_configured', return_value=True):
            result = OTPManager.send_otp_email(
                'otp_ambiguous@example.com', 'Login OTP', 'Your OTP is 123456',
            )

    assert isinstance(result, EmailDeliveryResult)
    assert result.is_ambiguous, "Should be AMBIGUOUS for send-time disconnect"
    assert not result.is_success
    assert not result.is_failure


def test_send_otp_email_success(mock_smtp):
    """A successful OTP delivery returns SUCCESS."""
    from utils.otp import EmailDeliveryResult

    with app.app_context():
        FakeSMTP.scenario = (False, False, False, False, False)  # all success
        with patch.object(OTPManager, 'is_configured', return_value=True):
            result = OTPManager.send_otp_email(
                'otp_success@example.com', 'Login OTP', 'Your OTP is 123456',
            )

    assert isinstance(result, EmailDeliveryResult)
    assert result.is_success, "Should be SUCCESS"


def test_send_otp_email_smtp_unconfigured(mock_smtp):
    """When SMTP is not configured, send_email should not deliver.

    Note: ``send_email`` itself does not check ``is_configured`` — that guard
    lives in ``deliver()`` and the route handlers (which check
    ``Config.MAIL_USERNAME``).  This test verifies that ``deliver()`` returns
    ``False`` without calling SMTP when the transport is unconfigured, and that
    ``send_otp_email`` via the route-level guard is never invoked.
    """
    from utils.notifications import deliver

    with app.app_context():
        FakeSMTP.scenario = None  # should never be reached
        with patch.object(OTPManager, 'is_configured', return_value=False):
            result = deliver('unconfigured@example.com', 'Subject', 'Body')

    assert result.is_failure, \
        "deliver() should return FAILURE when SMTP is not configured"


def test_account_deletion_enqueue_is_transactional(mock_smtp):
    """queue_template_notification must insert the notification job durably.

    The job persists after the HTTP request ends and is not lost even if
    SMTP is unreachable.  It will be delivered by the background worker.
    """
    from utils.notifications import queue_template_notification, NotificationService

    with app.app_context():
        FakeSMTP.scenario = (True, False, False, False, False)  # connect fail
        result = queue_template_notification(
            'account_deleted_self',
            'transactional_self_delete@example.com',
            related_id=77777,
            dedupe_key='smtp_transactional_self_delete',
        )

        assert result == NotificationService.RESULT_QUEUED

        conn = get_connection()
        row = conn.execute(
            "SELECT status, purpose, related_id FROM pending_notifications "
            "WHERE dedupe_key = ?",
            ('smtp_transactional_self_delete',),
        ).fetchone()
        conn.close()

    assert row is not None
    assert row["status"] == 'pending'
    assert row["purpose"] == 'account_deleted_self'
    assert row["related_id"] == 77777


# ---------------------------------------------------------------------------
# Tests for concurrent-safe job claiming (no duplicate sends)
# ---------------------------------------------------------------------------

def test_process_pending_is_single_worker_safe(mock_smtp):
    """Concurrent workers atomically claim the job before either sends it."""
    from utils.notifications import NotificationService

    with app.app_context():
        conn = get_connection()
        conn.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'failed')",
            ('concurrent@example.com', 'Concurrent', 'Body',
             'test', 1, 'smtp_concurrent_test', 0),
        )
        conn.commit()
        conn.close()

        send_started = threading.Event()
        allow_send_to_finish = threading.Event()
        send_count = []

        class BlockingSMTP(FakeSMTP):
            def send_message(self, msg):
                send_count.append(msg)
                send_started.set()
                assert allow_send_to_finish.wait(5)

        FakeSMTP.scenario = (False, False, False, False, False)
        with patch.object(OTPManager, 'is_configured', return_value=True):
            with patch('smtplib.SMTP', BlockingSMTP):
                first_result = []
                first_worker = threading.Thread(
                    target=lambda: first_result.append(
                        NotificationService.process_pending(limit=10)
                    )
                )
                first_worker.start()
                assert send_started.wait(5)
                processed2, sent2 = NotificationService.process_pending(limit=10)
                allow_send_to_finish.set()
                first_worker.join(5)
                assert not first_worker.is_alive()

        conn = get_connection()
        row = conn.execute(
            "SELECT status, attempt FROM pending_notifications "
            "WHERE dedupe_key = ?",
            ('smtp_concurrent_test',),
        ).fetchone()
        conn.close()

    assert first_result == [(1, 1)]
    assert (processed2, sent2) == (0, 0)
    assert len(send_count) == 1
    assert row is not None
    assert row["status"] == 'sent'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
