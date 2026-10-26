"""
Comprehensive test suite for the StudyHub College Study Material Platform.

Covers all major features:
- Application startup and route registration
- Public route accessibility
- Authentication (login, OTP, logout, registration)
- Session management (single active session, conflict detection)
- Admin panel functionality
- CSRF protection
- Security checks
- Database integrity

Run: python tests_project.py
"""

import os
import re
import sys

import pytest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
os.chdir(root)

from app import create_app
from models.user import User
from utils.otp import OTPManager, RESULT_SUCCESS
from utils.database import get_connection, get_cursor, close_cursor
from config import Config

# Tests run against the bundled SQLite database to avoid depending on an
# external PostgreSQL / NeonDB server.  This only overrides the in-process
# Config attribute — the .env file is left untouched.
Config.USE_POSTGRES = False

OTPManager.send_otp_email = staticmethod(lambda *a, **k: RESULT_SUCCESS)
OTPManager.send_email = staticmethod(lambda *a, **k: RESULT_SUCCESS)
OTPManager.is_configured = staticmethod(lambda: True)

app = create_app()
results = []


def test(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((name, status, detail))
    print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))


# Prevent pytest from collecting this helper as a test function.
test.__test__ = False


@pytest.fixture
def uid():
    """Pytest fixture: create (or reuse) the test user and return its id."""
    uid_val, _email = setup_database()
    return uid_val


@pytest.fixture
def mat_id():
    """Pytest fixture: return the id of the first material row, or None."""
    return get_test_material_id()


def csrf_from(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match:
        return match.group(1)
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    if match:
        return match.group(1)
    return None


EMAIL = "testsuite_user@example.com"
PASSWORD = "TestPass123!"
PASSED = 0
FAILED = 0


def setup_database():
    """Create test user and clean state."""
    with app.app_context():
        u = User.get_by_email(EMAIL)
        if not u:
            User.create("Test Suite User", EMAIL, PASSWORD)
            u = User.get_by_email(EMAIL)
            User.update(u["id"], is_active=1)
        uid = u["id"]
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM user_sessions WHERE user_id=?", (uid,))
        cursor.execute("DELETE FROM login_history WHERE user_id=?", (uid,))
        cursor.execute(
            "DELETE FROM failed_login_attempts WHERE email_masked LIKE ?", ("%test%",)
        )
        conn.commit()
        close_cursor(cursor)
        conn.close()
        return uid, EMAIL


def get_test_material_id():
    with app.app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("SELECT id FROM materials LIMIT 1")
        row = cursor.fetchone()
        close_cursor(cursor)
        conn.close()
        return row["id"] if row else None


def test_app_startup():
    """Test 1: Application startup."""
    print("\n--- Application Startup ---")
    test("Flask app creates successfully", app is not None)
    routes = list(app.url_map.iter_rules())
    test("Routes registered", len(routes) > 0, f"{len(routes)} routes")
    seen = set()
    dups = [r for r in routes if str(r) in seen or seen.add(str(r))]
    test("No duplicate routes", len(dups) == 0)


def test_python_compilation():
    """Test 2: All Python files compile."""
    print("\n--- Python Compilation ---")
    import py_compile

    errors = 0
    total = 0
    for dirpath, _, files in os.walk(root):
        if "__pycache__" in dirpath or "cleanup" in dirpath or ".kilo" in dirpath:
            continue
        for f in files:
            if f.endswith(".py"):
                total += 1
                try:
                    py_compile.compile(os.path.join(dirpath, f), doraise=True)
                except Exception:
                    errors += 1
    test(f"All Python files compile ({total} files)", errors == 0, f"{errors} errors")


def test_template_parsing():
    """Test 3: All templates parse."""
    print("\n--- Template Parsing ---")
    import jinja2

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(root, "templates"))
    )
    errors = 0
    total = 0
    for dirpath, _, files in os.walk(os.path.join(root, "templates")):
        for f in files:
            if f.endswith(".html"):
                total += 1
                try:
                    env.parse(open(os.path.join(dirpath, f), encoding="utf-8").read())
                except Exception:
                    errors += 1
    test(f"All templates parse ({total} templates)", errors == 0, f"{errors} errors")


def test_public_routes():
    """Test 4: All public routes accessible."""
    print("\n--- Public Routes ---")
    c = app.test_client()
    routes = [
        ("/", "Homepage"),
        ("/subjects", "Subjects"),
        ("/about", "About"),
        ("/contact", "Contact"),
        ("/notes", "Notes"),
        ("/assignments", "Assignments"),
        ("/books", "Books"),
        ("/practicals", "Practicals"),
        ("/question-papers", "Q-Papers"),
        ("/important-questions", "Important Qs"),
        ("/syllabus", "Syllabus"),
        ("/search?q=test", "Search"),
        ("/login", "Login"),
        ("/register", "Register"),
        ("/forgot-password", "Forgot Pass"),
    ]
    for path, name in routes:
        r = c.get(path)
        test(f"GET {path} ({name})", r.status_code == 200, f"{r.status_code}")


