# 3rd Year StudyHub

A complete, modern, responsive College Study Material Management Platform built with Flask.
Supports explicitly configured SQLite (optional local development), PostgreSQL/NeonDB, and MySQL.

## Features

- **Student Portal**: Register, login, browse subjects, live search suggestions, view materials, download files, bookmark favorites, manage profile, and receive persistent real-time notifications
- **Admin Panel**: Complete dashboard with subject management, material upload/management, user management, contact messages, announcements, categories, and statistics
- **Database-Driven**: All subjects, materials, and categories managed from Admin Panel
- **Multi-Database Support**: explicitly selected SQLite (optional local development), PostgreSQL/NeonDB, and MySQL
- **Responsive Design**: Works on mobile, tablet, and desktop with dark mode support
- **Announcements**: Admin-published announcements are persisted as private user notifications and delivered in real time
- **Cookie consent**: Necessary session/security cookies remain enabled; optional display preferences are opt-in
- **Social previews**: Public pages include page-aware Open Graph and Twitter/X metadata with a configurable canonical HTTPS origin
- **Google sign-in**: Optional Google OpenID Connect login and registration using verified email claims and the existing server-side session policy
- **Secure**: Password hashing, session management, file upload validation, environment variables for credentials

## Technology Stack

- **Backend**: Python 3.8+, Flask 3.0
- **Database**: SQLite (development), PostgreSQL/NeonDB (production), MySQL
- **Frontend**: HTML5, CSS3, Bootstrap 5, JavaScript (Vanilla), Socket.IO client, Font Awesome 6
- **Real time**: Flask-SocketIO with the threading backend and notification polling fallback
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
│   ├── message.py
│   ├── notification.py
│   └── oauth_account.py
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
│   ├── images/                # og-studyhub.png (1200 x 630 default preview)
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
│   ├── security.py
│   ├── realtime.py
│   ├── cookie_consent.py
│   ├── google_oauth.py
│   └── seo.py
├── instance/
│   ├── college_study.sqlite        # SQLite database (dev)
│   └── college_study.sqlite.bak    # SQLite backup
├── tests/
│   ├── test_studyhub_features.py
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
└── Procfile                    # Production: threaded Gunicorn application factory
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
DATABASE_BACKEND=sqlite
USE_SQLITE=true
SECRET_KEY=your_very_long_random_secret_key

# SMTP for OTP and queued notification emails
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password
MAIL_CONNECT_TIMEOUT=3
MAIL_TIMEOUT=10
MAIL_SEND_TIMEOUT=8
```

#### Option B: Local PostgreSQL

Set `DEV_DATABASE_URL` to the local PostgreSQL instance in `.env`; do not put
the Render production URL in the local environment:

```env
USE_SQLITE=false
DATABASE_BACKEND=postgresql
DEV_DATABASE_URL=postgresql://postgres@localhost:5432/college_study

# Optional: SMTP for OTP emails
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password
```

For Render, configure the production PostgreSQL connection as `DATABASE_URL`
in the Render environment. Local development never falls back to a remote
`DATABASE_URL`. To use MySQL locally instead, explicitly configure the `DB_*`
variables. SQLite is used only when `USE_SQLITE=true`; its optional path can be
set with `SQLITE_DB`. Set `DATABASE_BACKEND=sqlite` to choose SQLite even when
a development PostgreSQL URL is also present.

### Step 5: Initialize Database

#### For SQLite:

Set `DATABASE_BACKEND=sqlite` in the local `.env` first; SQLite is never
selected automatically and is disabled on Render.

```bash
python init_sqlite.py
```

#### For PostgreSQL/NeonDB:

```bash
python init_postgres.py
```

If you have existing data in SQLite and want to migrate it to PostgreSQL, see the [Database Setup Guide](docs/DATABASE_SETUP.md).

### Step 6: Run Application

```bash
python app.py
```

Visit: https://college-study-material.onrender.com

- **Student Site**: http://127.0.0.1:5000
- **Admin Login**: http://127.0.0.1:5000/admin/login

### Open Graph and canonical URLs

Set `PUBLIC_BASE_URL` to the canonical production origin (for example,
`https://studyhub.your-domain.tld`, without a path or trailing slash). StudyHub
uses it to emit absolute canonical, Open Graph, Twitter/X, and preview-image
URLs; it deliberately does not publish localhost or request-host URLs when the
setting is absent or invalid in production. In local testing/debug, a loopback
origin may be used for local rendering only; it is never accepted as a
production canonical origin. Production must use the exact public HTTPS domain,
not a localhost address or temporary development tunnel. Put the default
1200 × 630 preview image at
`static/images/og-studyhub.png`. Replace that file to customize the shared
preview image. Public material and subject pages use their own titles and
descriptions, and Login and Registration have dedicated metadata. Private
account/admin pages use generic metadata and are marked `noindex`.

### Legal pages

The public Privacy Policy and Terms of Service are available at
`/privacy-policy` and `/terms-of-service`. Their content and “Last updated”
dates are maintained in `templates/privacy_policy.html` and
`templates/terms_of_service.html`. Review both with an appropriate legal
professional before relying on them as binding terms or a jurisdiction-specific
privacy notice. For the supplied Render deployment, set `PUBLIC_BASE_URL` to
`https://college-study-material.onrender.com` in the deployment environment;
the application does not infer a production canonical URL from a local host or
temporary tunnel.

### Google sign-in setup

