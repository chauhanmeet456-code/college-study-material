from utils.database import get_connection, get_cursor, close_cursor
from config import Config
from flask import session

class GuestActivity:
    @staticmethod
    def get_session_id():
        if 'guest_session_id' not in session:
            import secrets
            session['guest_session_id'] = secrets.token_hex(16)
        return session['guest_session_id']

    @staticmethod
    def get_view_count():
        if 'user_id' in session:
            return None
        session_id = GuestActivity.get_session_id()
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT COUNT(*) as count FROM guest_views WHERE session_id = ?", (session_id,))
            return cursor.fetchone()['count']
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_download_count():
        if 'user_id' in session:
            return None
        session_id = GuestActivity.get_session_id()
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT COUNT(*) as count FROM guest_downloads WHERE session_id = ?", (session_id,))
            return cursor.fetchone()['count']
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def can_view_material():
        if 'user_id' in session:
            return True
        session_id = GuestActivity.get_session_id()
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT COUNT(*) as count FROM guest_views WHERE session_id = ?", (session_id,))
            count = cursor.fetchone()['count']
            return count < Config.GUEST_VIEW_LIMIT
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def can_download_material():
        if 'user_id' in session:
            return True
        session_id = GuestActivity.get_session_id()
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT COUNT(*) as count FROM guest_downloads WHERE session_id = ?", (session_id,))
            count = cursor.fetchone()['count']
            return count < Config.GUEST_DOWNLOAD_LIMIT
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def record_view(material_id):
        if 'user_id' in session:
            return
        session_id = GuestActivity.get_session_id()
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("INSERT INTO guest_views (session_id, material_id) VALUES (?, ?)", (session_id, material_id))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def record_download(material_id):
        if 'user_id' in session:
            return
        session_id = GuestActivity.get_session_id()
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("INSERT INTO guest_downloads (session_id, material_id) VALUES (?, ?)", (session_id, material_id))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()
