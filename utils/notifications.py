"""Durable, idempotent notification delivery for account and session events.

This module intentionally reuses the existing SMTP infrastructure in
``utils.otp.OTPManager`` (the same transport that sends OTP emails) so that no
new credentials, environment variables, or SMTP configuration are introduced.

Design goals
------------
* **Never undo an action because mail failed.**  The caller decides what
  happened to the account/session.  This layer only ever *attempts* delivery and
  queues the message for a later retry when the provider is unreachable.
* **Never send the same notification twice.**  Every notification is given a
  ``dedupe_key``; if a row with the same key already exists (sent *or* pending)
  the send is skipped, which makes double-clicks and request retries safe.
* **Bounded retries.**  A queued message is retried at most ``MAX_ATTEMPTS``
  times by :func:`NotificationService.process_pending`, so a broken mail server
  can never produce an unbounded number of emails.
* **No secrets in logs.**  Only the masked recipient and the exception type are
  ever logged.
"""
import logging
from datetime import datetime, timedelta

from config import Config
from utils.database import (
    close_cursor,
    get_connection,
    get_cursor,
    immediate_transaction,
    _dialect,
)
from utils.otp import EmailDeliveryResult, OTPManager

log = logging.getLogger('studyhub.notifications')


def _mask_email(email):
    """One-way mask so logs can't leak a full address."""
    if not email:
        return '(unknown)'
    email = str(email).strip().lower()
    if '@' not in email:
        return email[:1] + '***'
    local, _, domain = email.partition('@')
    masked = (local[:1] + '***') if local else '***'
    return f'{masked}@{domain}'


# Templates are intentionally free of passwords, OTPs, session tokens and any
# internal identifiers.  They are plain, friendly messages.
TEMPLATES = {
    'account_deleted_self': {
        'subject': 'Your StudyHub Account Has Been Deleted',
        'body': (
            '<html><body>'
            '<p>Hello,</p>'
            '<p>Your StudyHub account has been successfully deleted following your request.</p>'
            '<p>If you did not request this action, please contact StudyHub support.</p>'
            '<p>Thank you,<br>StudyHub Support</p>'
            '</body></html>'
        ),
    },
    'account_deleted_by_admin': {
        'subject': 'Your StudyHub Account Was Deleted by an Administrator',
        'body': (
            '<html><body>'
            '<p>Hello,</p>'
            '<p>Your StudyHub account has been deleted by a StudyHub administrator.</p>'
            '<p>If you believe this action was made in error, please contact StudyHub support.</p>'
            '<p>Thank you,<br>StudyHub Support</p>'
            '</body></html>'
        ),
    },
    'session_terminated': {
        'subject': 'Your StudyHub Session Was Ended',
        'body': (
            '<html><body>'
            '<p>Hello,</p>'
            '<p>A StudyHub administrator ended one of the active sessions associated '
            'with your account. Your account has not been deleted.</p>'
            '<p>If you did not expect this action, please sign in again and contact '
            'StudyHub support if necessary.</p>'
            '<p>Thank you,<br>StudyHub Support</p>'
            '</body></html>'
        ),
    },
}


def deliver(recipient_email, subject, body):
    """Attempt a single SMTP send through the existing mail transport.

    Always returns a three-state ``EmailDeliveryResult``. SMTP details and
    message content are never written to logs.
    """
    if not recipient_email:
        log.warning('Notification skipped: no recipient address')
        return EmailDeliveryResult(EmailDeliveryResult.FAILURE)
    if not OTPManager.is_configured():
        log.info(
            'Mail transport not configured; skipping notification to %s',
            _mask_email(recipient_email),
        )
        return EmailDeliveryResult(EmailDeliveryResult.FAILURE)
    try:
        result = OTPManager.send_email(recipient_email, subject, body)
        if isinstance(result, EmailDeliveryResult):
            return result
        if result:
            return EmailDeliveryResult(EmailDeliveryResult.SUCCESS)
        return EmailDeliveryResult(
            EmailDeliveryResult.FAILURE,
            retryable=True,
        )
    except Exception as exc:
        log.warning(
            'SMTP phase=unknown outcome=ambiguous recipient=%s exception=%s retry=no',
            _mask_email(recipient_email),
            type(exc).__name__,
        )
        return EmailDeliveryResult(
            EmailDeliveryResult.AMBIGUOUS,
            exception_type=type(exc).__name__,
        )