Google sign-in is optional. Create an OAuth 2.0 Client ID of type **Web
application** in Google Cloud Console, configure the OAuth consent screen, and
add this exact **Authorized redirect URI**:

```text
https://your-production-domain.tld/google/callback
```

Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in the deployment environment
(never commit them), and set `PUBLIC_BASE_URL` to the same HTTPS origin. For
local development, configure the exact local callback URI displayed/used by
your local deployment. StudyHub uses OpenID Connect `openid email profile`,
requires Google's verified email claim, and lets Authlib validate OAuth state.
New Google identities are stored separately from password credentials. An
existing account is linked only after Google returns a verified matching
email; inactive and deleted accounts cannot sign in.

### Email notification worker

Login, registration, and password-reset OTPs remain synchronous and are sent
only after credentials are validated. Account-deletion confirmation emails are
stored in the database transaction with the deletion and are delivered by a
separate worker process; the Flask request does not wait for SMTP.

Run the worker in a separate terminal or supervised process:

```bash
python -m utils.notification_worker
```

The worker is not started when Flask is imported. It polls every
`NOTIFICATION_POLL_INTERVAL` seconds (default 15), retries confirmed transient
failures with bounded exponential backoff, and limits retries using
`NOTIFICATION_WORKER_MAX_ATTEMPTS` (default 5). Configure `MAIL_CONNECT_TIMEOUT`,
`MAIL_TIMEOUT`, and `MAIL_SEND_TIMEOUT` for bounded SMTP waits.

Queue jobs are stored in `pending_notifications`. Inspect their outcome with:

```sql
SELECT id, purpose, status, attempt, retryable, next_attempt_at, last_error
FROM pending_notifications
ORDER BY created_at DESC;
```

`pending` means delivery has not been attempted or SMTP was not configured;
`processing` is a leased job; `sent` means SMTP acceptance was confirmed;
`failed` is a confirmed failure (retryable jobs have a future attempt time);
and `ambiguous` means SMTP may have accepted the message, so it is not retried
automatically. After a worker restart, expired `processing` leases are marked
`ambiguous` rather than resent. Investigate permanent failures and ambiguous
jobs manually; do not mark ambiguous messages as delivered without evidence.

Online payments and financial transaction processing were audited and no
payment gateway, checkout, refund, or payment route/model/dependency was found.
Database transactions (`BEGIN`, `COMMIT`, `ROLLBACK`, and savepoints) remain
part of the required persistence layer.

## Database

The application supports three database backends with explicit environment
selection:

| Backend    | Config                         | Use Case               |
| ---------- | ------------------------------ | ---------------------- |
| SQLite     | `USE_SQLITE=true`              | Local development      |
| PostgreSQL | `DEV_DATABASE_URL` locally; `DATABASE_URL` on Render | Local or production |
| MySQL      | Explicit `DB_HOST`, `DB_NAME`, and `DB_USER` | Local/alternative deployment |

Render requires `DATABASE_URL` and never falls back to SQLite or MySQL. Locally,
`DEV_DATABASE_URL` or `LOCAL_DATABASE_URL` selects PostgreSQL when configured;
otherwise `USE_SQLITE=true` selects SQLite or explicit `DB_*` settings select
MySQL. Set `DATABASE_BACKEND=postgresql` to require a local PostgreSQL URL.
A production `DATABASE_URL` is never selected locally.

The database abstraction layer (`utils/database.py`) handles:

- Connection management for all three backends
- Placeholder conversion (`?` → `%s` for PostgreSQL/MySQL)
- Row factory abstraction (dict-based for all backends)
- Transaction management (`BEGIN IMMEDIATE` for SQLite, standard transactions for PostgreSQL/MySQL)

See [DATABASE_SETUP.md](docs/DATABASE_SETUP.md) for detailed database configuration.

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
- Keyboard-accessible debounced autocomplete for materials and subjects
- Private notification history, unread counts, and read controls
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
- Socket.IO rooms are joined only after validating the signed Flask session and active server-side user session
- Notification reads and updates are scoped to the authenticated user
- Cookie preferences use a signed, HttpOnly, SameSite=Lax cookie; HTTPS deployments should set `SESSION_COOKIE_SECURE=true`

## Notifications and cookie preferences

The application adds `user_notifications` and extends existing `announcements`
with publication time, optional link, and audience fields using additive
startup schema updates. Publishing active notes, books, assignments, practicals,
or announcements creates deduplicated notifications for the selected users.
The Socket.IO browser connection falls back to one notification poll per minute
when disconnected or unavailable. Current assignment records do not include a
deadline field, so deadline reminders are not scheduled.

The cookie banner offers accept, reject, and individual preference settings.
No analytics, advertising, or tracking cookies are introduced. A signed
`studyhub_cookie_consent` cookie records the selection; dark-mode persistence
is used only when optional preferences are enabled.

## Production Deployment

### Using Gunicorn

```bash
gunicorn --worker-class gthread --workers 1 --threads 100 'app:create_app()'
```

The threaded worker and `simple-websocket` dependency support Socket.IO
WebSocket upgrades. Configure the reverse proxy to forward WebSocket upgrade
headers. A single worker is intentional for the in-process Socket.IO server;
multi-worker or multi-instance deployments need a shared Socket.IO message
queue before scaling. Set `SESSION_COOKIE_SECURE=true` behind HTTPS.

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

See [TESTING.md](docs/TESTING.md) for detailed test documentation and results.

## License

MIT License
