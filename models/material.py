from utils.database import get_connection, get_cursor, close_cursor
from datetime import datetime
from config import Config

class Material:
    @staticmethod
    def create(title, description, subject_id, category_id, file_name, file_path, file_type, file_size, uploaded_by=None, is_featured=0, is_active=1):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "INSERT INTO materials (title, description, subject_id, category_id, file_name, file_path, file_type, file_size, uploaded_by, is_featured, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (title, description, subject_id, category_id, file_name, file_path, file_type, file_size, uploaded_by, is_featured, is_active)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_id(material_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM materials WHERE id = ?", (material_id,))
            return cursor.fetchone()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_all(filters=None, sort='latest', page=1, per_page=12):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = ["m.is_active = 1"]
            params = []
            if filters:
                if filters.get('subject_id'):
                    where.append("m.subject_id = ?")
                    params.append(filters['subject_id'])
                if filters.get('category_id'):
                    where.append("m.category_id = ?")
                    params.append(filters['category_id'])
                if filters.get('file_type'):
                    where.append("m.file_type = ?")
                    params.append(filters['file_type'])
                if filters.get('search'):
                    where.append("(m.title LIKE ? OR m.description LIKE ?)")
                    params.extend([f"%{filters['search']}%", f"%{filters['search']}%"])
            where_clause = "WHERE " + " AND ".join(where)
            sort_clause = "ORDER BY m.uploaded_at DESC"
            if sort == 'oldest':
                sort_clause = "ORDER BY m.uploaded_at ASC"
            elif sort == 'most_downloaded':
                sort_clause = "ORDER BY m.download_count DESC"
            elif sort == 'most_viewed':
                sort_clause = "ORDER BY m.view_count DESC"
            elif sort == 'a_z':
                sort_clause = "ORDER BY m.title ASC"
            cursor = get_cursor(conn)
            cursor.execute(f"""
                SELECT m.*, s.name as subject_name, s.code as subject_code, c.name as category_name
                FROM materials m
                JOIN subjects s ON m.subject_id = s.id
                JOIN categories c ON m.category_id = c.id
                {where_clause}
                {sort_clause}
                LIMIT ? OFFSET ?
            """, params + [per_page, offset])
            materials = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) as total FROM materials m {where_clause}", params)
            total = cursor.fetchone()['total']
            return materials, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_featured(limit=6):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("""
                SELECT m.*, s.name as subject_name, s.code as subject_code, c.name as category_name
                FROM materials m
                JOIN subjects s ON m.subject_id = s.id
                JOIN categories c ON m.category_id = c.id
                WHERE m.is_featured = 1 AND m.is_active = 1
                ORDER BY m.uploaded_at DESC
                LIMIT ?
            """, (limit,))
            return cursor.fetchall()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_latest(limit=6):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("""
                SELECT m.*, s.name as subject_name, s.code as subject_code, c.name as category_name
                FROM materials m
                JOIN subjects s ON m.subject_id = s.id
                JOIN categories c ON m.category_id = c.id
                WHERE m.is_active = 1
                ORDER BY m.uploaded_at DESC
                LIMIT ?
            """, (limit,))
            return cursor.fetchall()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_category(category_id, page=1, per_page=12):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            cursor = get_cursor(conn)
            cursor.execute("""
                SELECT m.*, s.name as subject_name, s.code as subject_code, c.name as category_name
                FROM materials m
                JOIN subjects s ON m.subject_id = s.id
                JOIN categories c ON m.category_id = c.id
                WHERE m.category_id = ? AND m.is_active = 1
                ORDER BY m.uploaded_at DESC
                LIMIT ? OFFSET ?
            """, (category_id, per_page, offset))
            materials = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) as total FROM materials WHERE category_id = ? AND is_active = 1", (category_id,))
            total = cursor.fetchone()['total']
            return materials, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_subject(subject_id, category_id=None, page=1, per_page=12):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = ["m.subject_id = ?", "m.is_active = 1"]
            params = [subject_id]
            if category_id:
                where.append("m.category_id = ?")
                params.append(category_id)
            where_clause = "WHERE " + " AND ".join(where)
            cursor = get_cursor(conn)
            cursor.execute(f"""
                SELECT m.*, s.name as subject_name, s.code as subject_code, c.name as category_name
                FROM materials m
                JOIN subjects s ON m.subject_id = s.id
                JOIN categories c ON m.category_id = c.id
                {where_clause}
                ORDER BY m.uploaded_at DESC
                LIMIT ? OFFSET ?
            """, params + [per_page, offset])
            materials = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) as total FROM materials m {where_clause}", params)
            total = cursor.fetchone()['total']
            return materials, total
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def increment_view(material_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE materials SET view_count = view_count + 1 WHERE id = ?", (material_id,))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def increment_download(material_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("UPDATE materials SET download_count = download_count + 1 WHERE id = ?", (material_id,))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update(material_id, **kwargs):
        conn = get_connection()
        cursor = None
        try:
            allowed = ['title', 'description', 'subject_id', 'category_id', 'is_featured', 'is_active']
            fields = []
            values = []
            for key, value in kwargs.items():
                if key in allowed:
                    fields.append(f"{key} = ?")
                    values.append(value)
            if not fields:
                return
            values.append(material_id)
            cursor = get_cursor(conn)
            cursor.execute(f"UPDATE materials SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def update_file(material_id, file_name, file_path, file_type, file_size):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute(
                "UPDATE materials SET file_name = ?, file_path = ?, file_type = ?, file_size = ? WHERE id = ?",
                (file_name, file_path, file_type, file_size, material_id)
            )
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def delete(material_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("DELETE FROM materials WHERE id = ?", (material_id,))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_all_admin(search='', subject_id=None, category_id=None, featured=None, status='all', page=1, per_page=10):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            where = []
            params = []
            if search:
                where.append("(m.title LIKE ? OR m.description LIKE ?)")
                params.extend([f"%{search}%", f"%{search}%"])
            if subject_id:
                where.append("m.subject_id = ?")
                params.append(subject_id)
            if category_id:
                where.append("m.category_id = ?")
                params.append(category_id)
            if featured is not None:
                where.append("m.is_featured = ?")
                params.append(featured)
            if status == 'active':
                where.append("m.is_active = 1")
            elif status == 'inactive':
                where.append("m.is_active = 0")
            where_clause = "WHERE " + " AND ".join(where) if where else ""
            cursor = get_cursor(conn)
            cursor.execute(f"""
                SELECT m.*, s.name as subject_name, c.name as category_name
                FROM materials m
                JOIN subjects s ON m.subject_id = s.id
                JOIN categories c ON m.category_id = c.id
                {where_clause}
                ORDER BY m.uploaded_at DESC
                LIMIT ? OFFSET ?
            """, params + [per_page, offset])
            materials = cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) as total FROM materials m {where_clause}", params)
            total = cursor.fetchone()['total']
            return materials, total
        finally:
            close_cursor(cursor)
            conn.close()

class Favorite:
    @staticmethod
    def add(user_id, material_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            if Config.USE_SQLITE:
                cursor.execute("INSERT OR IGNORE INTO favorites (user_id, material_id) VALUES (?, ?)", (user_id, material_id))
            elif Config.USE_POSTGRES:
                cursor.execute("INSERT INTO favorites (user_id, material_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user_id, material_id))
            else:
                cursor.execute("INSERT IGNORE INTO favorites (user_id, material_id) VALUES (%s, %s)", (user_id, material_id))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def remove(user_id, material_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("DELETE FROM favorites WHERE user_id = ? AND material_id = ?", (user_id, material_id))
            conn.commit()
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def is_favorited(user_id, material_id):
        conn = get_connection()
        cursor = None
        try:
            cursor = get_cursor(conn)
            cursor.execute("SELECT * FROM favorites WHERE user_id = ? AND material_id = ?", (user_id, material_id))
            return cursor.fetchone() is not None
        finally:
            close_cursor(cursor)
            conn.close()

    @staticmethod
    def get_by_user(user_id, page=1, per_page=12):
        conn = get_connection()
        cursor = None
        try:
            offset = (page - 1) * per_page
            cursor = get_cursor(conn)
            cursor.execute("""
                SELECT m.*, s.name as subject_name, s.code as subject_code, c.name as category_name
                FROM materials m
                JOIN favorites f ON m.id = f.material_id
                JOIN subjects s ON m.subject_id = s.id
                JOIN categories c ON m.category_id = c.id
                WHERE f.user_id = ? AND m.is_active = 1
                ORDER BY f.created_at DESC
                LIMIT ? OFFSET ?
            """, (user_id, per_page, offset))
            materials = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) as total FROM favorites WHERE user_id = ?", (user_id,))
            total = cursor.fetchone()['total']
            return materials, total
        finally:
            close_cursor(cursor)
            conn.close()
