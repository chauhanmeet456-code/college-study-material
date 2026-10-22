"""Models for login sessions, login history and security event logging.

All storage is server side and only a hash of the random session token is
persisted.  The raw token lives solely in the signed Flask session cookie, so a
database leak cannot be replayed as a live session and a token that is revoked
or expired on the server can no longer be used even if the cookie is still
present in a browser.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from utils.database import (
    get_connection,
    get_cursor,
    close_cursor,
    immediate_transaction,
)


def generate_session_token():
    """Return a new cryptographically random session token."""
    return secrets.token_urlsafe(32)


def hash_session_token(token):
    """Hash a session token for storage. Plain tokens are never persisted."""
    if not token:
        return None
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _normalize_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace('T', ' ')
    formats = (
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        try:
            parsed = parsed.replace(tzinfo=None)
        except Exception:
            pass
        return parsed
    return None


def _as_utc_naive(value):
    parsed = _normalize_datetime(value)
    if parsed is None:
        return None
    try:
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
    except Exception:
        pass
    return parsed


class UserSession:
    """A server side login session for a student (non admin) account."""

    STATUS_ACTIVE = 'active'
    STATUS_LOGGED_OUT = 'logged_out'
    STATUS_REVOKED = 'revoked'
    STATUS_EXPIRED = 'expired'

    VALID_STATUSES = (
        STATUS_ACTIVE,
        STATUS_LOGGED_OUT,
        STATUS_REVOKED,
        STATUS_EXPIRED,
    )

    @staticmethod
    def create(conn, user_id, token_hash, device_name, browser_name,
               operating_system, ip_address, expires_at):
        """Insert a new session row using an existing open transaction."""
        if conn is None:
            raise ValueError('a transaction connection is required')
        cursor = get_cursor(conn)
        cursor.execute(
            """INSERT INTO user_sessions
               (user_id, session_token_hash, device_name, browser_name,
                operating_system, ip_address, created_at, last_activity_at,
                expires_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                user_id, token_hash, device_name, browser_name,
                operating_system, ip_address,
                datetime.now(), datetime.now(), expires_at,
            ),
        )
        session_id = cursor.lastrowid
        close_cursor(cursor)
        return session_id

    @staticmethod
    def get_by_token_hash(token_hash):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "SELECT * FROM user_sessions WHERE session_token_hash = ?",
                (token_hash,),
            )
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_id(session_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM user_sessions WHERE id = ?", (session_id,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_active_for_user(conn, user_id, limit=1):
        """Return the active sessions for a user (in a transaction)."""
        cursor = get_cursor(conn)
        cursor.execute(
            "SELECT * FROM user_sessions WHERE user_id = ? AND status = 'active' "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = cursor.fetchall()
        close_cursor(cursor)
        return rows

    @staticmethod
    def get_active_sessions_for_user(user_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "SELECT id, device_name, browser_name, operating_system, "
                "ip_address, created_at, last_activity_at, expires_at "
                "FROM user_sessions WHERE user_id = ? AND status = 'active' "
                "ORDER BY last_activity_at DESC, id DESC",
                (user_id,),
            )
            return cursor.fetchall()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def touch(conn, session_id, when=None):
        """Update last_activity_at for a session inside a transaction."""
        when = when or datetime.now()
        cursor = get_cursor(conn)
        cursor.execute(
            "UPDATE user_sessions SET last_activity_at = ? WHERE id = ?",
            (when, session_id),
        )
        close_cursor(cursor)

    @staticmethod
    def revoke(conn, session_id, reason, when=None):
        """Mark a session revoked and record why."""
        when = when or datetime.now()
        cursor = get_cursor(conn)
        cursor.execute(
            "UPDATE user_sessions SET status = 'revoked', revoked_at = ?, "
            "termination_reason = ? WHERE id = ?",
            (when, reason, session_id),
        )
        close_cursor(cursor)

    @staticmethod
    def mark_logged_out(conn, session_id, when=None):
        """Mark a session ended by an explicit user logout."""
        when = when or datetime.now()
        cursor = get_cursor(conn)
        cursor.execute(
            "UPDATE user_sessions SET status = 'logged_out', logout_at = ?, "
            "termination_reason = COALESCE(termination_reason, 'User logged out') "
            "WHERE id = ?",
            (when, session_id),
        )
        close_cursor(cursor)

    @staticmethod
    def mark_expired(conn, session_id, when=None):
        """Mark a session ended because it passed expires_at."""
        when = when or datetime.now()
        cursor = get_cursor(conn)
        cursor.execute(
            "UPDATE user_sessions SET status = 'expired', "
            "termination_reason = COALESCE(termination_reason, 'Session expired') "
            "WHERE id = ?",
            (session_id,),
        )
        close_cursor(cursor)

    @staticmethod
    def revoke_all_for_user(conn, user_id, reason, exclude_id=None, when=None):
        """Revoke every active session for a user, optionally keeping one."""
        when = when or datetime.now()
        query = (
            "UPDATE user_sessions SET status = 'revoked', revoked_at = ?, "
            "termination_reason = ? WHERE user_id = ? AND status = 'active'"
        )
        params = [when, reason, user_id]
        if exclude_id is not None:
            query += " AND id != ?"
            params.append(exclude_id)
        cursor = get_cursor(conn)
        cursor.execute(query, params)
        changed = cursor.rowcount
        close_cursor(cursor)
        return changed

    @staticmethod
    def count_active_for_user(user_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "SELECT COUNT(*) AS total FROM user_sessions "
                "WHERE user_id = ? AND status = 'active'",
                (user_id,),
            )
            row = cursor.fetchone()
            return row['total'] if row else 0
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_active_sessions_admin(page=1, per_page=20):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            cursor = get_cursor(conn)
            cursor.execute(
                """SELECT s.id, s.user_id, s.device_name, s.browser_name,
                          s.operating_system, s.ip_address, s.created_at,
                          s.last_activity_at, s.expires_at, u.name, u.email
                   FROM user_sessions s
                   LEFT JOIN users u ON u.id = s.user_id
                   WHERE s.status = 'active'
                   ORDER BY s.last_activity_at DESC, s.id DESC
                   LIMIT ? OFFSET ?""",
                (per_page, offset),
            )
            rows = cursor.fetchall()
            cursor.execute(
                "SELECT COUNT(*) AS total FROM user_sessions WHERE status = 'active'"
            )
            total = cursor.fetchone()['total']
            return rows, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def cleanup_expired(idle_minutes=60, absolute_hours=24):
        """Expire dormant sessions left open by a closed browser tab.

        Sessions flagged as expired already produce a login history event when
        they are detected on the next request; this is a sweep for rows that
        never come back.  Returns the number of rows updated.
        """
        conn = get_connection()
        cursor = None
        try:
            now = datetime.now()
            cursor = get_cursor(conn)
            cursor.execute(
                "UPDATE user_sessions SET status = 'expired', "
                "termination_reason = ? "
                "WHERE status = 'active' AND expires_at IS NOT NULL AND "
                "(last_activity_at < ? OR expires_at < ?)",
                (
                    'Session expired (idle timeout or lifetime exceeded)',
                    now - timedelta(minutes=idle_minutes),
                    now,
                ),
            )
            changed = cursor.rowcount
            conn.commit()
            return changed
        finally:
            close_cursor(cursor)
            conn.close()


class LoginHistory:
    """Immutable record of authentication events for a user."""

    EVENT_LOGIN_SUCCESS = 'login_success'
    EVENT_LOGIN_FAILED = 'login_failed'
    EVENT_LOGOUT = 'logout'
    EVENT_SESSION_REVOKED = 'session_revoked'
    EVENT_SESSION_EXPIRED = 'session_expired'
    EVENT_SESSION_REPLACED = 'session_replaced'
    EVENT_FORCED_LOGOUT = 'forced_logout'
    EVENT_ADMIN_REVOKED = 'admin_revoked'
    EVENT_RATE_LIMITED = 'rate_limited'

    RESULT_SUCCESS = 'success'
    RESULT_FAILURE = 'failure'
    RESULT_INFO = 'info'

    @staticmethod
    def create(conn, user_id, event_type, device_description=None,
               ip_address=None, login_method=None, result=RESULT_INFO,
               failure_reason=None, related_session_id=None):
        cursor = get_cursor(conn)
        cursor.execute(
            """INSERT INTO login_history
               (user_id, event_type, device_description, ip_address,
                login_method, result, failure_reason, related_session_id,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, event_type, device_description, ip_address,
                login_method, result, failure_reason, related_session_id,
                datetime.now(),
            ),
        )
        row_id = cursor.lastrowid
        close_cursor(cursor)
        return row_id

    @staticmethod
    def create_standalone(**kwargs):
        """Convenience wrapper for callers outside a transaction."""
        with immediate_transaction() as conn:
            return LoginHistory.create(conn, **kwargs)

    @staticmethod
    def get_for_user(user_id, page=1, per_page=15, event_type=None):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = ["user_id = ?"]
            params = [user_id]
            if event_type:
                where.append("event_type = ?")
                params.append(event_type)
            where_clause = "WHERE " + " AND ".join(where)
            cursor = get_cursor(conn)
            cursor.execute(
                f"SELECT * FROM login_history {where_clause} "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                params + [per_page, offset],
            )
            rows = cursor.fetchall()
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM login_history {where_clause}",
                params,
            )
            total = cursor.fetchone()['total']
            return rows, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_all(search='', user_id=None, event_type=None, status=None,
                date_from=None, date_to=None, page=1, per_page=20):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = []
            params = []
            if search:
                where.append("(u.name LIKE ? OR u.email LIKE ? OR lh.ip_address LIKE ?)")
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
            if user_id is not None:
                where.append("lh.user_id = ?")
                params.append(user_id)
            if event_type:
                where.append("lh.event_type = ?")
                params.append(event_type)
            if status:
                where.append("lh.result = ?")
                params.append(status)
            if date_from:
                where.append("lh.created_at >= ?")
                params.append(date_from)
            if date_to:
                where.append("lh.created_at <= ?")
                params.append(date_to)
            where_clause = ("WHERE " + " AND ".join(where)) if where else ""
            cursor = get_cursor(conn)
            cursor.execute(
                f"""SELECT lh.*, u.name AS user_name, u.email AS user_email
                    FROM login_history lh
                    LEFT JOIN users u ON u.id = lh.user_id
                    {where_clause}
                    ORDER BY lh.created_at DESC, lh.id DESC
                    LIMIT ? OFFSET ?""",
                params + [per_page, offset],
            )
            rows = cursor.fetchall()
            cursor.execute(
                f"""SELECT COUNT(*) AS total FROM login_history lh
                    LEFT JOIN users u ON u.id = lh.user_id {where_clause}""",
                params,
            )
            total = cursor.fetchone()['total']
            return rows, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_last_success_for_user(user_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "SELECT * FROM login_history WHERE user_id = ? AND event_type = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (user_id, LoginHistory.EVENT_LOGIN_SUCCESS),
            )
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()


class FailedLoginAttempt:
    """Failed authentication attempts. Passwords are never stored."""

    @staticmethod
    def record(email, ip_address, device_description=None, user_id=None,
               failure_category='invalid_credentials', rate_limited=False):
        with immediate_transaction() as conn:
            cursor = get_cursor(conn)
            cursor.execute(
                """INSERT INTO failed_login_attempts
                   (user_id, email_masked, ip_address, device_description,
                    failure_category, rate_limited, attempted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, mask_email(email), ip_address, device_description,
                    failure_category, 1 if rate_limited else 0, datetime.now(),
                ),
            )
            row_id = cursor.lastrowid
            close_cursor(cursor)
            return row_id

    @staticmethod
    def count_recent_for_identifier(email, minutes=15):
        conn = get_connection()
        cursor = None
        try:
            since = datetime.now() - timedelta(minutes=minutes)
            cursor = get_cursor(conn)
            cursor.execute(
                "SELECT COUNT(*) AS total FROM failed_login_attempts "
                "WHERE email_masked = ? AND attempted_at >= ?",
                (mask_email(email), since),
            )
            row = cursor.fetchone()
            return row['total'] if row else 0
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def count_recent_for_ip(ip_address, minutes=15):
        conn = get_connection()
        cursor = None
        try:
            since = datetime.now() - timedelta(minutes=minutes)
            cursor = get_cursor(conn)
            cursor.execute(
                "SELECT COUNT(*) AS total FROM failed_login_attempts "
                "WHERE ip_address = ? AND attempted_at >= ?",
                (ip_address, since),
            )
            row = cursor.fetchone()
            return row['total'] if row else 0
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_recent(page=1, per_page=20, email=None, ip_address=None):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = []
            params = []
            if email:
                where.append("email_masked LIKE ?")
                params.append(f"%{mask_email(email)}%")
            if ip_address:
                where.append("ip_address = ?")
                params.append(ip_address)
            where_clause = ("WHERE " + " AND ".join(where)) if where else ""
            cursor = get_cursor(conn)
            cursor.execute(
                f"SELECT * FROM failed_login_attempts {where_clause} "
                "ORDER BY attempted_at DESC, id DESC LIMIT ? OFFSET ?",
                params + [per_page, offset],
            )
            rows = cursor.fetchall()
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM failed_login_attempts {where_clause}",
                params,
            )
            total = cursor.fetchone()['total']
            return rows, total
        finally:
            close_cursor(cursor)
            conn.close()


class AdminAuditLog:
    """Audit trail for administrative security actions."""

    @staticmethod
    def log(conn, admin_id, action, target_type=None, target_id=None,
            details=None, ip_address=None):
        """Write an audit entry. Pass a transaction connection or None."""
        def _write(connection):
            cursor = get_cursor(connection)
            cursor.execute(
                """INSERT INTO admin_audit_log
                   (admin_id, action, target_type, target_id, details,
                    ip_address, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    admin_id, action, target_type, target_id,
                    details if details is None else str(details),
                    ip_address, datetime.now(),
                ),
            )
            row_id = cursor.lastrowid
            close_cursor(cursor)
            return row_id

        if conn is not None:
            return _write(conn)
        with immediate_transaction() as connection:
            return _write(connection)

    @staticmethod
    def get_all(page=1, per_page=20, action=None, admin_id=None):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = []
            params = []
            if action:
                where.append("al.action = ?")
                params.append(action)
            if admin_id is not None:
                where.append("al.admin_id = ?")
                params.append(admin_id)
            where_clause = ("WHERE " + " AND ".join(where)) if where else ""
            cursor = get_cursor(conn)
            cursor.execute(
                f"""SELECT al.*, a.name AS admin_name, a.username AS admin_username
                    FROM admin_audit_log al
                    LEFT JOIN admins a ON a.id = al.admin_id
                    {where_clause}
                    ORDER BY al.created_at DESC, al.id DESC
                    LIMIT ? OFFSET ?""",
                params + [per_page, offset],
            )
            rows = cursor.fetchall()
            cursor.execute(
                f"""SELECT COUNT(*) AS total FROM admin_audit_log al
                    LEFT JOIN admins a ON a.id = al.admin_id {where_clause}""",
                params,
            )
            total = cursor.fetchone()['total']
            return rows, total
        finally:
            close_cursor(cursor)
            conn.close()


def mask_email(email):
    """Reduce an identifier to the minimum needed for rate limiting lookups."""
    if not email:
        return None
    email = str(email).strip().lower()
    if '@' not in email:
        return email[:1] + '***'
    local, _, domain = email.partition('@')
    if len(local) <= 1:
        masked_local = local[:1] + '***'
    else:
        masked_local = local[:2] + '***'
    return f'{masked_local}@{domain}'
