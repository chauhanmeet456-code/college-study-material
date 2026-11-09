import logging
import mimetypes
import os
import re
import urllib.parse
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

    fallback_rel = '/'.join(raw_parts + [file_name])
    return None, fallback_rel


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
    page = int(request.args.get('page', 1))
    
    filters = {}
    if query:
        filters['search'] = query
    if subject_filter:
        try:
            filters['subject_id'] = int(subject_filter)
        except ValueError:
            pass
    if category_filter:
        try:
            filters['category_id'] = int(category_filter)
        except ValueError:
            pass
    if file_type_filter:
        filters['file_type'] = file_type_filter
    
    materials, total = Material.get_all(filters=filters, sort=sort, page=page, per_page=12)
    has_more = total > page * 12
    
    return jsonify({
        'materials': materials,
        'total': total,
        'has_more': has_more,
        'page': page
    })

@main_bp.route('/')
def index():
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

    announcements = optional_home_query(
        'announcements', lambda: Announcement.get_all_active(limit=5), []
    )
    subjects = optional_home_query(
        'subjects', lambda: Subject.get_all(active_only=True), []
    )
    latest_materials = optional_home_query(
        'latest materials', lambda: Material.get_latest(limit=6), []
    )
    featured_materials = optional_home_query(
        'featured materials', lambda: Material.get_featured(limit=6), []
    )
    total_materials = 0
    total_notes = 0
    total_students = 0
    total_subjects = len(subjects)

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

    counts = optional_home_query('counts', get_homepage_counts, {})
    total_materials = counts.get('total_materials') or 0
    total_notes = counts.get('total_notes') or 0
    total_students = counts.get('total_students') or 0
    return render_template('index.html', subjects=subjects, latest_materials=latest_materials,
                           featured_materials=featured_materials, announcements=announcements,
                           total_subjects=total_subjects, total_materials=total_materials,
                           total_notes=total_notes, total_students=total_students,
                           current_user=get_current_user(),
                           meta_title='StudyHub - College Study Materials, Notes & Resources',
                           meta_description=(
                               'Access college study materials, notes, assignments, '
                               'books, practicals and question papers in one place '
                               'with StudyHub.'
                           ))

