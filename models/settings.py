from utils.database import get_connection, get_cursor, close_cursor
from datetime import datetime
import time
import threading

_settings_cache = None
_settings_cache_time = 0
_settings_cache_lock = threading.Lock()
_SETTINGS_CACHE_TTL = 60


class Settings:
    @staticmethod
    def get(key, default=None):
        all_settings = Settings.get_all()
        return all_settings.get(key, default)

    @staticmethod
    def set(key, value):
        global _settings_cache, _settings_cache_time
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)", (key, value, datetime.now()))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()
        with _settings_cache_lock:
            _settings_cache = None
            _settings_cache_time = 0

    @staticmethod
    def get_all():
        global _settings_cache, _settings_cache_time
        now = time.time()
        with _settings_cache_lock:
            if _settings_cache is not None and (now - _settings_cache_time) < _SETTINGS_CACHE_TTL:
                return _settings_cache.copy()

        all_settings = Settings._fetch_all()
        with _settings_cache_lock:
            _settings_cache = all_settings
            _settings_cache_time = now
        return all_settings.copy()

    @staticmethod
    def _fetch_all():
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
