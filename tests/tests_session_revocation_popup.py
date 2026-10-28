"""Pytest tests for the admin-revoked-session popup (in-browser notification).

These tests verify:
* ``/account/session-status`` returns the correct JSON for an active session.
* After an admin revokes a single session, the endpoint returns
  ``valid: false`` with ``reason: 'revoked_by_admin'``.
* The popup modal markup is present for logged-in regular users but absent for
  anonymous visitors and admin-only sessions.
* No email notification is sent for individual session revocation.
* The session is correctly invalidated (existing behaviour preserved).
* Admin users never see the popup modal.

Run:  python -m pytest tests/tests_session_revocation_popup.py -v
"""
import os
import re
import sys

import pytest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
# Ensure tests dir is importable for conftest (saves originals before
# tests_project.py overrides them at module import time).
sys.path.insert(0, os.path.join(root, 'tests'))
os.chdir(root)

from config import Config  # noqa: E402

# Use SQLite so tests are self-contained; .env is untouched.
Config.USE_POSTGRES = False
Config.USE_SQLITE = True

from app import create_app  # noqa: E402
from models.session import (  # noqa: E402
    AdminAuditLog,
    LoginHistory,
    UserSession,
    generate_session_token,
    hash_session_token,
)
from models.user import User  # noqa: E402
from utils.database import (  # noqa: E402
    get_connection,
    get_cursor,
    close_cursor,
    immediate_transaction,
)
from utils.otp import OTPManager, RESULT_SUCCESS  # noqa: E402

# ``tests_project.py`` overrides ``OTPManager.send_email`` / ``send_otp_email``
# / ``is_configured`` with always-success lambdas at module import time.  We do
# NOT restore the originals (which would attempt real SMTP); instead we rely on
# those overrides and only temporarily swap ``send_email`` when we need to
# capture sent emails (see the ``sent_emails`` fixture).
from conftest import (  # noqa: E402
    SAVED_SEND_EMAIL,
    SAVED_SEND_OTP_EMAIL,
    SAVED_IS_CONFIGURED,
    SAVED_MAX_SEND_ATTEMPTS,
    SAVED_RETRY_DELAY_SECONDS,
)

TEST_EMAIL = 'popup_test_user@example.com'
TEST_PASSWORD = 'TestPass123'

app = create_app()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _csrf_from(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else None


def _get_or_create_user(email, name='Popup Test User'):
    with app.app_context():
        u = User.get_by_email(email)
        if not u:
            User.create(name, email, TEST_PASSWORD)
            u = User.get_by_email(email)
        User.update(u['id'], is_active=1)
        return u


def _setup_test_user():
    user = _get_or_create_user(TEST_EMAIL)
    with app.app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (user['id'],))
        cursor.execute("DELETE FROM login_history WHERE user_id = ?", (user['id'],))
        cursor.execute(
            "DELETE FROM pending_notifications WHERE dedupe_key LIKE 'session_terminated%'"
        )
        conn.commit()
        close_cursor(cursor)
        conn.close()
    return user['id']


def _login_user(client, email, password):
    """Perform both steps of login (credentials + OTP) and return the logged-in client."""
    page = client.get('/login')
    token = _csrf_from(page.get_data(as_text=True))
    client.post('/login', data={
        'email': email, 'password': password, 'csrf_token': token,
    }, follow_redirects=False)

    with client.session_transaction() as sess:
        otp = sess.get('otp_login_%s' % email, {}).get('otp')

    page2 = client.get('/login')
    token2 = _csrf_from(page2.get_data(as_text=True))
    client.post('/login', data={'otp': otp, 'csrf_token': token2},
                follow_redirects=False)


def _login_admin(client):
    """Log in as the built-in admin account."""
    page = client.get('/admin/login')
    token = _csrf_from(page.get_data(as_text=True))
    client.post('/admin/login', data={
        'username': 'admin', 'password': 'admin@123', 'csrf_token': token,
    }, follow_redirects=False)


