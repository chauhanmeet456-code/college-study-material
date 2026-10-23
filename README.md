# 3rd Year StudyHub

A complete, modern, responsive College Study Material Management Platform built with Flask.
Supports SQLite (development), PostgreSQL/NeonDB (production), and MySQL.

## Features

- **Student Portal**: Register, login, browse subjects, search materials, view PDFs/DOCX/PPTX/TXT, download files, bookmark favorites, manage profile
- **Admin Panel**: Complete dashboard with subject management, material upload/management, user management, contact messages, announcements, categories, and statistics
- **Database-Driven**: All subjects, materials, and categories managed from Admin Panel
- **Multi-Database Support**: SQLite (default for development), PostgreSQL/NeonDB (recommended for production), MySQL
- **Responsive Design**: Works on mobile, tablet, and desktop with dark mode support
- **Secure**: Password hashing, session management, file upload validation, environment variables for credentials

## Technology Stack

- **Backend**: Python 3.8+, Flask 3.0
- **Database**: SQLite (development), PostgreSQL/NeonDB (production), MySQL
- **Frontend**: HTML5, CSS3, Bootstrap 5, JavaScript (Vanilla), Font Awesome 6
- **Production**: Gunicorn
- **PostgreSQL Driver**: psycopg (v3)

## Project Structure

```
college-study-material/
├── app.py                    # Flask application factory and entry point
├── config.py                 # Configuration (database, sessions, security)
├── requirements.txt          # Python dependencies
├── .env.example              # Environment variable template
├── .env                      # Actual environment config (.gitignore'd)
├── init_sqlite.py            # SQLite database initialization
├── init_postgres.py          # PostgreSQL schema initialization
├── migrate_to_postgres.py    # SQLite → PostgreSQL migration script
├── models/
│   ├── __init__.py
│   ├── user.py
│   ├── admin.py
│   ├── subject.py
│   ├── material.py
│   ├── session.py
│   ├── settings.py
│   └── message.py
├── routes/
│   ├── auth.py
│   ├── user.py
│   ├── admin.py
│   └── main.py
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── subjects.html
│   ├── subject_details.html
│   ├── material_details.html
│   ├── pdf_viewer.html
│   ├── search.html
│   ├── notes.html
│   ├── assignments.html
│   ├── books.html
│   ├── practicals.html
│   ├── question_papers.html
│   ├── important_questions.html
│   ├── syllabus.html
│   ├── about.html
│   ├── contact.html
│   ├── auth/
│   │   ├── login.html
│   │   ├── register.html
│   │   ├── forgot_password.html
│   │   └── reset_password.html
│   ├── admin/
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── dashboard.html
│   │   ├── subjects.html
│   │   ├── add_subject.html
│   │   ├── edit_subject.html
│   │   ├── add_material.html
│   │   ├── edit_material.html
│   │   ├── materials.html
│   │   ├── users.html
│   │   ├── user_detail.html
│   │   ├── messages.html
│   │   ├── message_detail.html
│   │   ├── categories.html
│   │   ├── announcements.html
│   │   ├── settings.html
│   │   ├── profile.html
│   │   ├── forgot_password.html
│   │   ├── reset_password.html
│   │   ├── deleted_users.html
│   │   ├── deletion_requests.html
│   │   ├── user_security.html
│   │   ├── user_login_history.html
│   │   ├── audit_log.html
│   │   └── failed_attempts.html
│   └── user/
│       ├── profile.html
│       ├── favorites.html
│       ├── confirm_delete_account.html
│       └── login_history.html
├── static/
│   ├── 404.html
│   ├── 500.html
│   ├── images/
│   └── uploads/
│       ├── assignments/
│       ├── books/
│       ├── important_questions/
│       ├── notes/
│       ├── other/
│       ├── practicals/
│       ├── profiles/
│       ├── question_papers/
│       └── syllabus/
├── utils/
│   ├── database.py       # Database abstraction layer (SQLite/PostgreSQL/MySQL)
│   ├── auth.py
│   ├── decorators.py
│   ├── device.py
│   ├── guest_activity.py
│   ├── helpers.py
│   ├── otp.py
│   └── security.py
├── instance/
│   ├── college_study.sqlite        # SQLite database (dev)
│   └── college_study.sqlite.bak    # SQLite backup
├── tests/
│   ├── tests_project.py            # Main test suite (69 tests)
│   ├── tests_session_management.py # Session management tests (40 tests)
│   └── tests_session_edge_cases.py # Session edge case tests (29 tests)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DESIGN.md
│   ├── EMAIL_SETUP.md
│   ├── IMPLEMENTATION_REPORT.md
│   ├── MEMORY.md
│   ├── NO_ERROR_REPORT.md
│   ├── PRD.md
│   ├── RULES.md
│   └── TASKS.md
├── reports/
│   └── cleanup_candidates.md
├── DATABASE_SETUP.md
├── CHANGELOG.md
├── TESTING.md
├── PROJECT_REPORT.md
└── Procfile                    # Production: web: gunicorn app:app
```

## Installation

### Prerequisites

- Python 3.8+
- pip
- Virtual environment tool
- For production: PostgreSQL (NeonDB) or MySQL account

### Step 1: Clone or Download

```bash
cd college-study-material
```

### Step 2: Create Virtual Environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Configure Environment

