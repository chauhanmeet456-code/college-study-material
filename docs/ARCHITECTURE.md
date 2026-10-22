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
│   ├── guest_activity.py   # Guest tracking
│   └── helpers.py          # File & formatting helpers
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
│   │   └── register.html
│   ├── admin/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── login.html
│   │   └── ...
│   └── user/
│       ├── profile.html
│       └── favorites.html
│
├── static/                  # Static assets
│   ├── css/
│   │   ├── style.css
│   │   ├── admin.css
│   │   └── responsive.css
│   ├── js/
│   │   ├── main.js
│   │   ├── admin.js
│   │   └── search.js
│   ├── images/
│   ├── uploads/            # Uploaded files
│   ├── 404.html
│   └── 500.html
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
    └── MEMORY.md
```

## Architecture Patterns

### Blueprint Organization
- `main_bp`: Public-facing routes and APIs
- `auth_bp`: Login, register, logout
- `user_bp`: User-specific routes (profile, favorites)
- `admin_bp`: Admin panel routes

### Database Layer
- Direct SQL queries via `get_connection()`
- DictCursor for MySQL, custom `dict_factory` for SQLite
- Connection per request pattern
- Manual cursor cleanup

### Authentication
- Session-based with `user_id` / `admin_id`
- `get_current_user()` / `get_current_admin()` helpers
- `@user_required` and `@admin_required` decorators

### File Storage
- Local filesystem under `static/uploads/`
- Category-based subdirectories
- UUID-based filenames for safety

## Data Flow

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
