import logging
import mimetypes
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    send_from_directory,
    url_for,
)
from models.subject import Subject
from models.material import Material
from models.message import Announcement
from utils.auth import get_current_user
from utils.guest_activity import GuestActivity
from utils.database import (
    close_cursor,
    get_connection,
    get_cursor,
    is_database_connection_error,
    is_database_unavailable_error,
)
from utils.security import csrf_protect
from config import Config
from utils.cookie_consent import (
    COOKIE_NAME,
    MAX_AGE as COOKIE_CONSENT_MAX_AGE,
    serialize_cookie_preferences,
)
from utils.helpers import positive_query_int
from utils.storage import B2_PATH_PREFIX, B2StorageError, b2_key, get_storage
from utils.seo import absolute_public_url

logger = logging.getLogger('studyhub.main')
main_bp = Blueprint('main', __name__)


def _find_file_case_insensitive(base_dir, path_parts):
    """Traverse path_parts case-insensitively from base_dir to match file on disk."""
    if not os.path.isdir(base_dir):
        return None
    current = base_dir
    for part in path_parts:
        if not part:
            continue
        part_clean = urllib.parse.unquote(part).strip()
        part_lower = part_clean.lower()
        try:
            entries = os.listdir(current)
        except (OSError, FileNotFoundError):
            return None
        found = None
        for entry in entries:
            if entry == part or entry == part_clean or entry.lower() == part_lower:
                found = entry
                break
        if found is None:
            return None
        current = os.path.join(current, found)
    return current if os.path.exists(current) else None


def _is_path_within(base_dir, path):
    try:
        return os.path.commonpath(
            (os.path.realpath(base_dir), os.path.realpath(path))
        ) == os.path.realpath(base_dir)
    except (OSError, ValueError):
        return False


def _find_unique_upload_by_name(roots, file_name):
    """Find an exact filename only when it has one match in approved roots."""
    matches = set()
    wanted_name = file_name.casefold()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for directory, _, filenames in os.walk(root):
            if not any(name.casefold() == wanted_name for name in filenames):
                continue
            for name in filenames:
                if name.casefold() != wanted_name:
                    continue
                candidate = os.path.join(directory, name)
                if os.path.isfile(candidate) and _is_path_within(root, candidate):
                    matches.add(os.path.realpath(candidate))
                    if len(matches) > 1:
                        return None
    if len(matches) != 1:
        return None
    matched = matches.pop()
    for root in roots:
        if _is_path_within(root, matched):
            rel_name = os.path.relpath(matched, root).replace('\\', '/')
            return matched, rel_name
    return None


def resolve_material_disk_path(material):
    """Resolve stored upload references under approved roots only."""
    if not material:
        return None, None
    raw_path = str(material.get('file_path') or '').strip()
    file_name = str(material.get('file_name') or '').replace('\\', '/').strip()
    if not file_name:
        return None, None
    file_parts = [
        urllib.parse.unquote(part).strip()
        for part in file_name.split('/')
        if part.strip()
    ]
    if len(file_parts) != 1:
        return None, None
    file_name = file_parts[0]
    if file_name in ('.', '..') or '/' in file_name or '\\' in file_name:
        return None, None

    candidate_roots = []
    if Config.UPLOAD_FOLDER:
        candidate_roots.append(os.path.abspath(Config.UPLOAD_FOLDER))
    static_root = current_app.static_folder if current_app else os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
    if static_root:
        candidate_roots.append(os.path.abspath(os.path.join(static_root, 'uploads')))
    proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate_roots.append(os.path.abspath(os.path.join(proj_dir, 'static', 'uploads')))

    seen = set()
    roots = []
    for r in candidate_roots:
        if r and r not in seen:
            seen.add(r)
            roots.append(r)

    raw_parts = [
        urllib.parse.unquote(part).strip()
        for part in raw_path.replace('\\', '/').split('/')
        if part.strip()
    ]
    if any(part in ('.', '..') or '/' in part or '\\' in part for part in raw_parts):
        return None, None
    while raw_parts and raw_parts[0].lower() in ('static', 'uploads'):
        raw_parts.pop(0)

    for root in roots:
        if not os.path.isdir(root):
            continue

        root_parts = raw_parts
        if os.path.isabs(raw_path) and _is_path_within(root, raw_path):
            root_parts = os.path.relpath(raw_path, root).replace('\\', '/').split('/')
            if root_parts == ['.']:
                root_parts = []

        direct = os.path.realpath(os.path.join(root, *root_parts, file_name))
        if _is_path_within(root, direct) and os.path.isfile(direct):
            rel_name = os.path.relpath(direct, root).replace('\\', '/')
            return direct, rel_name

        matched = _find_file_case_insensitive(root, root_parts + [file_name])
        if matched and _is_path_within(root, matched) and os.path.isfile(matched):
            rel_name = os.path.relpath(matched, root).replace('\\', '/')
            return matched, rel_name

    # Older records may contain only a category hint (for example
    # "notes/<name>") even though uploads were stored under another category.
    # Resolve that legacy mismatch only when the exact filename is unique.
    unique_match = _find_unique_upload_by_name(roots, file_name)
    if unique_match:
        return unique_match

    fallback_rel = '/'.join(raw_parts + [file_name])
    return None, fallback_rel