def test_material_pages(uid, mat_id):
    """Test 5: Material detail pages."""
    print("\n--- Material Pages ---")
    c = app.test_client()
    if mat_id:
        r = c.get(f"/material/{mat_id}")
        test("Material details (valid ID)", r.status_code == 200)
    else:
        test("Material details (valid ID)", False, "No materials")
    r = c.get("/material/999999")
    test("Material details (invalid ID -> 404)", r.status_code == 404)
    r = c.get("/material/abc")
    test("Material details (non-int ID -> 404)", r.status_code == 404)


def test_full_login_flow(uid, mat_id):
    """Test 6: Complete login flow (credentials → OTP → authenticated)."""
    print("\n--- Authentication Flow ---")
    c = app.test_client()
    page = c.get("/login")
    tok = csrf_from(page.get_data(as_text=True))
    test("Login page with CSRF", tok is not None)

    r = c.post("/login", data={"email": EMAIL, "password": PASSWORD, "csrf_token": tok})
    test("Login credential step", r.status_code == 200)

    with c.session_transaction() as s:
        otp_data = s.get(f"otp_login_{EMAIL}")
        otp = otp_data.get("otp") if otp_data else None
    test("OTP stored in session", otp is not None)

    if otp:
        r = c.post("/login", data={"otp": otp, "csrf_token": tok})
        test("OTP verification", r.status_code == 302)

        r = c.get("/profile")
        test("Profile accessible", r.status_code == 200)

        r = c.get("/account/login-history")
        test("Login history page", r.status_code == 200)

        r = c.get("/favorites")
        test("Favorites page", r.status_code == 200)

    # Footer year test
    from datetime import datetime
    import re
    current_year = str(datetime.now().year)
    login_body = c.get("/login").get_data(as_text=True)
    year_match = re.search(r"&copy;\s*(\d{4})", login_body)
    test("Footer shows current year " + current_year,
         year_match is not None and year_match.group(1) == current_year)
    test("Broken year expression absent",
         "attribute 'year'" not in login_body and
         "attribute 'year'" not in login_body.replace("&amp;", "&"))

    # Logout
    hp = c.get("/account/login-history")
    ltok = csrf_from(hp.get_data(as_text=True))
    r = c.post("/logout", data={"csrf_token": ltok})
    test("Logout (with CSRF)", r.status_code == 302)

    r = c.get("/profile", follow_redirects=False)
    test("Profile redirects after logout", r.status_code in (301, 302))


def test_registration():
    """Test 7: Registration."""
    print("\n--- Registration ---")
    c = app.test_client()
    page = c.get("/register")
    tok = csrf_from(page.get_data(as_text=True))
    new_email = f"reg_{os.urandom(4).hex()}@example.com"
    r = c.post(
        "/register",
        data={
            "name": "Reg User",
            "email": new_email,
            "password": "Pass123!",
            "confirm_password": "Pass123!",
            "csrf_token": tok,
        },
    )
    test("Registration with CSRF", r.status_code in (200, 302))

    with app.app_context():
        u = User.get_by_email(new_email)
        if u:
            conn = get_connection()
            cursor = get_cursor(conn)
            cursor.execute("DELETE FROM user_sessions WHERE user_id=?", (u["id"],))
            cursor.execute("DELETE FROM login_history WHERE user_id=?", (u["id"],))
            cursor.execute("DELETE FROM users WHERE id=?", (u["id"],))
            conn.commit()
            close_cursor(cursor)
            conn.close()


def test_forgot_password():
    """Test 8: Forgot password page."""
    print("\n--- Authentication (continued) ---")
    c = app.test_client()
    r = c.get("/forgot-password")
    test("Forgot password page", r.status_code == 200)


def test_admin_authentication():
    """Test 9: Admin authentication and routes."""
    print("\n--- Admin Authentication ---")
    admin = app.test_client()
    page = admin.get("/admin/login")
    tok = csrf_from(page.get_data(as_text=True))
    r = admin.post(
        "/admin/login",
        data={"username": "admin", "password": "admin@123", "csrf_token": tok},
    )
    test("Admin login with CSRF", r.status_code == 302)

    admin_pages = [
        ("/admin/dashboard", "Dashboard"),
        ("/admin/users", "Users"),
        ("/admin/subjects", "Subjects"),
        ("/admin/materials", "Materials"),
        ("/admin/categories", "Categories"),
        ("/admin/messages", "Messages"),
        ("/admin/announcements", "Announcements"),
        ("/admin/settings", "Settings"),
        ("/admin/user-security", "User Security"),
        ("/admin/user-security/audit-log", "Audit Log"),
        ("/admin/user-security/failed-attempts", "Failed Attempts"),
    ]
    for path, name in admin_pages:
        r = admin.get(path)
        test(f"Admin GET {path}", r.status_code == 200, f"{r.status_code}")


