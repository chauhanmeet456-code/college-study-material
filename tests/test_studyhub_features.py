import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from html.parser import HTMLParser

import pytest

from config import Config
from models.notification import Notification
from models.oauth_account import OAuthAccount
from models.session import hash_session_token
from utils.database import get_connection, get_cursor, close_cursor
from utils.realtime import socketio


@pytest.fixture(scope='module')
def feature_app(tmp_path_factory):
    from app import create_app

    original = {
        'USE_SQLITE': Config.USE_SQLITE,
        'USE_POSTGRES': Config.USE_POSTGRES,
        'SQLITE_DB': Config.SQLITE_DB,
        'SECRET_KEY': Config.SECRET_KEY,
        'SESSION_COOKIE_SECURE': Config.SESSION_COOKIE_SECURE,
    }
    Config.USE_SQLITE = True
    Config.USE_POSTGRES = False
    Config.SQLITE_DB = str(tmp_path_factory.mktemp('studyhub') / 'features.sqlite')
    Config.SECRET_KEY = 'test-secret-key'
    Config.SESSION_COOKIE_SECURE = False

    conn = sqlite3.connect(Config.SQLITE_DB)
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
        CREATE TABLE login_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            event_type TEXT, device_description TEXT, ip_address TEXT,
            login_method TEXT, result TEXT, failure_reason TEXT,
            related_session_id INTEGER, created_at DATETIME
        );
        CREATE TABLE guest_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, material_id INTEGER
        );
        CREATE TABLE guest_downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, material_id INTEGER
        );
        CREATE TABLE announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            description TEXT, is_active INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL
        );
        CREATE TABLE subjects (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            description TEXT, subject_id INTEGER, category_id INTEGER,
            file_name TEXT, file_path TEXT, file_type TEXT, file_size INTEGER,
            uploaded_by INTEGER, is_featured INTEGER DEFAULT 0,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE settings (
            id INTEGER PRIMARY KEY, key TEXT UNIQUE, value TEXT,
            updated_at DATETIME
        );
        INSERT INTO users (id, name, email, password_hash, is_active, deleted_by_user)
            VALUES (1, 'One', 'one@example.test', 'x', 1, 0),
                   (2, 'Two', 'two@example.test', 'x', 1, 0),
                   (3, 'Disabled', 'disabled@example.test', 'x', 0, 0);
        INSERT INTO categories (id, name) VALUES
            (1, 'Notes'), (2, 'Assignment'), (3, 'Book'), (4, 'Practical');
        INSERT INTO subjects (id, name, code, is_active)
            VALUES (1, 'Mathematics', 'MATH', 1);
        INSERT INTO materials
            (id, title, description, subject_id, category_id, file_name, is_active)
            VALUES (1, '100% Algebra Notes', 'Review', 1, 1, 'algebra.pdf', 1);
    """)
    conn.commit()
    conn.close()

    app = create_app()
    app.config.update(TESTING=True)
    yield app

    for key, value in original.items():
        setattr(Config, key, value)


def _session(client, user_id=1, csrf='feature-csrf'):
    with client.session_transaction() as current:
        current['user_id'] = user_id
        current['_csrf_token'] = csrf


def test_suggestions_return_typed_links_and_reject_invalid_queries(feature_app):
    client = feature_app.test_client()
    response = client.get('/api/search/suggestions?q=100%25')
    assert response.status_code == 200
    suggestions = response.get_json()['suggestions']
    assert any(
        item['title'] == '100% Algebra Notes'
        and item['type'] == 'Notes'
        and item['url'] == '/material/1'
        for item in suggestions
    )
    assert {'title': 'Mathematics', 'type': 'Subject', 'url': '/subject/1'} not in suggestions
    assert client.get('/api/search/suggestions?q=%00').status_code == 400
    assert client.get('/api/search/suggestions?q=' + ('a' * 101)).status_code == 400


def test_notification_persistence_deduplication_and_private_reads(feature_app):
    client = feature_app.test_client()
    _session(client)
    created = Notification.create_for_users(
        'New note', 'Algebra notes are available.', '/material/1',
        dedupe_key='material:1', audience='active',
    )
    assert created == 2
    assert Notification.create_for_users(
        'New note', 'Algebra notes are available.', '/material/1',
        dedupe_key='material:1', audience='active',
    ) == 0

    response = client.get('/api/notifications')
    assert response.status_code == 200
    assert response.get_json()['unread_count'] == 1
    notification_id = response.get_json()['notifications'][0]['id']
    marked = client.post(
        '/api/notifications/{}/read'.format(notification_id),
        json={}, headers={'X-CSRF-Token': 'feature-csrf'},
    )
    assert marked.status_code == 200
    assert marked.get_json()['unread_count'] == 0

    _session(client, user_id=2)
    denied = client.post(
        '/api/notifications/{}/read'.format(notification_id),
        json={}, headers={'X-CSRF-Token': 'feature-csrf'},
    )
    assert denied.status_code == 404


def test_admin_announcement_publish_and_republish_are_idempotent(feature_app):
    client = feature_app.test_client()
    with client.session_transaction() as current:
        current['admin_id'] = 1
        current['_csrf_token'] = 'admin-csrf'
    response = client.post(
        '/admin/announcements/add',
        data={
            'csrf_token': 'admin-csrf',
            'title': 'Exam timetable',
            'description': 'The timetable is ready.',
            'link': '/subjects',
            'audience': 'active',
            'is_active': '1',
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute("SELECT id, published_at FROM announcements WHERE title = ?", ('Exam timetable',))
    announcement = cursor.fetchone()
    close_cursor(cursor)
    conn.close()
    assert announcement['published_at']

    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute(
        "SELECT COUNT(*) AS total FROM user_notifications WHERE dedupe_key = ?",
        ('announcement:{}'.format(announcement['id']),),
    )
    assert cursor.fetchone()['total'] == 2
    close_cursor(cursor)
    conn.close()

    client.post(
        '/admin/announcements/edit/{}'.format(announcement['id']),
        data={
            'csrf_token': 'admin-csrf',
            'title': 'Exam timetable updated',
            'description': 'The timetable is ready.',
            'link': '/subjects',
            'audience': 'active',
            'is_active': '1',
        },
    )
    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute(
        "SELECT COUNT(*) AS total FROM user_notifications WHERE dedupe_key = ?",
        ('announcement:{}'.format(announcement['id']),),
    )
    assert cursor.fetchone()['total'] == 2
    close_cursor(cursor)
    conn.close()


def test_material_publication_notifies_once_when_first_published(feature_app):
    from models.material import Material

    def count_for_material(material_id):
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute(
            "SELECT COUNT(*) AS total FROM user_notifications WHERE dedupe_key = ?",
            ('material:{}'.format(material_id),),
        )
        total = cursor.fetchone()['total']
        close_cursor(cursor)
        conn.close()
        return total

    draft_id = Material.create(
        'Draft practical', 'Draft', 1, 4, 'draft.pdf', 'static/uploads',
        'pdf', 10, is_active=0,
    )
    assert count_for_material(draft_id) == 0
    Material.update(draft_id, is_active=1)
    assert count_for_material(draft_id) == 2
    Material.update(draft_id, is_active=1, title='Updated practical')
    assert count_for_material(draft_id) == 2


def test_cookie_consent_is_signed_http_only_and_persisted(feature_app, monkeypatch):
    client = feature_app.test_client()
    monkeypatch.setitem(feature_app.config, 'SESSION_COOKIE_SECURE', True)
    page = client.get('/cookie-policy', base_url='https://localhost')
    assert page.status_code == 200
    assert b'Necessary cookies' in page.data
    assert b'cookieConsentBanner' in page.data
    assert b'data-preferences-enabled="false"' in page.data
    with client.session_transaction() as current:
        csrf = current['_csrf_token']

    response = client.post(
        '/api/cookie-consent',
        json={'choice': 'settings', 'preferences': True},
        headers={'X-CSRF-Token': csrf},
        base_url='https://localhost',
    )
    assert response.status_code == 200
    cookie_header = '\n'.join(response.headers.getlist('Set-Cookie'))
    assert 'studyhub_cookie_consent=' in cookie_header
    assert 'HttpOnly' in cookie_header
    assert 'SameSite=Lax' in cookie_header
    assert 'Secure' in cookie_header
    page = client.get('/cookie-policy', base_url='https://localhost')
    assert b'cookieConsentBanner' not in page.data
    assert b'Optional preferences' in page.data
    assert b'data-preferences-enabled="true"' in page.data

    revoked = client.post(
        '/api/cookie-consent',
        json={'choice': 'settings', 'preferences': False},
        headers={'X-CSRF-Token': csrf},
        base_url='https://localhost',
    )
    assert revoked.status_code == 200
    assert b'data-preferences-enabled="false"' in client.get(
        '/cookie-policy', base_url='https://localhost',
    ).data

    invalid = client.post(
        '/api/cookie-consent',
        json={'choice': 'settings', 'preferences': 'enabled'},
        headers={'X-CSRF-Token': csrf},
        base_url='https://localhost',
    )
    assert invalid.status_code == 400


def test_socket_connections_require_live_user_session(feature_app):
    from utils.realtime import socketio

    client = feature_app.test_client()
    _session(client)
    unauthenticated = socketio.test_client(feature_app, flask_test_client=client)
    assert not unauthenticated.is_connected()

    token = 'live-session-token'
    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute(
        "INSERT INTO user_sessions "
        "(id, user_id, session_token_hash, status, last_activity_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            1, 1, hash_session_token(token), 'active', datetime.now(),
            datetime.now() + timedelta(hours=1),
        ),
    )
    conn.commit()
    close_cursor(cursor)
    conn.close()
    with client.session_transaction() as current:
        current['session_token'] = token

    connected = socketio.test_client(feature_app, flask_test_client=client)
    assert connected.is_connected()
    Notification.create_for_users(
        'Realtime', 'Socket delivery works.', '/material/1',
        dedupe_key='realtime:test', audience='active',
    )
    events = connected.get_received()
    assert any(event['name'] == 'notification:new' for event in events)
    connected.disconnect()


def test_admin_favicon_map_uses_distinct_static_assets(feature_app):
    from flask import render_template, request

    categories = [
        {'id': 1, 'name': 'Notes'},
        {'id': 2, 'name': 'Assignment'},
        {'id': 3, 'name': 'Book'},
        {'id': 4, 'name': 'Practical'},
    ]
    cases = [
        ('/admin/dashboard', 'admin.dashboard', None, 'favicon-admin-dashboard.svg'),
        ('/admin/users', 'admin.users_list', None, 'favicon-admin-users.svg'),
        ('/admin/subjects', 'admin.subjects_list', None, 'favicon-admin-subjects.svg'),
        ('/admin/materials?category_id=1', 'admin.materials_list', 1, 'favicon-admin-notes.svg'),
        ('/admin/materials?category_id=2', 'admin.materials_list', 2, 'favicon-admin-assignments.svg'),
        ('/admin/materials?category_id=3', 'admin.materials_list', 3, 'favicon-admin-books.svg'),
        ('/admin/materials?category_id=4', 'admin.materials_list', 4, 'favicon-admin-practicals.svg'),
    ]
    for path, endpoint, category_id, filename in cases:
        with feature_app.test_request_context(path):
            request.url_rule = SimpleNamespace(endpoint=endpoint)
            html = render_template(
                'admin/base.html',
                admin={'name': 'Admin'},
                all_categories=categories,
                category_id=category_id,
            )
        assert filename in html


def test_notification_poll_fallback_is_bounded(feature_app):
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / 'static' / 'js' / 'studyhub.js'
    source = script.read_text(encoding='utf-8')
    assert 'setInterval(refreshNotifications, 60000)' in source
    assert "socket.on('disconnect', startPolling)" in source
    assert 'function stopPolling()' in source


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = []

    def handle_starttag(self, tag, attrs):
        if tag == 'meta':
            self.meta.append(dict(attrs))


def _metadata(response):
    parser = _MetaParser()
    parser.feed(response.get_data(as_text=True))
    return parser.meta


def test_homepage_autocomplete_is_only_on_hero_and_uses_active_suggestions(feature_app):
    feature_app.config['PUBLIC_BASE_URL'] = 'https://studyhub.example'
    client = feature_app.test_client()
    page = client.get('/')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'id="heroSearchInput"' in html
    assert 'id="heroSearchSuggestions"' in html
    assert 'id="navbarSearchInput"' not in html
    assert 'id="darkModeToggle"' in html
    assert '>Contact</a>' in html
    assert 'data-bs-target="#navbarNav"' in html
    assert 'aria-controls="heroSearchSuggestions"' in html
    assert 'aria-controls="globalSearchSuggestions"' not in html

    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute(
        "INSERT INTO materials (title, description, subject_id, category_id, is_active) "
        "VALUES (?, ?, ?, ?, ?)",
        ('Inactive algebra notes', 'Hidden', 1, 1, 0),
    )
    close_cursor(cursor)
    conn.close()
    suggestions = client.get('/api/search/suggestions?q=algebra').get_json()['suggestions']
    assert any(item['title'] == '100% Algebra Notes' for item in suggestions)
    assert all(item['title'] != 'Inactive algebra notes' for item in suggestions)
    assert next(item for item in suggestions if item['title'] == '100% Algebra Notes')[
        'subtitle'
    ] == 'Mathematics'


def test_public_pages_have_absolute_page_specific_social_metadata(feature_app):
    feature_app.config['PUBLIC_BASE_URL'] = 'https://studyhub.example'
    client = feature_app.test_client()
    homepage_response = client.get('/')
    homepage_html = homepage_response.get_data(as_text=True)
    homepage_meta = _metadata(homepage_response)
    assert '<link rel="canonical" href="https://studyhub.example/">' in homepage_html
    assert (
        '<meta property="og:url" content="https://studyhub.example/">' 
        in homepage_html
    )
    assert (
        '<meta property="og:image" '
        'content="https://studyhub.example/static/images/og-studyhub.png">'
        in homepage_html
    )
    assert (
        '<meta name="twitter:card" content="summary_large_image">'
        in homepage_html
    )
    assert (
        '<meta name="twitter:image" '
        'content="https://studyhub.example/static/images/og-studyhub.png">'
        in homepage_html
    )
    og_meta = [
        item for item in homepage_meta
        if item.get('property', '').startswith('og:')
    ]
    assert len([item for item in og_meta if item.get('property') == 'og:title']) == 1
    assert next(item['content'] for item in og_meta if item.get('property') == 'og:title') == (
        'StudyHub - 3rd Year Study Materials'
    )
    assert next(item['content'] for item in og_meta if item.get('property') == 'og:url') == (
        'https://studyhub.example/'
    )
    assert next(item['content'] for item in og_meta if item.get('property') == 'og:image') == (
        'https://studyhub.example/static/images/og-studyhub.png'
    )
    assert next(
        item['content'] for item in homepage_meta
        if item.get('name') == 'twitter:card'
    ) == 'summary_large_image'

    material_meta = _metadata(client.get('/material/1'))
    assert next(
        item['content'] for item in material_meta
        if item.get('property') == 'og:title'
    ) == '100% Algebra Notes - StudyHub'
    assert next(
        item['content'] for item in material_meta
        if item.get('property') == 'og:url'
    ) == 'https://studyhub.example/material/1'

    subject_meta = _metadata(client.get('/subject/1'))
    assert next(
        item['content'] for item in subject_meta
        if item.get('property') == 'og:title'
    ) == 'Mathematics - StudyHub'
    notes_meta = _metadata(client.get('/notes'))
    assert next(
        item['content'] for item in notes_meta
        if item.get('property') == 'og:url'
    ) == 'https://studyhub.example/notes'


def test_social_metadata_never_uses_local_or_private_urls(feature_app):
    feature_app.config['PUBLIC_BASE_URL'] = 'https://localhost:5000'
    response = feature_app.test_client().get('/')
    html = response.get_data(as_text=True)
    assert 'localhost' not in html
    assert 'og:url' not in html
    assert 'og:image' not in html


def test_local_loopback_origin_renders_metadata_only_for_local_requests(
    feature_app, monkeypatch,
):
    feature_app.config['PUBLIC_BASE_URL'] = 'http://127.0.0.1:5000'
    monkeypatch.setitem(feature_app.config, 'TESTING', False)
    monkeypatch.setitem(feature_app.config, 'DEBUG', False)
    client = feature_app.test_client()

    production_html = client.get('/').get_data(as_text=True)
    assert '<link rel="canonical"' not in production_html
    assert 'property="og:image"' not in production_html
    assert 'name="twitter:image"' not in production_html

    monkeypatch.setitem(feature_app.config, 'DEBUG', True)
    local_html = client.get('/').get_data(as_text=True)
    assert '<link rel="canonical" href="http://127.0.0.1:5000/">' in local_html
    assert (
        '<meta property="og:image" '
        'content="http://127.0.0.1:5000/static/images/og-studyhub.png">'
        in local_html
    )
    assert (
        '<meta name="twitter:card" content="summary_large_image">'
        in local_html
    )

    monkeypatch.setitem(feature_app.config, 'DEBUG', False)
    external_html = client.get(
        '/',
        environ_base={'REMOTE_ADDR': '203.0.113.10'},
        headers={'Host': 'studyhub.example'},
    ).get_data(as_text=True)
    assert '<link rel="canonical"' not in external_html
    assert 'property="og:image"' not in external_html
    assert 'name="twitter:image"' not in external_html


def test_social_metadata_is_rendered_once_on_public_and_auth_pages(feature_app):
    feature_app.config['PUBLIC_BASE_URL'] = 'https://studyhub.example'
    client = feature_app.test_client()
    routes = (
        ('/', '/'),
        ('/subjects', '/subjects'),
        ('/subject/1', '/subject/1'),
        ('/material/1', '/material/1'),
        ('/notes', '/notes'),
        ('/assignments', '/assignments'),
        ('/books', '/books'),
        ('/practicals', '/practicals'),
        ('/question-papers', '/question-papers'),
        ('/important-questions', '/important-questions'),
        ('/syllabus', '/syllabus'),
        ('/search?q=algebra', '/search'),
        ('/about', '/about'),
        ('/cookie-policy', '/cookie-policy'),
        ('/contact', '/contact'),
        ('/login', '/login'),
        ('/register', '/register'),
    )
    required_properties = (
        'og:title',
        'og:description',
        'og:image',
        'og:url',
        'og:type',
        'og:site_name',
    )

    for path, canonical_path in routes:
        response = client.get(path)
        assert response.status_code == 200, path
        html = response.get_data(as_text=True)
        metadata = _metadata(response)
        for property_name in required_properties:
            tags = [
                item for item in metadata
                if item.get('property') == property_name
            ]
            assert len(tags) == 1, (path, property_name)
        for name in (
            'twitter:card',
            'twitter:title',
            'twitter:description',
            'twitter:image',
        ):
            tags = [item for item in metadata if item.get('name') == name]
            assert len(tags) == 1, (path, name)
        assert (
            f'<link rel="canonical" href="https://studyhub.example{canonical_path}">'
            in html
        )
        assert (
            '<meta property="og:image" '
            'content="https://studyhub.example/static/images/og-studyhub.png">'
            in html
        )
        assert (
            '<meta name="twitter:card" content="summary_large_image">'
            in html
        )
        assert (
            '<meta name="twitter:image" '
            'content="https://studyhub.example/static/images/og-studyhub.png">'
            in html
        )

    login_meta = _metadata(client.get('/login'))
    register_meta = _metadata(client.get('/register'))
    assert next(item['content'] for item in login_meta if item.get('property') == 'og:title') == (
        'Student Login - StudyHub'
    )
    assert next(
        item['content'] for item in register_meta
        if item.get('property') == 'og:title'
    ) == 'Student Registration - StudyHub'

    image = client.get('/static/images/og-studyhub.png')
    assert image.status_code == 200
    assert image.mimetype == 'image/png'
    assert len(image.data) < 5 * 1024 * 1024
    assert image.data[:8] == b'\x89PNG\r\n\x1a\n'
    assert int.from_bytes(image.data[16:20], 'big') == 1200
    assert int.from_bytes(image.data[20:24], 'big') == 630


def test_social_metadata_escapes_content_and_hides_authenticated_page_data(feature_app):
    feature_app.config['PUBLIC_BASE_URL'] = 'https://studyhub.example'
    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute(
        "UPDATE materials SET title = ?, description = ? WHERE id = ?",
        ('<script>alert(1)</script>', '<img src=x onerror=alert(1)>Safe description', 1),
    )
    conn.commit()
    close_cursor(cursor)
    conn.close()

    client = feature_app.test_client()
    material_response = client.get('/material/1')
    material_html = material_response.get_data(as_text=True)
    assert '<script>alert(1)</script>' not in material_html
    assert '&lt;script&gt;alert(1)&lt;/script&gt; - StudyHub' in material_html
    material_meta = _metadata(material_response)
    description = next(
        item['content'] for item in material_meta
        if item.get('property') == 'og:description'
    )
    assert 'onerror' not in description
    assert 'Safe description' in description

    _session(client, user_id=1)
    private_html = client.get('/notifications').get_data(as_text=True)
    assert 'noindex, nofollow' in private_html
    assert 'one@example.test' not in private_html
    assert '<meta property="og:title" content="StudyHub">' in private_html


def test_google_oauth_buttons_are_available_on_login_and_register(
    feature_app, monkeypatch,
):
    feature_app.config.update(
        GOOGLE_CLIENT_ID='test-client-id',
        GOOGLE_CLIENT_SECRET='test-client-secret',
        PUBLIC_BASE_URL='http://127.0.0.1:5000',
    )
    monkeypatch.setitem(feature_app.config, 'TESTING', False)
    monkeypatch.setitem(feature_app.config, 'DEBUG', False)
    from utils.google_oauth import init_google_oauth
    init_google_oauth(feature_app)

    client = feature_app.test_client()
    for path in ('/login', '/register'):
        html = client.get(path).get_data(as_text=True)
        assert 'Continue with Google' in html
        assert '<span>OR</span>' in html
        assert 'href="/google"' in html
        assert 'google-signin-logo' in html
    with feature_app.test_request_context():
        from utils.google_oauth import google_callback_uri
        assert google_callback_uri() == 'http://127.0.0.1:5000/google/callback'


def test_google_oauth_buttons_are_hidden_without_configured_client(feature_app):
    feature_app.config.update(
        GOOGLE_CLIENT_ID='',
        GOOGLE_CLIENT_SECRET='',
    )
    from utils.google_oauth import init_google_oauth
    init_google_oauth(feature_app)

    client = feature_app.test_client()
    for path in ('/login', '/register'):
        html = client.get(path).get_data(as_text=True)
        assert 'Continue with Google' not in html
        assert 'google-signin-logo' not in html


def test_google_oauth_button_requires_https_for_non_loopback_origins(
    feature_app, monkeypatch,
):
    feature_app.config.update(
        GOOGLE_CLIENT_ID='test-client-id',
        GOOGLE_CLIENT_SECRET='test-client-secret',
        PUBLIC_BASE_URL='http://studyhub.example',
    )
    monkeypatch.setitem(feature_app.config, 'TESTING', False)
    monkeypatch.setitem(feature_app.config, 'DEBUG', False)
    from utils.google_oauth import init_google_oauth
    init_google_oauth(feature_app)

    client = feature_app.test_client()
    for path in ('/login', '/register'):
        html = client.get(path).get_data(as_text=True)
        assert 'Continue with Google' not in html
        assert 'google-signin-logo' not in html


def test_google_callback_requires_verified_email_and_registers_user(feature_app):
    class FakeGoogle:
        profile = {
            'sub': 'google-sub-200',
            'email': 'new.google@example.test',
            'email_verified': True,
            'name': 'New Google User',
        }

        def authorize_access_token(self):
            return {'userinfo': self.profile}

    feature_app.extensions['google_oauth_client'] = FakeGoogle()
    client = feature_app.test_client()
    response = client.get('/google/callback')
    assert response.status_code == 302
    with client.session_transaction() as current:
        created_user_id = current['user_id']
        assert current.get('session_token')

    user = OAuthAccount.get_user('google', 'google-sub-200')
    assert user['id'] == created_user_id
    assert user['email'] == 'new.google@example.test'

    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute(
        "SELECT login_method FROM login_history WHERE user_id = ? "
        "AND event_type = 'login_success'",
        (created_user_id,),
    )
    assert cursor.fetchone()['login_method'] == 'google'
    close_cursor(cursor)
    conn.close()


def test_google_oauth_links_existing_accounts_only_with_verified_email(feature_app):
    class FakeGoogle:
        profile = {
            'sub': 'google-sub-existing',
            'email': 'one@example.test',
            'email_verified': False,
            'name': 'One',
        }

        def authorize_access_token(self):
            return {'userinfo': self.profile}

    feature_app.extensions['google_oauth_client'] = FakeGoogle()
    client = feature_app.test_client()
    response = client.get('/google/callback')
    assert response.status_code == 302
    assert OAuthAccount.get_user('google', 'google-sub-existing') is None

    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (1,))
    conn.commit()
    close_cursor(cursor)
    conn.close()
    FakeGoogle.profile['email_verified'] = True
    response = client.get('/google/callback')
    assert response.status_code == 302
    linked = OAuthAccount.get_user('google', 'google-sub-existing')
    assert linked['id'] == 1


def test_google_oauth_uses_configured_https_callback_origin(feature_app):
    feature_app.config['PUBLIC_BASE_URL'] = 'https://studyhub.example'

    class FakeGoogle:
        callback_uri = None

        def authorize_redirect(self, redirect_uri):
            self.callback_uri = redirect_uri
            from flask import redirect
            return redirect('https://accounts.google.com/auth?state=oauth-state')

    google = FakeGoogle()
    feature_app.extensions['google_oauth_client'] = google
    response = feature_app.test_client().get('/google')
    assert response.status_code == 302
    assert google.callback_uri == 'https://studyhub.example/google/callback'
    assert 'state=oauth-state' in response.headers['Location']


def test_google_oauth_requires_public_origin_outside_local_development(
    feature_app, monkeypatch,
):
    class FakeGoogle:
        def authorize_redirect(self, redirect_uri):
            raise AssertionError('OAuth must not start with an untrusted callback URL')

    monkeypatch.setitem(feature_app.config, 'TESTING', False)
    monkeypatch.setitem(feature_app.config, 'DEBUG', False)
    monkeypatch.setitem(feature_app.config, 'PUBLIC_BASE_URL', '')
    feature_app.extensions['google_oauth_client'] = FakeGoogle()
    response = feature_app.test_client().get('/google')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/login')


def test_authlib_rejects_mismatched_google_oauth_state(feature_app):
    from authlib.integrations.flask_client import OAuth
    from urllib.parse import parse_qs, urlparse

    feature_app.config['PUBLIC_BASE_URL'] = 'https://studyhub.example'
    oauth = OAuth()
    oauth.init_app(feature_app)
    oauth.register(
        name='google',
        client_id='state-test-client',
        client_secret='state-test-secret',
        authorize_url='https://accounts.google.com/o/oauth2/v2/auth',
        access_token_url='https://oauth2.googleapis.com/token',
        client_kwargs={'scope': 'openid email profile'},
    )
    feature_app.extensions['google_oauth_client'] = oauth.create_client('google')

    client = feature_app.test_client()
    authorization = client.get('/google')
    params = parse_qs(urlparse(authorization.headers['Location']).query)
    state = params['state'][0]
    with client.session_transaction() as current:
        assert current.get('_state_google_' + state)

    callback = client.get('/google/callback?code=not-used&state=incorrect-state')
    assert callback.status_code == 302
    assert OAuthAccount.get_user_by_email('state-test@example.test') is None
