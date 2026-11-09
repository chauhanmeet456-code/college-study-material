import os
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.parse import parse_qs
from dotenv import dotenv_values


def _load_project_env(env_file=None, environ=None):
    """Load project-root .env values without overriding configured environment values."""
    env_file = Path(env_file or Path(__file__).resolve().with_name('.env'))
    environ = os.environ if environ is None else environ
    is_render = (
        str(environ.get('RENDER', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
        or bool(environ.get('RENDER_SERVICE_ID'))
    )
    if is_render:
        return
    for key, value in dotenv_values(env_file).items():
        if value is not None and not environ.get(key):
            environ[key] = value


_load_project_env()


def _is_truthy(value):
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _is_loopback_database_host(host):
    return (host or '').lower() in {'localhost', '127.0.0.1', '::1'}


def _normalize_postgres_url(value):
    """Return a normalized PostgreSQL URL or a safe, non-secret error."""
    if not isinstance(value, str) or not value.strip():
        return None, 'PostgreSQL URL is empty.'

    url = value.strip()
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None, 'PostgreSQL URL is malformed.'

    if (
        parsed.scheme not in {'postgres', 'postgresql'}
        or not parsed.hostname
        or not parsed.path.strip('/')
        or parsed.fragment
    ):
        return None, 'PostgreSQL URL must include a valid host and database name.'
    if port is not None and not 1 <= port <= 65535:
        return None, 'PostgreSQL URL contains an invalid port.'
    sslmodes = parse_qs(parsed.query).get('sslmode', [])
    if any(mode not in {
        'disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full',
    } for mode in sslmodes):
        return None, 'PostgreSQL URL contains an invalid SSL mode.'
    return url, None


def resolve_database_configuration(environ):
    """Select a DB URL without ever falling back from local to production."""
    is_render = (
        _is_truthy(environ.get('RENDER'))
        or bool(environ.get('RENDER_SERVICE_ID'))
    )
    use_sqlite = _is_truthy(environ.get('USE_SQLITE'))
    backend = str(environ.get('DATABASE_BACKEND', '')).strip().lower()

    if is_render:
        selected = (
            environ.get('DATABASE_URL')
            or environ.get('PRODUCTION_DATABASE_URL')
            or environ.get('PROD_DATABASE_URL')
        )
        if not selected:
            return None, 'Production PostgreSQL DATABASE_URL is not configured.', 'render'
        normalized, error = _normalize_postgres_url(selected)
        if error:
            return None, error, 'render'
        return normalized, None, 'render'

    if backend and backend not in {'postgresql', 'sqlite', 'mysql'}:
        return (
            None,
            'DATABASE_BACKEND must be postgresql, sqlite, or mysql.',
            'development',
        )

    if backend == 'postgresql':
        selected = environ.get('DEV_DATABASE_URL') or environ.get('LOCAL_DATABASE_URL')
        if not selected:
            return (
                None,
                'PostgreSQL was explicitly selected, but DEV_DATABASE_URL (or '
                'LOCAL_DATABASE_URL) is not configured.',
                'development',
            )
        normalized, error = _normalize_postgres_url(selected)
        if error:
            return None, error, 'development'
        return normalized, None, 'development'

    if backend == 'sqlite':
        return None, None, 'development'

    if backend == 'mysql':
        return None, None, 'development'

    selected = environ.get('DEV_DATABASE_URL') or environ.get('LOCAL_DATABASE_URL')
    if selected:
        normalized, error = _normalize_postgres_url(selected)
        if error:
            return None, error, 'development'
        return normalized, None, 'development'

    if use_sqlite:
        return None, None, 'development'

    # Support standard libpq variables for explicit local PostgreSQL setups.
    host = environ.get('PGHOST')
    if host:
        if not _is_loopback_database_host(host):
            return (
                None,
                'Local PGHOST must point to localhost; configure DEV_DATABASE_URL '
                'for another development database.',
                'development',
            )
        user = environ.get('PGUSER', 'postgres')
        password = environ.get('PGPASSWORD', '')
        auth = quote(user, safe='') if user else ''
        if password:
            auth += ':' + quote(password, safe='')
        if auth:
            auth += '@'
        port = environ.get('PGPORT', '5432')
        database = quote(environ.get('PGDATABASE', 'college_study'), safe='')
        normalized, error = _normalize_postgres_url(
            'postgresql://{}{}:{}/{}'.format(auth, host, port, database)
        )
        if error:
            return None, error, 'development'
        return normalized, None, 'development'

    if any(environ.get(key) for key in ('DB_HOST', 'DB_NAME', 'DB_USER')):
        return None, None, 'development'
    if environ.get('DATABASE_URL'):
        return (
            None,
            'Local PostgreSQL is not configured. Set DEV_DATABASE_URL in .env; '
            'DATABASE_URL is reserved for production and is intentionally ignored.',
            'development',
        )
    return None, None, 'development'


def _positive_int(value, default):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY') or None
    MYSQL_HOST = os.getenv('DB_HOST', 'localhost')
    MYSQL_PORT = int(os.getenv('DB_PORT', 3306))
    MYSQL_DB = os.getenv('DB_NAME', 'college_study')
    MYSQL_USER = os.getenv('DB_USER', 'root')
    MYSQL_PASSWORD = os.getenv('DB_PASSWORD', '')
    MYSQL_CURSORCLASS = 'DictCursor'

    # Environment detection
    IS_RENDER = (
        _is_truthy(os.getenv('RENDER'))
        or bool(os.getenv('RENDER_SERVICE_ID'))
    )

    # Database configuration
    # Render automatically provides DATABASE_URL in production.
    # For local development on Windows, DEV_DATABASE_URL or LOCAL_DATABASE_URL can be configured
    # in .env to target a local PostgreSQL database (e.g. postgresql://postgres:password@localhost:5432/college_study)
    # without modifying the production DATABASE_URL.
    DATABASE_URL, DATABASE_CONFIG_ERROR, DATABASE_ENVIRONMENT = (
        resolve_database_configuration(os.environ)
    )

    DATABASE_BACKEND = os.getenv('DATABASE_BACKEND', '').strip().lower()
    USE_SQLITE = (
        (not IS_RENDER)
        and DATABASE_URL is None
        and not DATABASE_CONFIG_ERROR
        and (
            DATABASE_BACKEND == 'sqlite'
            or (
                DATABASE_BACKEND not in {'postgresql', 'mysql'}
                and _is_truthy(os.getenv('USE_SQLITE'))
            )
        )
    )
    SQLITE_DB = os.path.abspath(os.getenv(
        'SQLITE_DB',
        os.getenv(
            'SQLITE_SOURCE_DB',
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                'instance',
                'college_study.sqlite',
            ),
        ),
    ))
    USE_POSTGRES = (not USE_SQLITE) and DATABASE_URL is not None and DATABASE_URL.strip() != ''

    DB_CONNECT_TIMEOUT = _positive_int(os.getenv('DB_CONNECT_TIMEOUT', '10'), 10)
    DB_POOL_MAX_PER_THREAD = _positive_int(os.getenv('DB_POOL_MAX_PER_THREAD', '2'), 2)

    # Configurable upload storage directory (supports Render Persistent Disk, e.g. /var/data/uploads)
    UPLOAD_FOLDER = os.getenv(
        'UPLOAD_FOLDER',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
    )
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'ppt', 'pptx', 'zip', 'txt', 'mp4', 'webm', 'ogg', 'avi', 'mov', 'wmv', 'flv', 'mkv', 'png', 'jpg', 'jpeg', 'gif', 'webp'}
    ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    GUEST_VIEW_LIMIT = int(os.getenv('GUEST_VIEW_LIMIT', 4))
    GUEST_DOWNLOAD_LIMIT = int(os.getenv('GUEST_DOWNLOAD_LIMIT', 4))
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', '587'))
    MAIL_USERNAME = os.getenv('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')
    # Socket timeout for the SMTP conversation. Without one, an unreachable or
    # unresponsive mail server can block a login request indefinitely.
    MAIL_TIMEOUT = int(os.getenv('MAIL_TIMEOUT', '10'))
    MAIL_CONNECT_TIMEOUT = int(os.getenv('MAIL_CONNECT_TIMEOUT', '3'))
    MAIL_SEND_TIMEOUT = int(os.getenv('MAIL_SEND_TIMEOUT', '8'))
    MAIL_USE_TLS = True
    APP_NAME = os.getenv('APP_NAME', 'StudyHub')
    PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
    DEBUG = (not IS_RENDER) and _is_truthy(os.getenv('FLASK_DEBUG'))
    SESSION_COOKIE_SECURE = IS_RENDER or _is_truthy(
        os.getenv('SESSION_COOKIE_SECURE')
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Login session management.  A single active session per user is enforced by
    # default; raise MAX_ACTIVE_SESSIONS_PER_USER to allow several devices
    # without changing any other code.
    MAX_ACTIVE_SESSIONS_PER_USER = int(os.getenv('MAX_ACTIVE_SESSIONS_PER_USER', '1'))
    SESSION_LIFETIME_MINUTES = int(os.getenv('SESSION_LIFETIME_MINUTES', '720'))
    SESSION_IDLE_TIMEOUT_MINUTES = int(os.getenv('SESSION_IDLE_TIMEOUT_MINUTES', '60'))
    LOGIN_RATE_LIMIT_ATTEMPTS = int(os.getenv('LOGIN_RATE_LIMIT_ATTEMPTS', '5'))
    LOGIN_RATE_LIMIT_WINDOW = int(os.getenv('LOGIN_RATE_LIMIT_WINDOW', '900'))
    LOGIN_HISTORY_RETENTION_DAYS = int(os.getenv('LOGIN_HISTORY_RETENTION_DAYS', '180'))
    # Only enable behind a trusted reverse proxy; clients can otherwise forge
    # X-Forwarded-For and evade the login rate limit.
    TRUST_PROXY_HEADERS = os.getenv('TRUST_PROXY_HEADERS', 'false').lower() == 'true'

    # OTP configuration
    OTP_EXPIRY_SECONDS = int(os.getenv('OTP_EXPIRY_SECONDS', '300'))
    OTP_MAX_RESENDS = int(os.getenv('OTP_MAX_RESENDS', '3'))
    OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv('OTP_RESEND_COOLDOWN_SECONDS', '30'))
    OTP_MAX_VERIFICATION_ATTEMPTS = int(os.getenv('OTP_MAX_VERIFICATION_ATTEMPTS', '5'))

    # Notification queue worker
    NOTIFICATION_POLL_INTERVAL = int(os.getenv('NOTIFICATION_POLL_INTERVAL', '15'))
    NOTIFICATION_WORKER_MAX_ATTEMPTS = int(os.getenv('NOTIFICATION_WORKER_MAX_ATTEMPTS', '5'))
    NOTIFICATION_RETRY_BASE_SECONDS = int(os.getenv('NOTIFICATION_RETRY_BASE_SECONDS', '60'))
    NOTIFICATION_RETRY_MAX_SECONDS = int(os.getenv('NOTIFICATION_RETRY_MAX_SECONDS', '3600'))
    NOTIFICATION_PROCESSING_LEASE_SECONDS = int(os.getenv('NOTIFICATION_PROCESSING_LEASE_SECONDS', '300'))