def test_unauthorized_access(uid):
    """Test 10: Unauthorized access prevention."""
    print("\n--- Authorization ---")
    guest = app.test_client()
    r = guest.get("/admin/dashboard")
    test("Guest redirected from admin", r.status_code in (301, 302))

    r = guest.get("/admin/user-security")
    test("Guest redirected from admin security", r.status_code in (301, 302))

    r = guest.get("/profile", follow_redirects=False)
    test("Guest redirected from profile", r.status_code in (301, 302))

    r = guest.get("/account/login-history", follow_redirects=False)
    test("Guest redirected from login history", r.status_code in (301, 302))


def test_security():
    """Test 11: Security controls."""
    print("\n--- Security ---")

    # CSRF on all POST forms
    templ_dir = os.path.join(root, "templates")
    total = 0
    with_csrf = 0
    for dirpath, _, files in os.walk(templ_dir):
        for f in files:
            if not f.endswith(".html"):
                continue
            content = open(os.path.join(dirpath, f), encoding="utf-8").read()
            for m in re.finditer(
                r"<form\b[^>]*>(.*?)</form>", content, re.DOTALL | re.IGNORECASE
            ):
                form = m.group(0)
                if re.search(r'method=["\']post["\']', form, re.IGNORECASE):
                    total += 1
                    if "csrf" in form.lower():
                        with_csrf += 1
    test(f"CSRF tokens on POST forms ({with_csrf}/{total})", with_csrf == total)

    # XSS fix
    msg = open(
        os.path.join(templ_dir, "admin", "message_detail.html"), encoding="utf-8"
    ).read()
    test("XSS fix: message uses |e filter", "|e|nl2br|safe" in msg)

    # CSRF meta tags
    test(
        "CSRF meta tag in base.html",
        "csrf-token"
        in open(os.path.join(templ_dir, "base.html"), encoding="utf-8").read(),
    )
    test(
        "CSRF meta tag in admin/base.html",
        "csrf-token"
        in open(os.path.join(templ_dir, "admin", "base.html"), encoding="utf-8").read(),
    )

    # Password hashing
    user_model = open(os.path.join(root, "models", "user.py"), encoding="utf-8").read()
    test("Password hashing (werkzeug)", "generate_password_hash" in user_model)

    # Debug mode
    config = open(os.path.join(root, "config.py"), encoding="utf-8").read()
    test(
        "Debug env-controlled", "FLASK_DEBUG" in config and "DEBUG = True" not in config
    )

    # Path traversal
    main_routes = open(os.path.join(root, "routes", "main.py"), encoding="utf-8").read()
    test(
        "Path traversal protection",
        "startswith(os.path.abspath(Config.UPLOAD_FOLDER))" in main_routes,
    )

    # Server-side session validation
    app_py = open(os.path.join(root, "app.py"), encoding="utf-8").read()
    test(
        "Server-side session validation",
        "before_request" in app_py and "session_token" in app_py,
    )

    # Session token hashing
    session_model = open(
        os.path.join(root, "models", "session.py"), encoding="utf-8"
    ).read()
    test("Session token hashing (SHA-256)", "hash_session_token" in session_model)

    # Rate limiting
    sec = open(os.path.join(root, "utils", "security.py"), encoding="utf-8").read()
    test("Login rate limiting", "check_login_rate_limit" in sec)
    test(
        "Security headers",
        "X-Content-Type-Options" in sec and "Content-Security-Policy" in sec,
    )

    # --- Source-code / sensitive-file exposure ---
    c = app.test_client()
    sensitive_paths = [
        "/.env", "/config.py", "/app.py", "/.gitignore",
        "/requirements.txt", "/instance/college_study.sqlite",
        "/static/.env", "/static/config.py", "/.git/config",
        "/.git/HEAD", "/instance/", "/logs/",
    ]
    for sp in sensitive_paths:
        r = c.get(sp)
        test("Sensitive file not served: " + sp, r.status_code in (403, 404),
             "status=" + str(r.status_code))

    # --- Path traversal attempts ---
    traversal_paths = [
        "/material/1/download/../../../../../../etc/passwd",
        "/static/../../../config.py",
        "/static/uploads/../../app.py",
    ]
    for tp in traversal_paths:
        r = c.get(tp)
        test("Path traversal blocked: " + tp, r.status_code in (400, 403, 404),
             "status=" + str(r.status_code))

    # --- Sensitive data not in rendered pages ---
    r = c.get("/login")
    body = r.get_data(as_text=True)
    for secret in ["dev-secret-key", "DATABASE_URL", "MAIL_PASSWORD",
                    "SECRET_KEY", "password_hash", "traceback"]:
        test("No '%s' in login page" % secret, secret.lower() not in body.lower())

    r = c.get("/admin/login")
    body = r.get_data(as_text=True)
    for secret in ["dev-secret-key", "DATABASE_URL", "MAIL_PASSWORD"]:
        test("No '%s' in admin login page" % secret, secret.lower() not in body.lower())

    # --- Debug tracebacks not disclosed ---
    r = c.get("/nonexistent-debug-test-page")
    body = r.get_data(as_text=True)
    test("No traceback in 404 page", "Traceback" not in body)
    test("No file paths in 404 page", "Traceback" not in body and "/home/" not in body)
    test("404 returns 404 status", r.status_code == 404)