def _get_active_session_id(user_id):
    """Return the id of the user's single active session, or None."""
    sessions = UserSession.get_active_sessions_for_user(user_id)
    return sessions[0]['id'] if sessions else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_email_methods():
    """Ensure OTPManager email methods are mocked (not the real SMTP).

    ``tests_smtp_resilience.py`` has an autouse fixture that restores the
    *real* ``send_email`` after each of its tests.  Because pytest runs that
    module before this one, the real SMTP method would be in effect here —
    causing 10-second timeouts on every login.  This fixture re-applies the
    always-success stubs that ``tests_project.py`` set at import time.
    """
    OTPManager.send_email = staticmethod(lambda *a, **k: RESULT_SUCCESS)
    OTPManager.send_otp_email = staticmethod(lambda *a, **k: RESULT_SUCCESS)
    OTPManager.is_configured = staticmethod(lambda: True)
    yield


@pytest.fixture
def test_user():
    """Create or reuse the test user and return (user_id, email)."""
    uid = _setup_test_user()
    return uid, TEST_EMAIL


@pytest.fixture
def sent_emails():
    """Capture emails sent via OTPManager.send_email.

    ``_mock_email_methods`` (autouse) sets ``send_email`` to an
    always-success lambda.  This fixture temporarily replaces it with a
    capturing stub, then restores the lambda on teardown so that other
    tests are unaffected.
    """
    captured = []

    def capture(to_email, subject, body):
        captured.append((to_email, subject, body))
        return RESULT_SUCCESS

    original_send_email = OTPManager.send_email
    OTPManager.send_email = staticmethod(capture)
    yield captured
    OTPManager.send_email = staticmethod(original_send_email)


# ---------------------------------------------------------------------------
# Tests: session-status endpoint
# ---------------------------------------------------------------------------

def test_session_status_active(test_user):
    """A logged-in user with a valid session gets valid:true."""
    client = app.test_client()
    _login_user(client, TEST_EMAIL, TEST_PASSWORD)

    with client.session_transaction() as sess:
        assert 'session_token' in sess, "user should have a session token"

    r = client.get('/account/session-status', follow_redirects=False)
    assert r.status_code == 200
    data = r.get_json()
    assert data['valid'] is True
    assert data['reason'] == 'active'


def test_session_status_no_token_for_anonymous():
    """An anonymous visitor (no session_token) gets valid:true, no_session."""
    client = app.test_client()
    r = client.get('/account/session-status', follow_redirects=False)
    assert r.status_code == 200
    data = r.get_json()
    assert data['valid'] is True
    assert data['reason'] == 'no_session'


def test_session_status_revoked_by_admin(test_user):
    """After admin revokes the session, endpoint returns revoked_by_admin."""
    user_id, email = test_user

    # Browser client logs in
    browser = app.test_client()
    _login_user(browser, email, TEST_PASSWORD)

    with browser.session_transaction() as sess:
        token = sess.get('session_token')
    assert token is not None, "user should have a session token"

    # Find the active session and have admin revoke it
    sid = _get_active_session_id(user_id)
    assert sid is not None, "user should have an active session"

    admin = app.test_client()
    _login_admin(admin)
    page = admin.get('/admin/user-security')
    csrf = _csrf_from(page.get_data(as_text=True))
    r = admin.post('/admin/users/%d/revoke-session/%d' % (user_id, sid),
                    data={'csrf_token': csrf}, follow_redirects=False)
    assert r.status_code == 302

    # The session row should be revoked
    row = UserSession.get_by_id(sid)
    assert row is not None
    assert row.get('status') == 'revoked'
    assert row.get('ended_by_admin') == 1

    # The browser's session-status endpoint should now report revoked_by_admin
    r2 = browser.get('/account/session-status', follow_redirects=False)
    assert r2.status_code == 200
    data = r2.get_json()
    assert data['valid'] is False
    assert data['reason'] == 'revoked_by_admin'


def test_session_status_revoked_not_by_admin(test_user):
    """A self-revoked (non-admin) session returns reason revoked, not revoked_by_admin."""
    user_id, email = test_user

    browser = app.test_client()
    _login_user(browser, email, TEST_PASSWORD)

    sid = _get_active_session_id(user_id)
    assert sid is not None

    # Revoke the session directly (simulating user self-revocation or timeout)
    with immediate_transaction() as conn:
        UserSession.revoke(
            conn, sid,
            'Session ended by the user from the active sessions list',
            ended_by_admin=False,
        )

    r = browser.get('/account/session-status', follow_redirects=False)
    assert r.status_code == 200
    data = r.get_json()
    assert data['valid'] is False
    assert data['reason'] == 'revoked'


