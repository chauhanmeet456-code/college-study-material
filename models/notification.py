from datetime import datetime

from utils.database import close_cursor, get_connection, get_cursor


class Notification:
    @staticmethod
    def create_for_users(title, message, link=None, dedupe_key=None, audience='all'):
        conn = get_connection()
        cursor = None
        created = []
        try:
            cursor = get_cursor(conn)
            dialect = _dialect_name()
            where = "WHERE deleted_by_user = 0"
            if audience == 'active':
                where += " AND is_active = 1"
            cursor.execute("SELECT id FROM users " + where)
            user_ids = [row['id'] for row in cursor.fetchall()]
            created_at = datetime.now()

            for user_id in user_ids:
                if dedupe_key:
                    if dialect == 'postgres':
                        sql = (
                            "INSERT INTO user_notifications "
                            "(user_id, title, message, link, dedupe_key) "
                            "VALUES (?, ?, ?, ?, ?) "
                            "ON CONFLICT (user_id, dedupe_key) DO NOTHING"
                        )
                    elif dialect == 'mysql':
                        sql = (
                            "INSERT IGNORE INTO user_notifications "
                            "(user_id, title, message, link, dedupe_key) "
                            "VALUES (?, ?, ?, ?, ?)"
                        )
                    else:
                        sql = (
                            "INSERT OR IGNORE INTO user_notifications "
                            "(user_id, title, message, link, dedupe_key) "
                            "VALUES (?, ?, ?, ?, ?)"
                        )
                    cursor.execute(sql, (user_id, title, message, link, dedupe_key))
                else:
                    cursor.execute(
                        "INSERT INTO user_notifications "
                        "(user_id, title, message, link, dedupe_key) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (user_id, title, message, link, None),
                    )

                if cursor.rowcount:
                    created.append(user_id)

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            close_cursor(cursor)
            conn.close()

        if created:
            from utils.realtime import emit_notification

            payload = {
                'title': title,
                'message': message,
                'link': link,
                'created_at': created_at.isoformat(),
                'is_read': False,
            }
            for user_id in created:
                emit_notification(user_id, payload)
        return len(created)

    @staticmethod
    def get_for_user(user_id, limit=30):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "SELECT id, title, message, link, created_at, is_read "
                "FROM user_notifications WHERE user_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (user_id, limit),
            )
            notifications = cursor.fetchall()
            cursor.execute(
                "SELECT COUNT(*) AS total FROM user_notifications "
                "WHERE user_id = ? AND is_read = 0",
                (user_id,),
            )
            unread_count = cursor.fetchone()['total']
            return notifications, unread_count
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def mark_read(user_id, notification_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "UPDATE user_notifications SET is_read = 1 "
                "WHERE id = ? AND user_id = ?",
                (notification_id, user_id),
            )
            updated = cursor.rowcount > 0
            if not updated:
                cursor.execute(
                    "SELECT 1 FROM user_notifications WHERE id = ? AND user_id = ?",
                    (notification_id, user_id),
                )
                updated = cursor.fetchone() is not None
            conn.commit()
            return updated
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def mark_all_read(user_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "UPDATE user_notifications SET is_read = 1 "
                "WHERE user_id = ? AND is_read = 0",
                (user_id,),
            )
            changed = cursor.rowcount
            conn.commit()
            return changed
        finally:
            close_cursor(cursor)
            conn.close()


def _dialect_name():
    from config import Config

    if Config.USE_SQLITE:
        return 'sqlite'
    if Config.USE_POSTGRES:
        return 'postgres'
    return 'mysql'


def notify_material_published(material_id):
    from utils.database import get_connection, get_cursor

    conn = get_connection()
    cursor = None
    try:
        cursor = get_cursor(conn)
        cursor.execute(
            "SELECT m.title, c.name AS category_name FROM materials m "
            "JOIN categories c ON c.id = m.category_id "
            "WHERE m.id = ? AND m.is_active = 1",
            (material_id,),
        )
        material = cursor.fetchone()
    finally:
        close_cursor(cursor)
        conn.close()

    if not material:
        return 0
    category = material['category_name']
    if category.lower() not in ('notes', 'book', 'assignment', 'practical'):
        return 0
    label = {'book': 'book', 'assignment': 'assignment'}.get(category.lower(), category.lower())
    return Notification.create_for_users(
        'New {} published'.format(label.capitalize()),
        material['title'],
        '/material/{}'.format(material_id),
        dedupe_key='material:{}'.format(material_id),
        audience='active',
    )
