"""Tests for session-termination and account-deletion notification delivery.

These tests verify the ``NotificationService`` layer that backs:

* session-termination emails sent when an admin revokes a session
* account-deletion emails sent when a user or admin deletes an account

Run:  python tests/tests_account_deletion_and_session_email.py
"""
import os
import sys
import re

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
os.chdir(root)

from app import create_app  # noqa: E402
from models.session import (  # noqa: E402
    LoginHistory,
    UserSession,
    hash_session_token,
)
from models.user import User  # noqa: E402
from utils.notifications import (  # noqa: E402
    NotificationService,
    TEMPLATES,
    deliver,
    send_template_notification,
)
from utils.database import immediate_transaction, get_connection, get_cursor, close_cursor  # noqa: E402
from utils.otp import OTPManager, RESULT_SUCCESS, RESULT_FAILURE  # noqa: E402
from config import Config  # noqa: E402

Config.USE_POSTGRES = False

TEST_EMAIL = 'notif_test_user@example.com'
TEST_PASSWORD = 'TestPass123'

results = []


def record(name, passed, detail=''):
    results.append((name, bool(passed), detail))
    print('[%s] %s%s' % ('PASS' if passed else 'FAIL', name,
                        (' - ' + detail) if detail else ''))


def csrf_from(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


def setup_test_user():
    with create_app().app_context():
        u = User.get_by_email(TEST_EMAIL)
        if not u:
            User.create('Notif Test User', TEST_EMAIL, TEST_PASSWORD)
            u = User.get_by_email(TEST_EMAIL)
        User.update(u['id'], is_active=1)
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (u['id'],))
        cursor.execute("DELETE FROM login_history WHERE user_id = ?", (u['id'],))
        cursor.execute("DELETE FROM pending_notifications WHERE related_id = ? OR recipient_email = ?",
                       (u['id'], u.get('email')))
        conn.commit()
        close_cursor(cursor)
        conn.close()
        return u['id'], TEST_EMAIL


def _clear_pending_notifications():
    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute("DELETE FROM pending_notifications")
    conn.commit()
    close_cursor(cursor)
    conn.close()


def _count_pending():
    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute("SELECT COUNT(*) AS total FROM pending_notifications")
    row = cursor.fetchone()
    close_cursor(cursor)
    conn.close()
    return row['total'] if row else 0