# ---------------------------------------------------------------------------
# Tests: popup modal in base template
# ---------------------------------------------------------------------------

def test_popup_modal_present_for_logged_in_user(test_user):
    """The popup modal markup is rendered for an authenticated regular user."""
    client = app.test_client()
    _login_user(client, TEST_EMAIL, TEST_PASSWORD)

    r = client.get('/profile', follow_redirects=False)
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    # The modal element must be present
    assert 'sessionRevokedModal' in html
    # The popup message text must be present
    assert 'Admin has ended your session. Please log in again.' in html
    # The acknowledge button must be present
    assert 'sessionRevokedAck' in html
    # The polling endpoint must be referenced
    assert '/account/session-status' in html


def test_popup_modal_absent_for_anonymous_user():
    """The popup modal markup is NOT rendered for anonymous visitors."""
    client = app.test_client()
    r = client.get('/login', follow_redirects=False)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'sessionRevokedModal' not in html


def test_popup_modal_absent_for_admin():
    """Admin sessions (admin_id, no session_token) don't get the popup."""
    client = app.test_client()
    _login_admin(client)

    r = client.get('/admin/dashboard', follow_redirects=False)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'sessionRevokedModal' not in html


# ---------------------------------------------------------------------------
# Tests: no email for individual revocation, email still sent for bulk
# ---------------------------------------------------------------------------

def test_no_email_on_individual_session_revocation(test_user, sent_emails):
    """Admin revoking a single session must NOT trigger an email."""
    user_id, email = test_user

    browser = app.test_client()
    _login_user(browser, email, TEST_PASSWORD)

    sid = _get_active_session_id(user_id)
    assert sid is not None

    admin = app.test_client()
    _login_admin(admin)
    page = admin.get('/admin/user-security')
    csrf = _csrf_from(page.get_data(as_text=True))

    sent_emails.clear()  # Clear any emails from admin login
    admin.post('/admin/users/%d/revoke-session/%d' % (user_id, sid),
               data={'csrf_token': csrf}, follow_redirects=False)

    assert len(sent_emails) == 0, \
        "Expected no email for individual revocation, got %d" % len(sent_emails)


def test_no_notification_persisted_for_individual_revocation(test_user):
    """Individual revocation should not create a pending_notifications row."""
    user_id, email = test_user

    # tests_project.py already mocks send_email/is_configured, so login
    # works without real SMTP. No additional mocking needed here.
    browser = app.test_client()
    _login_user(browser, email, TEST_PASSWORD)

    sid = _get_active_session_id(user_id)
    assert sid is not None

    admin = app.test_client()
    _login_admin(admin)
    page = admin.get('/admin/user-security')
    csrf = _csrf_from(page.get_data(as_text=True))
    admin.post('/admin/users/%d/revoke-session/%d' % (user_id, sid),
               data={'csrf_token': csrf}, follow_redirects=False)

    with app.app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute(
            "SELECT COUNT(*) AS total FROM pending_notifications "
            "WHERE dedupe_key = ?",
            ('session_terminated:%d' % sid,),
        )
        row = cursor.fetchone()
        close_cursor(cursor)
        conn.close()

    assert row['total'] == 0, \
        "Expected no notification row for individual revocation"


# ---------------------------------------------------------------------------
# Tests: session invalidation (existing behaviour preserved)
# ---------------------------------------------------------------------------

def test_revoked_session_blocked_from_protected_page(test_user):
    """After revocation, the affected user is redirected away from protected pages."""
    user_id, email = test_user

    browser = app.test_client()
    _login_user(browser, email, TEST_PASSWORD)

    sid = _get_active_session_id(user_id)
    assert sid is not None

    admin = app.test_client()
    _login_admin(admin)
    page = admin.get('/admin/user-security')
    csrf = _csrf_from(page.get_data(as_text=True))
    admin.post('/admin/users/%d/revoke-session/%d' % (user_id, sid),
               data={'csrf_token': csrf}, follow_redirects=False)

    # The revoked session should be blocked
    r = browser.get('/profile', follow_redirects=False)
    assert r.status_code in (301, 302), \
        "Revoked session should be redirected, got %d" % r.status_code

    # The session cookie should be cleared
    with browser.session_transaction() as sess:
        assert 'session_token' not in sess, \
            "session_token should be cleared after revocation"


