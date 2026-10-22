from utils.database import get_connection, get_cursor, close_cursor
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

class Admin:
    @staticmethod
    def create(name, username, email, password):
        password_hash = generate_password_hash(password)
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "INSERT INTO admins (name, username, email, password_hash) VALUES (?, ?, ?, ?)",
                (name, username, email, password_hash)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_username(username):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM admins WHERE username = ?", (username,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_email(email):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM admins WHERE email = ?", (email,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_id(admin_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM admins WHERE id = ?", (admin_id,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_all(search='', page=1, per_page=10):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = []
            params = []
            if search:
                where.append("(name LIKE ? OR username LIKE ? OR email LIKE ?)")
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
            where_clause = "WHERE " + " AND ".join(where) if where else ""
            cursor = get_cursor(conn)
            cursor.execute(f"SELECT * FROM admins {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [per_page, offset])
            admins = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) as total FROM admins {where_clause}", params)
            total = cursor.fetchone()['total']
            return admins, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update(admin_id, **kwargs):
        conn = get_connection()
        cursor = None
        try:
            allowed = ['name', 'username', 'email', 'profile_image']
            fields = []
            values = []
            for key, value in kwargs.items():
                if key in allowed:
                    fields.append(f"{key} = ?")
                    values.append(value)
            if not fields:
                return
            values.append(admin_id)
            cursor = get_cursor(conn)
            cursor.execute(f"UPDATE admins SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update_password(admin_id, new_password):
        password_hash = generate_password_hash(new_password)
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE admins SET password_hash = ? WHERE id = ?", (password_hash, admin_id))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update_last_login(admin_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE admins SET last_login = ? WHERE id = ?", (datetime.now(), admin_id))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def verify_password(admin, password):
        return check_password_hash(admin['password_hash'], password)

    @staticmethod
    def set_reset_token(admin_id, token, expires_at):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE admins SET reset_token = ?, reset_token_expires_at = ? WHERE id = ?", (token, expires_at, admin_id))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_reset_token(token):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM admins WHERE reset_token = ?", (token,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def clear_reset_token(admin_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE admins SET reset_token = NULL, reset_token_expires_at = NULL WHERE id = ?", (admin_id,))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()
