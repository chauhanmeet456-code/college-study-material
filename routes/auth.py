from datetime import datetime, timedelta

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import logging
import re

from config import Config
from models.session import (
    FailedLoginAttempt,
    LoginHistory,
    UserSession,
    generate_session_token,
    hash_session_token,
)
from models.user import User
from utils.auth import get_current_user
from utils.device import get_request_device_info
from utils.database import immediate_transaction
from utils.otp import OTPManager, EmailDeliveryResult
from utils.security import (
    check_login_rate_limit,
    clear_login_rate_limit,
    csrf_protect,
    register_login_failure,
)

logger = logging.getLogger('studyhub.auth')


def _mask_email(email):
    """Redact an email address for diagnostic logging."""
    if not email:
        return 'unknown'
    parts = email.split('@')
    if len(parts) == 2 and len(parts[0]) > 1:
        return parts[0][0] + '***@' + parts[1]
    return '***'


def _log_auth(event, **kwargs):
    """Emit a sanitized diagnostic log line for authentication events.

    Never logs passwords, OTPs, session tokens, or cookie values.
    Only records the event name, redirect destination, and outcomes.
    """
    safe = {}
    for k, v in kwargs.items():
        v = str(v)
        if k in ('password', 'otp', 'csrf_token', 'session_token'):
            v = '[REDACTED]'
        safe[k] = v
    detail = ' '.join(f'{k}={v}' for k, v in sorted(safe.items()))
    logger.info('[LOGIN_DIAG] %s %s', event, detail)

auth_bp = Blueprint('auth', __name__)

LOGIN_RATE_SCOPE = 'login'

# Session keys used for the two step login before an authenticated session
# exists.  The plain password is never stored here or anywhere else.
PENDING_LOGIN_EMAIL = 'pending_login_email'
LOGIN_VERIFIED_USER = 'login_verified_user_id'


def _now():
    return datetime.now()


def _client_ip():
    """Client IP, preferring the socket peer over forwarded headers.

    X-Forwarded-For is only honoured when the deployment explicitly enables
    it, because a client can otherwise forge the header to evade rate limiting.
    """
    if Config.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.remote_addr


def _clear_login_state():
    session.pop(PENDING_LOGIN_EMAIL, None)
    session.pop(LOGIN_VERIFIED_USER, None)
    session.pop('login_email', None)
    session.pop('login_password', None)


def _clear_pending_otp(email, purpose):
    """Remove a stored OTP challenge for the given email and purpose."""
    session.pop(f'otp_{purpose}_{email}', None)


def _record_failed_login(user_id, email, category='invalid_credentials'):
    """Persist a failed attempt and an auth event. Never stores a password."""
    device = get_request_device_info(request)
    ip = _client_ip()
    try:
        FailedLoginAttempt.record(
            email=email or '',
            ip_address=ip,
            device_description=device['device_name'],
            user_id=user_id,
            failure_category=category,
        )
    except Exception:
        pass
    if user_id:
        try:
            with immediate_transaction() as conn:
                LoginHistory.create(
                    conn,
                    user_id,
                    LoginHistory.EVENT_LOGIN_FAILED,
                    device_description=device['device_name'],
                    ip_address=ip,
                    login_method='password_otp',
                    result=LoginHistory.RESULT_FAILURE,
                    failure_reason=category,
                )
        except Exception:
            pass