def test_other_user_unaffected_by_revocation():
    """Revoking user A's session does not affect user B's access."""
    other_email = 'popup_other_user@example.com'

    # Create and clean up both users
    user_a = _get_or_create_user(TEST_EMAIL)
    other_user = _get_or_create_user(other_email, name='Popup Other')

    # Clean up sessions for both users (outside any open connection)
    with app.app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM user_sessions WHERE user_id IN (?, ?)",
                       (user_a['id'], other_user['id']))
        cursor.execute("DELETE FROM login_history WHERE user_id IN (?, ?)",
                       (user_a['id'], other_user['id']))
        conn.commit()
        close_cursor(cursor)
        conn.close()

    admin = app.test_client()
    _login_admin(admin)

    # User A logs in
    browser_a = app.test_client()
    _login_user(browser_a, TEST_EMAIL, TEST_PASSWORD)

    sid_a = _get_active_session_id(user_a['id'])
    assert sid_a is not None

    # User B logs in
    browser_b = app.test_client()
    _login_user(browser_b, other_email, TEST_PASSWORD)

    # Admin revokes user A's session
    page = admin.get('/admin/user-security')
    csrf = _csrf_from(page.get_data(as_text=True))
    admin.post('/admin/users/%d/revoke-session/%d' % (user_a['id'], sid_a),
               data={'csrf_token': csrf}, follow_redirects=False)

    # User A is blocked
    r_a = browser_a.get('/profile', follow_redirects=False)
    assert r_a.status_code in (301, 302), "User A should be blocked"

    # User B is NOT blocked — their session is still active
    with browser_b.session_transaction() as sess:
        assert 'session_token' in sess, "User B should have a session token"
    r_b = browser_b.get('/profile', follow_redirects=False)
    assert r_b.status_code == 200, \
        "User B should still have access, got %d" % r_b.status_code

    # Cleanup user B's session
    with immediate_transaction() as conn:
        UserSession.revoke_all_for_user(
            conn, other_user['id'],
            'test cleanup',
        )


def test_session_status_admin_user_not_affected():
    """Admin sessions don't have session_token, so status endpoint is harmless."""
    client = app.test_client()
    _login_admin(client)

    r = client.get('/account/session-status', follow_redirects=False)
    assert r.status_code == 200
    data = r.get_json()
    # Admins use admin_id, not session_token — so no_session
    assert data['valid'] is True
    assert data['reason'] == 'no_session'


# ---------------------------------------------------------------------------
# Tests: revoke-all still sends email (preserved behaviour)
# ---------------------------------------------------------------------------

def test_revoke_all_sends_no_email(test_user, sent_emails):
    """Bulk session revocation (revoke-all) must NOT send an email either.

    Per requirements, ALL session revocation (individual and bulk) uses the
    in-browser popup instead of email notification.  OTP, password-reset,
    and account-deletion emails are unaffected.
    """
    user_id, email = test_user

    # Ensure no stale dedupe rows from previous test runs
    with app.app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM pending_notifications WHERE dedupe_key LIKE 'session_terminated%'")
        conn.commit()
        close_cursor(cursor)
        conn.close()

    browser = app.test_client()
    _login_user(browser, email, TEST_PASSWORD)

    admin = app.test_client()
    _login_admin(admin)
    page = admin.get('/admin/user-security')
    csrf = _csrf_from(page.get_data(as_text=True))

    sent_emails.clear()
    admin.post('/admin/users/%d/revoke-all-sessions' % user_id,
               data={'csrf_token': csrf}, follow_redirects=False)

    # No email at all for bulk session revocation
    assert len(sent_emails) == 0, \
        "Expected no email for bulk revocation, got %d" % len(sent_emails)

    # Also verify no notification row was persisted
    with app.app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute(
            "SELECT COUNT(*) AS total FROM pending_notifications "
            "WHERE dedupe_key LIKE 'session_terminated_all:%d'" % user_id)
        row = cursor.fetchone()
        close_cursor(cursor)
        conn.close()
    assert row['total'] == 0, \
        "Expected no notification row for bulk revocation"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
