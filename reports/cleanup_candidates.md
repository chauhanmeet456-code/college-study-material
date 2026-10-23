# Cleanup Candidates Report

This document records all files examined for cleanup, the analysis performed, and the final decision for each candidate.

## Cleanup Process

All files were inspected by:
1. Searching for imports and references across the codebase
2. Checking if routes, templates, or scripts reference the file
3. Verifying the file's purpose and age

No files were permanently deleted. Confirmed-obsolete generated artifacts (`__pycache__`, `.pyc`) are safe to regenerate and are ignored by `.gitignore`.

---

## Candidates

### 1. `__pycache__/` directories and `*.pyc` files

| Field | Detail |
|-------|--------|
| **Path** | Throughout project root, `models/`, `routes/`, `tests/`, `utils/`, `templates/`, `.kilo/worktrees/` |
| **Reason** | Compiled Python bytecode caches, automatically regenerated |
| **Reference checks** | Not imported or referenced by any code |
| **Risk** | None - standard build artifacts, excluded by `.gitignore` |
| **Decision** | **Leave in place** (harmless, auto-regenerated). Safe to delete with `find . -name __pycache__ -type d -exec rm -rf {} +` |

### 2. `.kilo/worktrees/unleashed-molasses/` directory

| Field | Detail |
|-------|--------|
| **Path** | `.kilo/worktrees/unleashed-molasses/` |
| **Reason** | Appears to be a Kilo Agent Manager worktree with copies of project files |
| **Reference checks** | Not referenced by application code; managed by Kilo CLI |
| **Risk** | High - managed by external tool, do not modify or delete |
| **Decision** | **Keep** - this is Kilo Agent Manager infrastructure, not a project file |

### 3. `.env.example`

| Field | Detail |
|-------|--------|
| **Path** | `.env.example` |
| **Reason** | Placeholder for environment configuration |
| **Reference checks** | Referenced in README.md as setup instructions |
| **Risk** | Critical - needed for project setup |
| **Decision** | **Updated** - added `DATABASE_URL=` placeholder line, kept all credential placeholders |

### 4. `init_sqlite.py`

| Field | Detail |
|-------|--------|
| **Path** | `init_sqlite.py` |
| **Reason** | SQLite schema initialization script |
| **Reference checks** | Not imported by application, but referenced in README for dev setup |
| **Risk** | None - safe to keep; needed for SQLite dev environment setup |
| **Decision** | **Keep** - still used for development SQLite setup |

### 5. `init_postgres.py`

| Field | Detail |
|-------|--------|
| **Path** | `init_postgres.py` |
| **Reason** | PostgreSQL schema initialization script (newly created) |
| **Reference checks** | Not imported by application, used standalone for PG schema setup |
| **Risk** | None - standalone initialization script |
| **Decision** | **Keep** - required for PostgreSQL schema initialization |

### 6. `migrate_to_postgres.py`

| Field | Detail |
|-------|--------|
| **Path** | `migrate_to_postgres.py` |
| **Reason** | SQLite-to-PostgreSQL data migration script (newly created) |
| **Reference checks** | Not imported by application, used standalone for migration |
| **Risk** | None - standalone migration script |
| **Decision** | **Keep** - required for one-time SQLite-to-PostgreSQL migration |

### 7. `docs/` directory files

| Field | Detail |
|-------|--------|
| **Path** | `docs/ARCHITECTURE.md`, `docs/DESIGN.md`, `docs/EMAIL_SETUP.md`, `docs/PRD.md`, `docs/RULES.md`, `docs/TASKS.md` |
| **Reason** | Project documentation files |
| **Reference checks** | Not referenced by application code |
| **Risk** | None - documentation, safe to keep |
| **Decision** | **Keep** - useful documentation; updated with current code state |

### 8. Duplicate Documentation (docs/ directory)

| Field | Detail |
|-------|--------|
| **Path** | `docs/CHANGELOG.md`, `docs/DATABASE_SETUP.md`, `docs/PROJECT_REPORT.md`, `docs/TESTING.md` |
| **Reason** | Duplicates of root-level documentation files (`CHANGELOG.md`, `DATABASE_SETUP.md`, `PROJECT_REPORT.md`, `TESTING.md`) with slightly different content |
| **Reference checks** | Not referenced by application code |
| **Risk** | Low - documentation only; some `docs/` files may lack latest performance optimization info |
| **Decision** | **Keep both, root-level is authoritative** - Root-level files include the latest session's performance optimizations (connection pooling, settings caching, CSP fixes); `docs/` versions predate those changes. Consolidating to one location is recommended for future maintenance. Updated unique docs files (`docs/MEMORY.md`, `docs/NO_ERROR_REPORT.md`) with current performance findings. |

### 9. `static/uploads/` files

| Field | Detail |
|-------|--------|
| **Path** | `static/uploads/` (90 files) |
| **Reason** | Uploaded study materials |
| **Reference checks** | Referenced by templates and routes for file serving |
| **Risk** | High - user-generated content, do not touch |
| **Decision** | **Keep** - essential project data |

---

## Unused Imports Removed (Code Cleanup)

The following unused imports were identified via AST analysis and removed as part of code cleanup (not file deletion):

| File | Import Removed | Reason |
|------|---------------|--------|
| `app.py:20` | `from utils.auth import get_current_user` | Not used in app.py (used in routes instead) |
| `routes/main.py:85` | `from models.user import User` | Inner import not used (raw SQL COUNT used instead) |
| `routes/admin.py:1` | `send_from_directory` from flask import | Not used anywhere in file |
| `routes/admin.py:3` | `from werkzeug.security import generate_password_hash` | Not used anywhere in file |
| `routes/admin.py:20` | `format_file_size` from utils.helpers | Not used in routes (function exists in helpers.py) |
| `routes/user.py:12` | `from models.admin import Admin` | Model not used (AdminAuditLog used instead) |
| `routes/user.py:13` | `from models.message import Announcement, ContactMessage` | Not used in file |
| `routes/user.py:24` | `allowed_file` from utils.helpers | Not used (allowed_image_file and generate_safe_filename are used) |
| `tests/tests_project.py:27` | `UserSession, LoginHistory, FailedLoginAttempt` | Imported but never referenced in tests |
| `utils/auth.py:1` | `flash` from flask | Not used (session is used, flash is not) |
| `utils/helpers.py:1` | `import os` | Not used (secure_filename and Config are used) |
| `init_postgres.py:13` | `from psycopg.rows import dict_row` | Not used (connects without row_factory) |
| `tests/tests_session_management.py` | BOM character (U+FEFF) | Removed non-printable character at file start |

---

## Summary

| Item | Action |
|------|--------|
| `__pycache__` / `.pyc` | Left in place (auto-regenerated, gitignored) |
| `.kilo/worktrees/` | Kept (Kilo CLI infrastructure) |
| Unused imports | Removed (13 imports across 8 files) |
| BOM character | Removed from `tests_session_management.py` |
| `.env` | Untouched (preserved original) |
| SQLite database | Untouched (both `.sqlite` and `.sqlite.bak` preserved) |
| `requirements.txt` | Restored to valid package list (removed pasted task description) |
| Test files | Fixed missing `os.chdir(root)` in session management tests |

No files were permanently deleted. No data was lost.
