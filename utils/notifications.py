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
from datetime import datetime

from utils.database import get_connection, get_cursor, close_cursor
from utils.otp import OTPManager

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

    Returns ``True`` only when the provider accepted the message.  Any SMTP
    timeout or connection failure is caught and logged safely (no credentials
    or message bodies are written to the log).
    """
    if not recipient_email:
        log.warning('Notification skipped: no recipient address')
        return False
    if not OTPManager.is_configured():
        log.info(
            'Mail transport not configured; skipping notification to %s',
            _mask_email(recipient_email),
        )
        return False
    try:
        return bool(OTPManager.send_email(recipient_email, subject, body))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning(
            'SMTP delivery failed for a notification to %s (%s)',
            _mask_email(recipient_email),
            type(exc).__name__,
        )
        return False


class NotificationService:
    """Persistent, idempotent notification delivery with bounded retries."""

    STATUS_PENDING = 'pending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'

    MAX_ATTEMPTS = 5

    # Results returned by send_or_enqueue.
    RESULT_SENT = 'sent'        # accepted by the provider right now
    RESULT_QUEUED = 'queued'    # provider rejected / unavailable; queued for retry
    RESULT_SKIPPED = 'skipped'  # duplicate request; already sent or in-flight
    RESULT_NOT_CONFIGURED = 'not_configured'

    @staticmethod
    def _insert(conn, recipient_email, subject, body, purpose, related_id,
                dedupe_key, status, attempt):
        cur = get_cursor(conn)
        cur.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status, created_at, last_attempt_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                recipient_email, subject, body, purpose, related_id,
                dedupe_key, attempt, status, datetime.now(),
                datetime.now() if status != NotificationService.STATUS_SENT else None,
            ),
        )
        close_cursor(cur)
        conn.commit()

    @staticmethod
    def send_or_enqueue(recipient_email, subject, body, purpose,
                        related_id=None, dedupe_key=None):
        """Deliver a notification, persisting it for retry on failure.

        Returns one of the ``NotificationService.RESULT_*`` constants.  This
        method never raises: a queue failure degrades to a best-effort attempt,
        and the caller's primary action (deletion / revocation) is never rolled
        back because of mail.
        """
        if not recipient_email:
            return NotificationService.RESULT_SKIPPED

        # Idempotency guard: never stack duplicates for the same logical event.
        if dedupe_key:
            existing = NotificationService._find_by_dedupe(dedupe_key)
            if existing:
                if existing.get('status') == NotificationService.STATUS_SENT:
                    return NotificationService.RESULT_SKIPPED
                # pending or failed: a retry is already scheduled.
                return NotificationService.RESULT_QUEUED

        sent = deliver(recipient_email, subject, body)
        try:
            conn = get_connection()
            try:
                if sent:
                    NotificationService._insert(
                        conn, recipient_email, subject, body, purpose,
                        related_id, dedupe_key,
                        NotificationService.STATUS_SENT, 1,
                    )
                else:
                    status = (NotificationService.STATUS_FAILED
                              if OTPManager.is_configured()
                              else NotificationService.STATUS_PENDING)
                    NotificationService._insert(
                        conn, recipient_email, subject, body, purpose,
                        related_id, dedupe_key, status, 1,
                    )
            finally:
                conn.close()
        except Exception as exc:  # queue unavailable -> delivery already attempted
            log.warning(
                'Could not persist notification for %s (%s)',
                _mask_email(recipient_email), type(exc).__name__,
            )

        if sent:
            return NotificationService.RESULT_SENT
        if not OTPManager.is_configured():
            return NotificationService.RESULT_NOT_CONFIGURED
        return NotificationService.RESULT_QUEUED

    @staticmethod
    def _find_by_dedupe(dedupe_key):
        if not dedupe_key:
            return None
        conn = None
        cur = None
        try:
            conn = get_connection()
            cur = get_cursor(conn)
            cur.execute(
                "SELECT id, status FROM pending_notifications "
                "WHERE dedupe_key = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (dedupe_key,),
            )
            return cur.fetchone()
        except Exception:
            return None
        finally:
            close_cursor(cur)
            conn.close() if conn else None

    @staticmethod
    def process_pending(limit=100, max_attempts=None):
        """Retry every queued notification whose attempt budget is not exhausted.

        Returns ``(processed, sent)``.  Each notification is retried at most
        ``MAX_ATTEMPTS`` times, after which it is left in the ``failed`` state
        for manual/operational review, which bounds the number of deliveries
        for any single event to ``MAX_ATTEMPTS``.
        """
        max_attempts = max_attempts or NotificationService.MAX_ATTEMPTS
        processed = 0
        sent = 0
        conn = None
        cur = None
        try:
            conn = get_connection()
            cur = get_cursor(conn)
            cur.execute(
                "SELECT id, recipient_email, subject, body FROM "
                "pending_notifications WHERE status = 'failed' "
                "AND attempt < ? ORDER BY created_at ASC, id ASC LIMIT ?",
                (max_attempts, limit),
            )
            rows = cur.fetchall()
            close_cursor(cur)
            for row in rows:
                processed += 1
                ok = deliver(row['recipient_email'], row['subject'], row['body'])
                cur = get_cursor(conn)
                if ok:
                    cur.execute(
                        "UPDATE pending_notifications SET status = 'sent', "
                        "last_attempt_at = ? WHERE id = ?",
                        (datetime.now(), row['id']),
                    )
                    sent += 1
                else:
                    cur.execute(
                        "UPDATE pending_notifications SET attempt = attempt + 1, "
                        "last_attempt_at = ? WHERE id = ?",
                        (datetime.now(), row['id']),
                    )
                conn.commit()
                close_cursor(cur)
        except Exception as exc:
            log.warning('Notification retry sweep failed (%s)', type(exc).__name__)
        finally:
            close_cursor(cur)
            if conn:
                conn.close()
        return processed, sent


def send_template_notification(purpose, recipient_email, related_id=None,
                               dedupe_key=None):
    """Convenience wrapper that pulls the subject/body for a known purpose."""
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
            "last_attempt_at DATETIME DEFAULT NULL)"
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
            "last_attempt_at TIMESTAMP DEFAULT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_notif_dedupe "
            "ON pending_notifications(dedupe_key, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_notif_status "
            "ON pending_notifications(status, created_at)"
        )
