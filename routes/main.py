from flask import Blueprint, render_template, request, flash, send_from_directory, redirect, url_for, jsonify, abort
from models.subject import Subject
from models.material import Material
from models.message import Announcement
from utils.auth import get_current_user
from utils.guest_activity import GuestActivity
from utils.database import get_connection, get_cursor, close_cursor
from utils.security import csrf_protect
from config import Config
import os

main_bp = Blueprint('main', __name__)

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
    if len(query) < 2:
        return jsonify({'suggestions': []})
    
    materials, _ = Material.get_all(filters={'search': query}, sort='latest', page=1, per_page=8)
    suggestions = [m['title'] for m in materials if m.get('title')]
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
    announcements = Announcement.get_all_active(limit=5)
    subjects = Subject.get_all(active_only=True)
    latest_materials = Material.get_latest(limit=6)
    featured_materials = Material.get_featured(limit=6)
    total_materials = 0
    total_notes = 0
    total_students = 0
    total_subjects = len(subjects)
    try:
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM materials WHERE is_active = 1) as total_materials,
                (SELECT COUNT(*) FROM materials m JOIN categories c ON m.category_id = c.id WHERE c.name = 'Notes' AND m.is_active = 1) as total_notes,
                (SELECT COUNT(*) FROM users WHERE is_active = 1) as total_students
        """)
        counts = cursor.fetchone()
        total_materials = counts['total_materials']
        total_notes = counts['total_notes']
        total_students = counts['total_students']
        close_cursor(cursor)
        conn.close()
    except Exception:
        pass
    return render_template('index.html', subjects=subjects, latest_materials=latest_materials,
                           featured_materials=featured_materials, announcements=announcements,
                           total_subjects=total_subjects, total_materials=total_materials,
                           total_notes=total_notes, total_students=total_students,
                           current_user=get_current_user())

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
    return render_template('subjects.html', subjects=all_subjects, current_user=get_current_user())

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
                           current_user=get_current_user())

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
    return render_template('material_details.html', material=material, subject=subject, current_user=current_user,
                           remaining_views=remaining_views, remaining_downloads=remaining_downloads, is_video=is_video)

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
    raw_path = material.get('file_path', '') or ''
    if raw_path.startswith('static\\'):
        rel_path = raw_path[7:].replace('\\', '/')
    elif raw_path.startswith('static/'):
        rel_path = raw_path[7:]
    else:
        rel_path = raw_path.replace('\\', '/')
    return render_template('pdf_viewer.html', material=material, subject=subject, current_user=get_current_user(),
                           remaining_views=remaining_views, is_video=is_video, rel_path=rel_path)

@main_bp.route('/material/<int:material_id>/download')
def download_material(material_id):
    material = Material.get_by_id(material_id)
    if not material or not material.get('is_active', 1):
        abort(404)
    if not GuestActivity.can_download_material():
        flash('You have reached the free download limit. Please register or login to continue downloading materials.', 'warning')
        return redirect(url_for('auth.login'))
    Material.increment_download(material_id)
    GuestActivity.record_download(material_id)
    
    directory = os.path.join(material['file_path'])
    filename = material['file_name']
    
    directory = os.path.abspath(directory)
    if not directory.startswith(os.path.abspath(Config.UPLOAD_FOLDER)):
        abort(403)
    
    return send_from_directory(directory, filename, as_attachment=True)

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