@auth_bp.route('/api/send-otp', methods=['POST'])
@csrf_protect
def send_otp():
    data = request.get_json()
    email = data.get('email', '').strip()
    purpose = data.get('purpose', 'login')

    if not email:
        return {'success': False, 'message': 'Email is required'}, 400

    if purpose == 'register' and User.get_by_email(email):
        return {'success': False, 'message': 'Email already registered'}, 400

    if check_login_rate_limit(LOGIN_RATE_SCOPE, email,
                              Config.LOGIN_RATE_LIMIT_ATTEMPTS,
                              Config.LOGIN_RATE_LIMIT_WINDOW):
        return {
            'success': False,
            'message': 'Too many login attempts. Please try again later.',
        }, 429

    allowed, reason = OTPManager.can_resend(email, purpose)
    if not allowed:
        if reason == 'max_resends_reached':
            return {
                'success': False,
                'message': 'You have reached the maximum of 3 resend attempts. Please try again with a new verification process later.',
            }, 429
        if reason and reason.startswith('cooldown'):
            seconds = int(reason.split(':')[1])
            return {
                'success': False,
                'message': 'Please wait %d seconds before requesting another code.' % seconds,
            }, 429

    if not OTPManager.is_configured():
        return {
            'success': False,
            'message': "We couldn't send the verification code. Please try again later.",
        }, 503

    otp = OTPManager.generate_otp()
    OTPManager.store_otp(email, otp, purpose, ip_address=_client_ip())

    result = OTPManager.send_otp_email(email, otp, purpose)
    if result.is_success:
        OTPManager.increment_resend_count(email, purpose)
        remaining = OTPManager.get_resend_remaining(email, purpose)
        return {
            'success': True,
            'message': 'Verification code sent to your email address.',
            'remaining_resends': remaining,
        }
    if result.is_ambiguous:
        # The SMTP server may have accepted the message before disconnecting.
        # Do not increment the resend counter so the user can retry if the
        # code never arrives.
        remaining = OTPManager.get_resend_remaining(email, purpose)
        return {
            'success': False,
            'ambiguous': True,
            'message': (
                'The verification code may have been sent. '
                'Please check your inbox before requesting another code.'
            ),
            'remaining_resends': remaining,
        }, 202
    return {
        'success': False,
        'message': "We couldn't send the verification code. Please try again later.",
        'remaining_resends': OTPManager.get_resend_remaining(email, purpose),
    }, 503


@auth_bp.route('/api/verify-otp', methods=['POST'])
@csrf_protect
def verify_otp():
    data = request.get_json()
    email = data.get('email', '').strip()
    otp = data.get('otp', '').strip()
    purpose = data.get('purpose', 'login')

    if not email or not otp:
        return {'success': False, 'message': 'Email and OTP are required'}, 400

    if OTPManager.verify_otp(email, otp, purpose):
        session[f'otp_verified_{purpose}_{email}'] = True
        session.permanent = True
        return {'success': True, 'message': 'OTP verified successfully'}

    return {'success': False, 'message': 'Invalid OTP'}, 400


@auth_bp.route('/login', methods=['GET', 'POST'])
@csrf_protect
def login():
    existing_session_warning = 'user_id' in session

    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()

        if otp:
            return _handle_otp_step(otp)

        if request.form.get('replace_session') == '1':
            return _handle_replace_choice()

        if request.form.get('keep_session') == '1':
            return _handle_keep_existing()

        if request.form.get('cancel_login') == '1':
            return _handle_cancel()

        return _handle_credential_step()

    _log_auth('login_page_rendered', method='GET')
    return render_template(
        'auth/login.html',
        current_user=get_current_user(),
        show_otp=False,
        existing_session_warning=existing_session_warning,
    )


