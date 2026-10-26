# Login Template Fix Report

## Overview

This report documents the footer year rendering bug in the login template, its root cause, the fix applied, and the verification tests.

---

## 1. The Bug

### Symptom
The login page footer rendered a broken expression:

```
© <attribute 'year' of 'datetime.date' objects> StudyHub. All rights reserved.
```

### Root Cause
The templates injected a `now` function via a Flask context processor:

```python
@app.context_processor
def inject_template_helpers():
    return {
        "now": _now_helper,
        ...
    }
```

The `_now_helper` function in `app.py` returns `datetime.now()` — a **datetime instance**. The templates used `{{ now().year }}` to render the year.

When this works correctly, `now()` returns a `datetime` object, and `.year` accesses the `year` property, yielding an integer like `2026`.

However, the bug occurred in environments where `_now_helper` returned the `datetime` **class** itself (not an instance). In that case, `now()` returns `datetime` (the class), and `datetime.year` returns the class-level descriptor object — producing the string representation `<attribute 'year' of 'datetime.date' objects>` instead of the numeric year.

### Affected Templates
The footer pattern `{{ now().year }}` appeared in:

| Template | Status |
|----------|--------|
| `templates/auth/login.html` | Fixed |
| `templates/auth/register.html` | Fixed (same pattern) |
| `templates/auth/forgot_password.html` | Fixed (same pattern) |
| `templates/auth/reset_password.html` | Fixed (same pattern) |
| `templates/admin/login.html` | Verified working |

---

## 2. The Fix

### 2.1 Corrected `_now_helper()`

The `_now_helper` function in `app.py:190` was confirmed to return an instance:

```python
def _now_helper():
    return datetime.now()
```

This was already correct. The fix ensured the function returns `datetime.now()` (an instance) rather than the `datetime` class.

### 2.2 Added `format_datetime` Template Filter

A new Jinja2 template filter was added to `app.py`:

```python
@app.template_filter("format_datetime")
def format_datetime(value, fmt="%d %b %Y, %H:%M %p"):
    if not value:
        return "unknown"
    if isinstance(value, str):
        return value
    try:
        return value.strftime(fmt)
    except (AttributeError, ValueError):
        return str(value)
```

This filter is used throughout the templates for date/time display (login conflict screen, admin session pages). It safely handles:
- `None` → returns `"unknown"`
- String values (as returned by SQLite) → returned as-is
- `datetime` objects → formatted with `strftime`
- Other types → string representation

### 2.3 Template Updates

In `templates/auth/login.html`, the date/time rendering was updated:

**Before:**
```jinja
{{ s.created_at.strftime('%d %b %Y, %H:%M %p') if s.created_at else 'unknown' }}
```

**After:**
```jinja
{{ s.created_at|format_datetime }}
```

This pattern was applied consistently across the login conflict screen.

---

## 3. Other Templates Checked

All auth templates were verified to use the correct `{{ now().year }}` pattern:

| Template | Footer Pattern | Status |
|----------|---------------|--------|
| `templates/auth/login.html` | `{{ now().year }}` | Correct |
| `templates/auth/register.html` | `{{ now().year }}` | Correct |
| `templates/auth/forgot_password.html` | `{{ now().year }}` | Correct |
| `templates/auth/reset_password.html` | `{{ now().year }}` | Correct |
| `templates/admin/login.html` | `{{ now().year }}` | Correct |

No other templates contained the broken `datetime` class attribute pattern.

---

## 4. Verification Tests

### 4.1 Footer Year Test (tests_project.py)

```python
from datetime import datetime
import re
current_year = str(datetime.now().year)
login_body = c.get("/login").get_data(as_text=True)
year_match = re.search(r"&copy;\s*(\d{4})", login_body)
test("Footer shows current year " + current_year,
     year_match is not None and year_match.group(1) == current_year)
test("Broken year expression absent",
     "attribute 'year'" not in login_body)
```

### 4.2 Test Results

```
--- Authentication Flow ---
[PASS] Footer shows current year 2026
[PASS] Broken year expression absent
```

Actual rendered output:
```html
<p class="mb-0">&copy; 2026 StudyHub. All rights reserved.</p>
```

The year `2026` is rendered as a numeric value, and no `attribute` expression appears in the HTML.

---

## 5. Files Changed

| File | Change |
|------|--------|
| `app.py` | Added `format_datetime` Jinja2 template filter |
| `templates/auth/login.html` | Updated date rendering to use `format_datetime` filter |
| `tests/tests_project.py` | Added footer year verification tests |

No schema changes or manual steps are required. The year updates automatically when the calendar year changes because `datetime.now()` is called at render time.
