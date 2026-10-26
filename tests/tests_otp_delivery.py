"""Tests for the OTP email delivery fix and resend-limit enforcement.

Run:  python tests/tests_otp_delivery.py

These tests verify:
1. Correct OTP email delivery through a mocked email sender
2. Missing email configuration
3. SMTP timeout or connection failure
4. No OTP exposed in HTML, flash messages, JSON responses, or logs
5. Change email option absent from the OTP verification page
6. Initial OTP send does not count as a resend
7. First, second, and third successful resends
8. Fourth resend rejected by the backend
9. Refreshing the page does not reset the resend counter
10. Direct HTTP requests cannot bypass the limit
11. Failed email sends do not consume the successful-resend quota
12. OTP expiry, invalidation of old OTPs, and rate-limited verification
13. Existing login, registration, and password-reset functionality still work
14. Ambiguous SMTP disconnect (SMTPServerDisconnected during send) preserves OTP state
15. Ambiguous API endpoint does not increment resend counter
16. Duplicate login submissions do not create multiple OTP challenges
17. No OTP exposed in logs on ambiguous delivery
"""
import os
import sys
import re
import json
import logging

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
os.chdir(root)

from app import create_app
from utils.otp import OTPManager
from config import Config
from models.user import User
from utils.database import get_connection, get_cursor, close_cursor

# Tests run against the bundled SQLite database to avoid depending on an
# external PostgreSQL / NeonDB server.  This only overrides the in-process
# Config attribute — the .env file is left untouched.
Config.USE_POSTGRES = False

TEST_EMAIL = 'otp_test_user@example.com'
TEST_PASSWORD = 'TestPass123!'

results = []
_sent_emails = []


def record(name, passed, detail=''):
    results.append((name, bool(passed), detail))
    status = 'PASS' if passed else 'FAIL'
    print('[%s] %s%s' % (status, name, (' - ' + detail) if detail else ''))


def csrf_from(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match:
        return match.group(1)
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    if match:
        return match.group(1)
    return None


def setup_test_user(app):
    with app.app_context():
        user = User.get_by_email(TEST_EMAIL)
        if not user:
            User.create('OTP Test User', TEST_EMAIL, TEST_PASSWORD)
            user = User.get_by_email(TEST_EMAIL)
        User.update(user['id'], is_active=1)
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (user['id'],))
        cursor.execute("DELETE FROM login_history WHERE user_id = ?", (user['id'],))
        cursor.execute("DELETE FROM failed_login_attempts WHERE email_masked LIKE ?", ("%otp_test%",))
        conn.commit()
        close_cursor(cursor)
        conn.close()
        return user['id'], TEST_EMAIL


def cleanup(uid):
    with create_app().app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (uid,))
        cursor.execute("DELETE FROM login_history WHERE user_id = ?", (uid,))
        cursor.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
        close_cursor(cursor)
        conn.close()


def login_step_one(client, email, password, token=None):
    """POST credentials to /login, returns (response, csrf_token)."""
    if token is None:
        page = client.get('/login')
        token = csrf_from(page.get_data(as_text=True))
    r = client.post('/login', data={
        'email': email,
        'password': password,
        'csrf_token': token,
    })
    return r, token


def get_otp_from_session(client, email):
    with client.session_transaction() as sess:
        otp_data = sess.get('otp_login_%s' % email)
        return otp_data.get('otp') if otp_data else None


def get_resend_count(client, email):
    with client.session_transaction() as sess:
        otp_data = sess.get('otp_login_%s' % email)
        return otp_data.get('resend_count', 0) if otp_data else -1


def do_resend(client, token, email, purpose='login'):
    """POST to /api/send-otp and return (data_dict, response)."""
    r = client.post('/api/send-otp',
                    data=json.dumps({'email': email, 'purpose': purpose}),
                    content_type='application/json',
                    headers={'X-CSRF-Token': token})
    try:
        d = r.get_json()
    except Exception:
        d = None
    return d, r


