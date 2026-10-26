# Setup Guide

## Prerequisites

- Python 3.12+
- pip (Python package installer)

No external database server is required for local development — SQLite is included with Python.

## Installation

```bash
# 1. Clone or navigate to the project directory
cd college-study-material

# 2. Install dependencies
pip install -r requirements.txt
```

## Environment Configuration

1. Copy the environment template:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set the following variables:

### Required for All Deployments
| Variable | Purpose | Example |
|---|---|---|
| `SECRET_KEY` | Flask session signing key | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `USE_SQLITE` | Use SQLite for local development | `true` |

### Database Options
| Variable | Purpose | Notes |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | If set, takes priority over SQLite |
| `USE_SQLITE` | Force SQLite mode | `true` for local dev |

**Priority**: `DATABASE_URL` (PostgreSQL) → `USE_SQLITE=true` (SQLite) → MySQL fallback

**Detailed database setup**: See [DATABASE_SETUP.md](DATABASE_SETUP.md)

### Email (OTP) Configuration
| Variable | Purpose | Default |
|---|---|---|
| `MAIL_SERVER` | SMTP server hostname | `smtp.gmail.com` |
| `MAIL_PORT` | SMTP port | `587` |
| `MAIL_USERNAME` | SMTP username (sender email) | (empty) |
| `MAIL_PASSWORD` | SMTP password or App Password | (empty) |
| `MAIL_CONNECT_TIMEOUT` | SMTP connection timeout (seconds) | `3` |
| `MAIL_TIMEOUT` | SMTP operation timeout (seconds) | `10` |

**Gmail note**: If 2FA is enabled, use an App Password, not your regular password.

**Detailed email setup**: See [EMAIL_SETUP.md](EMAIL_SETUP.md)

### OTP Settings
| Variable | Default | Description |
|---|---|---|
| `OTP_EXPIRY_SECONDS` | `300` | OTP expiration (5 minutes) |
| `OTP_MAX_RESENDS` | `3` | Max resend attempts per challenge |
| `OTP_RESEND_COOLDOWN_SECONDS` | `30` | Cooldown between resends |
| `OTP_MAX_VERIFICATION_ATTEMPTS` | `5` | Max OTP verification attempts |

## Initialize Database

```bash
# SQLite (local development)
python init_sqlite.py

# PostgreSQL (production)
python init_postgres.py
```

## Run the Application

```bash
# Development
python app.py
# Opens at http://127.0.0.1:5000

# Production
gunicorn app:app
# Or via Procfile: heroku local web / foreman start
```

## Quick Admin Access

- Username: `admin`
- Password: `admin@123`
- Change these immediately in production via the admin dashboard.

## Testing Without Email

If SMTP is not configured, OTP emails will not be sent. The application shows an error message and does not expose OTP values. To test locally without a real SMTP server, use [MailHog](https://github.com/mailhog/MailHog) or [smtp4dev](https://github.com/rnwood/smtp4dev/) on `localhost:1025`.

For automated tests, email sending is mocked — no real emails are sent. Run:
```bash
python tests/tests_project.py
python tests/tests_session_management.py
python tests/tests_session_edge_cases.py
python tests/tests_otp_delivery.py
python tests/tests_account_deletion_and_session_email.py
```

See [TESTING.md](TESTING.md) for full verification procedures.

## Environment Recovery

If `.env` is missing or corrupted, see [ENV_RECOVERY_REPORT.md](ENV_RECOVERY_REPORT.md) for recovery procedures. The application also ships with `.env.example` containing safe placeholders.
