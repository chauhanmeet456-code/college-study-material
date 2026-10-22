from utils.database import get_connection, get_cursor, close_cursor
from datetime import datetime

class ContactMessage:
    @staticmethod
    def create(name, email, subject, message):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "INSERT INTO contact_messages (name, email, subject, message) VALUES (?, ?, ?, ?)",
                (name, email, subject, message)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_all(page=1, per_page=10, read_filter='all'):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = []
            params = []
            if read_filter == 'read':
                where.append("is_read = 1")
            elif read_filter == 'unread':
                where.append("is_read = 0")
            where_clause = "WHERE " + " AND ".join(where) if where else ""
            cursor = get_cursor(conn)
            cursor.execute(f"SELECT * FROM contact_messages {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [per_page, offset])
            messages = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) as total FROM contact_messages {where_clause}", params)
            total = cursor.fetchone()['total']
            return messages, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_id(message_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM contact_messages WHERE id = ?", (message_id,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def mark_read(message_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE contact_messages SET is_read = 1 WHERE id = ?", (message_id,))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def delete(message_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("DELETE FROM contact_messages WHERE id = ?", (message_id,))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

class Announcement:
    @staticmethod
    def create(title, description):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("INSERT INTO announcements (title, description) VALUES (?, ?)", (title, description))
            conn.commit()
            return cursor.lastrowid
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_all_active(limit=5):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM announcements WHERE is_active = 1 ORDER BY created_at DESC LIMIT ?", (limit,))
            return cursor.fetchall()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_all_admin(search='', status='all', page=1, per_page=10):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = []
            params = []
            if search:
                where.append("title LIKE ?")
                params.append(f"%{search}%")
            if status == 'active':
                where.append("is_active = 1")
            elif status == 'inactive':
                where.append("is_active = 0")
            where_clause = "WHERE " + " AND ".join(where) if where else ""
            cursor = get_cursor(conn)
            cursor.execute(f"SELECT * FROM announcements {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [per_page, offset])
            announcements = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) as total FROM announcements {where_clause}", params)
            total = cursor.fetchone()['total']
            return announcements, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_id(announcement_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM announcements WHERE id = ?", (announcement_id,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update(announcement_id, **kwargs):
        conn = get_connection()
        cursor = None
        try:
            allowed = ['title', 'description', 'is_active']
            fields = []
            values = []
            for key, value in kwargs.items():
                if key in allowed:
                    fields.append(f"{key} = ?")
                    values.append(value)
            if not fields:
                return
            values.append(announcement_id)
            cursor = get_cursor(conn)
            cursor.execute(f"UPDATE announcements SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def delete(announcement_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()