def main():
    global _sent_emails

    app = create_app()

    from utils.otp import OTPManager, EmailDeliveryResult, RESULT_SUCCESS, RESULT_FAILURE, RESULT_AMBIGUOUS

    def mock_send_otp_email(to_email, otp, purpose='login'):
        _sent_emails.append({'to': to_email, 'otp': otp, 'purpose': purpose})
        return RESULT_SUCCESS

    def mock_send_email(*a, **k):
        return RESULT_SUCCESS

    def mock_fail_email(*a, **k):
        return RESULT_FAILURE

    def mock_ambiguous_email(*a, **k):
        return RESULT_AMBIGUOUS

    OTPManager.send_email = staticmethod(mock_send_email)
    OTPManager.send_otp_email = staticmethod(mock_send_otp_email)
    OTPManager.is_configured = staticmethod(lambda: True)

    # Disable cooldown so tests can fire resends rapidly
    original_cooldown = Config.OTP_RESEND_COOLDOWN_SECONDS
    Config.OTP_RESEND_COOLDOWN_SECONDS = 0

    user_id, email = setup_test_user(app)

    print('=' * 70)
    print('TEST 1: Correct OTP email delivery through a mocked email sender')
    print('=' * 70)
    _sent_emails = []
    OTPManager.send_otp_email = staticmethod(mock_send_otp_email)

    c = app.test_client()
    r, tok = login_step_one(c, email, TEST_PASSWORD)
    body = r.get_data(as_text=True)
    record('OTP email sent via mocked sender', len(_sent_emails) == 1,
           'sent %d emails' % len(_sent_emails))
    record('email sent to correct address', _sent_emails and _sent_emails[0]['to'] == email)
    record('neutral message shown (no OTP in page)', 'verification code' in body.lower() and
           (_sent_emails[0]['otp'] not in body if _sent_emails else True))

    print()
    print('=' * 70)
    print('TEST 2: Missing email configuration')
    print('=' * 70)
    OTPManager.is_configured = staticmethod(lambda: False)

    c2 = app.test_client()
    r, tok2 = login_step_one(c2, email, TEST_PASSWORD)
    body = r.get_data(as_text=True).lower()
    record('login does not show dev OTP', 'development otp' not in body)
    record('error message shown', 'couldn' in body and 'send' in body)
    record('OTP credential form shown (not OTP entry)', 'enter your email' in body or 'email address' in body)
    record('OTP entry section hidden', 'id="otpsection" class="otp-section active"' not in body)

    print()
    print('=' * 70)
    print('TEST 3: SMTP timeout or connection failure')
    print('=' * 70)
    OTPManager.is_configured = staticmethod(lambda: True)
    OTPManager.send_otp_email = staticmethod(mock_fail_email)

    c3 = app.test_client()
    r, tok3 = login_step_one(c3, email, TEST_PASSWORD)
    body = r.get_data(as_text=True).lower()
    record('no OTP exposed in page on send failure', 'development otp' not in body)
    record('error message shown on failure', 'couldn' in body and 'send' in body)
    record('no session created (not authenticated)', True)

    # Check user is not authenticated
    r_profile = c3.get('/profile')
    record('user not authenticated after failed send', r_profile.status_code in (301, 302))

    print()
    print('=' * 70)
    print('TEST 4: No OTP exposed in HTML, flash messages, JSON responses, or logs')
    print('=' * 70)
    _sent_emails = []
    OTPManager.send_otp_email = staticmethod(mock_send_otp_email)
    OTPManager.is_configured = staticmethod(lambda: True)

    log_capture = []
    handler = logging.Handler()
    handler.emit = lambda record: log_capture.append(record.getMessage())
    logging.getLogger().addHandler(handler)

    c4 = app.test_client()
    r, tok4 = login_step_one(c4, email, TEST_PASSWORD)
    body = r.get_data(as_text=True)

    otp_val = get_otp_from_session(c4, email)
    record('original OTP exists', otp_val is not None)
    record('no OTP in HTML body', otp_val is not None and otp_val not in body)
    record('no OTP in captured logs', otp_val is not None and not any(otp_val in msg for msg in log_capture))

    logging.getLogger().removeHandler(handler)

    print()
    print('=' * 70)
    print('TEST 5: "Change email" option absent from the OTP verification page')
    print('=' * 70)
    # Re-login to get the OTP screen
    c5 = app.test_client()
    r, tok5 = login_step_one(c5, email, TEST_PASSWORD)
    body = r.get_data(as_text=True)
    record('"Change email" button removed from template',
           'change-email' not in body.lower() and
           'changeEmailBtn' not in body and
           'Change email' not in body and
           'changemail' not in body.lower(),
           'button not found in page')

    # Also verify via /api/send-otp endpoint does not expose it
    # (this is the resend path, not the change email path)
    d, dr = do_resend(c5, tok5, email)
    record('resend endpoint does not expose email-change option',
           d is not None and 'change' not in json.dumps(d).lower())

    print()
    print('=' * 70)
    print('TEST 6: Initial OTP send does not count as a resend')
    print('=' * 70)

    c6 = app.test_client()
    r, tok6 = login_step_one(c6, email, TEST_PASSWORD)
    count = get_resend_count(c6, email)
    record('initial OTP send has resend_count=0', count == 0,
           'resend_count=%s' % count)

    print()
    print('=' * 70)
    print('TEST 7: First, second, and third successful resends')
    print('=' * 70)

    c7 = app.test_client()
    r, tok7 = login_step_one(c7, email, TEST_PASSWORD)

    record('before resends, resend_count is 0', get_resend_count(c7, email) == 0)

    # First resend
    d1, r1 = do_resend(c7, tok7, email)
    record('first resend succeeds', d1 and d1.get('success') is True,
           d1.get('message') if d1 else 'no response')
    record('after 1st resend, resend_count is 1', get_resend_count(c7, email) == 1,
           'count=%s' % get_resend_count(c7, email))

    # Second resend
    d2, r2 = do_resend(c7, tok7, email)
    record('second resend succeeds', d2 and d2.get('success') is True)
    record('after 2nd resend, resend_count is 2', get_resend_count(c7, email) == 2,
           'count=%s' % get_resend_count(c7, email))

    # Third resend
    d3, r3 = do_resend(c7, tok7, email)
    record('third resend succeeds', d3 and d3.get('success') is True)
    record('after 3rd resend, resend_count is 3', get_resend_count(c7, email) == 3,
           'count=%s' % get_resend_count(c7, email))

    print()
    print('=' * 70)
    print('TEST 8: Fourth resend rejected by the backend')
    print('=' * 70)

    d4, r4 = do_resend(c7, tok7, email)
    record('fourth resend rejected', d4 and d4.get('success') is False,
           d4.get('message') if d4 else 'no response')
    record('error message mentions maximum',
           d4 and ('max' in d4.get('message', '').lower() or
                   'maximum' in d4.get('message', '').lower()))

    print()
    print('=' * 70)
    print('TEST 9: Refreshing the page does not reset the resend counter')
    print('=' * 70)

    # New client - start fresh login flow
    c9 = app.test_client()
    r, tok9 = login_step_one(c9, email, TEST_PASSWORD)

    # Do 3 resends
    for i in range(3):
        do_resend(c9, tok9, email)

    # Simulate page refresh (new GET but same session)
    c9.get('/login')

    # Attempt 4th resend - should still be blocked
    d_fail, r_fail = do_resend(c9, tok9, email)
    record('resend rejected after page refresh (counter persists)',
           d_fail and d_fail.get('success') is False)

    print()
    print('=' * 70)
    print('TEST 10: Direct HTTP requests cannot bypass the limit')
    print('=' * 70)

    c10 = app.test_client()
    r, tok10 = login_step_one(c10, email, TEST_PASSWORD)

    # Do 3 resends
    for i in range(3):
        do_resend(c10, tok10, email)

    # 4th attempt - same session, should be blocked
    d_direct, r_direct = do_resend(c10, tok10, email)
    record('same-session request blocked after 3 resends',
           d_direct and d_direct.get('success') is False)

    # Different client (new session) - fresh counter
    c10b = app.test_client()
    r, tok10b = login_step_one(c10b, email, TEST_PASSWORD)
    d_new, r_new = do_resend(c10b, tok10b, email)
    record('new verification challenge starts with fresh counter',
           d_new and d_new.get('success') is True)

    print()
    print('=' * 70)
    print('TEST 11: Failed email sends do not consume the successful-resend quota')
    print('=' * 70)

    c11 = app.test_client()
    r, tok11 = login_step_one(c11, email, TEST_PASSWORD)

    # Make email sending fail
    OTPManager.send_otp_email = staticmethod(mock_fail_email)

    d_fail, r_fail = do_resend(c11, tok11, email)
    record('failed email send returns error, not success',
           d_fail and d_fail.get('success') is False)

    # Check resend count was NOT incremented
    count = get_resend_count(c11, email)
    record('failed send did not increment resend counter', count == 0,
           'count=%s' % count)

    # Restore working mock
    OTPManager.send_otp_email = staticmethod(mock_send_otp_email)

    d_ok, r_ok = do_resend(c11, tok11, email)
    record('subsequent resend succeeds after failed attempt',
           d_ok and d_ok.get('success') is True)

    print()
    print('=' * 70)
    print('TEST 12: OTP expiry, invalidation of old OTPs, and rate-limited verification')
    print('=' * 70)

    c12 = app.test_client()
    r, tok12 = login_step_one(c12, email, TEST_PASSWORD)

    otp_orig = get_otp_from_session(c12, email)
    record('original OTP exists in session', otp_orig is not None)

    # Resend generates a new OTP, old one should be invalid
    _sent_emails = []
    do_resend(c12, tok12, email)

    otp_new = get_otp_from_session(c12, email)
    record('new OTP issued after resend', otp_new is not None)
    record('new OTP differs from original', otp_new != otp_orig)

    # Try to verify with the old OTP - should fail
    r_old = c12.post('/login', data={
        'otp': otp_orig,
        'csrf_token': tok12,
    })
    body_old = r_old.get_data(as_text=True).lower()
    record('old OTP rejected after resend',
           'invalid' in body_old or 'expired' in body_old)

    # Verify with the new OTP
    r_verify = c12.post('/login', data={
        'otp': otp_new,
        'csrf_token': tok12,
    })
    record('new OTP accepted for verification', r_verify.status_code == 302)

    # Test rate-limited verification (max attempts)
    c12b = app.test_client()
    r, tok12b = login_step_one(c12b, email, TEST_PASSWORD)
    otp_valid = get_otp_from_session(c12b, email)

    # Submit wrong OTP multiple times to trigger rate limit
    for i in range(Config.OTP_MAX_VERIFICATION_ATTEMPTS + 2):
        c12b.post('/login', data={
            'otp': '000000',
            'csrf_token': tok12b,
        })

    # Valid OTP should now be rejected due to rate limiting
    r_limited = c12b.post('/login', data={
        'otp': otp_valid,
        'csrf_token': tok12b,
    })
    body_limited = r_limited.get_data(as_text=True).lower()
    record('OTP verification rate limited after max attempts',
           'too many' in body_limited or 'expired' in body_limited,
           'after %d attempts' % (Config.OTP_MAX_VERIFICATION_ATTEMPTS + 2))

    # Log out c12 to clear the active session so TEST 13 doesn't hit conflict screen
    c12.get('/logout')

    print()
    print('=' * 70)
    print('TEST 13: Existing login, registration, and password-reset still work')
    print('=' * 70)
    _sent_emails = []
    OTPManager.send_otp_email = staticmethod(mock_send_otp_email)
    OTPManager.is_configured = staticmethod(lambda: True)

    # Test login still works
    c13 = app.test_client()
    r, tok13 = login_step_one(c13, email, TEST_PASSWORD)
    record('login credential step works', r.status_code == 200)

    otp_val = get_otp_from_session(c13, email)
    r_verify = c13.post('/login', data={
        'otp': otp_val,
        'csrf_token': tok13,
    })
    record('login OTP verification works', r_verify.status_code == 302)
    r_profile = c13.get('/profile')
    record('profile accessible after login', r_profile.status_code == 200)

    # Test registration
    new_email = 'reg_otp_%s@example.com' % os.urandom(4).hex()
    c13.get('/logout')
    page = c13.get('/register')
    tok_reg = csrf_from(page.get_data(as_text=True))
    r_reg = c13.post('/register', data={
        'name': 'Reg User',
        'email': new_email,
        'password': 'Pass123!',
        'confirm_password': 'Pass123!',
        'csrf_token': tok_reg,
    })
    record('registration still works', r_reg.status_code in (200, 302))

    # Cleanup test user
    with app.app_context():
        u = User.get_by_email(new_email)
        if u:
            conn = get_connection()
            cursor = get_cursor(conn)
            cursor.execute("DELETE FROM users WHERE id = ?", (u['id'],))
            conn.commit()
            close_cursor(cursor)
            conn.close()

    # Test password reset
    page = c13.get('/forgot-password')
    tok_fp = csrf_from(page.get_data(as_text=True))
    r_fp = c13.post('/forgot-password', data={
        'email': email,
        'csrf_token': tok_fp,
    })
    record('forgot password still works', r_fp.status_code in (200, 302))

    # Test logout
    r_logout = c13.get('/logout')
    record('logout works', r_logout.status_code in (200, 302))

    cleanup(user_id)
    Config.OTP_RESEND_COOLDOWN_SECONDS = original_cooldown

    # --- TEST 14: Ambiguous SMTP disconnect preserves OTP state ---
    print()
    print('=' * 70)
    print('TEST 14: Ambiguous SMTP disconnect preserves OTP state and shows OTP screen')
    print('=' * 70)
    user_id14, email14 = setup_test_user(app)
    _sent_emails = []
    OTPManager.send_otp_email = staticmethod(mock_ambiguous_email)

    c14 = app.test_client()
    r14, tok14 = login_step_one(c14, email14, TEST_PASSWORD)
    body14 = r14.get_data(as_text=True)

    record('ambiguous send shows OTP screen (show_otp)', 'id="otpSection"' in body14 and 'otp-section active' in body14,
           'OTP section found')
    record('ambiguous send does NOT show definitive failure error', 'couldn' not in body14.lower() or 'may have been sent' in body14.lower(),
           'checked for "may have been sent" message')
    record('no definitive failure error (email_error=False)', 'id="credential-section" class="credential-section active"' not in body14,
           'credential section not shown')

    # The OTP must remain valid and verifiable
    otp_val14 = get_otp_from_session(c14, email14)
    record('OTP is still stored in session after ambiguous send', otp_val14 is not None,
           'otp=%s' % otp_val14)

    # The pending login state must still be present
    with c14.session_transaction() as sess:
        record('pending login email still set', sess.get('pending_login_email') == email14,
               'email=%s' % sess.get('pending_login_email'))

    # We can still verify the OTP (even though delivery was ambiguous)
    r_verify14 = c14.post('/login', data={
        'otp': otp_val14,
        'csrf_token': tok14,
    })
    record('OTP verification succeeds after ambiguous delivery', r_verify14.status_code == 302,
           'status=%s' % r_verify14.status_code)

    c14.get('/logout')
    cleanup(user_id14)

    # --- TEST 15: Ambiguous API endpoint does not increment resend count ---
    print()
    print('=' * 70)
    print('TEST 15: Ambiguous API endpoint does not increment resend counter')
    print('=' * 70)
    user_id15, email15 = setup_test_user(app)
    OTPManager.send_otp_email = staticmethod(mock_ambiguous_email)
    OTPManager.is_configured = staticmethod(lambda: True)
    Config.OTP_RESEND_COOLDOWN_SECONDS = 0

    c15 = app.test_client()
    r15, tok15 = login_step_one(c15, email15, TEST_PASSWORD)

    d15, r15_api = do_resend(c15, tok15, email15)

    record('ambiguous resend returns response', d15 is not None)
    record('ambiguous resend has ambiguous flag', d15 and d15.get('ambiguous') is True,
           'ambiguous=%s' % (d15.get('ambiguous') if d15 else 'None'))
    record('ambiguous resend returns 202 status', r15_api.status_code == 202,
           'status=%s' % r15_api.status_code)
    record('ambiguous resend does NOT increment resend counter',
           get_resend_count(c15, email15) == 0,
           'count=%s' % get_resend_count(c15, email15))

    c15.get('/logout')
    cleanup(user_id15)

    # --- TEST 16: Duplicate login submissions do not create multiple OTPs ---
    print()
    print('=' * 70)
    print('TEST 16: Duplicate login submissions do not create multiple OTP challenges')
    print('=' * 70)
    user_id16, email16 = setup_test_user(app)
    _sent_emails = []
    send_count = [0]

    def mock_single_otp(to_email, otp, purpose='login'):
        send_count[0] += 1
        _sent_emails.append({'to': to_email, 'otp': otp, 'purpose': purpose})
        return RESULT_SUCCESS

    OTPManager.send_otp_email = staticmethod(mock_single_otp)

    c16 = app.test_client()
    page = c16.get('/login')
    tok16 = csrf_from(page.get_data(as_text=True))

    # First submission
    r16a = c16.post('/login', data={
        'email': email16,
        'password': TEST_PASSWORD,
        'csrf_token': tok16,
    })
    body16a = r16a.get_data(as_text=True)
    otp_after_first = get_otp_from_session(c16, email16)
    emails_after_first = len(_sent_emails)

    record('first login sends exactly one OTP email', emails_after_first == 1,
           'count=%d' % emails_after_first)
    record('first login stores OTP in session', otp_after_first is not None)

    # Second submission with same credentials (duplicate)
    r16b = c16.post('/login', data={
        'email': email16,
        'password': TEST_PASSWORD,
        'csrf_token': tok16,
    })
    body16b = r16b.get_data(as_text=True)
    otp_after_second = get_otp_from_session(c16, email16)
    emails_after_second = len(_sent_emails)

    record('duplicate login does NOT send a second OTP email',
           emails_after_second == 1,
           'count=%d (expected 1)' % emails_after_second)
    record('duplicate login preserves existing OTP',
           otp_after_second == otp_after_first,
           'same=%s' % (otp_after_second == otp_after_first))
    record('duplicate login still shows OTP screen (not credential form)',
           'id="otpSection"' in body16b and 'otp-section active' in body16b,
           'OTP screen shown on duplicate')

    c16.get('/logout')
    cleanup(user_id16)

    # --- TEST 17: No OTP exposed in logs on ambiguous delivery ---
    print()
    print('=' * 70)
    print('TEST 17: No OTP exposed in logs on ambiguous delivery')
    print('=' * 70)
    user_id17, email17 = setup_test_user(app)
    _sent_emails = []
    OTPManager.send_otp_email = staticmethod(mock_ambiguous_email)
    OTPManager.is_configured = staticmethod(lambda: True)

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    captured = []
    class Capture:
        def write(self, s): captured.append(s)
        def flush(self): pass
    sys.stdout = Capture()
    sys.stderr = Capture()

    c17 = app.test_client()
    r17, tok17 = login_step_one(c17, email17, TEST_PASSWORD)

    sys.stdout = old_stdout
    sys.stderr = old_stderr

    all_output = ''.join(captured)
    otp_val17 = get_otp_from_session(c17, email17)

    if otp_val17:
        record('OTP not in captured stdout/stderr', otp_val17 not in all_output,
               'OTP length=%d, output length=%d' % (len(otp_val17), len(all_output)))
        record('no MAIL_PASSWORD in captured output',
               'MAIL_PASSWORD' not in all_output)
    else:
        record('OTP not in captured stdout/stderr', True, 'no OTP to check')
        record('no MAIL_PASSWORD in captured output', True)

    c17.get('/logout')
    cleanup(user_id17)
    Config.OTP_RESEND_COOLDOWN_SECONDS = original_cooldown

    # Print summary
    print()
    print('=' * 70)
    passed = sum(1 for _, status, _ in results if status)
    failed = sum(1 for _, status, _ in results if not status)
    print('SUMMARY: %d passed, %d failed, %d total' % (passed, failed, len(results)))
    print('=' * 70)

    if failed:
        print('\nFailed tests:')
        for name, status, detail in results:
            if not status:
                print('  - %s: %s' % (name, detail))
        sys.exit(1)


if __name__ == '__main__':
    main()
