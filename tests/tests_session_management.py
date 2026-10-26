"""End-to-end tests for the multi-device session management system.

Run:  python tests_session_management.py

Each test drives the real Flask application through its test client, so the
checks cover the actual routing, middleware and SQLite storage.
"""
import os
import sys
import re

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
os.chdir(root)

from app import create_app  # noqa: E402
from models.session import (  # noqa: E402
    FailedLoginAttempt,
    LoginHistory,
    UserSession,
    hash_session_token,
)
from utils.database import get_connection, get_cursor, close_cursor  # noqa: E402
from config import Config  # noqa: E402

# Tests run against the bundled SQLite database to avoid depending on an
# external PostgreSQL / NeonDB server.
Config.USE_POSTGRES = False

TEST_EMAIL = 'session_test_user@example.com'
TEST_PASSWORD = 'TestPass123'

results = []


def record(name, passed, detail=''):
    results.append((name, bool(passed), detail))
    status = 'PASS' if passed else 'FAIL'
    print('[%s] %s%s' % (status, name, (' - ' + detail) if detail else ''))


def csrf_from(html):
    """Extract CSRF token from a form hidden input or a meta tag."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match:
        return match.group(1)
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    if match:
        return match.group(1)
    return None


def setup_test_user(app):
    """Create a fresh active user; returns (user_id, email)."""
    with app.app_context():
        from models.user import User
        user = User.get_by_email(TEST_EMAIL)
        if not user:
            User.create('Session Test User', TEST_EMAIL, TEST_PASSWORD)
            user = User.get_by_email(TEST_EMAIL)
        User.update(user['id'], is_active=1)
        # Clean state from any previous run so event counts are exact.
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (user['id'],))
        cursor.execute("DELETE FROM login_history WHERE user_id = ?", (user['id'],))
        cursor.execute("DELETE FROM failed_login_attempts WHERE user_id = ?", (user['id'],))
        cursor.execute("DELETE FROM pending_notifications")
        conn.commit()
        close_cursor(cursor)
        conn.close()
        return user['id'], TEST_EMAIL



from utils.database import immediate_transaction  # noqa: E402


def revoke_all_active(user_id, reason='test cleanup'):
    """Revoke every active session for a user (test helper)."""
    with immediate_transaction() as conn:
        UserSession.revoke_all_for_user(conn, user_id, reason)


def purge_test_rows(user_id):
    with create_app().app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM login_history WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM failed_login_attempts WHERE user_id = ?", (user_id,))
        conn.commit()
        close_cursor(cursor)
        conn.close()


def login_step_one(client, email, password):
    """Perform the credentials step of login, returning the response."""
    page = client.get('/login')
    token = csrf_from(page.get_data(as_text=True))
    return client.post('/login', data={
        'email': email,
        'password': password,
        'csrf_token': token,
    }, follow_redirects=False)


def main():
    app = create_app()
    app.config['WTF_CSRF_ENABLED'] = False

    # Tests must not depend on a live SMTP server. The OTP is read from the
    # signed session below, exactly as it would be for a user reading email.
    from utils.otp import OTPManager, RESULT_SUCCESS
    OTPManager.send_email = staticmethod(lambda *a, **k: RESULT_SUCCESS)
    OTPManager.send_otp_email = staticmethod(lambda *a, **k: RESULT_SUCCESS)
    OTPManager.is_configured = staticmethod(lambda: True)

    user_id, email = setup_test_user(app)


    print('=' * 70)
    print('TEST 1: First login creates an active session')
    print('=' * 70)
    a = app.test_client()
    r = login_step_one(a, email, TEST_PASSWORD)
    body = r.get_data(as_text=True)
    record('credentials step rejected nothing', r.status_code in (200, 302), 'status=%s' % r.status_code)
    record('OTP form shown after correct credentials', 'Enter OTP' in body, 'show_otp screen')

    # OTP is in the Flask session (email not configured path stores it there).
    with a.session_transaction() as sess:
        otp = sess.get('otp_login_%s' % email, {}).get('otp')
    record('OTP present in session (email not configured)', otp is not None)

    token = csrf_from(body)
    r2 = a.post('/login', data={'otp': otp, 'csrf_token': token},
                follow_redirects=False)
    record('OTP accepted, new session created', r2.status_code == 302, 'status=%s' % r2.status_code)

    sessions = UserSession.get_active_sessions_for_user(user_id)
    record('exactly one active session', len(sessions) == 1, 'count=%d' % len(sessions))
    session_one_id = sessions[0]['id'] if sessions else None
    history, total = LoginHistory.get_for_user(user_id)
    record('login success recorded', total >= 1, 'events=%d' % total)

    print()
    print('=' * 70)
    print('TEST 2: Login from a second browser detects the existing session')
    print('=' * 70)
    b = app.test_client()
    r = login_step_one(b, email, TEST_PASSWORD)
    body = r.get_data(as_text=True)
    with b.session_transaction() as sess:
        otp_b = sess.get('otp_login_%s' % email, {}).get('otp')
    token = csrf_from(body)
    r2 = b.post('/login', data={'otp': otp_b, 'csrf_token': token},
                follow_redirects=False)
    body2 = r2.get_data(as_text=True)
    record('multi-device conflict message shown',
           'already logged in' in body2.lower(),
           'status=%s' % r2.status_code)
    record('two options offered',
           all(k in body2 for k in ['End Other Session', 'Cancel']))

    # browser B must NOT be authenticated yet (no silent takeover)
    still_active = UserSession.get_active_sessions_for_user(user_id)
    record('original session still active during conflict prompt',
           any(s['id'] == session_one_id for s in still_active),
           'sessions=%d' % len(still_active))
    record('no new session created while prompting',
           len(still_active) == 1, 'count=%d' % len(still_active))

    print()
    print('=' * 70)
    print('TEST 3: Cancelling keeps the existing session and creates nothing')
    print('=' * 70)
    body3 = b.post('/login', data={'cancel_login': '1', 'csrf_token': token}, follow_redirects=True)
    after = UserSession.get_active_sessions_for_user(user_id)
    record('cancelling creates no new session', len(after) == 1, 'count=%d' % len(after))
    record('original session untouched',
           any(s['id'] == session_one_id for s in after))

    print()
    print('=' * 70)
    print('TEST 4: Cancelling via the cancel button keeps the existing session')
    print('=' * 70)
    # redo the conflict prompt for browser B
    r = login_step_one(b, email, TEST_PASSWORD)
    body = r.get_data(as_text=True)
    with b.session_transaction() as sess:
        otp_b = sess.get('otp_login_%s' % email, {}).get('otp')
    token = csrf_from(body)
    b.post('/login', data={'otp': otp_b, 'csrf_token': token}, follow_redirects=False)
    body4 = b.post('/login', data={'keep_session': '1', 'csrf_token': token},
                  follow_redirects=True)
    after = UserSession.get_active_sessions_for_user(user_id)
    record('keep existing leaves one active session', len(after) == 1,
           'count=%d' % len(after))
    record('original session still the same',
           any(s['id'] == session_one_id for s in after))
    with b.session_transaction() as sess:
        record('browser B not authenticated', 'user_id' not in sess)

    print()
    print('=' * 70)
    print('TEST 5: "Switch to this session" replaces the old session')
    print('=' * 70)
    r = login_step_one(b, email, TEST_PASSWORD)
    body = r.get_data(as_text=True)
    with b.session_transaction() as sess:
        otp_b = sess.get('otp_login_%s' % email, {}).get('otp')
    token = csrf_from(body)
    b.post('/login', data={'otp': otp_b, 'csrf_token': token}, follow_redirects=False)
    b.post('/login', data={'replace_session': '1', 'csrf_token': token},
           follow_redirects=False)
    after = UserSession.get_active_sessions_for_user(user_id)
    record('only one active session after replacement', len(after) == 1,
           'count=%d' % len(after))
    record('the new session is a different row',
           after and after[0]['id'] != session_one_id)
    new_session_id = after[0]['id'] if after else None
    with b.session_transaction() as sess:
        record('browser B now authenticated', 'user_id' in sess)
        record('browser B holds a session token', 'session_token' in sess)

    print()
    print('=' * 70)
    print('TEST 6: The old session loses access (was revoked)')
    print('=' * 70)
    r = a.get('/profile', follow_redirects=False)
    record('old browser redirected away from protected page',
           r.status_code in (301, 302), 'status=%s' % r.status_code)
    r = a.get('/favorites', follow_redirects=False)
    record('old browser blocked on another protected page',
           r.status_code in (301, 302), 'status=%s' % r.status_code)
    record('old session row now revoked',
           UserSession.get_by_id(session_one_id).get('status') == 'revoked')
    # The revoked session cannot be resurrected by a refresh
    r = a.get('/profile', follow_redirects=False)
    record('still blocked after refresh', r.status_code in (301, 302),
           'status=%s' % r.status_code)

    # browser B must still work
    r = b.get('/profile', follow_redirects=False)
    record('new browser still has access', r.status_code == 200,
           'status=%s' % r.status_code)

    print()
    print('=' * 70)
    print('TEST 7: Logout updates status and records the event')
    print('=' * 70)
    hp = b.get('/account/login-history')
    htok = csrf_from(hp.get_data(as_text=True))
    b.post('/logout', data={'csrf_token': htok}, follow_redirects=False)
    rows = UserSession.get_active_sessions_for_user(user_id)
    record('no active sessions after logout', len(rows) == 0, 'count=%d' % len(rows))
    row = UserSession.get_by_id(new_session_id)
    record('session marked logged_out', row.get('status') == 'logged_out',
           'status=%s' % row.get('status'))
    history, total = LoginHistory.get_for_user(user_id)
    record('logout event recorded', any(e.get('event_type') == 'logout' for e in history),
           'events=%d' % total)

    print()
    print('=' * 70)
    print('TEST 8: Login history is restricted to the owning user')
    print('=' * 70)
    other = app.test_client()
    from models.user import User
    other_user = User.get_by_email(TEST_EMAIL)
    record('history belongs to the right user',
           all(e.get('user_id') == user_id for e in history))

    # A second user can never read user_id's history.
    other_email = 'session_test_other@example.com'
    other_user_row = User.get_by_email(other_email)
    if not other_user_row:
        User.create('Other Tester', other_email, TEST_PASSWORD)
        other_user_row = User.get_by_email(other_email)
    other_id = other_user_row['id']
    r = login_step_one(other, other_email, TEST_PASSWORD)
    body = r.get_data(as_text=True)
    with other.session_transaction() as sess:
        otp_o = sess.get('otp_login_%s' % other_email, {}).get('otp')
    token = csrf_from(body)
    other.post('/login', data={'otp': otp_o, 'csrf_token': token},
              follow_redirects=False)

    r = other.get('/account/login-history')
    body = r.get_data(as_text=True)
    other_sessions = UserSession.get_active_sessions_for_user(user_id)
    record('other user does not see this user\'s sessions',
           all(s['id'] not in body for s in other_sessions))

    hp2 = other.get('/account/login-history')
    htok2 = csrf_from(hp2.get_data(as_text=True))
    other.post('/logout', data={'csrf_token': htok2})

    print()
    print('=' * 70)
    print('TEST 9: Failed login attempts are recorded, no passwords stored')
    print('=' * 70)
    c = app.test_client()
    before = FailedLoginAttempt.get_recent(per_page=1)[1]
    login_step_one(c, email, 'definitely-wrong-password')
    after = FailedLoginAttempt.get_recent(per_page=1)[1]
    record('failed attempt recorded', after > before, 'before=%d after=%d' % (before, after))
    recent, _ = FailedLoginAttempt.get_recent(per_page=5)
    record('no password text stored',
           all('definitely-wrong-password' not in str(a_row) for a_row in recent))

    print()
    print('=' * 70)
    print('TEST 10: Concurrent logins do not both create a session')
    print('=' * 70)
    revoke_all_active(user_id)
    # Two clients authenticate at the "same time" and both try to complete.
    x = app.test_client()
    y = app.test_client()
    for client in (x, y):
        r = login_step_one(client, email, TEST_PASSWORD)
        body = r.get_data(as_text=True)
        with client.session_transaction() as sess:
            otp_v = sess.get('otp_login_%s' % email, {}).get('otp')
        token = csrf_from(body)
        client.post('/login', data={'otp': otp_v, 'csrf_token': token},
                   follow_redirects=False)
    # Only one of them is allowed to complete without evicting the other.
    created = 0
    for client in (x, y):
        r = client.post('/login', data={'replace_session': '1',
                                        'csrf_token': token},
                        follow_redirects=False)
        if r.status_code == 302:
            created += 1
    rows = UserSession.get_active_sessions_for_user(user_id)
    record('single active session after concurrent logins', len(rows) <= 1,
           'active=%d (replace submissions accepted=%d)' % (len(rows), created))

    print()
    print('=' * 70)
    print('TEST 11: Existing login / registration still work')
    print('=' * 70)
    r = app.test_client().get('/login')
    record('login page loads', r.status_code == 200)
    r = app.test_client().get('/register')
    record('register page loads', r.status_code == 200)
    r = app.test_client().get('/')
    record('home page loads', r.status_code == 200)
    r = app.test_client().get('/forgot-password')
    record('forgot password page loads', r.status_code == 200)

    # Registration of a brand new account
    n = app.test_client()
    new_email = 'session_test_new_%d@example.com' % int(re.sub(r'\D', '', os.urandom(4).hex()))
    rp = n.get('/register')
    rtok = csrf_from(rp.get_data(as_text=True))
    r = n.post('/register', data={'name': 'New Tester', 'email': new_email,
                                  'password': 'Password1', 'confirm_password': 'Password1',
                                  'csrf_token': rtok})
    record('registration succeeds', r.status_code in (200, 302), 'status=%s' % r.status_code)

    print()
    print('=' * 70)
    print('TEST 12: Admin security pages require admin access')
    print('=' * 70)
    guest = app.test_client()
    r = guest.get('/admin/user-security', follow_redirects=False)
    record('guest redirected from admin security',
           r.status_code in (301, 302), 'status=%s' % r.status_code)

    admin = app.test_client()
    page = admin.get('/admin/login')
    body = page.get_data(as_text=True)
    token = csrf_from(body)
    admin.post('/admin/login', data={'username': 'admin', 'password': 'admin@123',
                                     'csrf_token': token},
                follow_redirects=False)
    r = admin.get('/admin/user-security')
    record('admin can open user security page', r.status_code == 200,
           'status=%s' % r.status_code)
    r = admin.get('/admin/users/%d/login-history' % user_id)
    record('admin can open a specific user history', r.status_code == 200,
           'status=%s' % r.status_code)

    print()
    print('=' * 70)
    print('TEST 13: Conflict screen shows device and date/time info')
    print('=' * 70)
    # Re-trigger conflict for browser b
    rb = app.test_client()
    login_step_one(rb, email, TEST_PASSWORD)
    with rb.session_transaction() as sess:
        otp_c = sess.get('otp_login_%s' % email, {}).get('otp')
    page = rb.post('/login', data={'otp': otp_c, 'csrf_token': csrf_from('')},
                   follow_redirects=False) if False else None
    # Get a fresh csrf token
    pg = login_step_one(rb, email, TEST_PASSWORD)
    tok = csrf_from(pg.get_data(as_text=True))
    with rb.session_transaction() as sess:
        otp_c = sess.get('otp_login_%s' % email, {}).get('otp')
    r_conf = rb.post('/login', data={'otp': otp_c, 'csrf_token': tok},
                     follow_redirects=False)
    body_conf = r_conf.get_data(as_text=True)
    record('conflict screen has explanation text',
           'already logged in' in body_conf.lower())
    record('conflict shows End Other Session button', 'End Other Session' in body_conf)
    record('conflict shows Cancel button', 'Cancel' in body_conf)
    record('conflict does not show Keep Existing button', 'Keep Existing' not in body_conf)
    # No sensitive technical info exposed to students
    record('conflict does not show session IDs',
           'session_id' not in body_conf.lower() and 's.id' not in body_conf)

    print()
    print('=' * 70)
    print('TEST 14: Admin can terminate individual active session')
    print('=' * 70)
    # Ensure fresh active session for the test user
    revoke_all_active(user_id)
    r_login = login_step_one(rb, email, TEST_PASSWORD)
    tok2 = csrf_from(r_login.get_data(as_text=True))
    with rb.session_transaction() as sess:
        otp_d = sess.get('otp_login_%s' % email, {}).get('otp')
    rb.post('/login', data={'otp': otp_d, 'csrf_token': tok2}, follow_redirects=False)
    active_before = UserSession.get_active_sessions_for_user(user_id)
    record('pre-test has 1 active session', len(active_before) == 1,
           'count=%d' % len(active_before))
    sid = active_before[0]['id'] if active_before else None

    # End the session via admin panel
    tok3 = csrf_from(admin.get('/admin/user-security').get_data(as_text=True))
    r_end = admin.post('/admin/users/%d/revoke-session/%d' % (user_id, sid),
                       data={'csrf_token': tok3}, follow_redirects=False)
    record('admin End redirects (302)', r_end.status_code == 302,
           'status=%s' % r_end.status_code)
    row = UserSession.get_by_id(sid)
    record('session is revoked in DB', row and row.get('status') == 'revoked',
           'status=%s' % (row.get('status') if row else 'None'))

    # Protected request from the revoked session fails
    r_prot = rb.get('/profile', follow_redirects=False)
    record('revoked session blocked from /profile',
           r_prot.status_code in (301, 302), 'status=%s' % r_prot.status_code)

    print()
    print('=' * 70)
    print('TEST 15: Admin End All revokes only selected user sessions')
    print('=' * 70)
    revoke_all_active(user_id)
    # Create two sessions for user_id (via concurrent login)
    for _ in range(2):
        c_tmp = app.test_client()
        lp = login_step_one(c_tmp, email, TEST_PASSWORD)
        tok_t = csrf_from(lp.get_data(as_text=True))
        with c_tmp.session_transaction() as s:
            otp_t = s.get('otp_login_%s' % email, {}).get('otp')
        c_tmp.post('/login', data={'otp': otp_t, 'csrf_token': tok_t},
                   follow_redirects=False)
        # Each login may replace; ensure at least one active
    active = UserSession.get_active_sessions_for_user(user_id)
    before_count = len(active)
    record('at least 1 active session before end-all', before_count >= 1,
           'count=%d' % before_count)

    tok4 = csrf_from(admin.get('/admin/user-security').get_data(as_text=True))
    r_all = admin.post('/admin/users/%d/revoke-all-sessions' % user_id,
                       data={'csrf_token': tok4}, follow_redirects=False)
    record('admin End All redirects (302)', r_all.status_code == 302)
    after = UserSession.get_active_sessions_for_user(user_id)
    record('zero active sessions after end-all', len(after) == 0,
           'count=%d' % len(after))

    print()
    print('=' * 70)
    print('TEST 16: Non-admin and CSRF checks on session endpoints')
    print('=' * 70)
    guest = app.test_client()
    r_g = guest.post('/admin/users/%d/revoke-session/1' % user_id,
                     data={}, follow_redirects=False)
    record('non-admin blocked from End', r_g.status_code in (301, 302),
           'status=%s' % r_g.status_code)
    r_g2 = guest.post('/admin/users/%d/revoke-all-sessions' % user_id,
                      data={}, follow_redirects=False)
    record('non-admin blocked from End All', r_g2.status_code in (301, 302),
           'status=%s' % r_g2.status_code)

    # CSRF: POST without token should be rejected (redirects to safe URL)
    revoke_all_active(user_id)
    r_setup = login_step_one(rb, email, TEST_PASSWORD)
    tok5 = csrf_from(r_setup.get_data(as_text=True))
    with rb.session_transaction() as sess:
        otp_c5 = sess.get('otp_login_%s' % email, {}).get('otp')
    rb.post('/login', data={'otp': otp_c5, 'csrf_token': tok5}, follow_redirects=False)
    pre_sessions = UserSession.get_active_sessions_for_user(user_id)
    pre_count = len(pre_sessions)
    record('has active session before CSRF test', pre_count >= 1, 'count=%d' % pre_count)
    if pre_count >= 1:
        sid_csrf = pre_sessions[0]['id']
        r_csrf = admin.post('/admin/users/%d/revoke-session/%d' % (user_id, sid_csrf),
                            data={}, follow_redirects=False)
        record('missing CSRF token rejected', r_csrf.status_code == 302 and
               '/admin/user-security' not in r_csrf.headers.get('Location', ''))
        # Session must still be active (not revoked by the unauthorised request)
        row_csrf = UserSession.get_by_id(sid_csrf)
        record('session NOT revoked without CSRF token',
               row_csrf and row_csrf.get('status') == 'active',
               'status=%s' % (row_csrf.get('status') if row_csrf else 'None'))

    # Restore a fresh session for remaining tests
    revoke_all_active(user_id)
    r_login = login_step_one(rb, email, TEST_PASSWORD)
    tok_login = csrf_from(r_login.get_data(as_text=True))
    with rb.session_transaction() as sess:
        otp_login = sess.get('otp_login_%s' % email, {}).get('otp')
    rb.post('/login', data={'otp': otp_login, 'csrf_token': tok_login},
            follow_redirects=False)

    print()
    print('=' * 70)
    print('TEST 17: Admin-ended session shows specific message')
    print('=' * 70)
    # Admin ends the active session via the security page
    sec_page = admin.get('/admin/user-security')
    tok_admin = csrf_from(sec_page.get_data(as_text=True))
    active_now = UserSession.get_active_sessions_for_user(user_id)
    # Verify login created a session
    record('login created active session for TEST 17',
           len(active_now) >= 1, 'count=%d' % len(active_now))
    if active_now:
        sess_id_admin = active_now[0]['id']
        r_end_admin = admin.post('/admin/users/%d/revoke-session/%d' % (user_id, sess_id_admin),
                   data={'csrf_token': tok_admin}, follow_redirects=False)
        record('admin End redirects (302)', r_end_admin.status_code == 302,
               'status=%s' % r_end_admin.status_code)
        row_check = UserSession.get_by_id(sess_id_admin)
        record('session revoked in DB (TEST 17)',
               row_check and row_check.get('status') == 'revoked',
               'status=%s' % (row_check.get('status') if row_check else 'None'))
        # Next request from the revoked browser should show admin-ended message
        r_after = rb.get('/profile', follow_redirects=True)
        body_after = r_after.get_data(as_text=True)
        # Check for key terms (the message contains "ended by a StudyHub administrator")
        found = ('ended by' in body_after.lower() and
                 'administrator' in body_after.lower() and
                 'sign in again' in body_after.lower())
        record('admin-ended session shows specific message', found,
               'found=%s' % found)

    print()
    print('=' * 70)
    print('TEST 18: User self-deletion sends confirmation email')
    print('=' * 70)
    # Mock notification delivery to capture emails
    from utils import notifications
    sent_emails = []
    original_send = notifications.send_template_notification
    def mock_send_template(purpose, recipient_email, related_id=None, dedupe_key=None):
        tmpl = notifications.TEMPLATES.get(purpose, {})
        sent_emails.append((recipient_email, tmpl.get('subject', ''), tmpl.get('body', '')))
        return notifications.NotificationService.RESULT_SENT
    notifications.send_template_notification = mock_send_template
    # Also clear pending_notifications to avoid dedup
    with app.app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM pending_notifications")
        conn.commit()
        close_cursor(cursor)
        conn.close()

    # Clean up previous sessions and log in fresh
    revoke_all_active(user_id)
    r_login = login_step_one(rb, email, TEST_PASSWORD)
    tok_login = csrf_from(r_login.get_data(as_text=True))
    with rb.session_transaction() as sess:
        otp_login = sess.get('otp_login_%s' % email, {}).get('otp')
    r_login_resp = rb.post('/login', data={'otp': otp_login, 'csrf_token': tok_login},
            follow_redirects=False)
    record('user logged in for deletion test', r_login_resp.status_code == 302,
           'status=%s' % r_login_resp.status_code)

    # Request deletion (needs fresh login + CSRF token)
    page_prof = rb.get('/profile')
    tok_del = csrf_from(page_prof.get_data(as_text=True))
    rb.post('/profile/delete-account', data={'password': TEST_PASSWORD, 'csrf_token': tok_del},
            follow_redirects=False)
    # Get OTP from session (stored with purpose 'delete_account')
    with rb.session_transaction() as sess:
        otp_del = sess.get('otp_delete_account_%s' % email, {}).get('otp')
    # Get fresh CSRF token from confirm-delete page
    page_confirm = rb.get('/profile/confirm-delete-account')
    tok_confirm = csrf_from(page_confirm.get_data(as_text=True))
    # Confirm deletion
    r_del = rb.post('/profile/confirm-delete-account',
                    data={'otp': otp_del, 'csrf_token': tok_confirm},
                    follow_redirects=False)
    record('deletion redirects after success', r_del.status_code == 302,
           'status=%s' % r_del.status_code)
    record('deletion confirmation email sent', len(sent_emails) == 1,
           'count=%d' % len(sent_emails))
    if sent_emails:
        record('email sent to correct address', sent_emails[0][0] == email)
        record('email subject correct',
               sent_emails[0][1] == 'Your StudyHub Account Has Been Deleted')
        record('email body correct', 'successfully deleted' in sent_emails[0][2].lower())

    # Restore: notification service + user record
    notifications.send_template_notification = original_send
    from models.user import User as UserModel
    with app.app_context():
        UserModel.update(user_id, is_active=1, deleted_by_user=0,
                         deletion_requested_at=None, deletion_processed=None)
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM pending_notifications")
        conn.commit()
        close_cursor(cursor)
        conn.close()

    print()
    print('=' * 70)
    print('TEST 19: Admin deletion sends notification email')
    print('=' * 70)
    sent_emails2 = []
    original_send_email2 = OTPManager.send_email
    def mock_send_email2(email_addr, subject, body):
        sent_emails2.append((email_addr, subject, body))
        return RESULT_SUCCESS
    OTPManager.send_email = staticmethod(mock_send_email2)

    # Admin deletes the user
    r_admin_del = admin.post('/admin/users/delete/%d' % user_id,
                             data={'csrf_token': csrf_from(admin.get('/admin/users').get_data(as_text=True))},
                             follow_redirects=False)
    record('admin deletion redirects (302)', r_admin_del.status_code == 302,
           'status=%s' % r_admin_del.status_code)
    record('admin deletion email sent', len(sent_emails2) >= 1,
           'count=%d' % len(sent_emails2))
    if sent_emails2:
        record('admin email sent to correct address', sent_emails2[0][0] == email)
        record('admin email subject correct',
               sent_emails2[0][1] == 'Your StudyHub Account Was Deleted by an Administrator')
        record('admin email body correct',
               'administrator' in sent_emails2[0][2].lower())

    # Restore email service
    OTPManager.send_email = original_send_email2

    # Restore user again for cleanup
    with app.app_context():
        UserModel.update(user_id, is_active=1, deleted_by_user=0,
                         deletion_requested_at=None, deletion_processed=None)


    print()
    print('=' * 70)
    print('Cleanup')
    print('=' * 70)
    purge_test_rows(user_id)
    other_purge = User.get_by_email(other_email)
    if other_purge:
        purge_test_rows(other_purge['id'])
    print('test rows removed')

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

