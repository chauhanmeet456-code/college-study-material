import os
import time
from datetime import datetime, timedelta

from flask import (
    Flask,
    flash,
    redirect,
    request,
    session,
    url_for,
)
from markupsafe import Markup

from config import Config
from models.session import UserSession, hash_session_token
from routes.main import main_bp
from routes.auth import auth_bp
from routes.user import user_bp
from routes.admin import admin_bp
from utils.database import immediate_transaction, ensure_schema_extensions
from utils.security import (
    apply_security_headers,
    csrf_input,
    generate_csrf_token,
)

# Endpoints that must not trigger a session validity check, because running it
# would be circular (login, logout, OTP exchange, admin auth) or pointless
# (static assets, public index page).
SKIP_SESSION_CHECK_ENDPOINTS = {
    'static',
    'auth.login',
    'auth.register',
    'auth.logout',
    'auth.send_otp',
    'auth.verify_otp',
    'auth.forgot_password',
    'auth.reset_password',
    'admin.admin_login',
    'admin.admin_logout',
    'admin.admin_forgot_password',
    'admin.admin_reset_password',
    'user.confirm_delete_account',
    'user.login_history',
    'user.revoke_session',
    'user.revoke_all_sessions',
    'main.index',
}

ACTIVITY_TOUCH_INTERVAL_SECONDS = 60


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Hardened session cookies. HttpOnly blocks script access, SameSite=Lax is
    # the baseline CSRF defence, and Secure is enabled for HTTPS deployments.
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
        minutes=Config.SESSION_LIFETIME_MINUTES
    )
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = Config.SESSION_COOKIE_SECURE

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)

    # Add the ended_by_admin column and the notification queue table if they
    # are not already present.  This is wrapped defensively so a locked-down
    # or read-only database can never prevent the application from starting.
    try:
        ensure_schema_extensions()
    except Exception:
        pass

    @app.context_processor
    def inject_template_helpers():
        return {
            "now": _now_helper,
            "csrf_input": csrf_input,
            "csrf_token": generate_csrf_token,
            "app_name": Config.APP_NAME,
        }

    @app.context_processor
    def inject_settings():
        try:
            from models.settings import Settings
            return {"settings": Settings.get_all()}
        except Exception:
            return {"settings": {}}

    @app.before_request
    def log_request_time():
        request._start_time = time.time()

    @app.after_request
    def log_request_duration(response):
        start = getattr(request, '_start_time', None)
        if start is not None:
            duration = time.time() - start
            endpoint = request.endpoint or 'unknown'
            if duration > 1.0:
                print(f'[TIMING] {request.method} {endpoint} - {duration:.3f}s', flush=True)
        return response

    @app.after_request
    def add_security_headers(response):
        return apply_security_headers(response)

    @app.before_request
    def enforce_session_validity():
        """Reject cookies whose server side session is dead or expired.

        The signed Flask cookie is not trusted on its own: every request that
        carries ``user_id`` also has to name a session row that is still
        ``active``.  A revoked or expired session cannot keep using protected
        pages, and the browser is sent to the login page with an explanation.
        """
        endpoint = request.endpoint or ''

        if not endpoint or endpoint == 'static' or _is_admin_endpoint(endpoint):
            return None

        session_token = session.get('session_token')

        if not session_token:
            return None

        if endpoint in SKIP_SESSION_CHECK_ENDPOINTS:
            return None

        row = _resolve_session(session_token)

        if row and row.get('status') == UserSession.STATUS_ACTIVE and not _is_expired(row):
            if 'user_id' not in session or session.get('user_id') != row.get('user_id'):
                # The cookie still holds a live server side session, so the
                # app may keep using it, but a stale user_id is not trusted.
                session['user_id'] = row.get('user_id')
            if session.get('session_id') != row.get('id'):
                session['session_id'] = row.get('id')
            _touch_activity(row)
            return None

        # Either the row is gone, or its status marks it as finished.
        try:
            session_row = UserSession.get_by_token_hash(hash_session_token(session_token))
        except Exception:
            session_row = None
        previous_status = session_row.get('status') if session_row else 'missing'
        session.pop('session_token', None)
        session.pop('session_id', None)
        session.pop('user_id', None)
        session.pop('user_name', None)

        if previous_status == UserSession.STATUS_REVOKED:
            ended_by_admin = (session_row or {}).get('ended_by_admin') if session_row else None
            termination_reason = (session_row or {}).get('termination_reason') or ''
            is_admin_ended = (
                ended_by_admin == 1 or ended_by_admin is True
            ) or (
                'ended_by_admin' not in (session_row or {}) and
                'administrator' in termination_reason.lower()
            )
            if is_admin_ended:
                message = (
                    'Your session was ended by a StudyHub administrator. '
                    'Please sign in again.'
                )
            elif 'another device' in termination_reason.lower():
                message = (
                    'Your session has ended because your account was signed in '
                    'on another device.'
                )
            else:
                message = (
                    'Your session has ended. Please log in again to continue.'
                )
        elif previous_status == UserSession.STATUS_LOGGED_OUT:
            message = (
                'You have been signed out on this device. '
                'Please log in again to continue.'
            )
        else:
            message = 'Your session has expired. Please log in again.'

        flash(message, 'warning')
        return redirect(url_for('auth.login'))

    @app.errorhandler(404)
    def not_found(e):
        if os.path.exists(os.path.join(app.static_folder, "404.html")):
            return app.send_static_file("404.html"), 404
        return "<h1>404 - Page Not Found</h1>", 404

    @app.errorhandler(500)
    def server_error(e):
        if os.path.exists(os.path.join(app.static_folder, "500.html")):
            return app.send_static_file("500.html"), 500
        return "<h1>500 - Server Error</h1>", 500

    @app.template_filter("nl2br")
    def nl2br(value):
        if value is None:
            return ""
        return Markup(value.replace("\n", "<br>\n"))

    @app.template_filter("format_datetime")
    def format_datetime(value, fmt="%d %b %Y, %H:%M %p"):
        if not value:
            return "unknown"
        if isinstance(value, str):
            return value
        try:
            return value.strftime(fmt)
        except (AttributeError, ValueError):
            return str(value)

    return app


