# StudyHub Documentation

## What is StudyHub?

StudyHub is a college study material management platform built with Flask. Students can browse, search, and download study materials (notes, assignments, books, question papers, etc.). Admins manage content and users. Authentication uses email-based OTP verification with server-side session management.

## Documentation Index

| Document | Purpose |
|---|---|
| [SETUP.md](SETUP.md) | Installation, prerequisites, and environment variable reference |
| [DATABASE_SETUP.md](DATABASE_SETUP.md) | Database configuration (SQLite/PostgreSQL/MySQL), schema init, migration |
| [EMAIL_SETUP.md](EMAIL_SETUP.md) | SMTP configuration, provider guides, and email troubleshooting |
| [TESTING.md](TESTING.md) | Test suite commands and verification procedures |
| [CHANGELOG.md](CHANGELOG.md) | Chronological log of changes and fixes |
| [ENV_RECOVERY_REPORT.md](ENV_RECOVERY_REPORT.md) | Environment configuration recovery details |
| [SECURITY_AUDIT_REPORT.md](SECURITY_AUDIT_REPORT.md) | Security findings, session management, OTP/email security, CSP |
| [PERFORMANCE_OPTIMIZATION_REPORT.md](PERFORMANCE_OPTIMIZATION_REPORT.md) | Performance baseline, optimizations, and after-change measurements |
| [ACCOUNT_DELETION_AND_SESSION_EMAIL_REPORT.md](ACCOUNT_DELETION_AND_SESSION_EMAIL_REPORT.md) | Account deletion flows and session termination email implementation |
| [DESIGN.md](DESIGN.md) | Architecture and design decisions with rationale |
| [MEMORY.md](MEMORY.md) | Developer reference: coding patterns, pitfalls, environment variables |
| [RULES.md](RULES.md) | Development conventions and coding standards |
| [PRD.md](PRD.md) | Product requirements document |
| [NEVER_LOGGED_IN_FEATURE_REPORT.md](NEVER_LOGGED_IN_FEATURE_REPORT.md) | Documentation for the "Never Logged In" admin feature |
| [CLEANUP_REPORT.md](CLEANUP_REPORT.md) | File cleanup inventory and actions taken |

## Quick Start

```bash
# 1. Copy environment template
cp .env.example .env

# 2. Edit .env with your configuration
#    - SECRET_KEY (generate: python -c "import secrets; print(secrets.token_hex(32))")
#    - USE_SQLITE=true (for local development)
#    - MAIL_USERNAME and MAIL_PASSWORD (for OTP emails)

# 3. Initialize database
python init_sqlite.py

# 4. Run the application
python app.py
# Opens at http://127.0.0.1:5000
```

## Project Structure

```
college-study-material/
├── app.py                     # Flask app factory
├── config.py                  # Configuration (loads .env)
├── init_sqlite.py            # SQLite database initialization
├── init_postgres.py          # PostgreSQL schema initialization
├── migrate_to_postgres.py    # SQLite to PostgreSQL migration
├── requirements.txt
├── Procfile                  # Gunicorn startup (production)
├── .env                      # Environment variables (gitignored)
├── .env.example             # Environment template
│
├── models/                   # Database models
├── routes/                   # Flask blueprints (main, auth, user, admin)
├── utils/                    # Utilities (database, auth, OTP, security, notifications)
├── templates/                # Jinja2 templates (root, admin/, auth/, user/)
├── static/                   # Static assets (404.html, 500.html, images/, uploads/)
├── instance/                 # SQLite database (college_study.sqlite)
└── docs/                     # This documentation folder
```

## Key Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Flask session signing | (must set for production) |
| `USE_SQLITE` | Use SQLite instead of MySQL/PostgreSQL | `false` |
| `DATABASE_URL` | PostgreSQL connection string | (none — enables PostgreSQL) |
| `MAIL_SERVER` | SMTP server hostname | `smtp.gmail.com` |
| `MAIL_PORT` | SMTP port | `587` |
| `MAIL_USERNAME` | SMTP username | (empty) |
| `MAIL_PASSWORD` | SMTP password/app password | (empty) |
| `MAIL_CONNECT_TIMEOUT` | SMTP connection timeout (seconds) | `3` |
| `MAIL_TIMEOUT` | SMTP operation timeout (seconds) | `10` |
| `OTP_EXPIRY_SECONDS` | OTP expiration time | `300` (5 min) |
| `OTP_MAX_RESENDS` | Max OTP resends per challenge | `3` |
| `OTP_RESEND_COOLDOWN_SECONDS` | Resend cooldown period | `30` |
| `OTP_MAX_VERIFICATION_ATTEMPTS` | Max OTP verification attempts | `5` |

## Developer Reference

### Common Pitfalls
1. `404.html` must be in `static/` folder, not `templates/`
2. Use `?` for SQLite/MySQL compatibility — the cursor wrapper converts to `%s` automatically
3. Session keys: use `user_id` for users, `admin_id` for admins
4. Always pass `current_user` to templates
5. Check `get_current_user()` before applying guest limits
6. Run tests from any directory (tests call `os.chdir(root)` internally)
7. Use `get_connection()` for database access — never call `psycopg.connect()` directly
8. `Settings.get_all()` is cached for 60 seconds — call `Settings.set()` to invalidate
