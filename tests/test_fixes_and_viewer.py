import os
import sqlite3
import pytest
from flask import url_for
from config import Config
from utils.database import DatabaseConnectionError, PostgreSQLConnectionPool
import psycopg


@pytest.fixture(scope='module')
def app_with_test_data(tmp_path_factory):
    from app import create_app

    config_names = (
        'USE_SQLITE', 'USE_POSTGRES', 'SQLITE_DB', 'UPLOAD_FOLDER',
        'SECRET_KEY', 'SESSION_COOKIE_SECURE',
    )
    previous_config = {name: getattr(Config, name) for name in config_names}
    temp_dir = tmp_path_factory.mktemp('studyhub_fixes')
    db_path = str(temp_dir / 'test_fixes.sqlite')
    upload_dir = str(temp_dir / 'uploads')
    os.makedirs(upload_dir, exist_ok=True)

    # Set up test config
    Config.USE_SQLITE = True
    Config.USE_POSTGRES = False
    Config.SQLITE_DB = db_path
    Config.UPLOAD_FOLDER = upload_dir
    Config.SECRET_KEY = 'test-secret-key-fixes'
    Config.SESSION_COOKIE_SECURE = False

    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, password_hash TEXT,
            profile_image TEXT, is_active INTEGER DEFAULT 1,
            deleted_by_user INTEGER DEFAULT 0, created_at DATETIME,
            last_login DATETIME
        );
        CREATE TABLE user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            session_token_hash TEXT, device_name TEXT, browser_name TEXT,
            operating_system TEXT, ip_address TEXT, created_at DATETIME,
            last_activity_at DATETIME, expires_at DATETIME, status TEXT,
            ended_by_admin INTEGER DEFAULT 0
        );
        CREATE TABLE subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, code TEXT,
            description TEXT, semester INTEGER DEFAULT 5, icon TEXT,
            is_active INTEGER DEFAULT 1, created_at DATETIME
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT,
            icon TEXT, is_active INTEGER DEFAULT 1
        );
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT,
            subject_id INTEGER, category_id INTEGER, file_path TEXT,
            file_name TEXT, file_type TEXT, file_size INTEGER,
            view_count INTEGER DEFAULT 0, download_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1, created_by INTEGER,
            created_at DATETIME
        );
        CREATE TABLE guest_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, material_id INTEGER
        );
        CREATE TABLE guest_downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, material_id INTEGER
        );
        CREATE TABLE announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, content TEXT,
            type TEXT DEFAULT 'info', is_active INTEGER DEFAULT 1,
            created_at DATETIME
        );
        CREATE TABLE settings (
            key TEXT PRIMARY KEY, value TEXT, updated_at DATETIME
        );

        INSERT INTO subjects (id, name, code) VALUES (1, 'Computer Networks', 'CN301');
        INSERT INTO categories (id, name) VALUES (1, 'Notes'), (2, 'Other');

        -- Material 1: Missing file on disk
        INSERT INTO materials (id, title, subject_id, category_id, file_path, file_name, file_type, is_active)
        VALUES (1, 'Missing Chapter Notes', 1, 1, 'static/uploads/notes', 'missing_file.pdf', 'pdf', 1);

        -- Material 2: Existing PDF file
        INSERT INTO materials (id, title, subject_id, category_id, file_path, file_name, file_type, is_active)
        VALUES (2, 'Existing Sample PDF', 1, 1, 'uploads', 'sample.pdf', 'pdf', 1);

        -- Material 3: Existing Video file
        INSERT INTO materials (id, title, subject_id, category_id, file_path, file_name, file_type, is_active)
        VALUES (3, 'Day 5 Video Lecture', 1, 2, 'uploads/NETWORKING/Day 5', 'day_5.mp4', 'mp4', 1);
    """)
    conn.commit()
    conn.close()

    # Create the physical test files
    sample_pdf = os.path.join(upload_dir, 'sample.pdf')
    with open(sample_pdf, 'wb') as f:
        f.write(b"%PDF-1.4 sample content for testing")

    video_dir = os.path.join(upload_dir, 'NETWORKING', 'Day 5')
    os.makedirs(video_dir, exist_ok=True)
    video_file = os.path.join(video_dir, 'day_5.mp4')
    with open(video_file, 'wb') as f:
        f.write(b"dummy mp4 video bytes for range test")

    app = create_app()
    app.config['TESTING'] = True
    yield app
    for name, value in previous_config.items():
        setattr(Config, name, value)


def test_privacy_policy_route(app_with_test_data):
    """Test that /privacy-policy returns 200, works without login, and has proper structure."""
    client = app_with_test_data.test_client()
    resp = client.get('/privacy-policy')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Privacy Policy' in html
    assert 'Information processed' in html
    assert 'Changes and contact' in html


def test_terms_of_service_route(app_with_test_data):
    """Test that /terms-of-service returns 200, works without login, and has proper structure."""
    client = app_with_test_data.test_client()
    resp = client.get('/terms-of-service')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Terms of Service' in html
    assert 'Acceptance and service' in html
    assert 'Acceptable use' in html


def test_favicon_route(app_with_test_data):
    """Test that /favicon.ico returns 200 without a 404 error."""
    client = app_with_test_data.test_client()
    resp = client.get('/favicon.ico')
    assert resp.status_code == 200
    assert 'image/' in resp.headers.get('Content-Type', '')


def test_material_viewer_missing_file_handled_gracefully(app_with_test_data):
    """Test that viewing a material with missing file does NOT render a nested 404 iframe."""
    client = app_with_test_data.test_client()
    resp = client.get('/material/1/view')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Must contain informative missing file message
    assert 'File Currently Unavailable' in html
    assert 'not present on this server deployment storage' in html

    # Must NOT render an iframe pointing to static 404
    assert '<iframe src="/static/uploads/notes/missing_file.pdf"' not in html

    # JavaScript navigation functions must be defined
    assert 'function prevPage()' in html
    assert 'function nextPage()' in html
    assert 'function zoomIn()' in html
    assert 'function zoomOut()' in html


def test_material_viewer_existing_pdf(app_with_test_data):
    """Test that viewing an existing PDF renders the viewer iframe using raw_material_file url."""
    client = app_with_test_data.test_client()
    resp = client.get('/material/2/view')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '<iframe src="/material/2/file#view=FitH"' in html
    assert 'sample.pdf' in html or 'Existing Sample PDF' in html
    assert client.get('/material/2/file').status_code == 200


def test_material_viewer_existing_video(app_with_test_data):
    """Test that viewing an existing video renders the HTML5 video player."""
    client = app_with_test_data.test_client()
    resp = client.get('/material/3/view')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '<video controls' in html
    assert '<source src="/material/3/file" type="video/mp4">' in html
    response = client.get('/material/3/file')
    assert response.status_code == 200
    assert response.data == b"dummy mp4 video bytes for range test"


def test_material_details_reports_missing_upload(app_with_test_data):
    client = app_with_test_data.test_client()
    response = client.get('/material/1')

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'file is currently unavailable' in html
    assert 'href="/material/1/view"' not in html
    assert 'href="/material/1/download"' not in html


def test_raw_material_file_endpoint(app_with_test_data):
    """Test /material/<id>/file endpoint for existing and missing files."""
    client = app_with_test_data.test_client()

    # Existing PDF file
    resp = client.get('/material/2/file')
    assert resp.status_code == 200
    assert resp.data == b"%PDF-1.4 sample content for testing"

    # Missing file returns 404 cleanly
    resp_missing = client.get('/material/1/file')
    assert resp_missing.status_code == 404


def test_material_download_missing_file(app_with_test_data):
    """Test download endpoint when file is missing: redirects cleanly with flash, no crash."""
    client = app_with_test_data.test_client()
    resp = client.get('/material/1/download', follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'The requested file is currently unavailable' in html


def test_material_download_existing_file(app_with_test_data):
    """Test download endpoint when file exists: returns attachment."""
    client = app_with_test_data.test_client()
    resp = client.get('/material/2/download')
    assert resp.status_code == 200
    assert 'attachment' in resp.headers.get('Content-Disposition', '')
    assert resp.data == b"%PDF-1.4 sample content for testing"


def test_serve_uploaded_file_fallback(app_with_test_data):
    """Test case-insensitive and URL-encoded uploads fallback route."""
    client = app_with_test_data.test_client()

    # Exact path
    resp = client.get('/static/uploads/NETWORKING/Day%205/day_5.mp4')
    assert resp.status_code == 200
    assert resp.data == b"dummy mp4 video bytes for range test"

    # Lowercase path (case-insensitive test)
    resp_lower = client.get('/static/uploads/networking/day%205/day_5.mp4')
    assert resp_lower.status_code == 200
    assert resp_lower.data == b"dummy mp4 video bytes for range test"

    # Genuinely missing file returns 404
    resp_404 = client.get('/static/uploads/nonexistent/file.txt')
    assert resp_404.status_code == 404

    # Encoded path separators must not escape the configured uploads directory.
    resp_traversal = client.get('/uploads/%2E%2E/test_fixes.sqlite')
    assert resp_traversal.status_code == 404


def test_database_connection_error_formatting():
    """Test actionable connection guidance does not expose database credentials."""
    inner = psycopg.OperationalError("[Errno 11001] getaddrinfo failed")
    err = DatabaseConnectionError(
        "Could not connect to PostgreSQL database",
        original_error=inner,
        host="ep-bold-glitter-a1234567.ap-southeast-1.aws.neon.tech",
        port=5432,
        database="neondb",
        user="neondb_owner"
    )
    msg = str(err)
    assert "Errno 11001" in msg
    assert "DNS name resolution failed" in msg
    assert "DEV_DATABASE_URL" in msg
    assert "USE_SQLITE=true" in msg
    assert "ep-bold-glitter" not in msg
    assert "neondb_owner" not in msg
    assert "very-secret" not in msg


def test_announcements_empty_table_resilience(app_with_test_data):
    """Test that home page loads cleanly even when announcements table is empty."""
    from models.message import Announcement
    announcements = Announcement.get_all_active(limit=5)
    assert announcements == []


def test_homepage_survives_optional_announcement_database_failure(
    app_with_test_data, monkeypatch
):
    from models.message import Announcement
    from models.subject import Subject

    def fail_to_load(*args, **kwargs):
        raise psycopg.OperationalError('temporary database outage')

    def database_is_already_unavailable(*args, **kwargs):
        pytest.fail('Homepage retried the unavailable database')

    monkeypatch.setattr(Announcement, 'get_all_active', fail_to_load)
    monkeypatch.setattr(Subject, 'get_all', database_is_already_unavailable)
    response = app_with_test_data.test_client().get('/')

    assert response.status_code == 200
    assert 'StudyHub - College Study Materials, Notes &amp; Resources' in response.get_data(
        as_text=True
    )


def test_csrf_failure_does_not_redirect_to_external_host(app_with_test_data):
    response = app_with_test_data.test_client().post(
        '/api/cookie-consent',
        data={'choice': 'accept', 'next': 'https://attacker.example/'},
    )

    assert response.status_code == 302
    assert response.headers['Location'] == '/'


def test_admin_user_status_changes_reject_get_requests(app_with_test_data):
    client = app_with_test_data.test_client()

    assert client.get('/admin/users/activate/1').status_code == 405
    assert client.get('/admin/users/deactivate/1').status_code == 405


def test_forbidden_error_uses_friendly_error_page(app_with_test_data):
    from werkzeug.exceptions import Forbidden

    with app_with_test_data.test_request_context('/forbidden'):
        response = app_with_test_data.handle_http_exception(Forbidden())

    body, status = response
    assert status == 403
    body.direct_passthrough = False
    assert b'Access Denied' in body.get_data()


def test_render_app_requires_explicit_secret_key(app_with_test_data, monkeypatch):
    from app import create_app

    monkeypatch.setattr(Config, 'IS_RENDER', True)
    monkeypatch.setattr(Config, 'SECRET_KEY', None)

    with pytest.raises(RuntimeError, match='SECRET_KEY must be configured'):
        create_app()


def test_app_startup_rejects_invalid_database_configuration(
    app_with_test_data, monkeypatch
):
    from app import create_app

    monkeypatch.setattr(Config, 'DATABASE_CONFIG_ERROR', 'PostgreSQL URL is malformed.')

    with pytest.raises(RuntimeError, match='Database configuration error'):
        create_app()