def _sent_notifications():
    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute(
        "SELECT * FROM pending_notifications WHERE status = 'sent' "
        "ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    close_cursor(cursor)
    conn.close()
    return row


def _pending_notifications():
    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute(
        "SELECT * FROM pending_notifications WHERE status = 'pending' "
        "ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    close_cursor(cursor)
    conn.close()
    return row


def login_step_one(client, email, password):
    page = client.get('/login')
    token = csrf_from(page.get_data(as_text=True))
    return client.post('/login', data={
        'email': email, 'password': password, 'csrf_token': token,
    }, follow_redirects=False)


def login_step_two(client, email):
    with client.session_transaction() as sess:
        otp = sess.get('otp_login_%s' % email, {}).get('otp')
    page = client.get('/login')
    token = csrf_from(page.get_data(as_text=True))
    return client.post('/login', data={'otp': otp, 'csrf_token': token},
                      follow_redirects=False)


def get_admin_client(app):
    admin = app.test_client()
    p = admin.get('/admin/login')
    tok = csrf_from(p.get_data(as_text=True))
    admin.post('/admin/login', data={
        'username': 'admin', 'password': 'admin@123', 'csrf_token': tok,
    })
    return admin


def main():
    app = create_app()

    # Mock SMTP so no real emails are sent.
    sent_emails = []
    original_send_email = OTPManager.send_email
    original_send_notification = getattr(OTPManager, 'send_notification_email', None)

    def mock_send_email(to_email, subject, body):
        sent_emails.append((to_email, subject, body))
        return RESULT_SUCCESS

    OTPManager.send_email = staticmethod(mock_send_email)
    OTPManager.send_otp_email = staticmethod(lambda *a, **k: RESULT_SUCCESS)
    OTPManager.is_configured = staticmethod(lambda: True)

    user_id, email = setup_test_user()
    _clear_pending_notifications()

    print('=' * 70)
    print('TEST 1: TEMPLATES contain the expected keys')
    print('=' * 70)
    record('account_deleted_self template exists', 'account_deleted_self' in TEMPLATES)
    record('account_deleted_by_admin template exists', 'account_deleted_by_admin' in TEMPLATES)
    record('session_terminated template exists', 'session_terminated' in TEMPLATES)
    rec = TEMPLATES.get('session_terminated', {})
    record('session_terminated has subject', bool(rec.get('subject')))
    record('session_terminated body mentions admin', 'administrator' in rec.get('body', '').lower())

    print()
    print('=' * 70)
    print('TEST 2: send_or_enqueue persists a sent notification')
    print('=' * 70)
    before = _count_pending()
    result = NotificationService.send_or_enqueue(
        email, 'Test Subject', 'Test Body', 'session_terminated',
        related_id=user_id, dedupe_key='test_dedupe_1',
    )
    after = _count_pending()
    record('send_or_enqueue returns sent', result == NotificationService.RESULT_SENT,
           'got=%s' % result)
    record('notification row was persisted', after > before,
           'before=%d after=%d' % (before, after))
    sent_row = _sent_notifications()
    record('persisted row has status sent', sent_row and sent_row.get('status') == 'sent')
    record('persisted row stores recipient', sent_row and sent_row.get('recipient_email') == email)

    print()
    print('=' * 70)
    print('TEST 3: dedupe key prevents duplicate sends')
    print('=' * 70)
    sent_emails.clear()
    result2 = NotificationService.send_or_enqueue(
        email, 'Test Subject 2', 'Test Body 2', 'session_terminated',
        related_id=user_id, dedupe_key='test_dedupe_1',
    )
    record('duplicate returns skipped', result2 == NotificationService.RESULT_SKIPPED,
           'got=%s' % result2)
    record('no email sent for duplicate', len(sent_emails) == 0,
           'count=%d' % len(sent_emails))

    print()
    print('=' * 70)
    print('TEST 4: new dedupe key allows a new notification')
    print('=' * 70)
    sent_emails.clear()
    result3 = NotificationService.send_or_enqueue(
        email, 'Another Subject', 'Another Body', 'session_terminated',
        related_id=user_id, dedupe_key='test_dedupe_2',
    )
    record('new dedupe key sends', result3 == NotificationService.RESULT_SENT)
    record('email was sent for new dedupe', len(sent_emails) == 1)

    print()
    print('=' * 70)
    print('TEST 5: process_pending retries failed notifications')
    print('=' * 70)
    # Insert a failed notification manually
    with immediate_transaction() as conn:
        cur = get_cursor(conn)
        cur.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status, created_at, last_attempt_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (email, 'Retry Subject', 'Retry Body', 'session_terminated',
             user_id + 999, 'test_retry_key', 0, 'failed',
             '2026-01-01 00:00:00', '2026-01-01 00:00:00'),
        )
        conn.commit()
        close_cursor(cur)

    sent_emails.clear()
    processed, sent = NotificationService.process_pending(limit=10)
    record('process_pending returns counts', processed >= 1,
           'processed=%d sent=%d' % (processed, sent))
    record('retry email was sent', len(sent_emails) >= 1,
           'count=%d' % len(sent_emails))

    print()
    print('=' * 70)
    print('TEST 6: MAX_ATTEMPTS bounds retries')
    print('=' * 70)
    # Insert a notification at max attempts — it should NOT be retried
    with immediate_transaction() as conn:
        cur = get_cursor(conn)
        cur.execute(
            "INSERT INTO pending_notifications "
            "(recipient_email, subject, body, purpose, related_id, "
            "dedupe_key, attempt, status, created_at, last_attempt_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (email, 'MaxRetry Subject', 'MaxRetry Body', 'session_terminated',
             user_id + 998, 'test_max_retry_key',
             NotificationService.MAX_ATTEMPTS, 'failed',
             '2026-01-01 00:00:00', '2026-01-01 00:00:00'),
        )
        conn.commit()
        close_cursor(cur)

    sent_emails.clear()
    processed2, sent2 = NotificationService.process_pending(limit=10)
    record('max-attempt notification is not retried', sent2 == 0,
           'processed=%d sent=%d' % (processed2, sent2))

    print()
    print('=' * 70)
    print('TEST 7: send_template_notification uses named template')
    print('=' * 70)
    sent_emails.clear()
    _clear_pending_notifications()
    result = send_template_notification(
        'session_terminated', email,
        related_id=user_id, dedupe_key='template_test_1',
    )
    record('template notification sent', result == NotificationService.RESULT_SENT)
    record('template email sent', len(sent_emails) == 1)
    if sent_emails:
        record('subject from template', sent_emails[0][1] == TEMPLATES['session_terminated']['subject'])

    print()
    print('=' * 70)
    print('TEST 8: Admin session revoke sets ended_by_admin and emails user')
    print('=' * 70)
    sent_emails.clear()
    _clear_pending_notifications()

    # Create a session for the test user via a browser client
    client = app.test_client()
    login_step_one(client, email, TEST_PASSWORD)
    login_step_two(client, email)
    sent_emails.clear()  # Clear OTP emails from login

    sessions = UserSession.get_active_sessions_for_user(user_id)
    record('has active session to revoke', len(sessions) >= 1,
           'count=%d' % len(sessions))
    if sessions:
        sid = sessions[0]['id']
        admin = get_admin_client(app)
        sec_page = admin.get('/admin/user-security')
        tok = csrf_from(sec_page.get_data(as_text=True))
        r = admin.post('/admin/users/%d/revoke-session/%d' % (user_id, sid),
                       data={'csrf_token': tok}, follow_redirects=False)
        record('admin revoke redirects', r.status_code == 302,
               'status=%s' % r.status_code)

        row = UserSession.get_by_id(sid)
        record('session marked ended_by_admin=1',
               row and row.get('ended_by_admin') == 1,
               'ended_by_admin=%s' % (row.get('ended_by_admin') if row else 'N/A'))
        record('session status is revoked',
               row and row.get('status') == 'revoked')
        record('termination_reason mentions administrator',
               row and 'administrator' in str(row.get('termination_reason', '')).lower())

        record('session_terminated email sent', len(sent_emails) >= 1,
               'count=%d' % len(sent_emails))
        if sent_emails:
            record('email to correct user', sent_emails[0][0] == email)
            record('email subject is session_terminated',
                   sent_emails[0][1] == TEMPLATES['session_terminated']['subject'])

        # Verify notification persisted with correct dedupe key
        from utils.database import get_connection as _gc, get_cursor as _gcur, close_cursor as _cc
        conn = _gc()
        cur = _gcur(conn)
        cur.execute(
            "SELECT * FROM pending_notifications WHERE dedupe_key = ? "
            "ORDER BY id DESC LIMIT 1",
            ('session_terminated:%d' % sid,),
        )
        notif_row = cur.fetchone()
        _cc(cur)
        conn.close()
        record('notification persisted with correct dedupe key',
               notif_row is not None)
        record('persisted notification has status sent',
               notif_row and notif_row.get('status') == 'sent')

    print()
    print('=' * 70)
    print('TEST 9: Admin revoke ALL sessions emails user once')
    print('=' * 70)
    sent_emails.clear()
    _clear_pending_notifications()

    # Ensure active session exists
    login_step_one(client, email, TEST_PASSWORD)
    login_step_two(client, email)

    admin = get_admin_client(app)
    sec_page = admin.get('/admin/user-security')
    tok = csrf_from(sec_page.get_data(as_text=True))
    r = admin.post('/admin/users/%d/revoke-all-sessions' % user_id,
                   data={'csrf_token': tok}, follow_redirects=False)
    record('admin revoke-all redirects', r.status_code == 302,
           'status=%s' % r.status_code)

    remaining = UserSession.get_active_sessions_for_user(user_id)
    record('zero active sessions after revoke-all', len(remaining) == 0,
           'count=%d' % len(remaining))
    record('session_terminated email sent for revoke-all', len(sent_emails) >= 1,
           'count=%d' % len(sent_emails))

    print()
    print('=' * 70)
    print('TEST 10: Admin-ended session shows the right flash message')
    print('=' * 70)
    sent_emails.clear()
    _clear_pending_notifications()

    # Create active session and have admin revoke it
    login_step_one(client, email, TEST_PASSWORD)
    login_step_two(client, email)

    sessions = UserSession.get_active_sessions_for_user(user_id)
    if sessions:
        sid = sessions[0]['id']
        admin = get_admin_client(app)
        sec_page = admin.get('/admin/user-security')
        tok = csrf_from(sec_page.get_data(as_text=True))
        admin.post('/admin/users/%d/revoke-session/%d' % (user_id, sid),
                   data={'csrf_token': tok}, follow_redirects=False)

        # The revoked session should have ended_by_admin=1
        row = UserSession.get_by_id(sid)
        record('ended_by_admin column is set (TEST 10)',
               row and row.get('ended_by_admin') == 1)

        # Next request from the revoked browser follows the redirect and shows
        # the admin-ended flash message on the login page.
        r = client.get('/profile', follow_redirects=True)
        body = r.get_data(as_text=True)
        record('revoked session redirected', r.status_code == 200,
               'status=%s' % r.status_code)
        record('flash message says admin ended session',
               'ended by a studyhub administrator' in body.lower(),
               'found=%s' % ('ended by a studyhub administrator' in body.lower()))

    print()
    print('=' * 70)
    print('TEST 11: User self-deletion sends confirmation email via NotificationService')
    print('=' * 70)
    sent_emails.clear()
    _clear_pending_notifications()

    # Set up admin client to log in
    admin = get_admin_client(app)

    # Log in fresh
    login_step_one(client, email, TEST_PASSWORD)
    with client.session_transaction() as s:
        otp = s.get('otp_login_%s' % email, {}).get('otp')
    page = client.get('/login')
    tok = csrf_from(page.get_data(as_text=True))
    client.post('/login', data={'otp': otp, 'csrf_token': tok})
    sent_emails.clear()  # Clear OTP emails from login

    # Get the CSRF token for deletion
    profile_page = client.get('/profile')
    tok_del = csrf_from(profile_page.get_data(as_text=True))

    # Request deletion
    client.post('/profile/delete-account',
                data={'password': TEST_PASSWORD, 'csrf_token': tok_del},
                follow_redirects=False)
    sent_emails.clear()  # Clear OTP email from deletion request

    # Get OTP from session
    with client.session_transaction() as s:
        otp_del = s.get('otp_delete_account_%s' % email, {}).get('otp')

    # Confirm deletion
    r_del = client.post('/profile/confirm-delete-account',
                        data={'otp': otp_del, 'csrf_token': tok_del},
                        follow_redirects=False)
    record('deletion redirects after success', r_del.status_code == 302,
           'status=%s' % r_del.status_code)
    record('self-deletion email sent', len(sent_emails) >= 1,
           'count=%d' % len(sent_emails))
    if sent_emails:
        record('email subject is account_deleted_self',
               sent_emails[0][1] == TEMPLATES['account_deleted_self']['subject'])
        record('email body mentions successfully deleted',
               'successfully deleted' in sent_emails[0][2].lower())

    # Restore user for subsequent tests
    with app.app_context():
        User.update(user_id, is_active=1, deleted_by_user=0,
                    deletion_requested_at=None, deletion_processed=None)

    print()
    print('=' * 70)
    print('TEST 12: Admin deletion sends account_deleted_by_admin email')
    print('=' * 70)
    sent_emails.clear()
    _clear_pending_notifications()

    # Restore user to non-deleted state
    with app.app_context():
        User.update(user_id, is_active=1, deleted_by_user=0,
                    deletion_requested_at=None, deletion_processed=None)

    admin = get_admin_client(app)
    users_page = admin.get('/admin/users')
    tok_admin = csrf_from(users_page.get_data(as_text=True))
    r = admin.post('/admin/users/delete/%d' % user_id,
                   data={'csrf_token': tok_admin}, follow_redirects=False)
    record('admin deletion redirects', r.status_code == 302,
           'status=%s' % r.status_code)
    record('admin-deletion email sent', len(sent_emails) >= 1,
           'count=%d' % len(sent_emails))
    if sent_emails:
        record('email subject is account_deleted_by_admin',
               sent_emails[0][1] == TEMPLATES['account_deleted_by_admin']['subject'])
        record('email body mentions administrator',
               'administrator' in sent_emails[0][2].lower())

    # Restore user
    with app.app_context():
        User.update(user_id, is_active=1, deleted_by_user=0,
                    deletion_requested_at=None, deletion_processed=None)

    # Restore original mocks
    OTPManager.send_email = original_send_email
    if original_send_notification is not None:
        OTPManager.send_notification_email = original_send_notification

    # Cleanup
    with app.app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM login_history WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM pending_notifications")
        conn.commit()
        close_cursor(cursor)
        conn.close()

    print()
    print('=' * 70)
    failed = [r for r in results if not r[1]]
    print('SUMMARY: %d passed, %d failed, %d total'
          % (len([r for r in results if r[1]]), len(failed), len(results)))
    if failed:
        print()
        print('FAILURES:')
        for name, _, detail in failed:
            print('  -', name, '::', detail)
    print('=' * 70)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
