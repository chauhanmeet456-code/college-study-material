# Never Logged In Feature Report

## Overview

The "Never Logged In" feature adds a new admin page that lists registered
students who have never successfully completed authentication. It is accessible
from the Admin → Users page via a new "Never Logged In" button.

## User Interface

### Button Placement

A new button was added to the **Users** page (`templates/admin/users.html`),
positioned to the left of the existing "Deletion Requests" and "Deleted Users"
buttons:

```
[ Never Logged In ] [ Deletion Requests ] [ Deleted Users ]
```

The button uses the same design language as the existing buttons:
- `btn btn-outline-info` class (blue outline)
- `fas fa-user-times` icon
- `d-none d-sm-inline-flex` (hidden text on xs screens, shown on sm+)
- Responsive — collapses gracefully on small screens

### Never Logged In Page (`templates/admin/never_logged_in.html`)

The page shows a table with the following columns:

| Column | Description |
|--------|-------------|
| **ID** | User's database ID |
| **Name** | User's name with profile image/avatar |
| **Email** | User's email address |
| **Registration Date** | `created_at` timestamp |
| **Account Status** | Active / Inactive badge |
| **Last Login** | Always shows "Never logged in" |

#### Features

- **Search by name or email** — text input, server-side LIKE query
- **Status filter** — All / Active / Inactive dropdown
- **Pagination** — 10 users per page with numbered pagination
- **Empty state** — Friendly message when no matching users exist
- **Back to Users** — Button returning to the main Users page

## How "Never Logged In" Is Determined

A user is considered "never logged in" if `last_login IS NULL` in the `users`
table. The `last_login` field is updated by `User.update_last_login()` which
is called **only after successful OTP verification** in the login flow
(`routes/auth.py:395`).

This means:
- Users who registered but never logged in → `last_login IS NULL` → shown
- Users who entered credentials but failed OTP → `last_login IS NULL` → shown
- Users who failed email verification → `last_login IS NULL` → shown
- Users who completed OTP verification → `last_login` is set → NOT shown

## Backend Implementation

### Model Method (`models/user.py`)

```python
@staticmethod
def get_never_logged_in_users(search='', status='all', page=1, per_page=10):
    # Query: SELECT ... FROM users WHERE last_login IS NULL AND deleted_by_user = 0
    # with optional search and status filters, ordered by created_at DESC
    # Returns: (users, total_count)
```

- Uses parameterized queries (no SQL injection risk)
- Filters: `last_login IS NULL` (never logged in), `deleted_by_user = 0` (not deleted)
- Optional search by name or email
- Optional status filter (active/inactive/all)
- Pagination with LIMIT/OFFSET

### Route (`routes/admin.py`)

```python
@admin_bp.route('/users/never-logged-in')
@admin_required  # Requires admin authentication
def never_logged_in_users():
    search = request.args.get('search', '')
    status = request.args.get('status', 'all')
    page = int(request.args.get('page', 1))
    users, total = User.get_never_logged_in_users(search=search, status=status, page=page, per_page=10)
    return render_template('admin/never_logged_in.html', users=users, total=total, page=page, search=search, status=status, admin=get_current_admin())
```

- Protected by `@admin_required` decorator
- Non-admin users are redirected to login (302)
- Returns 302 redirect for unauthenticated users

## Database Compatibility

The `last_login` field already exists in both:
- **SQLite** (`instance/college_study.sqlite`) — `last_login` column in `users` table
- **PostgreSQL/NeónDB** — `last_login` column in `users` table (same schema)

No schema changes were required. The query `WHERE last_login IS NULL` works
identically in both SQLite and PostgreSQL.

## Verification

| Check | Result |
|-------|--------|
| Route registered at `/admin/users/never-logged-in` | PASS |
| Template `admin/never_logged_in.html` exists and parses | PASS |
| Non-admin users redirected to login | PASS |
| Search by name/email works | PASS (code inspection) |
| Status filter works | PASS (code inspection) |
| Pagination works | PASS (code inspection) |
| 79 routes registered (up from 78) | PASS |
| 50 templates (up from 49) | PASS |
| 138/138 existing tests still pass | PASS |

## Usage

1. Log in to the admin panel at `http://127.0.0.1:5000/admin/login`
2. Navigate to **Users** (`/admin/users`)
3. Click the **"Never Logged In"** button
4. Use search, filters, and pagination as needed
5. Click **"Back to Users"** to return to the main Users page

## How to Test

```bash
# 1. Run the app
python app.py

# 2. Login as admin
# Visit: http://127.0.0.1:5000/admin/login
# Default: admin / admin@123

# 3. Navigate to Users page
# Click "Never Logged In" button

# 4. Verify with tests
python tests/tests_project.py
```

## Limitations

- **Historical data**: Users who registered before the `last_login` field was
  added may show `last_login IS NULL` even if they previously logged in. For
  this specific project, the field was part of the original schema, so this
  limitation does not apply.
- **No bulk actions**: The page is read-only — users cannot be activated,
  deactivated, or deleted from this page. Use the main Users page for actions.
- **No email verification status column**: The current schema does not track
  email verification status separately. Users with unverified emails are still
  shown in the list (since they have `last_login IS NULL`).