# ---------------------------------------------------------------------------
# Session helpers used by create_app()
# ---------------------------------------------------------------------------

def _now_helper():
    return datetime.now()


def _is_admin_endpoint(endpoint):
    return endpoint.startswith('admin.')


def _parse_datetime(value):
    if not value:
        return None
    text = str(value).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _resolve_session(token):
    """Return the live session row for a raw token hash, or None.

    Both lifetime policies are enforced here: the absolute expiry
    (``expires_at``) and the idle timeout (``last_activity_at``).  A session
    that passes either limit is refused even if the cleanup sweep has not run
    yet, and the row is flipped to ``expired`` on the next request.
    """
    if not token:
        return None
    try:
        row = UserSession.get_by_token_hash(hash_session_token(token))
    except Exception:
        return None
    if not row or row.get('status') != UserSession.STATUS_ACTIVE:
        return None
    now = datetime.now()
    expires_at = _parse_datetime(row.get('expires_at'))
    if expires_at is not None and expires_at < now:
        return None
    idle = _parse_datetime(row.get('last_activity_at'))
    if idle is not None:
        idle_limit = now - timedelta(minutes=Config.SESSION_IDLE_TIMEOUT_MINUTES)
        if idle < idle_limit:
            return None
    return row


def _is_expired(row):
    expires_at = _parse_datetime(row.get('expires_at'))
    if expires_at is None:
        return False
    return expires_at < datetime.now()


def _status_of(token):
    """Return the stored status of a token, used to choose the logout reason."""
    if not token:
        return 'missing'
    try:
        row = UserSession.get_by_token_hash(hash_session_token(token))
    except Exception:
        return 'missing'
    if not row:
        return 'missing'
    return row.get('status')


def _touch_activity(row):
    """Throttle last_activity_at writes to once per minute per session."""
    last_activity = _parse_datetime(row.get('last_activity_at'))
    if last_activity is not None:
        if (datetime.now() - last_activity).total_seconds() < ACTIVITY_TOUCH_INTERVAL_SECONDS:
            return
    try:
        with immediate_transaction() as conn:
            UserSession.touch(conn, row['id'])
    except Exception:
        # A failed activity refresh must never break the request itself.
        pass


if __name__ == "__main__":
    app = create_app()
    app.run(debug=Config.DEBUG, port=5000)
