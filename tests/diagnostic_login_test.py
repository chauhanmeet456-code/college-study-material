"""Diagnostic test: reproduce the login redirect-back-to-login issue.

This script tests the login flow with the ACTUAL .env configuration
(USE_POSTGRES=True because DATABASE_URL is set) to confirm that the
login silently fails because the user exists in SQLite but not PostgreSQL.

It then tests the same flow with USE_POSTGRES forced to False to confirm
that the login succeeds when using SQLite.
"""
import os
import sys
import re

root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, root)
os.chdir(root)

from config import Config


def csrf_from(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match:
        return match.group(1)
    return None


def run_login_test(use_postgres, label):
    """Run a full login cycle and report each step's HTTP outcome."""
    Config.USE_POSTGRES = use_postgres

    from app import create_app
    app = create_app()

    # Mock email sending so we can test the OTP flow without SMTP
    from utils.otp import OTPManager, RESULT_SUCCESS
    OTPManager.send_email = staticmethod(lambda *a, **k: RESULT_SUCCESS)
    OTPManager.send_otp_email = staticmethod(lambda *a, **k: RESULT_SUCCESS)
    OTPManager.is_configured = staticmethod(lambda: True)

    TEST_EMAIL = 'testsuite_user@example.com'
    TEST_PASSWORD = 'TestPass123!'

    # Ensure the test user exists (in the active database)
    with app.app_context():
        from models.user import User
        user = User.get_by_email(TEST_EMAIL)
        if not user:
            User.create('Test Suite User', TEST_EMAIL, TEST_PASSWORD)
            user = User.get_by_email(TEST_EMAIL)
        if user:
            User.update(user['id'], is_active=1, deleted_by_user=0)
        else:
            print('  [WARN] Could not create test user')

    client = app.test_client()

    print(f"\n--- {label} ---")
    print(f"Config: USE_POSTGRES={Config.USE_POSTGRES}, USE_SQLITE={Config.USE_SQLITE}")

    # Step 1: GET /login (get CSRF token)
    r = client.get('/login')
    csrf = csrf_from(r.get_data(as_text=True))
    print(f"  GET /login -> {r.status_code}, CSRF token present: {csrf is not None}")

    # Step 2: POST /login with credentials
    r = client.post('/login', data={
        'email': TEST_EMAIL,
        'password': TEST_PASSWORD,
        'csrf_token': csrf,
    }, follow_redirects=False)
    body = r.get_data(as_text=True)
    print(f"  POST /login (credentials) -> {r.status_code}")
    print(f"    Location: {r.headers.get('Location', 'N/A')}")
    print(f"    OTP form shown: {'Enter OTP' in body}")
    print(f"    'Invalid email or password': {'Invalid email or password' in body}")

    # If credentials failed (302 redirect to /login), we're done
    if r.status_code == 302:
        print(f"  *** LOGIN FAILED at credential step (302 -> {r.headers.get('Location')})")
        return False

    # Step 3: Get OTP from session and submit
    with client.session_transaction() as sess:
        otp_data = sess.get('otp_login_%s' % TEST_EMAIL, {})
        otp = otp_data.get('otp') if otp_data else None
        pending_email = sess.get('pending_login_email')
    print(f"  OTP in session: {otp is not None}, pending_email: {pending_email}")

    if not otp:
        print("  *** No OTP found in session - credential step may have failed silently")
        return False

    # Get fresh CSRF token (may have changed)
    csrf = csrf_from(body)

    # Step 4: POST /login with OTP
    r = client.post('/login', data={
        'otp': otp,
        'csrf_token': csrf,
    }, follow_redirects=False)
    body = r.get_data(as_text=True)
    print(f"  POST /login (OTP) -> {r.status_code}")
    print(f"    Location: {r.headers.get('Location', 'N/A')}")
    print(f"    'Something went wrong': {'Something went wrong' in body}")
    print(f"    'Session expired': {'Session expired' in body}")

    if r.status_code == 302:
        loc = r.headers.get('Location', '')
        print(f"    Redirect to: {loc}")

    # Step 5: Check session state
    with client.session_transaction() as sess:
        has_session_token = 'session_token' in sess
        has_user_id = 'user_id' in sess
    print(f"  Session has session_token: {has_session_token}")
    print(f"  Session has user_id: {has_user_id}")

    success = has_session_token and has_user_id
    print(f"  *** LOGIN RESULT: {'SUCCESS' if success else 'FAILURE'}")
    return success


def main():
    print("=" * 70)
    print("DIAGNOSTIC: Login flow with production config (USE_POSTGRES=True)")
    print("=" * 70)
    prod_result = run_login_test(use_postgres=True, label="PROD CONFIG (PostgreSQL)")

    print("\n" + "=" * 70)
    print("DIAGNOSTIC: Login flow with SQLite override (USE_POSTGRES=False)")
    print("=" * 70)
    sqlite_result = run_login_test(use_postgres=False, label="SQLITE OVERRIDE")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  PostgreSQL (USE_POSTGRES=True): {'PASS' if prod_result else 'FAIL'}")
    print(f"  SQLite (USE_POSTGRES=False):    {'PASS' if sqlite_result else 'FAIL'}")
    print()
    if not prod_result and sqlite_result:
        print("  ROOT CAUSE CONFIRMED: The login fails with PostgreSQL because")
        print("  the user exists in SQLite but not in PostgreSQL. The app uses")
        print("  PostgreSQL when DATABASE_URL is set (USE_POSTGRES=True), ignoring")
        print("  USE_SQLITE=True. Setting USE_POSTGRES=False (SQLite) fixes it.")
    elif prod_result and not sqlite_result:
        print("  Unexpected: SQLite fails but PostgreSQL works.")
    elif not prod_result and not sqlite_result:
        print("  Both configurations fail - deeper issue.")
    else:
        print("  Both configurations pass - issue may not reproduce in test env.")
    print("=" * 70)


if __name__ == '__main__':
    main()
