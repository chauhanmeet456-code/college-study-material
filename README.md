# 3rd Year Study Hub

A complete, modern, responsive College Study Material Management Platform built with Flask.
Supports both SQLite (default for development) and MySQL (for production).

## Features

- **Student Portal**: Register, login, browse subjects, search materials, view PDFs/DOCX/PPTX/TXT, download files, bookmark favorites, manage profile
- **Admin Panel**: Complete dashboard with subject management, material upload/management, user management, contact messages, announcements, categories, and statistics
- **Database-Driven**: All subjects, materials, and categories managed from Admin Panel
- **Responsive Design**: Works on mobile, tablet, and desktop with dark mode support
- **Secure**: Password hashing, session management, file upload validation, environment variables for credentials

## Technology Stack

- **Backend**: Python, Flask
- **Database**: SQLite (default) / MySQL
- **Frontend**: HTML5, CSS3, Bootstrap 5, JavaScript (Vanilla), Font Awesome 6
- **Production**: Gunicorn / Waitress

## Project Structure

```
college-study-material/
├── app.py
├── config.py
├── requirements.txt
├── .env.example
├── init_sqlite.py
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
│   ├── database.py
│   ├── auth.py
│   ├── decorators.py
│   ├── device.py
│   ├── guest_activity.py
│   ├── helpers.py
│   ├── otp.py
│   └── security.py
├── instance/
    └── college_study.sqlite
```

## Installation

### Prerequisites

- Python 3.8+
- pip
- Virtual environment tool

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

Edit `.env`:

```env
USE_SQLITE=true
SECRET_KEY=your_very_long_random_secret_key

# Optional: SMTP for OTP emails
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_app_password
```

### Step 5: Initialize Database

```bash
python init_sqlite.py
```

### Step 6: Run Application

```bash
python app.py
```

Visit: http://127.0.0.1:5000

- **Student Site**: http://127.0.0.1:5000
- **Admin Login**: http://127.0.0.1:5000/admin/login

## Database

- Default: SQLite at `instance/college_study.sqlite`
- Optional: MySQL via `.env` settings
- Includes automatic migrations for added columns in `init_sqlite.py`

## Admin Features

- Dashboard with statistics
- Subject CRUD (Add, Edit, Delete, Activate/Deactivate)
- Material Management (Upload, Edit, Replace File, Delete, Feature/Unfeature)
  - Supported file types: PDF, DOC, DOCX, PPT, PPTX, TXT, ZIP, images, video
  - Maximum file size: 5GB
  - Supports direct file upload and folder uploads
- Category Management
- User Management (View, Activate, Deactivate, Soft Delete, Permanent Delete)
- Contact Messages (View, Mark Read, Delete)
- Announcements (Add, Edit, Delete, Activate/Deactivate)
- Admin Profile (Update info, Change password)
- Forgot password restricted to authorized admin email

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

### Using Waitress (Windows-friendly)

```bash
waitress-serve --port=5000 app:app
```

### Environment Variables

```env
USE_SQLITE=false
DB_HOST=your_db_host
DB_PORT=3306
DB_NAME=college_study
DB_USER=your_db_user
DB_PASSWORD=your_secure_password
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
- Use `gunicorn` or `waitress` in production
- Never commit `.env` to version control
- Change the default `SECRET_KEY` in production
- Use strong database passwords
- Admin forgot password sends reset link to authorized email only

## License

MIT License
