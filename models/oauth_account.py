import hashlib
import secrets
from datetime import datetime

from werkzeug.security import generate_password_hash

from utils.database import get_connection, get_cursor, close_cursor, immediate_transaction


class OAuthAccount:
    PROVIDER_GOOGLE = 'google'

    @staticmethod
    def _subject_hash(provider_subject):
        return hashlib.sha256(provider_subject.encode('utf-8')).hexdigest()

    @staticmethod
    def get_user(provider, provider_subject):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                """SELECT u.*
                   FROM user_oauth_accounts a
                   JOIN users u ON u.id = a.user_id
                   WHERE a.provider = ? AND a.provider_subject_hash = ?""",
                (provider, OAuthAccount._subject_hash(provider_subject)),
            )
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_user_by_email(email):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                'SELECT * FROM users WHERE LOWER(email) = ? LIMIT 1',
                (email.lower(),),
            )
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def link_user(user_id, provider, provider_subject):
        with immediate_transaction() as conn:
            cursor = get_cursor(conn)
            try:
                cursor.execute(
                    """INSERT INTO user_oauth_accounts
                       (provider, provider_subject_hash, user_id, linked_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        provider, OAuthAccount._subject_hash(provider_subject),
                        user_id, datetime.now(),
                    ),
                )
            finally:
                close_cursor(cursor)

    @staticmethod
    def create_google_user(name, email, provider_subject):
        password_hash = generate_password_hash(secrets.token_urlsafe(48))
        with immediate_transaction() as conn:
            cursor = get_cursor(conn)
            try:
                cursor.execute(
                    'INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)',
                    (name, email, password_hash),
                )
                user_id = cursor.lastrowid
                cursor.execute(
                    """INSERT INTO user_oauth_accounts
                       (provider, provider_subject_hash, user_id, linked_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        OAuthAccount.PROVIDER_GOOGLE,
                        OAuthAccount._subject_hash(provider_subject),
                        user_id,
                        datetime.now(),
                    ),
                )
            finally:
                close_cursor(cursor)

        from models.user import User
        return User.get_by_id(user_id)
