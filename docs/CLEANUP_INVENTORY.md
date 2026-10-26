# StudyHub Cleanup Inventory — Phase 1: File Audit

## 1. Application Code (Required)

| File | Purpose | Status |
|---|---|---|
| `app.py` | Main Flask app: route registration, before/after request handlers (timing, session validity, security headers), 404 handler, template context processors | Required |
| `config.py` | Loads `.env`, defines `Config` class with env-var-driven settings (SMTP, session, OTP, rate-limit, DB) | Required |
| `Procfile` | Heroku/gunicorn deployment entry point (`gunicorn app:app`) | Required |
| `requirements.txt` | Python dependencies: Flask, PyMySQL, python-dotenv, Werkzeug, gunicorn, psycopg | Required |
| `init_sqlite.py` | Creates SQLite schema (15 tables + indexes) in `instance/` | Required |
| `init_postgres.py` | Creates PostgreSQL/NeonDB schema (15 tables + indexes + extensions) | Required |
| `migrate_to_postgres.py` | One-time SQLite → PostgreSQL data migration script | Required |
| `README.md` (root) | Project root README; links to `docs/` | Required |

## 2. Python Packages

### `models/` — Data-layer models
| File | Purpose | Status |
|---|---|---|
| `models/__init__.py` | Package init, lazy-loads DB modules | Required |
| `models/admin.py` | Admin user model (auth, audit, password reset) | Required |
| `models/material.py` | Study material CRUD (categories, subjects, files) | Required |
| `models/message.py` | Contact-form message model | Required |
| `models/session.py` | `UserSession`, `LoginHistory`, `FailedLoginAttempt` — server-side session security | Required |
| `models/settings.py` | Key-value settings table wrapper with caching | Required |
| `models/subject.py` | Academic subject model | Required |
| `models/user.py` | Student user model (auth, password, account deletion) | Required |

### `routes/` — Blueprint route handlers
| File | Purpose | Status |
|---|---|---|
| `routes/__init__.py` | Package init | Required |
| `routes/admin.py` | Admin panel routes (login, dashboard, users, sessions, settings, audit) | Required |
| `routes/auth.py` | Auth routes: login (2FA/OTP), registration, password reset, OTP API | Required |
| `routes/main.py` | Public routes: index, categories, search, contact, downloads | Required |
| `routes/user.py` | User routes: profile, favorites, login history, account deletion | Required |

### `utils/` — Utility modules
| File | Purpose | Status |
|---|---|---|
| `utils/auth.py` | Per-request cached `get_current_user`/`get_current_admin` via Flask `g` | Required |
| `utils/database.py` | Connection management, cursor wrappers (DB-agnostic `?` → `%s`), schema extensions | Required |
| `utils/decorators.py` | `@user_required` decorator for protected routes | Required |
| `utils/device.py` | Browser/OS/device detection from User-Agent | Required |
| `utils/guest_activity.py` | Guest view/download tracking and limits | Required |
| `utils/helpers.py` | Template helpers (formatting, markdown, etc.) | Required |
| `utils/notifications.py` | Durable notification delivery (account deletion, session termination emails) | Required |
| `utils/otp.py` | OTP generation, storage, email delivery (SMTP) | Required (being modified for OTP fix) |
| `utils/security.py` | CSRF tokens, login rate limiting, security headers, device cookies | Required |

## 3. Templates

### Public templates (`templates/`)
All required for rendering the student-facing site.

### Auth templates (`templates/auth/`)
| File | Purpose | Status |
|---|---|---|
| `login.html` | Login page with inline OTP entry (credential → OTP flow) | Required |
| `register.html` | Registration page | Required |
| `forgot_password.html` | Forgot password page | Required |
| `reset_password.html` | Password reset page (OTP entry) | Required |

### Admin templates (`templates/admin/`)
All required for the admin panel. Notable:
- `user_security.html` — session controls per user
- `never_logged_in.html` — "never logged in" feature page

### User templates (`templates/user/`)
| File | Purpose | Status |
|---|---|---|
| `profile.html` | User profile view/edit | Required |
| `favorites.html` | Saved materials list | Required |
| `login_history.html` | Login/logout history table | Required |
| `confirm_delete_account.html` | OTP entry for account deletion confirmation | Required |