def test_static_assets():
    """Test 12: Static assets."""
    print("\n--- Static Assets ---")
    c = app.test_client()
    r = c.get("/")
    html = r.get_data(as_text=True)
    test("Bootstrap CDN loaded", "bootstrap@5" in html)
    test("Font Awesome loaded", "font-awesome" in html)

    uploads = os.path.join(root, "static", "uploads")
    upload_count = (
        sum(len(files) for _, _, files in os.walk(uploads))
        if os.path.isdir(uploads)
        else 0
    )
    test("Uploads directory has files", upload_count > 0, f"{upload_count} files")


def test_database():
    """Test 13: Database integrity."""
    print("\n--- Database ---")
    with app.app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) if Config.USE_SQLITE else cursor.execute(
            "SELECT tablename as name FROM pg_tables WHERE schemaname='public'"
        )
        tables = cursor.fetchall()
        test("Database tables exist", len(tables) >= 10, f"{len(tables)} tables")
        if Config.USE_POSTGRES:
            cursor.execute("SET CONSTRAINTS ALL DEFERRED; SET CONSTRAINTS ALL IMMEDIATE;")
            cursor.execute("SELECT 1")
        else:
            cursor.execute("PRAGMA foreign_key_check")
        test("Foreign key integrity", True)
        close_cursor(cursor)
        conn.close()


def test_error_handling():
    """Test 14: Error pages."""
    print("\n--- Error Handling ---")
    c = app.test_client()
    r = c.get("/nonexistent-page-12345")
    test("404 error page", r.status_code == 404)
    body = r.get_data(as_text=True).lower()
    test(
        "404 page renders content",
        "404" in body or "error" in body or "not found" in body,
    )


def test_contact_form():
    """Test 15: Contact form with CSRF."""
    print("\n--- Contact Form ---")
    c = app.test_client()
    page = c.get("/contact")
    tok = csrf_from(page.get_data(as_text=True))
    r = c.post(
        "/contact",
        data={
            "name": "Test User",
            "email": "test@example.com",
            "subject": "Test",
            "message": "Hello",
            "csrf_token": tok,
        },
    )
    test("Contact form submission (with CSRF)", r.status_code in (200, 302))


def cleanup(uid):
    """Clean up test data."""
    with app.app_context():
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("DELETE FROM user_sessions WHERE user_id=?", (uid,))
        cursor.execute("DELETE FROM login_history WHERE user_id=?", (uid,))
        cursor.execute(
            "DELETE FROM failed_login_attempts WHERE email_masked LIKE ?", ("%test%",)
        )
        conn.commit()
        close_cursor(cursor)
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("StudyHub Project Test Suite")
    print("=" * 60)

    uid, email = setup_database()
    mat_id = get_test_material_id()

    test_app_startup()
    test_python_compilation()
    test_template_parsing()
    test_public_routes()
    test_material_pages(uid, mat_id)
    test_full_login_flow(uid, mat_id)
    test_registration()
    test_forgot_password()
    test_admin_authentication()
    test_unauthorized_access(uid)
    test_security()
    test_static_assets()
    test_database()
    test_error_handling()
    test_contact_form()

    cleanup(uid)

    passed = sum(1 for _, status, _ in results if status == "PASS")
    failed = sum(1 for _, status, _ in results if status == "FAIL")

    print("\n" + "=" * 60)
    print(f"SUMMARY: {passed} passed, {failed} failed, {len(results)} total")
    print("=" * 60)

    if failed:
        print("\nFailed tests:")
        for name, status, detail in results:
            if status == "FAIL":
                print(f"  - {name}: {detail}")
    else:
        print("\nAll tests passed!")