Create `.env` file from example:

```bash
cp .env.example .env
```

Edit `.env` based on your database choice:

#### Option A: SQLite (Development - simplest)

```env
USE_SQLITE=true
SECRET_KEY=your_very_long_random_secret_key

# Optional: SMTP for OTP emails
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password
```

#### Option B: PostgreSQL / NeonDB (Production - recommended)

```env
USE_SQLITE=false
DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require

# Optional: SMTP for OTP emails
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password
```

> **Note**: Database connection priority is: PostgreSQL (`DATABASE_URL`) > SQLite (`USE_SQLITE=true`) > MySQL (default).

### Step 5: Initialize Database

#### For SQLite:

```bash
python init_sqlite.py
```

#### For PostgreSQL/NeonDB:

```bash
python init_postgres.py
```

If you have existing data in SQLite and want to migrate it to PostgreSQL, see the [Database Setup Guide](DATABASE_SETUP.md).

### Step 6: Run Application

```bash
python app.py
```

Visit: http://127.0.0.1:5000

- **Student Site**: http://127.0.0.1:5000
- **Admin Login**: http://127.0.0.1:5000/admin/login

## Database

The application supports three database backends with automatic selection:

| Backend | Config | Use Case |
|---------|--------|----------|
| SQLite | `USE_SQLITE=true` | Local development |
| PostgreSQL | `DATABASE_URL=<url>` | Production (NeonDB) |
| MySQL | Default (no SQLite/PostgreSQL) | Alternative production |

**Connection Priority**: `DATABASE_URL` (PostgreSQL) > `USE_SQLITE=true` (SQLite) > MySQL default

The database abstraction layer (`utils/database.py`) handles:
- Connection management for all three backends
- Placeholder conversion (`?` → `%s` for PostgreSQL/MySQL)
- Row factory abstraction (dict-based for all backends)
- Transaction management (`BEGIN IMMEDIATE` for SQLite, standard transactions for PostgreSQL/MySQL)

See [DATABASE_SETUP.md](DATABASE_SETUP.md) for detailed database configuration.

## Admin Features

- Dashboard with statistics
- Subject CRUD (Add, Edit, Delete, Activate/Deactivate)
- Material Management (Upload, Edit, Replace File, Delete, Feature/Unfeature)
  - Supported file types: PDF, DOC, DOCX, PPT, PPTX, ZIP, TXT, images, video
  - Maximum file size: 5GB
  - Supports direct file upload and folder uploads
- Category Management
- User Management (View, Activate, Deactivate, Soft Delete, Permanent Delete)
- Contact Messages (View, Mark Read, Delete)
- Announcements (Add, Edit, Delete, Activate/Deactivate)
- Admin Profile (Update info, Change password)
- Forgot password restricted to authorized admin email
- User Security: Login history, session management, audit log, failed login attempts

## Student Features

- Register / Login / Logout with OTP verification
- Browse subjects and materials
- Search materials
- Filter by subject, category, file type, sort
- View materials online:
  - PDF inline preview
  - DOC/DOCX/TXT inline preview where browser supports it
  - PPT/PPTX download-only preview
  - Video playback
- Download materials
- Bookmark favorites
- Manage profile
- Change password
- Request account deletion with OTP confirmation
- Multi-device session management (single active session, conflict detection)

## Security

- Passwords hashed with Werkzeug (PBKDF2)
- OTP-based authentication (login, password reset, account deletion)
- Server-side session validation with SHA-256 token hashing
- Single active session per user with multi-device conflict detection
- Login rate limiting (5 attempts per 15-minute window)
- CSRF protection on all POST forms and state-changing routes
- Session cookie hardening (HttpOnly, SameSite=Lax, Secure configurable)
- Security response headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy)
- Flask session management
- Admin authentication decorators (`@admin_required`, `@user_required`)
- Secure file upload validation
- Environment variables for credentials
- SQL injection prevention via parameterized queries
- XSS protection via Jinja2 auto-escaping and explicit `|e` filters
- Soft delete for users with OTP confirmation
- Inactive materials hidden from public access

## Production Deployment

### Using Gunicorn

```bash
gunicorn app:app
```

### Environment Variables

```env
USE_SQLITE=false
DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require
SECRET_KEY=your_very_long_random_secret_key
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password
```

### File Uploads

Configure your web server (Nginx/Apache) to serve `/static/uploads/` or use cloud storage.

## File Structure Notes

- Uploaded files are stored in `static/uploads/` organized by category
- Default categories: Notes, Assignment, Book, Practical, Question Paper, Important Question, Syllabus, Other
- Supported file types: PDF, DOC, DOCX, PPT, PPTX, ZIP, TXT, images, video

## Development Notes

- The development server (`python app.py`) is for development only
- Use `gunicorn` in production
- Never commit `.env` to version control
- Change the default `SECRET_KEY` in production
- Use strong database passwords
- Admin forgot password sends reset link to authorized email only

## Testing

Three test suites are available:

```bash
# Main test suite (69 tests)
python tests/tests_project.py

# Session management tests (40 tests)
python tests/tests_session_management.py

# Session edge case tests (29 tests)
python tests/tests_session_edge_cases.py
```

See [TESTING.md](TESTING.md) for detailed test documentation and results.

## License

MIT License
