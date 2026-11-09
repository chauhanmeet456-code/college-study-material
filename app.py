import logging
import os
import time
from datetime import datetime, timedelta

_session_logger = logging.getLogger('studyhub.session')
_app_logger = logging.getLogger('studyhub.app')

from flask import (
    Flask,
    flash,
    g,
    redirect,
    request,
    session,
    url_for,
)
from markupsafe import Markup
import pymysql

from config import Config
from models.session import UserSession, hash_session_token
from routes.main import main_bp
from routes.auth import auth_bp
from routes.user import user_bp
from routes.admin import admin_bp
from utils.database import (
    DatabaseConnectionError,
    ensure_schema_extensions,
    immediate_transaction,
    is_database_connection_error,
    is_database_unavailable_error,
)
from utils.security import (
    apply_security_headers,
    csrf_input,
    generate_csrf_token,
)
from utils.cookie_consent import get_cookie_preferences
from utils.realtime import init_socketio
from utils.google_oauth import google_oauth_enabled, init_google_oauth
from utils.seo import build_seo_context, clean_meta_description

# Endpoints that must not trigger a session validity check, because running it
# would be circular (login, logout, OTP exchange, admin auth) or pointless
# (static assets, public index page).
SKIP_SESSION_CHECK_ENDPOINTS = {
    'static',
    'auth.login',
    'auth.register',
    'auth.logout',
    'auth.google_login',
    'auth.google_callback',
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
    'user.session_status',
    'main.index',
    'main.favicon',
    'main.privacy_policy',
    'main.terms_of_service',
    'main.cookie_policy',
    'main.raw_material_file',
    'main.serve_uploaded_file',
    'main.save_cookie_consent',
}

ACTIVITY_TOUCH_INTERVAL_SECONDS = 60


def create_app():
    if Config.IS_RENDER and not Config.SECRET_KEY:
        raise RuntimeError(
            'SECRET_KEY must be configured in the Render environment.'
        )
    if Config.DATABASE_CONFIG_ERROR:
        raise RuntimeError(
            'Database configuration error: {}'.format(
                Config.DATABASE_CONFIG_ERROR
            )
        )
    app = Flask(__name__)
    app.config.from_object(Config)
    if not app.config.get('SECRET_KEY'):
        app.config['SECRET_KEY'] = 'dev-secret-key'

    # Hardened session cookies. HttpOnly blocks script access, SameSite=Lax is
    # the baseline CSRF defence, and Secure is enabled for HTTPS deployments.
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
        minutes=Config.SESSION_LIFETIME_MINUTES
    )
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = Config.SESSION_COOKIE_SECURE
    init_google_oauth(app)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)
    init_socketio(app)

    # Apply additive schema updates. Connectivity and missing-schema failures
    # are logged without preventing startup; unexpected errors remain visible.
    if not ensure_schema_extensions():
        _app_logger.error(
            "One or more database schema extensions could not be initialized."
        )

    @app.context_processor
    def inject_template_helpers():
        return {
            "now": _now_helper,
            "csrf_input": csrf_input,
            "csrf_token": generate_csrf_token,
            "app_name": Config.APP_NAME,
            "cookie_preferences": get_cookie_preferences(
                request.cookies.get('studyhub_cookie_consent')
            ),
            "seo": build_seo_context(),
            "google_oauth_enabled": google_oauth_enabled(),
        }

    @app.context_processor
    def inject_settings():
        if getattr(g, 'database_unavailable', False):
            return {"settings": {}}
        try:
            from models.settings import Settings
            return {"settings": Settings.get_all()}
        except Exception as exc:
            if not is_database_unavailable_error(exc):
                raise
            if is_database_connection_error(exc):
                g.database_unavailable = True
            _app_logger.warning(
                "Settings unavailable for this request (%s)",
                type(exc).__name__,
            )
            return {"settings": {}}

    app.jinja_env.filters['plain_meta'] = clean_meta_description

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
            # No token at all — nothing to validate.  This is the normal state
            # for anonymous visitors and for the login flow itself.
            _session_logger.debug(
                '[SESSION_DIAG] no_token endpoint=%s', endpoint
            )
            return None

        if endpoint in SKIP_SESSION_CHECK_ENDPOINTS:
            # Login/logout/OTP/admin-auth endpoints are intentionally exempt:
            # they may legitimately carry a stale token while establishing a
            # fresh one.
            _session_logger.debug(
                '[SESSION_DIAG] skip_check endpoint=%s', endpoint
            )
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
            _session_logger.debug(
                '[SESSION_DIAG] valid endpoint=%s status=active', endpoint
            )
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

        _session_logger.info(
            '[SESSION_DIAG] invalid_session endpoint=%s prev_status=%s cleared=True '
            'redirect=auth.login',
            endpoint, previous_status,
        )

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

    @app.errorhandler(403)
    def forbidden(e):
        if os.path.exists(os.path.join(app.static_folder, "403.html")):
            return app.send_static_file("403.html"), 403
        return "<h1>403 - Access Denied</h1>", 403

    @app.errorhandler(DatabaseConnectionError)
    def database_unavailable(e):
        _app_logger.error(
            "Required database operation unavailable: %s",
            str(e),
        )
        if request.path.startswith('/api/') or request.is_json:
            return {
                "success": False,
                "message": "The database is temporarily unavailable. Please try again later.",
            }, 503
        return (
            "<h1>Service temporarily unavailable</h1>"
            "<p>The database could not be reached. Please try again later.</p>",
            503,
        )

    @app.errorhandler(pymysql.err.OperationalError)
    def mysql_unavailable(e):
        _app_logger.error(
            "MySQL operation unavailable (%s)",
            type(e).__name__,
        )
        if request.path.startswith('/api/') or request.is_json:
            return {
                "success": False,
                "message": "The database is temporarily unavailable. Please try again later.",
            }, 503
        return (
            "<h1>Service temporarily unavailable</h1>"
            "<p>The database could not be reached. Please try again later.</p>",
            503,
        )

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
    from utils.realtime import socketio

    socketio.run(app, debug=Config.DEBUG, port=5000)
