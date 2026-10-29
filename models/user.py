from utils.database import get_connection, get_cursor, close_cursor
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

class User:
    @staticmethod
    def create(name, email, password):
        password_hash = generate_password_hash(password)
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                (name, email, password_hash)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_email(email):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_id(user_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def restore(user_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE users SET is_active = 1, deleted_by_user = 0, deletion_requested_at = NULL, deletion_processed = NULL WHERE id = ?", (user_id,))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update(user_id, **kwargs):
        conn = get_connection()
        cursor = None
        try:
            allowed = ['name', 'email', 'profile_image', 'is_active']
            fields = []
            values = []
            for key, value in kwargs.items():
                if key in allowed:
                    fields.append(f"{key} = ?")
                    values.append(value)
            if not fields:
                return
            values.append(user_id)
            cursor = get_cursor(conn)
            cursor.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update_password(user_id, new_password):
        password_hash = generate_password_hash(new_password)
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update_last_login(user_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE users SET last_login = ? WHERE id = ?", (datetime.now(), user_id))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_all(search='', status='all', page=1, per_page=10):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = ["deleted_by_user = 0"]
            params = []
            if search:
                where.append("(name LIKE ? OR email LIKE ?)")
                params.extend([f"%{search}%", f"%{search}%"])
            if status == 'active':
                where.append("is_active = 1")
            elif status == 'inactive':
                where.append("is_active = 0")
            where_clause = "WHERE " + " AND ".join(where)
            cursor = get_cursor(conn)
            cursor.execute(f"SELECT id, name, email, profile_image, is_active, created_at, last_login, deleted_by_user, deletion_requested_at FROM users {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [per_page, offset])
            users = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) as total FROM users {where_clause}", params)
            total = cursor.fetchone()['total']
            return users, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def delete(user_id, conn=None):
        own_conn = conn is None
        if own_conn:
            conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE users SET is_active = 0, deleted_by_user = 1, deletion_requested_at = ? WHERE id = ?", (datetime.now(), user_id))
            if own_conn:
                conn.commit()
            rc = cursor.rowcount
            return rc > 0 if rc >= 0 else True
        finally:
            close_cursor(cursor)
            if own_conn:
                conn.close()
    
    @staticmethod
    def permanent_delete(user_id, conn=None):
        own_conn = conn is None
        if own_conn:
            conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            if own_conn:
                conn.commit()
            rc = cursor.rowcount
            return rc > 0 if rc != -1 else True
        finally:
            close_cursor(cursor)
            if own_conn:
                conn.close()
    
    @staticmethod
    def get_deleted_users(search='', page=1, per_page=10):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = ["deleted_by_user = 1", "deletion_processed IS NOT NULL"]
            params = []
            if search:
                where.append("(name LIKE ? OR email LIKE ?)")
                params.extend([f"%{search}%", f"%{search}%"])
            where_clause = "WHERE " + " AND ".join(where)
            cursor = get_cursor(conn)
            cursor.execute(f"SELECT id, name, email, profile_image, is_active, created_at, last_login, deleted_by_user, deletion_requested_at, deletion_processed FROM users {where_clause} ORDER BY deletion_processed DESC LIMIT ? OFFSET ?", params + [per_page, offset])
            users = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) as total FROM users {where_clause}", params)
            total = cursor.fetchone()['total']
            return users, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_never_logged_in_users(search='', status='all', page=1, per_page=10):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = ["last_login IS NULL", "deleted_by_user = 0"]
            params = []
            if search:
                where.append("(name LIKE ? OR email LIKE ?)")
                params.extend([f"%{search}%", f"%{search}%"])
            if status == 'active':
                where.append("is_active = 1")
            elif status == 'inactive':
                where.append("is_active = 0")
            where_clause = "WHERE " + " AND ".join(where)
            cursor = get_cursor(conn)
            cursor.execute(
                f"SELECT id, name, email, profile_image, is_active, "
                f"created_at, last_login, deleted_by_user, deletion_requested_at "
                f"FROM users {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [per_page, offset]
            )
            users = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) as total FROM users {where_clause}", params)
            total = cursor.fetchone()['total']
            return users, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def request_deletion(user_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE users SET deleted_by_user = 1, deletion_requested_at = ? WHERE id = ?", (datetime.now(), user_id))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()
    
    @staticmethod
    def get_deletion_requests():
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM users WHERE deleted_by_user = 1 AND deletion_processed IS NULL ORDER BY deletion_requested_at DESC")
            return cursor.fetchall()
        finally:
            close_cursor(cursor)
            conn.close()
    
    @staticmethod
    def mark_deletion_processed(user_id, conn=None):
        own_conn = conn is None
        if own_conn:
            conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE users SET deletion_processed = ? WHERE id = ?", (datetime.now(), user_id))
            if own_conn:
                conn.commit()
        finally:
            close_cursor(cursor)
            if own_conn:
                conn.close()
    
    @staticmethod
    def has_deletion_request(user_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT deleted_by_user FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return row and row['deleted_by_user'] == 1
        finally:
            close_cursor(cursor)
            conn.close()
    
    @staticmethod
    def verify_password(user, password):
        return check_password_hash(user['password_hash'], password)