def material_file_exists(material):
    file_path = material.get('file_path') if material else None
    if isinstance(file_path, str) and file_path.startswith(B2_PATH_PREFIX):
        try:
            return get_storage().object_exists(b2_key(file_path))
        except B2StorageError:
            logger.exception(
                'Could not check B2 file availability for material %s.',
                material.get('id'),
            )
            flash(
                'Cloud storage is temporarily unavailable. Please try again later.',
                'warning',
            )
            return False
    disk_path, _ = resolve_material_disk_path(material)
    return bool(disk_path and os.path.isfile(disk_path))


def _b2_file_redirect(material, inline):
    try:
        storage = get_storage()
        key = b2_key(material.get('file_path'))
        if not storage.object_exists(key):
            abort(404)
        signed_url = storage.get_presigned_url(
            key,
            download_name=material.get('file_name'),
            inline=inline,
        )
    except B2StorageError:
        logger.exception(
            'Could not serve B2 file for material %s.', material.get('id')
        )
        abort(503, description='Cloud file storage is temporarily unavailable.')
    return redirect(signed_url, code=302)


@main_bp.route('/api/favorites/<int:material_id>', methods=['POST'])
@csrf_protect
def api_toggle_favorite(material_id):
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'message': 'Please login first'}), 401
    
    from models.material import Favorite
    if Favorite.is_favorited(user['id'], material_id):
        Favorite.remove(user['id'], material_id)
        return jsonify({'success': True, 'added': False})
    else:
        Favorite.add(user['id'], material_id)
        return jsonify({'success': True, 'added': True})

@main_bp.route('/api/search/suggestions')
def api_search_suggestions():
    query = request.args.get('q', '').strip()
    if len(query) > 100 or re.search(r'[\x00-\x1f\x7f]', query):
        return jsonify({'suggestions': [], 'message': 'Invalid search query.'}), 400
    if len(query) < 2:
        return jsonify({'suggestions': []})

    suggestions = Material.search_suggestions(query, limit=8)
    return jsonify({'suggestions': suggestions})

@main_bp.route('/api/search')
def api_search():
    query = request.args.get('q', '').strip()
    subject_filter = request.args.get('subject', '')
    category_filter = request.args.get('category', '')
    file_type_filter = request.args.get('file_type', '')
    sort = request.args.get('sort', 'latest')
    page = positive_query_int('page')
    
    filters = {}
    if query:
        filters['search'] = query
    if subject_filter:
        filters['subject_id'] = positive_query_int('subject')
    if category_filter:
        filters['category_id'] = positive_query_int('category')
    if file_type_filter:
        filters['file_type'] = file_type_filter
    
    materials, total = Material.get_all(filters=filters, sort=sort, page=page, per_page=12)
    has_more = total > page * 12
    public_materials = [
        {
            key: material.get(key)
            for key in (
                'id', 'title', 'description', 'view_count', 'download_count',
                'subject_name', 'subject_code', 'category_name',
            )
        }
        for material in materials
    ]
    
    return jsonify({
        'materials': public_materials,
        'total': total,
        'has_more': has_more,
        'page': page
    })