@main_bp.route('/subjects')
def subjects():
    all_subjects = Subject.get_all(active_only=True)
    if all_subjects:
        subject_ids = [s['id'] for s in all_subjects]
        conn = get_connection()
        cursor = get_cursor(conn)
        placeholders = ','.join(['?'] * len(subject_ids))
        cursor.execute(f"""
            SELECT subject_id, COUNT(*) as count 
            FROM materials 
            WHERE subject_id IN ({placeholders}) AND is_active = 1
            GROUP BY subject_id
        """, subject_ids)
        counts = {row['subject_id']: row['count'] for row in cursor.fetchall()}
        close_cursor(cursor)
        conn.close()
        for subject in all_subjects:
            subject['material_count'] = counts.get(subject['id'], 0)
    else:
        # No subjects, so no need to query
        pass
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
    page = int(request.args.get('page', 1))
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
    if not GuestActivity.can_view_material():
        flash('You have reached the free preview limit. Please register or login to continue viewing materials.', 'warning')
        return redirect(url_for('auth.login'))
    material['view_count'] = material.get('view_count', 0)
    from models.subject import Subject as Subj
    subject = Subj.get_by_id(material['subject_id'])
    remaining_views = None
    remaining_downloads = None
    is_favorited = False
    current_user = get_current_user()
    if not current_user:
        remaining_views = max(0, Config.GUEST_VIEW_LIMIT - GuestActivity.get_view_count())
        remaining_downloads = max(0, Config.GUEST_DOWNLOAD_LIMIT - GuestActivity.get_download_count())
    else:
        from models.material import Favorite
        is_favorited = Favorite.is_favorited(current_user['id'], material_id)
    material['is_favorited'] = is_favorited
    from utils.helpers import is_video_file
    is_video = is_video_file(material.get('file_name', ''))
    disk_path, _ = resolve_material_disk_path(material)
    file_exists = bool(disk_path and os.path.isfile(disk_path))
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
    if not GuestActivity.can_view_material():
        flash('You have reached the free preview limit. Please register or login to continue viewing materials.', 'warning')
        return redirect(url_for('auth.login'))
    Material.increment_view(material_id)
    GuestActivity.record_view(material_id)
    subject = Subject.get_by_id(material['subject_id'])
    remaining_views = None
    if not get_current_user():
        remaining_views = max(0, Config.GUEST_VIEW_LIMIT - GuestActivity.get_view_count())
    from utils.helpers import is_video_file
    is_video = is_video_file(material.get('file_name', ''))

    disk_path, rel_path = resolve_material_disk_path(material)
    file_exists = bool(disk_path and os.path.isfile(disk_path))
    file_url = url_for('main.raw_material_file', material_id=material_id)

    return render_template(
        'pdf_viewer.html',
        material=material,
        subject=subject,
        current_user=get_current_user(),
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

    disk_path, _ = resolve_material_disk_path(material)
    if not disk_path or not os.path.isfile(disk_path):
        flash('The requested file is currently unavailable on the server. Please contact support.', 'warning')
        return redirect(url_for('main.material_details', material_id=material_id))

    Material.increment_download(material_id)
    GuestActivity.record_download(material_id)

    directory = os.path.dirname(disk_path)
    filename = os.path.basename(disk_path)
    download_name = material.get('file_name') or filename
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

@main_bp.route('/notes')
def notes_page():
    from models.subject import Category
    from flask import request
    category = Category.get_by_name('Notes')
    if not category:
        return render_template('notes.html', materials=[], total=0, page=1, current_user=get_current_user())
    page = int(request.args.get('page', 1))
    materials, total = Material.get_by_category(category['id'], page=page, per_page=12)
    return render_template('notes.html', materials=materials, total=total, page=page, current_user=get_current_user())

@main_bp.route('/assignments')
def assignments_page():
    from models.subject import Category
    from flask import request
    category = Category.get_by_name('Assignment')
    if not category:
        return render_template('assignments.html', materials=[], total=0, page=1, current_user=get_current_user())
    page = int(request.args.get('page', 1))
    materials, total = Material.get_by_category(category['id'], page=page, per_page=12)
    return render_template('assignments.html', materials=materials, total=total, page=page, current_user=get_current_user())

@main_bp.route('/books')
def books_page():
    from models.subject import Category
    from flask import request
    category = Category.get_by_name('Book')
    if not category:
        return render_template('books.html', materials=[], total=0, page=1, current_user=get_current_user())
    page = int(request.args.get('page', 1))
    materials, total = Material.get_by_category(category['id'], page=page, per_page=12)
    return render_template('books.html', materials=materials, total=total, page=page, current_user=get_current_user())

@main_bp.route('/practicals')
def practicals_page():
    from models.subject import Category
    from flask import request
    category = Category.get_by_name('Practical')
    if not category:
        return render_template('practicals.html', materials=[], total=0, page=1, current_user=get_current_user())
    page = int(request.args.get('page', 1))
    materials, total = Material.get_by_category(category['id'], page=page, per_page=12)
    return render_template('practicals.html', materials=materials, total=total, page=page, current_user=get_current_user())

@main_bp.route('/question-papers')
def question_papers_page():
    from models.subject import Category
    from flask import request
    category = Category.get_by_name('Question Paper')
    if not category:
        return render_template('question_papers.html', materials=[], total=0, page=1, current_user=get_current_user())
    page = int(request.args.get('page', 1))
    materials, total = Material.get_by_category(category['id'], page=page, per_page=12)
    return render_template('question_papers.html', materials=materials, total=total, page=page, current_user=get_current_user())

@main_bp.route('/important-questions')
def important_questions_page():
    from models.subject import Category
    from flask import request
    category = Category.get_by_name('Important Question')
    if not category:
        return render_template('important_questions.html', materials=[], total=0, page=1, current_user=get_current_user())
    page = int(request.args.get('page', 1))
    materials, total = Material.get_by_category(category['id'], page=page, per_page=12)
    return render_template('important_questions.html', materials=materials, total=total, page=page, current_user=get_current_user())

@main_bp.route('/syllabus')
def syllabus_page():
    from models.subject import Category
    from flask import request
    category = Category.get_by_name('Syllabus')
    if not category:
        return render_template('syllabus.html', materials=[], total=0, page=1, current_user=get_current_user())
    page = int(request.args.get('page', 1))
    materials, total = Material.get_by_category(category['id'], page=page, per_page=12)
    return render_template('syllabus.html', materials=materials, total=total, page=page, current_user=get_current_user())

@main_bp.route('/search')
def search():
    from flask import request
    query = request.args.get('q', '').strip()
    subject_filter = request.args.get('subject', '')
    category_filter = request.args.get('category', '')
    file_type_filter = request.args.get('file_type', '')
    sort = request.args.get('sort', 'latest')
    page = int(request.args.get('page', 1))
    filters = {}
    if query:
        filters['search'] = query
    if subject_filter:
        filters['subject_id'] = int(subject_filter)
    if category_filter:
        filters['category_id'] = int(category_filter)
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
