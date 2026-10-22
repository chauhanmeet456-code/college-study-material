from utils.database import get_connection, get_cursor, close_cursor
from datetime import datetime

class Subject:
    @staticmethod
    def create(name, code, description='', icon='fa-book'):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "INSERT INTO subjects (name, code, description, icon) VALUES (?, ?, ?, ?)",
                (name, code, description, icon)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_id(subject_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_code(code):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM subjects WHERE code = ?", (code,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_all(active_only=True):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            if active_only:
                cursor.execute("SELECT * FROM subjects WHERE is_active = 1 ORDER BY name ASC")
            else:
                cursor.execute("SELECT * FROM subjects ORDER BY name ASC")
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
                where.append("(name LIKE ? OR code LIKE ?)")
                params.extend([f"%{search}%", f"%{search}%"])
            if status == 'active':
                where.append("is_active = 1")
            elif status == 'inactive':
                where.append("is_active = 0")
            where_clause = "WHERE " + " AND ".join(where) if where else ""
            cursor = get_cursor(conn)
            cursor.execute(f"SELECT * FROM subjects {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [per_page, offset])
            subjects = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) as total FROM subjects {where_clause}", params)
            total = cursor.fetchone()['total']
            return subjects, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update(subject_id, **kwargs):
        conn = get_connection()
        cursor = None
        try:
            allowed = ['name', 'code', 'description', 'icon', 'is_active']
            fields = []
            values = []
            for key, value in kwargs.items():
                if key in allowed:
                    fields.append(f"{key} = ?")
                    values.append(value)
            if not fields:
                return
            values.append(subject_id)
            cursor = get_cursor(conn)
            cursor.execute(f"UPDATE subjects SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def delete(subject_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_material_count(subject_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT COUNT(*) as count FROM materials WHERE subject_id = ? AND is_active = 1", (subject_id,))
            return cursor.fetchone()['count']
        finally:
            close_cursor(cursor)
            conn.close()

class Category:
    @staticmethod
    def get_all(active_only=True):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM categories ORDER BY name ASC")
            return cursor.fetchall()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_id(category_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_name(name):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM categories WHERE name = ?", (name,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def create(name, description=''):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("INSERT INTO categories (name, description) VALUES (?, ?)", (name, description))
            conn.commit()
            return cursor.lastrowid
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update(category_id, **kwargs):
        conn = get_connection()
        cursor = None
        try:
            allowed = ['name', 'description']
            fields = []
            values = []
            for key, value in kwargs.items():
                if key in allowed:
                    fields.append(f"{key} = ?")
                    values.append(value)
            if not fields:
                return
            values.append(category_id)
            cursor = get_cursor(conn)
            cursor.execute(f"UPDATE categories SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def delete(category_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()
