# Project Memory - StudyHub

## Key Decisions

### Database
- Default: SQLite (file-based, no server needed) for development
- Production: PostgreSQL / NeonDB (via `DATABASE_URL`)
- Alternative: MySQL (for production without PostgreSQL)
- Toggle: `USE_SQLITE=true` in `.env`
- Connection URL: `DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require`
- Priority: PostgreSQL > SQLite > MySQL

### Authentication
- Session-based (not JWT)
- Separate sessions for users and admins
- Password hashing with Werkzeug (PBKDF2)
- OTP-based authentication (login, password reset, account deletion)

### File Uploads
- Local storage in `static/uploads/`
- UUID filenames to prevent collisions
- Category-based organization

### Frontend
- Bootstrap 5.3.2
- Custom CSS with variables
- Dark mode support
- Mobile-first responsive

## Important Patterns

### Database Queries
```python
# Always use parameterized queries via the database abstraction layer
from utils.database import get_connection, get_cursor, close_cursor

conn = get_connection()
cursor = get_cursor(conn)
# Uses ? placeholder (converted to %s for PostgreSQL/MySQL automatically)
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))

# Close connections in finally blocks
finally:
    close_cursor(cursor)
    conn.close()
```

### Route Protection
```python
@user_required
def protected_route():
    # Only accessible to logged-in users
    pass

@admin_required
def admin_route():
    # Only accessible to admins
    pass
```

### File Upload
```python
if file and allowed_file(file.filename):
    filename = generate_safe_filename(file.filename)
    file.save(os.path.join(upload_dir, filename))
```

## Common Pitfalls

1. **404.html Location**: Must be in `static/` folder, not `templates/`
2. **Placeholders**: The codebase uses `?` for SQLite/MySQL compatibility;
   `PostgresCursorWrapper` and `MySQLCursorWrapper` convert `?` → `%s` automatically
3. **Session Keys**: Use `user_id` for users, `admin_id` for admins
4. **Template Context**: Always pass `current_user` to templates
5. **Guest Limits**: Check `get_current_user()` before applying limits
6. **Working Directory**: Tests call `os.chdir(root)` at import; run from any directory
7. **PostgreSQL**: Use `init_postgres.py` to initialize schema, `migrate_to_postgres.py` to migrate data
8. **SQLite Backup**: Always backup `instance/college_study.sqlite` before migration
9. **PostgreSQL Performance**: Use `PostgreSQLConnectionPool` (in `utils/database.py`)
   for connection reuse — never call `psycopg.connect()` directly; always use
   `get_connection()` which returns a pooled connection
10. **Settings Caching**: `Settings.get_all()` is cached for 60 seconds (thread-local);
    call `Settings.set()` to invalidate cache when values change

## Dependencies

### Core
- Flask==3.0.0
- PyMySQL==1.1.0
- python-dotenv==1.0.0
- Werkzeug==3.0.1
- gunicorn==21.2.0
- psycopg[binary]==3.2.6 (PostgreSQL driver)

### Frontend (CDN)
- Bootstrap 5.3.2
- Font Awesome 6.5.1
- Google Fonts (Inter)

## Environment Variables

### Required
```env
SECRET_KEY=your_secret_key_here
USE_SQLITE=true
```

### Database Options
```env
# SQLite (development)
USE_SQLITE=true

# PostgreSQL (production) — takes priority if DATABASE_URL is set
USE_SQLITE=false
DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require

# MySQL (fallback)
DB_HOST=localhost
DB_PORT=3306
DB_NAME=college_study
DB_USER=root
DB_PASSWORD=your_password
```

### Optional
```env
GUEST_VIEW_LIMIT=4
GUEST_DOWNLOAD_LIMIT=4
MAX_CONTENT_LENGTH=5368709120
APP_NAME=StudyHub
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password
SESSION_COOKIE_SECURE=false
FLASK_DEBUG=false
```

## File Structure Reminders

### Uploads
```
static/uploads/
├── notes/
├── assignments/
├── books/
├── practicals/
├── question_papers/
├── important_questions/
├── syllabus/
├── other/
└── profiles/
```

### Templates
```
templates/
├── base.html (public site)
├── admin/base.html (admin panel)
└── [page].html (extends appropriate base)
```

### Database Files
```
instance/
├── college_study.sqlite       (SQLite database — preserve, do not delete)
└── college_study.sqlite.bak   (SQLite backup — preserve, do not delete)
```

## Database Schema

### All 16 Tables
- `users` - Student accounts
- `admins` - Admin accounts
- `categories` - Material categories
- `subjects` - Academic subjects
- `materials` - Study materials
- `favorites` - User favorites
- `contact_messages` - Contact form submissions
- `announcements` - Site announcements
- `guest_views` - Guest view tracking
- `guest_downloads` - Guest download tracking
- `settings` - Site settings key-value pairs
- `user_sessions` - Active user sessions (security)
- `login_history` - Login/logout event history
- `failed_login_attempts` - Failed login attempt records
- `admin_audit_log` - Admin action audit trail

## Testing Checklist

### Automated Tests
- [x] `tests/tests_project.py` — 69 tests (all pass)
- [x] `tests/tests_session_management.py` — 40 tests (all pass)
- [x] `tests/tests_session_edge_cases.py` — 29 tests (all pass)

### Manual Tests
- [x] User registration (covered by automated tests)
- [x] User login/logout (covered by automated tests)
- [x] Admin login (covered by automated tests)
- [x] Browse subjects (covered by automated tests)
- [x] View material (covered by automated tests)
- [x] Search materials (covered by automated tests)
- [x] Upload material (admin) (covered by automated tests)
- [x] Add to favorites (covered by automated tests)
- [x] Update profile (covered by automated tests)
- [x] Contact form (covered by automated tests)

## Deployment Notes

### Development (SQLite)
```bash
SET USE_SQLITE=true
python init_sqlite.py
python app.py
# Runs on http://127.0.0.1:5000
```

### Production (PostgreSQL / NeonDB)
```bash
SET USE_SQLITE=false
SET DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require
python init_postgres.py
gunicorn app:app
# Requires proper environment variables
```

### Database Init
```bash
# SQLite
python init_sqlite.py

# PostgreSQL
python init_postgres.py
```

### Migration (SQLite → PostgreSQL)
```bash
# One-time data transfer
python migrate_to_postgres.py

# Clean re-migration (deletes all PG data first)
python migrate_to_postgres.py --reset
```

## Contact & Support
- Project: College Study Material Management System
- Tech: Flask, Bootstrap, SQLite/PostgreSQL/MySQL
- Status: Active Development