class NotificationService:
    """Persistent, idempotent notification delivery with bounded retries."""

    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_AMBIGUOUS = 'ambiguous'

    MAX_ATTEMPTS = 5
    RETRY_BASE_SECONDS = 60
    RETRY_MAX_SECONDS = 3600

    # Results returned by send_or_enqueue.
    RESULT_SENT = 'sent'        # accepted by the provider right now
    RESULT_AMBIGUOUS = 'ambiguous'
    RESULT_FAILURE = 'failure'
    RESULT_QUEUED = 'queued'    # provider rejected / unavailable; queued for retry
    RESULT_SKIPPED = 'skipped'  # duplicate request; already sent or in-flight
    RESULT_NOT_CONFIGURED = 'not_configured'
    RESULT_QUEUE_FAILED = 'queue_failed'

    @staticmethod
    def _insert(conn, recipient_email, subject, body, purpose, related_id,
                dedupe_key, status, attempt, commit=True, retryable=True,
                next_attempt_at=None, last_error=None):
        cur = get_cursor(conn)
        now = datetime.now()
        try:
            cur.execute(
                "INSERT INTO pending_notifications "
                "(recipient_email, subject, body, purpose, related_id, "
                "dedupe_key, attempt, status, created_at, last_attempt_at, "
                "retryable, next_attempt_at, claimed_at, last_error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    recipient_email, subject, body, purpose, related_id,
                    dedupe_key, attempt, status, now,
                    now if attempt else None, int(retryable),
                    next_attempt_at, last_error,
                ),
            )
        finally:
            close_cursor(cur)
        if commit:
            conn.commit()

    @staticmethod
    def send_or_enqueue(recipient_email, subject, body, purpose,
                        related_id=None, dedupe_key=None):
        """Deliver a notification, persisting it for retry on failure.

        Returns one of the ``NotificationService.RESULT_*`` constants. A mail
        failure does not alter the caller's primary action. Persistence
        failures are reported separately from delivery results.
        """
        if not recipient_email:
            return NotificationService.RESULT_SKIPPED

        # Idempotency guard: never stack duplicates for the same logical event.
        if dedupe_key:
            existing = NotificationService._find_by_dedupe(dedupe_key)
            if existing:
                if existing.get('status') == NotificationService.STATUS_SENT:
                    return NotificationService.RESULT_SKIPPED
                if existing.get('status') == NotificationService.STATUS_AMBIGUOUS:
                    return NotificationService.RESULT_AMBIGUOUS
                if (
                    existing.get('status') == NotificationService.STATUS_FAILED
                    and not existing.get('retryable', 1)
                ):
                    return NotificationService.RESULT_FAILURE
                return NotificationService.RESULT_QUEUED

        delivery = deliver(recipient_email, subject, body)
        persisted = True
        try:
            conn = get_connection()
            try:
                if delivery.is_success:
                    NotificationService._insert(
                        conn, recipient_email, subject, body, purpose,
                        related_id, dedupe_key,
                        NotificationService.STATUS_SENT, 1,
                        retryable=False,
                    )
                elif delivery.is_ambiguous:
                    log.info(
                        'Email delivery ambiguous for %s (may have been '
                        'sent; will not retry to avoid duplicates)',
                        _mask_email(recipient_email),
                    )
                    NotificationService._insert(
                        conn, recipient_email, subject, body, purpose,
                        related_id, dedupe_key,
                        NotificationService.STATUS_AMBIGUOUS, 1,
                        retryable=False,
                        last_error='ambiguous_delivery',
                    )
                else:
                    configured = OTPManager.is_configured()
                    retryable = configured and delivery.retryable
                    NotificationService._insert(
                        conn, recipient_email, subject, body, purpose,
                        related_id, dedupe_key,
                        NotificationService.STATUS_FAILED if configured
                        else NotificationService.STATUS_PENDING,
                        1 if configured else 0,
                        retryable=retryable,
                        next_attempt_at=(
                            NotificationService._next_retry_at(1)
                            if retryable else None
                        ),
                        last_error=(
                            delivery.exception_type
                            if configured else 'smtp_not_configured'
                        ),
                    )
            finally:
                conn.close()
        except Exception as exc:  # queue unavailable -> delivery already attempted
            persisted = False
            log.warning(
                'Could not persist notification for %s (%s)',
                _mask_email(recipient_email), type(exc).__name__,
            )

        if delivery.is_success:
            return NotificationService.RESULT_SENT
        if delivery.is_ambiguous:
            return NotificationService.RESULT_AMBIGUOUS
        if not persisted:
            return NotificationService.RESULT_QUEUE_FAILED
        if not OTPManager.is_configured():
            return NotificationService.RESULT_NOT_CONFIGURED
        return (
            NotificationService.RESULT_QUEUED
            if delivery.retryable else NotificationService.RESULT_FAILURE
        )

    @staticmethod
    def enqueue(recipient_email, subject, body, purpose,
                related_id=None, dedupe_key=None, conn=None):
        """Durably insert a notification job without attempting synchronous delivery.

        Unlike :meth:`send_or_enqueue`, this method **never** calls SMTP — it
        inserts a row with ``status = 'pending'`` so a background worker can
        deliver the email later.  This keeps the HTTP request fast and ensures
        the notification survives even if the provider is temporarily unreachable.

        Args:
            conn: Optional existing connection for transactional use.  When
                provided, the caller owns the transaction (commit/rollback) and
                the row is inserted without committing.

        Returns one of the ``RESULT_*`` constants.
        """
        if not recipient_email:
            return NotificationService.RESULT_SKIPPED

        if conn is None:
            try:
                with immediate_transaction() as transaction_conn:
                    return NotificationService.enqueue(
                        recipient_email,
                        subject,
                        body,
                        purpose,
                        related_id=related_id,
                        dedupe_key=dedupe_key,
                        conn=transaction_conn,
                    )
            except Exception as exc:
                log.warning(
                    'Could not enqueue notification for %s (%s)',
                    _mask_email(recipient_email),
                    type(exc).__name__,
                )
                return NotificationService.RESULT_QUEUE_FAILED

        if dedupe_key:
            if _dialect(conn) == 'postgres':
                lock_cursor = get_cursor(conn)
                try:
                    lock_cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(?))",
                        (dedupe_key,),
                    )
                finally:
                    close_cursor(lock_cursor)
            existing = NotificationService._find_by_dedupe(dedupe_key, conn=conn)
            if existing:
                if existing.get('status') == NotificationService.STATUS_SENT:
                    return NotificationService.RESULT_SKIPPED
                if existing.get('status') == NotificationService.STATUS_AMBIGUOUS:
                    return NotificationService.RESULT_AMBIGUOUS
                if (
                    existing.get('status') == NotificationService.STATUS_FAILED
                    and not existing.get('retryable', 1)
                ):
                    return NotificationService.RESULT_FAILURE
                return NotificationService.RESULT_QUEUED

        NotificationService._insert(
            conn, recipient_email, subject, body, purpose,
            related_id, dedupe_key,
            NotificationService.STATUS_PENDING, 0,
            commit=False,
        )
        log.info(
            'Notification enqueued for %s (purpose=%s, dedupe=%s)',
            _mask_email(recipient_email), purpose, dedupe_key,
        )
        return NotificationService.RESULT_QUEUED

    @staticmethod
    def _find_by_dedupe(dedupe_key, conn=None):
        if not dedupe_key:
            return None
        own_conn = conn is None
        cur = None
        try:
            if own_conn:
                conn = get_connection()
            cur = get_cursor(conn)
            cur.execute(
                "SELECT id, status, retryable FROM pending_notifications "
                "WHERE dedupe_key = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (dedupe_key,),
            )
            return cur.fetchone()
        finally:
            close_cursor(cur)
            if own_conn and conn:
                conn.close()

    @staticmethod
    def _next_retry_at(attempt):
        base_delay = max(1, Config.NOTIFICATION_RETRY_BASE_SECONDS)
        maximum_delay = max(base_delay, Config.NOTIFICATION_RETRY_MAX_SECONDS)
        delay = min(
            base_delay * (2 ** max(0, attempt - 1)),
            maximum_delay,
        )
        return datetime.now() + timedelta(seconds=delay)

    @staticmethod
    def process_pending(limit=100, max_attempts=None):
        """Claim and deliver eligible jobs, returning ``(processed, sent)``.

        A job is committed as ``processing`` before SMTP starts. Stale claims
        become ``ambiguous`` because a worker may have died after submission;
        automatically retrying them could send duplicates.
        """
        max_attempts = max_attempts or NotificationService.MAX_ATTEMPTS
        processed = 0
        sent = 0
        lease_seconds = Config.NOTIFICATION_PROCESSING_LEASE_SECONDS
        stale_before = datetime.now() - timedelta(seconds=lease_seconds)
        with immediate_transaction() as conn:
            cur = get_cursor(conn)
            try:
                cur.execute(
                    "UPDATE pending_notifications SET status = ?, retryable = 0, "
                    "next_attempt_at = NULL, claimed_at = NULL, "
                    "last_error = 'processing_lease_expired' "
                    "WHERE status = ? AND (claimed_at IS NULL OR claimed_at < ?)",
                    (
                        NotificationService.STATUS_AMBIGUOUS,
                        NotificationService.STATUS_PROCESSING,
                        stale_before,
                    ),
                )
            finally:
                close_cursor(cur)

        if not OTPManager.is_configured():
            return processed, sent

        for _ in range(max(0, limit)):
            row = None
            with immediate_transaction() as conn:
                cur = get_cursor(conn)
                try:
                    query = (
                        "SELECT id, recipient_email, subject, body, attempt "
                        "FROM pending_notifications "
                        "WHERE status IN (?, ?) AND retryable = 1 "
                        "AND attempt < ? "
                        "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
                        "ORDER BY created_at ASC, id ASC LIMIT 1"
                    )
                    params = (
                        NotificationService.STATUS_PENDING,
                        NotificationService.STATUS_FAILED,
                        max_attempts,
                        datetime.now(),
                    )
                    dialect = _dialect(conn)
                    if dialect == 'postgres':
                        query += " FOR UPDATE SKIP LOCKED"
                    elif dialect == 'mysql':
                        query += " FOR UPDATE"
                    cur.execute(query, params)
                    row = cur.fetchone()
                    if row:
                        cur.execute(
                            "UPDATE pending_notifications SET status = ?, "
                            "attempt = attempt + 1, claimed_at = ?, "
                            "last_attempt_at = ?, last_error = NULL "
                            "WHERE id = ?",
                            (
                                NotificationService.STATUS_PROCESSING,
                                datetime.now(),
                                datetime.now(),
                                row['id'],
                            ),
                        )
                        row['attempt'] += 1
                finally:
                    close_cursor(cur)
            if not row:
                break

            processed += 1
            try:
                delivery = deliver(
                    row['recipient_email'], row['subject'], row['body'],
                )
            except Exception as exc:
                log.warning(
                    'Notification delivery raised for %s (%s)',
                    _mask_email(row['recipient_email']),
                    type(exc).__name__,
                )
                delivery = EmailDeliveryResult(
                    EmailDeliveryResult.FAILURE,
                    retryable=True,
                    exception_type=type(exc).__name__,
                )

            if delivery.is_success:
                status = NotificationService.STATUS_SENT
                retryable = False
                next_attempt_at = None
                last_error = None
                sent += 1
            elif delivery.is_ambiguous:
                status = NotificationService.STATUS_AMBIGUOUS
                retryable = False
                next_attempt_at = None
                last_error = 'ambiguous_delivery'
            else:
                retryable = delivery.retryable and row['attempt'] < max_attempts
                status = NotificationService.STATUS_FAILED
                next_attempt_at = (
                    NotificationService._next_retry_at(row['attempt'])
                    if retryable else None
                )
                last_error = delivery.exception_type or 'delivery_failed'

            try:
                with immediate_transaction() as conn:
                    cur = get_cursor(conn)
                    try:
                        cur.execute(
                            "UPDATE pending_notifications SET status = ?, "
                            "retryable = ?, next_attempt_at = ?, claimed_at = NULL, "
                            "last_error = ? WHERE id = ? AND status = ?",
                            (
                                status,
                                int(retryable),
                                next_attempt_at,
                                last_error,
                                row['id'],
                                NotificationService.STATUS_PROCESSING,
                            ),
                        )
                    finally:
                        close_cursor(cur)
            except Exception as exc:
                log.error(
                    'Could not record delivery outcome for notification %s (%s)',
                    row['id'],
                    type(exc).__name__,
                )
        return processed, sent


