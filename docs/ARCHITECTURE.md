# System Architecture - StudyHub

## Tech Stack
- **Backend**: Flask (Python)
- **Frontend**: HTML, CSS, JavaScript (Bootstrap 5.3.2)
- **Database**: SQLite (default) / MySQL
- **Authentication**: Session-based
- **File Upload**: Werkzeug secure_filename

## Project Structure

```
college-study-material/
├── app.py                    # Flask app factory
├── config.py                 # Configuration settings
├── init_sqlite.py           # SQLite database initialization
├── requirements.txt         # Python dependencies
├── .env                     # Environment variables
├── .env.example            # Environment template
│
├── models/                  # Database models
│   ├── __init__.py
│   ├── user.py             # User model
│   ├── admin.py            # Admin model
│   ├── subject.py          # Subject & Category models
│   ├── material.py         # Material & Favorite models
│   ├── session.py          # UserSession, LoginHistory, AdminAuditLog models
│   ├── settings.py         # Settings model
│   └── message.py          # ContactMessage & Announcement models
│
├── routes/                  # Blueprint routes
│   ├── __init__.py
│   ├── main.py             # Public routes + APIs
│   ├── auth.py             # Authentication routes
│   ├── user.py             # User dashboard routes
│   └── admin.py            # Admin panel routes
│
├── utils/                   # Utility modules
│   ├── database.py         # Database connection helpers
│   ├── auth.py             # Auth helper functions
│   ├── decorators.py       # Auth decorators
│   ├── device.py           # Device/browser/OS detection (User-Agent)
│   ├── guest_activity.py   # Guest tracking
│   ├── helpers.py          # File & formatting helpers
│   ├── otp.py              # OTP generation, storage, SMTP email
│   └── security.py         # CSRF tokens, rate limiting, security headers
│
├── templates/               # Jinja2 templates
│   ├── base.html           # Base template
│   ├── index.html          # Homepage
│   ├── about.html
│   ├── contact.html
│   ├── search.html
│   ├── subjects.html
│   ├── subject_details.html
│   ├── material_details.html
│   ├── pdf_viewer.html
│   ├── notes.html
│   ├── assignments.html
│   ├── books.html
│   ├── practicals.html
│   ├── question_papers.html
│   ├── important_questions.html
│   ├── syllabus.html
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
│   │   ├── materials.html
│   │   ├── add_material.html
│   │   ├── edit_material.html
│   │   ├── users.html
│   │   ├── user_detail.html
│   │   ├── deleted_users.html
│   │   ├── deletion_requests.html
│   │   ├── messages.html
│   │   ├── message_detail.html
│   │   ├── categories.html
│   │   ├── announcements.html
│   │   ├── settings.html
│   │   ├── profile.html
│   │   ├── forgot_password.html
│   │   ├── reset_password.html
│   │   ├── user_security.html
│   │   ├── user_login_history.html
│   │   ├── audit_log.html
│   │   └── failed_attempts.html
│   └── user/
│       ├── profile.html
│       ├── favorites.html
│       ├── login_history.html
│       └── confirm_delete_account.html
│
├── static/                  # Static assets
│   ├── 404.html             # Custom 404 error page
│   ├── 500.html             # Custom 500 error page
│   ├── images/              # Image assets
│   └── uploads/            # Uploaded files
│       ├── assignments/
│       ├── books/
│       ├── important_questions/
│       ├── notes/
│       ├── other/
│       ├── practicals/
│       ├── profiles/
│       ├── question_papers/
│       └── syllabus/
│       # CSS is inline in base.html; Bootstrap & Font Awesome via CDN
│       # JS is inline in base.html; Bootstrap bundle via CDN
│
├── instance/                # Instance folder
│   └── college_study.sqlite
│
└── docs/                    # Documentation
    ├── PRD.md
    ├── ARCHITECTURE.md
    ├── RULES.md
    ├── DESIGN.md
    ├── TASKS.md
    ├── MEMORY.md
    ├── EMAIL_SETUP.md
    ├── IMPLEMENTATION_REPORT.md
    └── NO_ERROR_REPORT.md
```

## Architecture Patterns

### Blueprint Organization
- `main_bp`: Public-facing routes and APIs (homepage, subjects, materials, search, category pages, contact form, API endpoints)
- `auth_bp`: Authentication (OTP-based login, register, logout, forgot/reset password)
- `user_bp`: User routes (profile, favorites, login history, session revocation)
- `admin_bp`: Admin panel routes (dashboard, subjects, materials, users, user security, settings, messages)

### Database Layer
- Direct SQL queries via `get_connection()`
- DictCursor for MySQL, custom `dict_factory` for SQLite
- Connection per request pattern
- Manual cursor cleanup

### Authentication
- OTP-based login (email via SMTP, or development mode without email config)
- Session-based with `user_id` / `admin_id`
- Server-side session validation: SHA-256 token hash stored in `user_sessions` table, validated on every protected request via `before_request`
- Single active session per user with multi-device conflict detection
- `get_current_user()` / `get_current_admin()` helpers
- `@user_required` and `@admin_required` decorators
- Login rate limiting (5 attempts per 15-minute sliding window)
- CSRF protection on all state-changing routes via `@csrf_protect`

### File Storage
- Local filesystem under `static/uploads/`
- Category-based subdirectories
- UUID-based filenames for safety

## Data Flow

### Login & Session Management
1. User submits email and password
2. Rate limit and credential validation
3. OTP generated and sent (email or flashed in development)
4. OTP verified; if an active session exists on another device, show conflict screen
5. New server-side session created with SHA-256 token hash in `user_sessions` table
6. `before_request` validates the session token on every protected request
7. Logout revokes the session row and records a `logout` history event

### Material View
1. User requests `/material/<id>`
2. Check guest activity limits
3. Increment view count
4. Render material details with subject info

### Material Upload (Admin)
1. Admin submits form with file
2. Validate file type and size
3. Generate safe filename
4. Save to category subdirectory
5. Create database record

### Search
1. User submits search query
2. Frontend calls `/api/search` with filters
3. Backend queries with LIKE and JOINs
4. Returns JSON with materials and pagination
5. Search suggestions available via `/api/search/suggestions`