### Static error pages
| File | Purpose | Status |
|---|---|---|
| `static/404.html` | Custom 404 page (must be in static/, not templates/) | Required |
| `static/500.html` | Custom 500 page | Required |

### User uploads (`static/uploads/`)
All required user-generated content. Directories:
- `assignments/`, `books/`, `important_questions/`, `notes/`, `other/`, `practicals/`, `profiles/`, `question_papers/`, `syllabus/`

## 4. Instance Data
| File | Purpose | Status |
|---|---|---|
| `instance/college_study.sqlite` | SQLite database (gitignored) | Required (data) |
| `instance/college_study.sqlite.bak` | SQLite backup (gitignored) | Required (backup) |

## 5. Configuration Files
| File | Purpose | Status |
|---|---|---|
| `.env` | Environment secrets (gitignored, never commit) | Required (data) |
| `.env.example` | Template for environment variables | Required |
| `.gitignore` | Git ignore rules (`.env`, `__pycache__`, `venv/`, `instance/`, uploads) | Required |

## 6. Tests
| File | Tests | Status |
|---|---|---|
| `tests/tests_account_deletion_and_session_email.py` | Account deletion flows, session termination emails | Required |
| `tests/tests_otp_delivery.py` | OTP generation, delivery, resend limits, security | Required |
| `tests/tests_project.py` | Project-wide tests (routing, CRUD, auth, admin) | Required |
| `tests/tests_session_edge_cases.py` | Session edge cases (concurrent, expiry, revocation) | Required |
| `tests/tests_session_management.py` | Multi-device session management, conflict resolution | Required |

## 7. Documentation (`docs/`)
| File | Purpose | Status |
|---|---|---|
| `docs/README.md` | Documentation index with quick-start and file structure | Required |
| `docs/CHANGELOG.md` | Chronological change log | Required |
| `docs/TESTING.md` | Test commands and procedures | Required |
| `docs/DESIGN.md` | Architecture and design decisions | Required |
| `docs/SETUP.md` | Quick-start setup guide (prerequisites, env vars, first run) | Required |
| `docs/RULES.md` | Development conventions and coding standards | Required |
| `docs/DATABASE_SETUP.md` | Database init, migration, troubleshooting | Required |
| `docs/EMAIL_SETUP.md` | SMTP configuration for Gmail/Outlook/etc. | Required |
| `docs/ENV_RECOVERY_REPORT.md` | Environment configuration recovery | Required |
| `docs/SECURITY_AUDIT_REPORT.md` | Consolidated security findings | Required |
| `docs/PERFORMANCE_OPTIMIZATION_REPORT.md` | Performance baselines and optimizations | Required |
| `docs/ACCOUNT_DELETION_AND_SESSION_EMAIL_REPORT.md` | Account deletion + session termination email impl | Required |
| `docs/NEVER_LOGGED_IN_FEATURE_REPORT.md` | "Never logged in" admin feature documentation | Review |
| `docs/PRD.md` | Product requirements document | Review |
| `docs/CLEANUP_REPORT.md` | This cleanup task's inventory and actions | Required |
| `docs/CLEANUP_INVENTORY.md` | This file — Phase 1 file audit | Required |