def _handle_credential_step():
    """Step 1: validate credentials, then start OTP verification."""
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')

    if not email or not password:
        _log_auth('credential_step_failed', reason='empty_fields',
                  redirect_to='auth.login')
        flash('Please enter both email and password.', 'danger')
        return redirect(url_for('auth.login'))

    if check_login_rate_limit(LOGIN_RATE_SCOPE, email,
                              Config.LOGIN_RATE_LIMIT_ATTEMPTS,
                              Config.LOGIN_RATE_LIMIT_WINDOW):
        _log_auth('credential_step_failed', reason='rate_limited',
                  redirect_to='auth.login')
        _record_failed_login(None, email, 'rate_limited')
        retry_minutes = max(1, Config.LOGIN_RATE_LIMIT_WINDOW // 60)
        flash(
            'Too many failed login attempts. Please try again in about %d minute(s).'
            % retry_minutes,
            'warning',
        )
        return redirect(url_for('auth.login'))

    user = User.get_by_email(email)

    # A single generic message so the form never reveals whether an address
    # is registered.
    if not user:
        register_login_failure(LOGIN_RATE_SCOPE, email, Config.LOGIN_RATE_LIMIT_WINDOW)
        _record_failed_login(None, email, 'invalid_credentials')
        _log_auth('credential_step_failed', reason='user_not_found',
                  redirect_to='auth.login')
        flash('Invalid email or password.', 'danger')
        return redirect(url_for('auth.login'))

    if not User.verify_password(user, password):
        register_login_failure(LOGIN_RATE_SCOPE, email, Config.LOGIN_RATE_LIMIT_WINDOW)
        _record_failed_login(user['id'], email, 'invalid_credentials')
        _log_auth('credential_step_failed', reason='password_mismatch',
                  redirect_to='auth.login')
        flash('Invalid email or password.', 'danger')
        return redirect(url_for('auth.login'))

    if not user.get('is_active', 1):
        _record_failed_login(user['id'], email, 'account_inactive')
        _log_auth('credential_step_failed', reason='account_inactive',
                  redirect_to='auth.login')
        flash('Your account is deactivated. Please contact support.', 'warning')
        return redirect(url_for('auth.login'))

    _log_auth('credential_step_passed', user_id=user['id'])

    # Credentials are correct but the user is not authenticated yet: the OTP
    # step, and any multi-device confirmation, still have to succeed.

    # Duplicate-submission guard: if a valid OTP challenge is already pending
    # for this email, do not generate a new one (avoids duplicate emails from
    # double-clicks / fast double-submits).
    if OTPManager.is_otp_valid(email, 'login'):
        session[PENDING_LOGIN_EMAIL] = email
        session.permanent = True
        _log_auth('otp_already_pending', email=_mask_email(email))
        flash('A verification code has already been sent. Please check your email.',
            'info')
        return render_template(
            'auth/login.html',
            current_user=get_current_user(),
            show_otp=True,
            login_email=email,
            remaining_attempts=_otp_remaining_attempts(email),
        )

    session[PENDING_LOGIN_EMAIL] = email
    session.permanent = True

    if not OTPManager.is_configured():
        _log_auth('otp_delivery_skipped', reason='smtp_not_configured',
                  redirect_to='auth.login')
        flash("We couldn't send the verification code. Please try again later.", 'danger')
        _clear_login_state()
        _clear_pending_otp(email, 'login')
        return render_template(
            'auth/login.html',
            current_user=get_current_user(),
            show_otp=False,
            email_error=True,
        )

    otp_code = OTPManager.generate_otp()
    OTPManager.store_otp(email, otp_code, 'login', ip_address=_client_ip())

    result = OTPManager.send_otp_email(email, otp_code, 'login')
    if result.is_success:
        _log_auth('otp_sent', email=_mask_email(email))
        flash('Verification code sent to your email address. Please verify to complete login.',
            'success')
        return render_template(
            'auth/login.html',
            current_user=get_current_user(),
            show_otp=True,
            login_email=email,
            remaining_attempts=Config.OTP_MAX_VERIFICATION_ATTEMPTS,
        )
    if result.is_ambiguous:
        _log_auth('otp_send_ambiguous', email=_mask_email(email))
        # The SMTP server accepted the message data but disconnected before
        # confirming with a 250 reply.  The email may have been delivered —
        # keep the pending OTP state and show the verification screen so the
        # user can enter the code they received.
        flash(
            'We sent the verification code to your email address. '
            'If you do not see it within a few minutes, please check your '
            'spam folder or request another code.',
            'info',
        )
        return render_template(
            'auth/login.html',
            current_user=get_current_user(),
            show_otp=True,
            login_email=email,
            remaining_attempts=Config.OTP_MAX_VERIFICATION_ATTEMPTS,
            delivery_ambiguous=True,
        )
    # Definitive delivery failure
    _log_auth('otp_delivery_failed', email=_mask_email(email),
              redirect_to='auth.login')
    flash("We couldn't send the verification code. Please try again later.", 'danger')
    _clear_login_state()
    _clear_pending_otp(email, 'login')
    return render_template(
        'auth/login.html',
        current_user=get_current_user(),
        show_otp=False,
        email_error=True,
    )


def _handle_otp_step(otp):
    """Step 2: verify the OTP, then continue to the session decision."""
    email = session.get(PENDING_LOGIN_EMAIL)

    if not email:
        _log_auth('otp_step_failed', reason='no_pending_email',
                  redirect_to='auth.login')
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('auth.login'))

    if not OTPManager.verify_otp(email, otp, 'login'):
        remaining = _otp_remaining_attempts(email)
        _log_auth('otp_step_failed', reason='otp_invalid',
                  redirect_to='auth.login (render)')
        flash('Invalid or expired OTP.', 'danger')
        return render_template(
            'auth/login.html',
            current_user=get_current_user(),
            show_otp=True,
            login_email=email,
            remaining_attempts=remaining,
        )

    user = User.get_by_email(email)
    if not user or not user.get('is_active', 1):
        _clear_login_state()
        _log_auth('otp_step_failed', reason='account_inactive',
                  redirect_to='auth.login')
        flash('Your account is deactivated. Please contact support.', 'warning')
        return redirect(url_for('auth.login'))

    _log_auth('otp_verified', user_id=user['id'])

    # The account is now proven, but not yet authenticated in a new session.
    session[LOGIN_VERIFIED_USER] = user['id']

    conflicts = _active_sessions_other_than(user['id'])
    if conflicts:
        return render_template(
            'auth/login.html',
            current_user=get_current_user(),
            show_otp=False,
            conflict=True,
            login_email=email,
            conflict_sessions=conflicts,
        )

    return _complete_login(user)


