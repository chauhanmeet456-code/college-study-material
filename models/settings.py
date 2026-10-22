from utils.database import get_connection, get_cursor, close_cursor
from datetime import datetime

class Settings:
    @staticmethod
    def get(key, default=None):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row['value'] if row else default
        finally:
            close_cursor(cursor)
            conn.close()
    
    @staticmethod
    def set(key, value):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)", (key, value, datetime.now()))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()
    
    @staticmethod
    def get_all():
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT key, value FROM settings")
            rows = cursor.fetchall()
            return {row['key']: row['value'] for row in rows}
        finally:
            close_cursor(cursor)
            conn.close()
