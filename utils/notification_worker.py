"""Background worker for the StudyHub notification queue.

The worker is a standalone process that polls the ``pending_notifications``
table for eligible jobs and calls :meth:`NotificationService.process_pending`
on each sweep.  It is **not** started automatically by the Flask application
import or by debug reloader — it must be launched as a separate process:

    Development / testing:
        python -m utils.notification_worker

    Production (recommended):
        Run in a systemd service or similar process manager:
        ExecStart=/path/to/python -m utils.notification_worker
        Restart=always

Key safety properties
---------------------
* **Safe claim**: jobs are atomically changed to ``processing`` before SMTP.
  SQLite serializes claims with ``BEGIN IMMEDIATE``; PostgreSQL uses
  ``FOR UPDATE SKIP LOCKED``.
* **Idempotent**: rows are identified by ``dedupe_key``; a duplicate attempt
  is detected by ``send_or_enqueue`` / ``_find_by_dedupe`` before delivery.
* **Bounded**: ``MAX_ATTEMPTS`` caps retries so permanently failing jobs do
  not loop forever.
* **Resilient**: a single failing job is caught inside ``process_pending``;
  the worker continues with the next batch.
* **Graceful shutdown**: ``SIGINT`` and ``SIGTERM`` cause the active sweep to
  finish, then the loop exits cleanly so the process manager can stop it.
* **No auto-start**: the worker is never imported or started by ``app.py``
  or the Flask debug reloader.
"""

import logging
import signal
import time
import threading

from config import Config
from utils import notifications as notif_mod
from utils.database import ensure_schema_extensions

log = logging.getLogger('studyhub.notification_worker')

# Allow graceful shutdown: when a termination signal is received, finish the
# current sweep then exit.
_shutdown = False


def _request_shutdown(signum, frame):
    """Signal handler that flips the shutdown flag."""
    global _shutdown
    _shutdown = True
    log.info('Worker shutdown requested (signal %d); will exit after current sweep.', signum)


def install_signal_handlers():
    """Attach SIGINT/SIGTERM handlers so the worker can drain gracefully."""
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)


def run_worker(poll_interval=None, max_attempts=None, once=False):
    """Run the notification worker loop.

    Args:
        poll_interval: Seconds between sweeps (default: ``Config.NOTIFICATION_POLL_INTERVAL``).
        max_attempts:  Override for ``MAX_ATTEMPTS`` passed to ``process_pending``.
        once:          When ``True``, perform exactly one sweep and exit.
                       Useful for testing and for cron-based deployment:
                       ``python -c "from utils.notification_worker import run_worker; run_worker(once=True)"``
    """
    global _shutdown
    _shutdown = False
    if poll_interval is None:
        poll_interval = Config.NOTIFICATION_POLL_INTERVAL
    poll_interval = max(1, int(poll_interval))
    if max_attempts is None:
        max_attempts = Config.NOTIFICATION_WORKER_MAX_ATTEMPTS
    max_attempts = max(1, int(max_attempts))

    if not ensure_schema_extensions():
        log.error('Notification queue schema is unavailable; worker will exit.')
        return (0, 0)

    # If SMTP is not configured, there is no point sleeping in a tight loop.
    # Log once and exit the worker so the operator knows to configure mail.
    if not notif_mod.OTPManager.is_configured():
        log.warning(
            'SMTP is not configured (MAIL_USERNAME / MAIL_PASSWORD missing). '
            'Worker will exit. Start with valid SMTP credentials for delivery.',
        )
        return (0, 0)

    log.info('Notification worker started (poll_interval=%ds, max_attempts=%d).',
             poll_interval, max_attempts)

    if not once and threading.current_thread() is threading.main_thread():
        install_signal_handlers()

    processed = 0
    sent = 0
    while not _shutdown:
        try:
            processed, sent = notif_mod.NotificationService.process_pending(
                limit=100,
                max_attempts=max_attempts,
            )
            if processed:
                log.info('Worker sweep: processed=%d, sent=%d', processed, sent)
        except Exception as exc:
            # A single failed sweep must not kill the worker.
            log.error('Worker sweep encountered an error (%s); continuing.', type(exc).__name__)

        if once:
            break

        # Sleep in small increments so we can respond promptly to a
        # shutdown signal rather than blocking for the full interval.
        slept = 0
        while slept < poll_interval and not _shutdown:
            time.sleep(1)
            slept += 1

    log.info('Notification worker stopped.')
    return processed, sent


if __name__ == '__main__':
    run_worker()