def _handle_keep_existing():
    """Cancel the new login; the existing active session stays untouched."""
    _clear_login_state()
    flash('No new session was created. The existing session is still active.', 'info')
    return redirect(url_for('auth.login'))


def _handle_cancel():
    _clear_login_state()
    flash('Login cancelled.', 'info')
    return redirect(url_for('auth.login'))


def _handle_replace_choice():
    """End the other session and create the new one."""
    user_id = session.get(LOGIN_VERIFIED_USER)

    if not user_id:
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('auth.login'))

    user = User.get_by_id(user_id)
    if not user or not user.get('is_active', 1):
        _clear_login_state()
        flash('Session expired. Please login again.', 'warning')
        return redirect(url_for('auth.login'))

    return _complete_login(user)


def _complete_login(user):
    """Create the authenticated server side session for this device.

    The old session is revoked inside the same transaction that creates the
    new one, so two simultaneous logins cannot both end up active.
    """
    device = get_request_device_info(request)
    ip = _client_ip()
    now = _now()
    expires_at = now + timedelta(minutes=Config.SESSION_LIFETIME_MINUTES)
    device_name = device['device_name']

    token = generate_session_token()
    token_hash = hash_session_token(token)

    try:
        with immediate_transaction() as conn:
            active_rows = UserSession.get_active_for_user(conn, user['id'])

            if len(active_rows) >= max(1, Config.MAX_ACTIVE_SESSIONS_PER_USER):
                keep = active_rows[:max(0, Config.MAX_ACTIVE_SESSIONS_PER_USER - 1)]
                for row in active_rows[len(keep):] if keep else active_rows:
                    UserSession.revoke(
                        conn,
                        row['id'],
                        'Session ended because the account signed in on '
                        'another device',
                        now,
                    )
                    LoginHistory.create(
                        conn,
                        user['id'],
                        LoginHistory.EVENT_SESSION_REPLACED,
                        device_description=device_name,
                        ip_address=ip,
                        login_method='password_otp',
                        result=LoginHistory.RESULT_INFO,
                        related_session_id=row['id'],
                    )

            session_row_id = UserSession.create(
                conn,
                user['id'],
                token_hash,
                device_name,
                device['browser_name'],
                device['operating_system'],
                ip,
                expires_at,
            )

            LoginHistory.create(
                conn,
                user['id'],
                LoginHistory.EVENT_LOGIN_SUCCESS,
                device_description=device_name,
                ip_address=ip,
                login_method='password_otp',
                result=LoginHistory.RESULT_SUCCESS,
                related_session_id=session_row_id,
            )
    except Exception:
        _log_auth('login_completion_failed', user_id=user['id'],
                  reason='exception', redirect_to='auth.login')
        flash('Something went wrong while signing you in. Please try again.', 'danger')
        _clear_login_state()
        return redirect(url_for('auth.login'))

    # The raw token is held only in this signed, HttpOnly cookie.
    session['user_id'] = user['id']
    session['user_name'] = user['name']
    session['session_id'] = session_row_id
    session['session_token'] = token
    session.permanent = True

    _clear_login_state()
    clear_login_rate_limit(LOGIN_RATE_SCOPE, user['email'])

    try:
        User.update_last_login(user['id'])
    except Exception:
        pass

    _log_auth('login_success', user_id=user['id'], redirect_to='main.index')
    flash('Login successful!', 'success')
    return redirect(url_for('main.index'))