## 8. Quarantined Documentation (outside `docs/`, git-tracked, recoverable)
| File | Original location | Reason for quarantine |
|---|---|---|
| `quarantine_docs/ARCHITECTURE.md` | `docs/ARCHITECTURE.md` | Content covered by `docs/DESIGN.md`; referenced non-existent files |
| `quarantine_docs/MEMORY.md` | `docs/MEMORY.md` | Content consolidated into `docs/SETUP.md`, `docs/README.md`, `docs/RULES.md` |
| `quarantine_docs/TASKS.md` | `docs/TASKS.md` | Outdated task tracking with stale checkbox states |
| `reports/quarantine/SESSION_SECURITY_FIX_REPORT.md` | `docs/` | Consolidated into `docs/SECURITY_AUDIT_REPORT.md` |
| `reports/quarantine/SOURCE_CODE_SECURITY_AUDIT.md` | `docs/` | Consolidated into `docs/SECURITY_AUDIT_REPORT.md` |
| `reports/quarantine/LOGIN_OTP_FLOW_FIX_REPORT.md` | `docs/` | Consolidated into `docs/SECURITY_AUDIT_REPORT.md` |
| `reports/quarantine/LOGIN_EMAIL_FIX_REPORT.md` | `docs/` | Consolidated into `docs/SECURITY_AUDIT_REPORT.md` |
| `reports/quarantine/LOGIN_TEMPLATE_FIX_REPORT.md` | `docs/` | Consolidated into `docs/SECURITY_AUDIT_REPORT.md` |
| `reports/quarantine/OTP_EMAIL_DELIVERY_FIX_REPORT.md` | `docs/` | Consolidated into `docs/SECURITY_AUDIT_REPORT.md` |
| `reports/quarantine/ACCOUNT_DELETION_AND_SESSION_EMAIL_REPORT.md` | `docs/` | Consolidated into `docs/SECURITY_AUDIT_REPORT.md` |
| `reports/quarantine/PERFORMANCE_REPORT.md` | `docs/` | Consolidated into `docs/PERFORMANCE_OPTIMIZATION_REPORT.md` |
| `reports/quarantine/PROJECT_ERROR_AUDIT_REPORT.md` | `docs/` | Consolidated into `docs/SECURITY_AUDIT_REPORT.md` |
| `reports/quarantine/PROJECT_REPORT.md` | `docs/` | Superseded by focused reports |
| `reports/quarantine/cleanup_candidates.md` | `docs/` | Replaced by this inventory |

## 9. Cache / Build Artifacts (Safe to delete, gitignored or regenerable)
| File/Directory | Purpose | Action |
|---|---|---|
| `.pytest_cache/` | pytest cache (`.gitignore`, `CACHEDIR.TAG`, `README.md`, `v/cache/nodeids`) | **Delete** — not in `.gitignore`, regenerable |
| `__pycache__/` directories | Python bytecode cache | **Delete** — gitignored, regenerable |
| `*.pyc` files | Compiled Python bytecode | **Delete** — gitignored, regenerable |

## 10. Git Worktree (Not part of audit)
| Path | Notes |
|---|---|
| `.kilo/worktrees/unleashed-molasses/` | Git worktree — separate working copy managed by git. Contains an older revision of the project. NOT a duplicate of main project files; excluded from this audit. |

## 11. Classification Summary

| Category | Count | Files |
|---|---|---|
| **Required** (application code) | 40 files | `app.py`, `config.py`, `Procfile`, `requirements.txt`, `init_sqlite.py`, `init_postgres.py`, `migrate_to_postgres.py`, `README.md`, 8 model files, 5 route files, 9 util files |
| **Required** (templates + static) | 67 files | 14 public templates, 4 auth templates, 25 admin templates, 4 user templates, 2 static error pages, 53 upload files + directories |
| **Required** (instance + config) | 4 files | `.env`, `.env.example`, `.gitignore`, `instance/*.sqlite*` |
| **Required** (tests) | 5 files | All test files |
| **Required** (documentation) | 13 files | All docs/ files except PRD.md and NEVER_LOGGED_IN_FEATURE_REPORT.md |
| **Review candidates** | 2 files | `docs/PRD.md`, `docs/NEVER_LOGGED_IN_FEATURE_REPORT.md` |
| **Quarantined** | 15 files | 3 in `quarantine_docs/`, 12 in `reports/quarantine/` |
| **Cache artifacts** | `.pytest_cache/` + `__pycache__` | Safe to delete, regenerable |

## 12. Actions for Phase 1

| Action | Files | Notes |
|---|---|---|
| Add `.pytest_cache/` to `.gitignore` | `.gitignore` | Prevents accidental commits |
| Clean `__pycache__/` and `.pyc` files | All directories | Regenerable; gitignored |
| Delete `.pytest_cache/` | Project root | Regenerable; safe to remove |
| Quarantine completed (already done) | 15 files in `quarantine_docs/` and `reports/quarantine/` | Git-tracked, recoverable |
| No application or user-uploaded files should be deleted or moved | — | All are required |
