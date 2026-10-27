import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
    MYSQL_HOST = os.getenv('DB_HOST', 'localhost')
    MYSQL_PORT = int(os.getenv('DB_PORT', 3306))
    MYSQL_DB = os.getenv('DB_NAME', 'college_study')
    MYSQL_USER = os.getenv('DB_USER', 'root')
    MYSQL_PASSWORD = os.getenv('DB_PASSWORD', '')
    MYSQL_CURSORCLASS = 'DictCursor'
    USE_SQLITE = os.getenv('USE_SQLITE', 'false').lower() == 'true'
    SQLITE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'college_study.sqlite')
    DATABASE_URL = os.getenv('DATABASE_URL', None)
    # USE_SQLITE=true explicitly selects the bundled SQLite database and
    # takes priority over DATABASE_URL, so that a locally present SQLite
    # database (with real user data) is never shadowed by a stale or empty
    # PostgreSQL endpoint that may also be configured via DATABASE_URL.
    USE_POSTGRES = (not USE_SQLITE) and DATABASE_URL is not None and DATABASE_URL.strip() != ''
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
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
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
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