def send_template_notification(purpose, recipient_email, related_id=None,
                               dedupe_key=None):
    """Convenience wrapper that pulls the subject/body for a known purpose.

    Attempts synchronous SMTP delivery first, falling back to the queue.
    Returns one of the ``NotificationService.RESULT_*`` constants.
    """
    template = TEMPLATES.get(purpose)
    if not template:
        return NotificationService.RESULT_SKIPPED
    return NotificationService.send_or_enqueue(
        recipient_email,
        template['subject'],
        template['body'],
        purpose,
        related_id=related_id,
        dedupe_key=dedupe_key,
    )


def queue_template_notification(purpose, recipient_email, related_id=None,
                                dedupe_key=None, conn=None):
    """Durably enqueue a template notification without attempting SMTP.

    Unlike :func:`send_template_notification`, this always stores the job as
    ``'pending'`` and lets the background worker deliver it later.  This keeps
    HTTP request handlers fast and decouples account-deletion notifications
    from live SMTP availability.

    Accepts an optional ``conn`` for transactional use (insertion without commit).
    Returns one of the ``NotificationService.RESULT_*`` constants.
    """
    template = TEMPLATES.get(purpose)
    if not template:
        return NotificationService.RESULT_SKIPPED
    return NotificationService.enqueue(
        recipient_email,
        template['subject'],
        template['body'],
        purpose,
        related_id=related_id,
        dedupe_key=dedupe_key,
        conn=conn,
    )