@auth_bp.route('/register', methods=['GET', 'POST'])
@csrf_protect
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        if not all([name, email, password, confirm_password]):
            flash('All fields are required.', 'danger')
        elif password != confirm_password:
            flash('Passwords do not match.', 'danger')
        elif len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
        else:
            existing = User.get_by_email(email)
            if existing:
                if existing.get('deleted_by_user') == 1:
                    User.permanent_delete(existing['id'])
                else:
                    flash('Email already registered.', 'danger')
                    return redirect(url_for('auth.register'))
            User.create(name, email, password)
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('auth.login'))
    return render_template('auth/register.html', current_user=get_current_user())


@auth_bp.route('/logout', methods=['GET', 'POST'])
@csrf_protect
def logout():
    session_token = session.get('session_token')

    if session_token:
        try:
            row = UserSession.get_by_token_hash(hash_session_token(session_token))
            if row and row.get('status') == UserSession.STATUS_ACTIVE:
                now = _now()
                with immediate_transaction() as conn:
                    UserSession.mark_logged_out(conn, row['id'], now)
                    LoginHistory.create(
                        conn,
                        row['user_id'],
                        LoginHistory.EVENT_LOGOUT,
                        device_description=row.get('device_name'),
                        ip_address=_client_ip(),
                        result=LoginHistory.RESULT_INFO,
                        related_session_id=row['id'],
                    )
        except Exception:
            # A failed audit write must never block the logout itself.
            pass

    session.pop('session_token', None)
    session.pop('session_id', None)
    session.pop('user_id', None)
    session.pop('user_name', None)
    _clear_login_state()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('main.index'))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
@csrf_protect
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        user = User.get_by_email(email)

        # The same response either way, so the page cannot be used to
        # enumerate registered addresses.
        if user:
            if OTPManager.is_configured():
                session['forgot_email'] = email
                session.permanent = True
                otp = OTPManager.generate_otp()
                OTPManager.store_otp(email, otp, 'forgot', ip_address=_client_ip())
                result = OTPManager.send_otp_email(email, otp, 'forgot')
                if result.is_success:
                    flash('If that email is registered, an OTP has been sent.', 'info')
                elif result.is_ambiguous:
                    # The email may have been delivered despite the server
                    # disconnect.  Show the same generic message as success so
                    # the user can check their inbox and try to verify.
                    flash(
                        'If that email is registered, an OTP has been sent. '
                        'If you do not receive it shortly, please check your '
                        'spam folder.',
                        'info',
                    )
                else:
                    flash("We couldn't send the verification code. Please try again later.", 'danger')
            else:
                flash("We couldn't send the verification code. Please try again later.", 'danger')
        else:
            flash('If that email is registered, an OTP has been sent.', 'info')
        return redirect(url_for('auth.reset_password'))

    return render_template('auth/forgot_password.html', current_user=get_current_user())


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
@csrf_protect
def reset_password():
    email = session.get('forgot_email')
    if not email:
        flash('Please request a password reset first.', 'warning')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not all([otp, new_password, confirm_password]):
            flash('All fields are required.', 'danger')
        elif new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
        elif len(new_password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
        else:
            if OTPManager.verify_otp(email, otp, 'forgot'):
                user = User.get_by_email(email)
                if user:
                    User.update_password(user['id'], new_password)
                    session.pop('forgot_email', None)
                    flash('Password reset successful! Please login.', 'success')
                    return redirect(url_for('auth.login'))
                flash('User not found.', 'danger')
            else:
                flash('Invalid or expired OTP.', 'danger')

    return render_template('auth/reset_password.html', current_user=get_current_user(), email=email)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _otp_remaining_attempts(email):
    key = f'otp_login_{email}'
    stored = session.get(key) or {}
    attempts = stored.get('attempts', 0)
    return max(0, Config.OTP_MAX_VERIFICATION_ATTEMPTS - attempts)


def _active_sessions_other_than(user_id, current_session_id=None):
    """Safe-to-display descriptors for the user's active sessions."""
    try:
        rows = UserSession.get_active_sessions_for_user(user_id)
    except Exception:
        return []

    conflicts = []
    now = _now()
    for row in rows:
        if current_session_id is not None and row.get('id') == current_session_id:
            continue
        conflicts.append({
            'id': row['id'],
            'device_name': row.get('device_name') or 'Unknown device',
            'browser_name': row.get('browser_name'),
            'operating_system': row.get('operating_system'),
            'ip_address': row.get('ip_address'),
            'created_at': row.get('created_at'),
            'last_activity_at': row.get('last_activity_at'),
        })
    return conflicts
