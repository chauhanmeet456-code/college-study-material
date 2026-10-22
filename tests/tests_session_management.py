"""End-to-end tests for the multi-device session management system.

Run:  python tests_session_management.py

Each test drives the real Flask application through its test client, so the
checks cover the actual routing, middleware and SQLite storage.
"""
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app  # noqa: E402
from models.session import (  # noqa: E402
    FailedLoginAttempt,
    LoginHistory,
    UserSession,
    hash_session_token,
)

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
        import sqlite3
        from config import Config
        conn = sqlite3.connect(Config.SQLITE_DB)
        conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user['id'],))
        conn.execute("DELETE FROM login_history WHERE user_id = ?", (user['id'],))
        conn.execute("DELETE FROM failed_login_attempts WHERE user_id = ?", (user['id'],))
        conn.commit()
        conn.close()
        return user['id'], TEST_EMAIL



from utils.database import immediate_transaction  # noqa: E402


def revoke_all_active(user_id, reason='test cleanup'):
    """Revoke every active session for a user (test helper)."""
    with immediate_transaction() as conn:
        UserSession.revoke_all_for_user(conn, user_id, reason)


def purge_test_rows(user_id):
    with create_app().app_context():
        import sqlite3
        from config import Config
        conn = sqlite3.connect(Config.SQLITE_DB)
        conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM login_history WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM failed_login_attempts WHERE user_id = ?", (user_id,))
        conn.commit()
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
    from utils.otp import OTPManager
    OTPManager.send_email = staticmethod(lambda *a, **k: True)
    OTPManager.send_otp_email = staticmethod(lambda *a, **k: True)

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
           'currently logged in on another device' in body2.lower(),
           'status=%s' % r2.status_code)
    record('three options offered',
           all(k in body2 for k in ['End Other Session', 'Keep Existing Session', 'Cancel']))

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
    print('TEST 4: "Keep Existing Session" cancels the new login')
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
    print('TEST 5: "End Other Session & Login Here" replaces the session')
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