@main_bp.route('/', methods=['GET', 'HEAD'])
def index():
    if request.method == 'HEAD':
        return current_app.response_class('', mimetype='text/html')

    homepage_timings = {}
    g.homepage_timings = homepage_timings

    def timed_home_query(name, callback, fallback):
        started = time.perf_counter()
        try:
            return optional_home_query(name, callback, fallback)
        finally:
            homepage_timings[name] = (
                time.perf_counter() - started
            ) * 1000

    database_connection_unavailable = False

    def optional_home_query(name, callback, fallback):
        nonlocal database_connection_unavailable
        if database_connection_unavailable:
            return fallback
        try:
            return callback()
        except Exception as exc:
            if not is_database_unavailable_error(exc):
                raise
            database_connection_unavailable = is_database_connection_error(exc)
            if database_connection_unavailable:
                g.database_unavailable = True
            logger.warning(
                "Homepage %s query unavailable (%s); using fallback data",
                name,
                type(exc).__name__,
            )
            return fallback

    announcements = timed_home_query(
        'announcements', lambda: Announcement.get_all_active(limit=1), []
    )
    subjects = timed_home_query(
        'subjects', lambda: Subject.get_homepage_list(limit=6), []
    )
    latest_materials = timed_home_query(
        'latest materials', lambda: Material.get_latest(limit=6), []
    )
    featured_materials = timed_home_query(
        'featured materials', lambda: Material.get_featured(limit=6), []
    )
    total_materials = 0
    total_notes = 0
    total_students = 0
    total_subjects = subjects[0].get('total_subjects', 0) if subjects else 0

    def get_homepage_counts():
        conn = None
        cursor = None
        try:
            conn = get_connection()
            cursor = get_cursor(conn)
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM materials WHERE is_active = 1) as total_materials,
                    (SELECT COUNT(*) FROM materials m JOIN categories c ON m.category_id = c.id WHERE c.name = 'Notes' AND m.is_active = 1) as total_notes,
                    (SELECT COUNT(*) FROM users WHERE is_active = 1) as total_students
            """)
            return cursor.fetchone() or {}
        finally:
            close_cursor(cursor)
            if conn:
                conn.close()

    counts = timed_home_query('counts', get_homepage_counts, {})
    total_materials = counts.get('total_materials') or 0
    total_notes = counts.get('total_notes') or 0
    total_students = counts.get('total_students') or 0
    user_lookup_started = time.perf_counter()
    current_user = get_current_user()
    homepage_timings['user_session_lookup'] = (
        time.perf_counter() - user_lookup_started
    ) * 1000
    context_started = time.perf_counter()
    template_context = {
        'subjects': subjects,
        'latest_materials': latest_materials,
        'featured_materials': featured_materials,
        'announcements': announcements,
        'total_subjects': total_subjects,
        'total_materials': total_materials,
        'total_notes': total_notes,
        'total_students': total_students,
        'current_user': current_user,
        'meta_title': 'StudyHub - College Study Materials, Notes & Resources',
        'meta_description': (
            'Access college study materials, notes, assignments, books, '
            'practicals and question papers in one place with StudyHub.'
        ),
    }
    homepage_timings['context_generation'] = (
        time.perf_counter() - context_started
    ) * 1000
    render_started = time.perf_counter()
    response = render_template('index.html', **template_context)
    homepage_timings['template_rendering'] = (
        time.perf_counter() - render_started
    ) * 1000
    homepage_timings['total_before_response'] = sum(homepage_timings.values())
    return response


@main_bp.route('/healthz')
def health_check():
    return current_app.response_class(
        '{"status":"ok"}\n',
        mimetype='application/json',
    )

@main_bp.route('/subjects')
def subjects():
    all_subjects = Subject.get_all_with_material_counts()
    return render_template(
        'subjects.html',
        subjects=all_subjects,
        current_user=get_current_user(),
        meta_title='Subjects - StudyHub',
        meta_description='Browse StudyHub subjects and find relevant course materials.',
    )

@main_bp.route('/subject/<int:subject_id>')
def subject_details(subject_id):
    subject = Subject.get_by_id(subject_id)
    if not subject:
        abort(404)
    from models.subject import Category
    categories = Category.get_all()
    page = positive_query_int('page')
    category_filter = request.args.get('category', None)
    cat_id = None
    if category_filter:
        cat = next((c for c in categories if c['name'] == category_filter), None)
        if cat:
            cat_id = cat['id']
    materials, total = Material.get_by_subject(subject_id, category_id=cat_id, page=page, per_page=12)
    return render_template('subject_details.html', subject=subject, categories=categories,
                           materials=materials, total=total, page=page, current_category=category_filter,
                           current_user=get_current_user(),
                           meta_title='{} - StudyHub'.format(subject['name']),
                           meta_description=(
                               subject.get('description')
                               or 'Browse notes, assignments, books, and other materials for {}.'.format(
                                   subject['name']
                               )
                           ))

@main_bp.route('/material/<int:material_id>')
def material_details(material_id):
    material = Material.get_by_id(material_id)
    if not material or not material.get('is_active', 1):
        abort(404)
    current_user = get_current_user()
    guest_view_count = GuestActivity.get_view_count()
    if (
        guest_view_count is not None
        and guest_view_count >= Config.GUEST_VIEW_LIMIT
    ):
        flash('You have reached the free preview limit. Please register or login to continue viewing materials.', 'warning')
        return redirect(url_for('auth.login'))
    material['view_count'] = material.get('view_count', 0)
    from models.subject import Subject as Subj
    subject = Subj.get_by_id(material['subject_id'])
    remaining_views = None
    remaining_downloads = None
    is_favorited = False
    if guest_view_count is not None:
        remaining_views = max(0, Config.GUEST_VIEW_LIMIT - guest_view_count)
        remaining_downloads = max(0, Config.GUEST_DOWNLOAD_LIMIT - GuestActivity.get_download_count())
    else:
        from models.material import Favorite
        is_favorited = Favorite.is_favorited(current_user['id'], material_id)
    material['is_favorited'] = is_favorited
    from utils.helpers import is_video_file
    is_video = is_video_file(material.get('file_name', ''))
    file_exists = material_file_exists(material)
    return render_template('material_details.html', material=material, subject=subject, current_user=current_user,
                           remaining_views=remaining_views, remaining_downloads=remaining_downloads,
                           is_video=is_video, file_exists=file_exists,
                           meta_title='{} - StudyHub'.format(material['title']),
                           meta_description=(
                               material.get('description')
                               or 'View this study material on StudyHub.'
                           ),
                           meta_type='article')

@main_bp.route('/material/<int:material_id>/view')
def view_material(material_id):
    material = Material.get_by_id(material_id)
    if not material or not material.get('is_active', 1):
        abort(404)
    current_user = get_current_user()
    guest_view_count = GuestActivity.get_view_count()
    if (
        guest_view_count is not None
        and guest_view_count >= Config.GUEST_VIEW_LIMIT
    ):
        flash('You have reached the free preview limit. Please register or login to continue viewing materials.', 'warning')
        return redirect(url_for('auth.login'))
    Material.increment_view(material_id)
    GuestActivity.record_view(material_id)
    subject = Subject.get_by_id(material['subject_id'])
    remaining_views = None
    if guest_view_count is not None:
        remaining_views = max(
            0, Config.GUEST_VIEW_LIMIT - guest_view_count - 1
        )
    from utils.helpers import is_video_file
    is_video = is_video_file(material.get('file_name', ''))

    disk_path, rel_path = resolve_material_disk_path(material)
    file_exists = material_file_exists(material)
    file_url = url_for('main.raw_material_file', material_id=material_id)

    return render_template(
        'pdf_viewer.html',
        material=material,
        subject=subject,
        current_user=current_user,
        remaining_views=remaining_views,
        is_video=is_video,
        rel_path=rel_path,
        file_url=file_url,
        file_exists=file_exists,
        file_mime_type=mimetypes.guess_type(material.get('file_name') or '')[0]
        or 'application/octet-stream',
        meta_title=f"{material.get('title', 'Study Material')} - Viewer - StudyHub",
        meta_description=material.get('description') or f"Preview {material.get('title', 'study material')} on StudyHub.",
    )

@main_bp.route('/material/<int:material_id>/stream')
@main_bp.route('/material/<int:material_id>/file')
def raw_material_file(material_id):
    material = Material.get_by_id(material_id)
    if not material or not material.get('is_active', 1):
        abort(404)

    if isinstance(material.get('file_path'), str) and material['file_path'].startswith(B2_PATH_PREFIX):
        return _b2_file_redirect(material, inline=True)

    disk_path, _ = resolve_material_disk_path(material)
    if not disk_path or not os.path.isfile(disk_path):
        abort(404)

    directory = os.path.dirname(disk_path)
    filename = os.path.basename(disk_path)
    return send_from_directory(directory, filename, as_attachment=False, conditional=True)

@main_bp.route('/material/<int:material_id>/download')
def download_material(material_id):
    material = Material.get_by_id(material_id)
    if not material or not material.get('is_active', 1):
        abort(404)
    if not GuestActivity.can_download_material():
        flash('You have reached the free download limit. Please register or login to continue downloading materials.', 'warning')
        return redirect(url_for('auth.login'))

    is_b2_file = (
        isinstance(material.get('file_path'), str)
        and material['file_path'].startswith(B2_PATH_PREFIX)
    )
    disk_path = None
    signed_url = None
    if is_b2_file:
        try:
            storage = get_storage()
            key = b2_key(material['file_path'])
            if not storage.object_exists(key):
                abort(404)
            signed_url = storage.get_presigned_url(
                key,
                download_name=material.get('file_name'),
                inline=False,
            )
        except B2StorageError:
            logger.exception(
                'Could not prepare B2 download for material %s.', material_id
            )
            abort(503, description='Cloud file storage is temporarily unavailable.')
    else:
        disk_path, _ = resolve_material_disk_path(material)
    if not is_b2_file and (not disk_path or not os.path.isfile(disk_path)):
        flash('The requested file is currently unavailable on the server. Please contact support.', 'warning')
        return redirect(url_for('main.material_details', material_id=material_id))

    Material.increment_download(material_id)
    GuestActivity.record_download(material_id)

    download_name = material.get('file_name') or (
        os.path.basename(disk_path) if disk_path else 'material-{}'.format(material_id)
    )
    if is_b2_file:
        return redirect(signed_url, code=302)

    directory = os.path.dirname(disk_path)
    filename = os.path.basename(disk_path)
    return send_from_directory(directory, filename, as_attachment=True, download_name=download_name, conditional=True)

@main_bp.route('/static/uploads/<path:filepath>')
@main_bp.route('/uploads/<path:filepath>')
def serve_uploaded_file(filepath):
    """Fallback handler for uploaded files to support custom UPLOAD_FOLDER and case-insensitive matching."""
    decoded_path = urllib.parse.unquote(filepath).strip('/\\')
    parts = [p for p in decoded_path.replace('\\', '/').split('/') if p]
    if any(part in ('.', '..') for part in parts):
        abort(404)
    while parts and parts[0].lower() in ('static', 'uploads'):
        parts.pop(0)
    if not parts:
        abort(404)

    candidate_roots = []
    if Config.UPLOAD_FOLDER:
        candidate_roots.append(os.path.abspath(Config.UPLOAD_FOLDER))
    static_root = current_app.static_folder if current_app else os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
    if static_root:
        candidate_roots.append(os.path.abspath(os.path.join(static_root, 'uploads')))

    seen = set()
    for root in candidate_roots:
        if not root or root in seen or not os.path.isdir(root):
            continue
        seen.add(root)
        direct = os.path.realpath(os.path.join(root, *parts))
        if _is_path_within(root, direct) and os.path.isfile(direct):
            return send_from_directory(os.path.dirname(direct), os.path.basename(direct), conditional=True)
        matched = _find_file_case_insensitive(root, parts)
        if matched and _is_path_within(root, matched) and os.path.isfile(matched):
            return send_from_directory(os.path.dirname(matched), os.path.basename(matched), conditional=True)

    abort(404)

@main_bp.route('/favicon.ico')
def favicon():
    """Serve favicon.ico or favicon.svg from static or static/favicons directory to prevent 404 errors."""
    static_dir = current_app.static_folder or os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
    candidates = [
        (static_dir, 'favicon.ico', 'image/vnd.microsoft.icon'),
        (os.path.join(static_dir, 'favicons'), 'favicon.ico', 'image/vnd.microsoft.icon'),
        (os.path.join(static_dir, 'favicons'), 'favicon.svg', 'image/svg+xml'),
        (static_dir, 'favicon.svg', 'image/svg+xml'),
    ]
    for directory, filename, mimetype in candidates:
        if os.path.isfile(os.path.join(directory, filename)):
            return send_from_directory(directory, filename, mimetype=mimetype)
    abort(404)


@main_bp.route('/robots.txt')
def robots_txt():
    sitemap_url = absolute_public_url(url_for('main.sitemap_xml'))
    lines = [
        'User-agent: *',
        'Allow: /',
        'Disallow: /admin',
        'Disallow: /login',
        'Disallow: /register',
        'Disallow: /forgot-password',
        'Disallow: /reset-password',
        'Disallow: /profile',
        'Disallow: /favorites',
        'Disallow: /notifications',
        'Disallow: /account',
        'Disallow: /api/',
        'Disallow: /uploads/',
        'Disallow: /static/uploads/',
        'Disallow: /material/*/file',
        'Disallow: /material/*/stream',
        'Disallow: /material/*/download',
    ]
    if sitemap_url:
        lines.extend(('', 'Sitemap: {}'.format(sitemap_url)))
    return current_app.response_class(
        '\n'.join(lines) + '\n',
        mimetype='text/plain',
    )


def _sitemap_lastmod(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(
            text.replace('Z', '+00:00')
        ).date().isoformat()
    except ValueError:
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return None


@main_bp.route('/sitemap.xml')
def sitemap_xml():
    namespace = 'http://www.sitemaps.org/schemas/sitemap/0.9'
    ET.register_namespace('', namespace)
    urlset = ET.Element('{{{}}}urlset'.format(namespace))

    def add_url(path, lastmod=None):
        location = absolute_public_url(path)
        if not location:
            abort(503, description='The canonical public site URL is not configured.')
        entry = ET.SubElement(urlset, '{{{}}}url'.format(namespace))
        ET.SubElement(entry, '{{{}}}loc'.format(namespace)).text = location
        formatted_lastmod = _sitemap_lastmod(lastmod)
        if formatted_lastmod:
            ET.SubElement(
                entry, '{{{}}}lastmod'.format(namespace)
            ).text = formatted_lastmod

    public_endpoints = (
        'main.index',
        'main.explore',
        'main.subjects',
        'main.notes_page',
        'main.assignments_page',
        'main.books_page',
        'main.practicals_page',
        'main.question_papers_page',
        'main.important_questions_page',
        'main.syllabus_page',
        'main.about',
        'main.cookie_policy',
        'main.privacy_policy',
        'main.terms_of_service',
        'main.contact',
    )
    for endpoint in public_endpoints:
        add_url(url_for(endpoint))

    conn = get_connection()
    cursor = None
    try:
        cursor = get_cursor(conn)
        cursor.execute(
            'SELECT id, updated_at FROM subjects '
            'WHERE is_active = 1 ORDER BY id'
        )
        subjects = cursor.fetchall()
        cursor.execute(
            'SELECT id, updated_at FROM materials '
            'WHERE is_active = 1 ORDER BY id'
        )
        materials = cursor.fetchall()
    finally:
        close_cursor(cursor)
        conn.close()

    for subject in subjects:
        add_url(
            url_for('main.subject_details', subject_id=subject['id']),
            subject.get('updated_at'),
        )
    for material in materials:
        add_url(
            url_for('main.material_details', material_id=material['id']),
            material.get('updated_at'),
        )

    xml = ET.tostring(urlset, encoding='utf-8', xml_declaration=True)
    return current_app.response_class(xml, mimetype='application/xml')


@main_bp.route('/service-worker.js')
def service_worker():
    response = send_from_directory(
        current_app.static_folder,
        'service-worker.js',
        mimetype='application/javascript',
    )
    response.headers['Service-Worker-Allowed'] = '/'
    return response


@main_bp.route('/explore')
def explore():
    return render_template(
        'explore.html',
        current_user=get_current_user(),
        latest_materials=Material.get_latest(limit=6),
        popular_materials=Material.get_popular(limit=6),
        recently_viewed=Material.get_recently_viewed(
            session.get('guest_session_id'),
            limit=6,
        ),
        meta_title='Explore Study Materials - StudyHub',
        meta_description=(
            'Search and explore StudyHub notes, assignments, books, '
            'practicals, question papers, and subjects.'
        ),
    )


@main_bp.route('/notes')
def notes_page():
    return _material_category_page('Notes', 'notes.html')

@main_bp.route('/assignments')
def assignments_page():
    return _material_category_page('Assignment', 'assignments.html')

@main_bp.route('/books')
def books_page():
    return _material_category_page('Book', 'books.html')

@main_bp.route('/practicals')
def practicals_page():
    return _material_category_page('Practical', 'practicals.html')

@main_bp.route('/question-papers')
def question_papers_page():
    return _material_category_page('Question Paper', 'question_papers.html')

@main_bp.route('/important-questions')
def important_questions_page():
    return _material_category_page('Important Question', 'important_questions.html')

@main_bp.route('/syllabus')
def syllabus_page():
    return _material_category_page('Syllabus', 'syllabus.html')


def _material_category_page(category_name, template_name):
    page = positive_query_int('page')
    materials, total = Material.get_by_category_name(
        category_name, page=page, per_page=12
    )
    return render_template(
        template_name,
        materials=materials,
        total=total,
        page=page,
        current_user=get_current_user(),
    )

@main_bp.route('/search')
def search():
    from flask import request
    query = request.args.get('q', '').strip()
    subject_filter = request.args.get('subject', '')
    category_filter = request.args.get('category', '')
    file_type_filter = request.args.get('file_type', '')
    sort = request.args.get('sort', 'latest')
    page = positive_query_int('page')
    filters = {}
    if query:
        filters['search'] = query
    if subject_filter:
        filters['subject_id'] = positive_query_int('subject')
    if category_filter:
        filters['category_id'] = positive_query_int('category')
    if file_type_filter:
        filters['file_type'] = file_type_filter
    materials, total = Material.get_all(filters=filters, sort=sort, page=page, per_page=12)
    all_subjects = Subject.get_all(active_only=True)
    from models.subject import Category
    all_categories = Category.get_all()
    return render_template('search.html', query=query, materials=materials, total=total, page=page,
                           subjects=all_subjects, categories=all_categories,
                           selected_subject=subject_filter, selected_category=category_filter,
                           selected_file_type=file_type_filter, selected_sort=sort,
                           current_user=get_current_user())

@main_bp.route('/about')
def about():
    return render_template('about.html', current_user=get_current_user())

@main_bp.route('/cookie-policy')
def cookie_policy():
    return render_template('cookie_policy.html', current_user=get_current_user())

@main_bp.route('/privacy-policy')
def privacy_policy():
    return render_template(
        'privacy_policy.html',
        current_user=get_current_user(),
        meta_title='Privacy Policy - StudyHub',
        meta_description=(
            'Learn what account, login, and study activity information StudyHub '
            'processes, why it is used, and how to contact us about your data.'
        ),
        meta_type='website',
        meta_robots='index, follow',
    )

@main_bp.route('/terms-of-service')
def terms_of_service():
    return render_template(
        'terms_of_service.html',
        current_user=get_current_user(),
        meta_title='Terms of Service - StudyHub',
        meta_description=(
            'Read the terms for using StudyHub accounts, study resources, '
            'user-provided content, and platform features.'
        ),
        meta_type='website',
        meta_robots='index, follow',
    )

@main_bp.route('/api/cookie-consent', methods=['POST'])
@csrf_protect
def save_cookie_consent():
    data = request.get_json(silent=True) or request.form
    choice = data.get('choice', '')
    if choice == 'accept':
        preferences = {'preferences': True}
    elif choice == 'reject':
        preferences = {'preferences': False}
    elif choice == 'settings':
        preferences_value = data.get('preferences', False)
        if isinstance(preferences_value, str):
            normalized = preferences_value.lower()
            if normalized not in ('true', 'false', '1', '0', 'on', 'off', ''):
                return jsonify({'success': False, 'message': 'Invalid preference value.'}), 400
            preferences_value = normalized in ('true', '1', 'on')
        elif not isinstance(preferences_value, bool):
            return jsonify({'success': False, 'message': 'Invalid preference value.'}), 400
        preferences = {'preferences': preferences_value}
    else:
        return jsonify({'success': False, 'message': 'Invalid consent choice.'}), 400

    response = jsonify({
        'success': True,
        'preferences': {'necessary': True, **preferences},
    })
    response.set_cookie(
        COOKIE_NAME,
        serialize_cookie_preferences(preferences),
        max_age=COOKIE_CONSENT_MAX_AGE,
        httponly=True,
        secure=current_app.config.get('SESSION_COOKIE_SECURE', False),
        samesite='Lax',
        path='/',
    )
    return response

@main_bp.route('/contact', methods=['GET', 'POST'])
@csrf_protect
def contact():
    from flask import request, flash
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        subject = request.form.get('subject', '').strip()
        message = request.form.get('message', '').strip()
        if not all([name, email, subject, message]):
            flash('All fields are required.', 'danger')
        else:
            from models.message import ContactMessage
            ContactMessage.create(name, email, subject, message)
            flash('Message sent successfully! We will get back to you soon.', 'success')
    return render_template('contact.html', current_user=get_current_user())