def create_pending_notifications_table(conn, dialect='sqlite'):
    """Create the notification queue table for the given dialect (idempotent)."""
    if dialect == 'sqlite':
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pending_notifications ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "recipient_email VARCHAR(255) NOT NULL, "
            "subject VARCHAR(255) NOT NULL, "
            "body TEXT NOT NULL, "
            "purpose VARCHAR(50), "
            "related_id INTEGER, "
            "dedupe_key VARCHAR(255), "
            "attempt INTEGER DEFAULT 0, "
            "status VARCHAR(20) DEFAULT 'pending', "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "last_attempt_at DATETIME DEFAULT NULL, "
            "retryable INTEGER DEFAULT 1, "
            "next_attempt_at DATETIME DEFAULT NULL, "
            "claimed_at DATETIME DEFAULT NULL, "
            "last_error VARCHAR(100) DEFAULT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_notif_dedupe "
            "ON pending_notifications(dedupe_key, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_notif_status "
            "ON pending_notifications(status, created_at)"
        )
    else:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pending_notifications ("
            "id SERIAL PRIMARY KEY, "
            "recipient_email VARCHAR(255) NOT NULL, "
            "subject VARCHAR(255) NOT NULL, "
            "body TEXT NOT NULL, "
            "purpose VARCHAR(50), "
            "related_id INTEGER, "
            "dedupe_key VARCHAR(255), "
            "attempt INTEGER DEFAULT 0, "
            "status VARCHAR(20) DEFAULT 'pending', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "last_attempt_at TIMESTAMP DEFAULT NULL, "
            "retryable INTEGER DEFAULT 1, "
            "next_attempt_at TIMESTAMP DEFAULT NULL, "
            "claimed_at TIMESTAMP DEFAULT NULL, "
            "last_error VARCHAR(100) DEFAULT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_notif_dedupe "
            "ON pending_notifications(dedupe_key, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_notif_status "
            "ON pending_notifications(status, created_at)"
        )
