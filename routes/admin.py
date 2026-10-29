from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.utils import secure_filename
import os
from datetime import datetime
from models.admin import Admin
from models.subject import Subject, Category
from models.material import Material, Favorite
from models.message import ContactMessage, Announcement
from models.session import (
    AdminAuditLog,
    FailedLoginAttempt,
    LoginHistory,
    UserSession,
)
from models.user import User
from utils.decorators import admin_required
from utils.auth import get_current_admin
from utils.database import immediate_transaction
from utils.helpers import allowed_file, generate_safe_filename, get_category_subdir
from utils.security import csrf_protect
import secrets
from datetime import timedelta
from config import Config


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

@admin_bp.route('/bulk-action', methods=['POST'])
@admin_required
@csrf_protect
def bulk_action():
    data = request.get_json()
    action = data.get('action')
    ids = data.get('ids', [])
    
    if not action or not ids:
        return jsonify({'success': False, 'message': 'Invalid request'})
    
    if action == 'delete':
        for id_val in ids:
            Material.delete(int(id_val))
        return jsonify({'success': True, 'message': f'{len(ids)} items deleted'})
    elif action == 'activate':
        for id_val in ids:
            Material.update(int(id_val), is_active=1)
        return jsonify({'success': True, 'message': f'{len(ids)} items activated'})
    elif action == 'deactivate':
        for id_val in ids:
            Material.update(int(id_val), is_active=0)
        return jsonify({'success': True, 'message': f'{len(ids)} items deactivated'})
    
    return jsonify({'success': False, 'message': 'Unknown action'})

@admin_bp.route('/login', methods=['GET', 'POST'])
@csrf_protect
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        admin = Admin.get_by_username(username)
        if not admin:
            admin = Admin.get_by_email(username)
        if admin and Admin.verify_password(admin, password):
            session['admin_id'] = admin['id']
            session['admin_name'] = admin['name']
            Admin.update_last_login(admin['id'])
            flash('Admin login successful!', 'success')
            return redirect(url_for('admin.dashboard'))
        flash('Invalid credentials.', 'danger')
    return render_template('admin/login.html')

@admin_bp.route('/logout', methods=['GET', 'POST'])
@admin_required
@csrf_protect
def admin_logout():
    session.pop('admin_id', None)
    session.pop('admin_name', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('admin.admin_login'))

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    total_students = 0
    total_materials = 0
    total_subjects = 0
    total_downloads = 0
    total_views = 0
    try:
        from utils.database import get_connection, get_cursor, close_cursor
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM users WHERE is_active = 1) as total_students,
                (SELECT COUNT(*) FROM subjects WHERE is_active = 1) as total_subjects,
                (SELECT COUNT(*) FROM materials WHERE is_active = 1) as total_materials,
                (SELECT COALESCE(SUM(download_count), 0) FROM materials) as total_downloads,
                (SELECT COALESCE(SUM(view_count), 0) FROM materials) as total_views
        """)
        row = cursor.fetchone()
        total_students = row['total_students']
        total_subjects = row['total_subjects']
        total_materials = row['total_materials']
        total_downloads = row['total_downloads']
        total_views = row['total_views']
        close_cursor(cursor)
        conn.close()
    except Exception as e:
        pass
    all_subjects = Subject.get_all(active_only=False)
    all_materials = Material.get_all_admin(per_page=100)[0]
    total_notes = sum(1 for m in all_materials if m.get('category_name') == 'Notes')
    total_assignments = sum(1 for m in all_materials if m.get('category_name') == 'Assignment')
    total_books = sum(1 for m in all_materials if m.get('category_name') == 'Book')
    total_practicals = sum(1 for m in all_materials if m.get('category_name') == 'Practical')
    return render_template('admin/dashboard.html', admin=get_current_admin(),
                           total_students=total_students, total_subjects=total_subjects,
                           total_materials=total_materials, total_notes=total_notes,
                           total_assignments=total_assignments, total_books=total_books,
                           total_practicals=total_practicals, total_downloads=total_downloads,
                           total_views=total_views)

@admin_bp.route('/subjects')
@admin_required
def subjects_list():
    search = request.args.get('search', '')
    status = request.args.get('status', 'all')
    page = int(request.args.get('page', 1))
    subjects, total = Subject.get_all_admin(search=search, status=status, page=page, per_page=10)
    return render_template('admin/subjects.html', subjects=subjects, total=total, page=page,
                           search=search, status=status, admin=get_current_admin())

@admin_bp.route('/subjects/add', methods=['GET', 'POST'])
@admin_required
@csrf_protect
def add_subject():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip()
        description = request.form.get('description', '').strip()
        icon = request.form.get('icon', 'fa-book')
        if not all([name, code]):
            flash('Name and code are required.', 'danger')
        else:
            Subject.create(name, code, description, icon)
            flash('Subject added successfully!', 'success')
            return redirect(url_for('admin.subjects_list'))
    return render_template('admin/add_subject.html', admin=get_current_admin())

@admin_bp.route('/subjects/edit/<int:subject_id>', methods=['GET', 'POST'])
@admin_required
@csrf_protect
def edit_subject(subject_id):
    subject = Subject.get_by_id(subject_id)
    if not subject:
        flash('Subject not found.', 'danger')
        return redirect(url_for('admin.subjects_list'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip()
        description = request.form.get('description', '').strip()
        icon = request.form.get('icon', 'fa-book')
        is_active = 1 if request.form.get('is_active') else 0
        if not all([name, code]):
            flash('Name and code are required.', 'danger')
        else:
            Subject.update(subject_id, name=name, code=code, description=description, icon=icon, is_active=is_active)
            flash('Subject updated successfully!', 'success')
            return redirect(url_for('admin.subjects_list'))
    return render_template('admin/edit_subject.html', subject=subject, admin=get_current_admin())

@admin_bp.route('/subjects/delete/<int:subject_id>', methods=['POST'])
@admin_required
@csrf_protect
def delete_subject(subject_id):
    Subject.delete(subject_id)
    flash('Subject deleted successfully!', 'success')
    return redirect(url_for('admin.subjects_list'))

@admin_bp.route('/materials')
@admin_required
def materials_list():
    search = request.args.get('search', '')
    subject_id = request.args.get('subject_id', None)
    category_id = request.args.get('category_id', None)
    featured = request.args.get('featured', None)
    status = request.args.get('status', 'all')
    page = int(request.args.get('page', 1))
    if featured == '1':
        featured = 1
    elif featured == '0':
        featured = 0
    else:
        featured = None
    if subject_id:
        try:
            subject_id = int(subject_id)
        except ValueError:
            subject_id = None
    if category_id:
        try:
            category_id = int(category_id)
        except ValueError:
            category_id = None
    materials, total = Material.get_all_admin(search=search, subject_id=subject_id, category_id=category_id,
                                              featured=featured, status=status, page=page, per_page=10)
    all_subjects = Subject.get_all(active_only=False)
    all_categories = Category.get_all()
    return render_template('admin/materials.html', materials=materials, total=total, page=page,
                           search=search, subject_id=subject_id, category_id=category_id,
                           featured=featured, status=status, all_subjects=all_subjects,
                           all_categories=all_categories, admin=get_current_admin())

@admin_bp.route('/materials/add', methods=['GET', 'POST'])
@admin_required
@csrf_protect
def add_material():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        subject_id = request.form.get('subject_id')
        category_id = request.form.get('category_id')
        is_featured = 1 if request.form.get('is_featured') else 0
        is_active = 1 if request.form.get('is_active') else 0
        if not all([title, subject_id, category_id]):
            flash('Title, subject and category are required.', 'danger')
        elif 'file' not in request.files and 'folder' not in request.files:
            flash('No file or folder uploaded.', 'danger')
        else:
            category = Category.get_by_id(category_id)
            subdir = get_category_subdir(category['name'] if category else 'Other')
            upload_dir = os.path.join(Config.UPLOAD_FOLDER, subdir)
            os.makedirs(upload_dir, exist_ok=True)

            saved_count = 0
            if 'folder' in request.files and request.files['folder'].filename != '':
                files = request.files.getlist('folder')
                for f in files:
                    if not f.filename:
                        continue
                    if not allowed_file(f.filename):
                        continue
                    rel_path = os.path.relpath(os.path.dirname(f.filename), '.')
                    safe_rel = os.path.normpath(rel_path).replace('\\', '/').lstrip('/')
                    if safe_rel:
                        folder_dir = os.path.join(upload_dir, safe_rel)
                        os.makedirs(folder_dir, exist_ok=True)
                        file_path = os.path.join(folder_dir, secure_filename(os.path.basename(f.filename)))
                    else:
                        file_path = os.path.join(upload_dir, secure_filename(f.filename))
                    f.save(file_path)
                    file_size = os.path.getsize(file_path)
                    file_type = os.path.splitext(file_path)[1].lower().lstrip('.')
                    file_name = os.path.basename(file_path)
                    stored_path = os.path.join('static', 'uploads', subdir)
                    if safe_rel:
                        stored_path = os.path.join(stored_path, safe_rel)
                    Material.create(title, description, int(subject_id), int(category_id), file_name,
                                   stored_path, file_type, file_size,
                                   session.get('admin_id'), is_featured=is_featured, is_active=is_active)
                    saved_count += 1
                if saved_count == 0:
                    flash('No valid files found in folder. Allowed: PDF, DOC, DOCX, PPT, PPTX, ZIP, TXT, Images (PNG/JPG/GIF/WebP), Video files.', 'danger')
                else:
                    flash(f'{saved_count} file(s) uploaded successfully from folder!', 'success')
                    return redirect(url_for('admin.materials_list'))
            else:
                file = request.files.get('file')
                if not file or file.filename == '':
                    flash('No file selected.', 'danger')
                elif file and allowed_file(file.filename):
                    filename = generate_safe_filename(file.filename)
                    file_path = os.path.join(upload_dir, filename)
                    file.save(file_path)
                    file_size = os.path.getsize(file_path)
                    file_type = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
                    Material.create(title, description, int(subject_id), int(category_id), filename,
                                   os.path.join('static', 'uploads', subdir), file_type, file_size,
                                   session.get('admin_id'), is_featured=is_featured, is_active=is_active)
                    flash('Material uploaded successfully!', 'success')
                    return redirect(url_for('admin.materials_list'))
                else:
                    flash('Invalid file type. Allowed: PDF, DOC, DOCX, PPT, PPTX, ZIP, TXT, Images (PNG/JPG/ GIF/WebP), Video files.', 'danger')
    all_subjects = Subject.get_all(active_only=False)
    all_categories = Category.get_all()
    return render_template('admin/add_material.html', all_subjects=all_subjects, all_categories=all_categories, admin=get_current_admin())

@admin_bp.route('/materials/edit/<int:material_id>', methods=['GET', 'POST'])
@admin_required
@csrf_protect
def edit_material(material_id):
    material = Material.get_by_id(material_id)
    if not material:
        flash('Material not found.', 'danger')
        return redirect(url_for('admin.materials_list'))
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        subject_id = request.form.get('subject_id')
        category_id = request.form.get('category_id')
        is_featured = 1 if request.form.get('is_featured') else 0
        is_active = 1 if request.form.get('is_active') else 0
        if not all([title, subject_id, category_id]):
            flash('Title, subject and category are required.', 'danger')
        else:
            Material.update(material_id, title=title, description=description, subject_id=int(subject_id),
                           category_id=int(category_id), is_featured=is_featured, is_active=is_active)
            if 'file' in request.files and request.files['file'].filename != '':
                file = request.files['file']
                if file and allowed_file(file.filename):
                    old_path = os.path.join(material['file_path'], material['file_name'])
                    if os.path.exists(old_path):
                        os.remove(old_path)
                    category = Category.get_by_id(int(category_id))
                    subdir = get_category_subdir(category['name'] if category else 'Other')
                    filename = generate_safe_filename(file.filename)
                    upload_dir = os.path.join(Config.UPLOAD_FOLDER, subdir)
                    os.makedirs(upload_dir, exist_ok=True)
                    file_path = os.path.join(upload_dir, filename)
                    file.save(file_path)
                    file_size = os.path.getsize(file_path)
                    file_type = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
                    Material.update_file(material_id, filename, os.path.join('static', 'uploads', subdir), file_type, file_size)
            flash('Material updated successfully!', 'success')
            return redirect(url_for('admin.materials_list'))
    all_subjects = Subject.get_all(active_only=False)
    all_categories = Category.get_all()
    return render_template('admin/edit_material.html', material=material, all_subjects=all_subjects,
                           all_categories=all_categories, admin=get_current_admin())

@admin_bp.route('/materials/delete/<int:material_id>', methods=['POST'])
@admin_required
@csrf_protect
def delete_material(material_id):
    material = Material.get_by_id(material_id)
    if material:
        old_path = os.path.join(material['file_path'], material['file_name'])
        if os.path.exists(old_path):
            os.remove(old_path)
    Material.delete(material_id)
    flash('Material deleted successfully!', 'success')
    return redirect(url_for('admin.materials_list'))

@admin_bp.route('/categories')
@admin_required
def categories_list():
    categories = Category.get_all(active_only=False)
    return render_template('admin/categories.html', categories=categories, admin=get_current_admin())

@admin_bp.route('/categories/add', methods=['POST'])
@admin_required
@csrf_protect
def add_category():
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    if name:
        Category.create(name, description)
        flash('Category added successfully!', 'success')
    else:
        flash('Category name is required.', 'danger')
    return redirect(url_for('admin.categories_list'))

@admin_bp.route('/categories/edit/<int:category_id>', methods=['POST'])
@admin_required
@csrf_protect
def edit_category(category_id):
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    if name:
        Category.update(category_id, name=name, description=description)
        flash('Category updated successfully!', 'success')
    else:
        flash('Category name is required.', 'danger')
    return redirect(url_for('admin.categories_list'))

@admin_bp.route('/categories/delete/<int:category_id>', methods=['POST'])
@admin_required
@csrf_protect
def delete_category(category_id):
    Category.delete(category_id)
    flash('Category deleted successfully!', 'success')
    return redirect(url_for('admin.categories_list'))

@admin_bp.route('/users')
@admin_required
def users_list():
    search = request.args.get('search', '')
    status = request.args.get('status', 'all')
    page = int(request.args.get('page', 1))
    users, total = User.get_all(search=search, status=status, page=page, per_page=10)
    deletion_requests = User.get_deletion_requests()
    deletion_count = len(deletion_requests)
    return render_template('admin/users.html', users=users, total=total, page=page,
                           search=search, status=status, admin=get_current_admin(),
                           deletion_count=deletion_count)

@admin_bp.route('/deletion-requests')
@admin_required
def deletion_requests():
    users = User.get_deletion_requests()
    return render_template('admin/deletion_requests.html', users=users, admin=get_current_admin())

@admin_bp.route('/deleted-users')
@admin_required
def deleted_users_list():
    search = request.args.get('search', '')
    page = int(request.args.get('page', 1))
    users, total = User.get_deleted_users(search=search, page=page, per_page=10)
    end = min(page * 10, total)
    return render_template('admin/deleted_users.html', users=users, total=total, page=page,
                           search=search, admin=get_current_admin(), end=end)

@admin_bp.route('/users/never-logged-in')
@admin_required
def never_logged_in_users():
    search = request.args.get('search', '')
    status = request.args.get('status', 'all')
    page = int(request.args.get('page', 1))
    users, total = User.get_never_logged_in_users(search=search, status=status, page=page, per_page=10)
    return render_template('admin/never_logged_in.html', users=users, total=total, page=page,
                           search=search, status=status, admin=get_current_admin())

@admin_bp.route('/users/<int:user_id>')
@admin_required
def view_user(user_id):
    user = User.get_by_id(user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.users_list'))
    from models.material import Favorite
    favorites, fav_total = Favorite.get_by_user(user_id, page=1, per_page=1000)
    session_row = _active_session_for_user(user_id)
    return render_template('admin/user_detail.html', user=user, favorites=favorites,
                           fav_total=fav_total, admin=get_current_admin(),
                           active_session=session_row)


@admin_bp.route('/users/activate/<int:user_id>')
@admin_required
def activate_user(user_id):
    User.restore(user_id)
    flash('User activated.', 'success')
    return redirect(url_for('admin.users_list'))

@admin_bp.route('/users/deactivate/<int:user_id>')
@admin_required
def deactivate_user(user_id):
    User.update(user_id, is_active=0)
    flash('User deactivated.', 'success')
    return redirect(url_for('admin.users_list'))

@admin_bp.route('/users/delete/<int:user_id>', methods=['POST'])
@admin_required
@csrf_protect
def delete_user(user_id):
    next_url = request.form.get('next') or request.referrer or url_for('admin.users_list')
    user = User.get_by_id(user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(next_url)
    user_email = user.get('email') or ''
    deleted = False
    if user.get('deleted_by_user') == 1 and user.get('deletion_processed') is not None:
        with immediate_transaction() as conn:
            deleted = User.permanent_delete(user_id, conn=conn)
            # Enqueue the account-deletion notification transactionally: if the
            # enqueue fails (e.g. DB error) the whole transaction rolls back and
            # the deletion is not committed — preventing an orphaned user with
            # no notification queued.  SMTP delivery itself is deferred to the
            # background worker, so the HTTP request is not blocked.
            if deleted and user_email:
                from utils.notifications import queue_template_notification
                queue_template_notification(
                    'account_deleted_by_admin',
                    user_email,
                    related_id=user_id,
                    dedupe_key='account_deleted_by_admin:%d' % user_id,
                    conn=conn,
                )
        if deleted:
            flash('User permanently deleted from database.', 'success')
    else:
        with immediate_transaction() as conn:
            User.mark_deletion_processed(user_id, conn=conn)
            deleted = User.delete(user_id, conn=conn)
            # Same transactional enqueue as above.
            if deleted and user_email:
                from utils.notifications import queue_template_notification
                queue_template_notification(
                    'account_deleted_by_admin',
                    user_email,
                    related_id=user_id,
                    dedupe_key='account_deleted_by_admin:%d' % user_id,
                    conn=conn,
                )
        if deleted:
            flash('User deletion processed successfully!', 'success')
    return redirect(next_url)

@admin_bp.route('/messages')
@admin_required
def messages_list():
    read_filter = request.args.get('filter', 'all')
    page = int(request.args.get('page', 1))
    messages, total = ContactMessage.get_all(page=page, per_page=10, read_filter=read_filter)
    return render_template('admin/messages.html', messages=messages, total=total, page=page,
                           read_filter=read_filter, admin=get_current_admin())

@admin_bp.route('/messages/<int:message_id>')
@admin_required
def view_message(message_id):
    message = ContactMessage.get_by_id(message_id)
    if message and not message['is_read']:
        ContactMessage.mark_read(message_id)
    return render_template('admin/message_detail.html', message=message, admin=get_current_admin())

@admin_bp.route('/messages/delete/<int:message_id>', methods=['POST'])
@admin_required
@csrf_protect
def delete_message(message_id):
    ContactMessage.delete(message_id)
    flash('Message deleted.', 'success')
    return redirect(url_for('admin.messages_list'))

@admin_bp.route('/announcements')
@admin_required
def announcements_list():
    search = request.args.get('search', '')
    status = request.args.get('status', 'all')
    page = int(request.args.get('page', 1))
    announcements, total = Announcement.get_all_admin(search=search, status=status, page=page, per_page=10)
    return render_template('admin/announcements.html', announcements=announcements, total=total, page=page,
                           search=search, status=status, admin=get_current_admin())

@admin_bp.route('/announcements/add', methods=['POST'])
@admin_required
@csrf_protect
def add_announcement():
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    is_active = 1 if request.form.get('is_active') else 0
    if title:
        Announcement.create(title, description)
        flash('Announcement created successfully!', 'success')
    else:
        flash('Title is required.', 'danger')
    return redirect(url_for('admin.announcements_list'))

@admin_bp.route('/announcements/edit/<int:announcement_id>', methods=['POST'])
@admin_required
@csrf_protect
def edit_announcement(announcement_id):
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    is_active = 1 if request.form.get('is_active') else 0
    if title:
        Announcement.update(announcement_id, title=title, description=description, is_active=is_active)
        flash('Announcement updated successfully!', 'success')
    else:
        flash('Title is required.', 'danger')
    return redirect(url_for('admin.announcements_list'))

@admin_bp.route('/announcements/delete/<int:announcement_id>', methods=['POST'])
@admin_required
@csrf_protect
def delete_announcement(announcement_id):
    Announcement.delete(announcement_id)
    flash('Announcement deleted successfully!', 'success')
    return redirect(url_for('admin.announcements_list'))

@admin_bp.route('/profile', methods=['GET', 'POST'])
@admin_required
def profile():
    admin = Admin.get_by_id(session['admin_id'])
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        username = request.form.get('username', '').strip()
        Admin.update(session['admin_id'], name=name, email=email, username=username)
        session['admin_name'] = name
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('admin.profile'))
    return render_template('admin/profile.html', admin=admin, admin_user=admin)

@admin_bp.route('/profile/change-password', methods=['POST'])
@admin_required
@csrf_protect
def change_admin_password():
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')
    admin = Admin.get_by_id(session['admin_id'])
    if not Admin.verify_password(admin, current_password):
        flash('Current password is incorrect.', 'danger')
    elif new_password != confirm_password:
        flash('New passwords do not match.', 'danger')
    elif len(new_password) < 6:
        flash('Password must be at least 6 characters.', 'danger')
    else:
        Admin.update_password(session['admin_id'], new_password)
        flash('Password changed successfully!', 'success')
    return redirect(url_for('admin.profile'))

@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
@csrf_protect
def settings():
    from models.settings import Settings
    if request.method == 'POST':
        Settings.set('site_name', request.form.get('site_name', ''))
        Settings.set('site_description', request.form.get('site_description', ''))
        Settings.set('contact_email', request.form.get('contact_email', ''))
        Settings.set('support_email', request.form.get('support_email', ''))
        Settings.set('footer_text', request.form.get('footer_text', ''))
        flash('Settings saved successfully!', 'success')
        return redirect(url_for('admin.settings'))
    
    settings_data = Settings.get_all()
    return render_template('admin/settings.html', admin=get_current_admin(), settings=settings_data)

ALLOWED_ADMIN_EMAIL = 'studyhubsupport456@gmail.com'

@admin_bp.route('/forgot-password', methods=['GET', 'POST'])
@csrf_protect
def admin_forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        allowed_email = 'studyhubsupport456@gmail.com'
        if email != allowed_email:
            flash('Password reset is only allowed for the registered StudyHub admin email.', 'danger')
            return redirect(url_for('admin.admin_forgot_password'))
        admin = Admin.get_by_email(email)
        if not admin:
            flash('Admin account not found.', 'danger')
            return redirect(url_for('admin.admin_forgot_password'))
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=1)
        Admin.set_reset_token(admin['id'], token, expires_at)
        reset_link = url_for('admin.admin_reset_password', token=token, _external=True)
        if Config.MAIL_USERNAME and Config.MAIL_PASSWORD:
            from utils.otp import OTPManager
            body = f'Click this link to reset your admin password: <a href="{reset_link}">Reset Password</a><br><br>If you did not request this, please ignore this email.'
            OTPManager.send_email(email, 'Reset your StudyHub admin password', body)
            flash('Password reset link sent to your email.', 'success')
        else:
            flash('Reset link: ' + reset_link, 'info')
        return redirect(url_for('admin.admin_login'))
    return render_template('admin/forgot_password.html')

@admin_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
@csrf_protect
def admin_reset_password(token):
    admin = Admin.get_by_reset_token(token)
    if not admin:
        flash('Invalid or expired reset link.', 'danger')
        return redirect(url_for('admin.admin_login'))
    expires_at = admin.get('reset_token_expires_at')
    if expires_at:
        try:
            expires_at_dt = datetime.strptime(str(expires_at), '%Y-%m-%d %H:%M:%S.%f')
        except ValueError:
            expires_at_dt = datetime.strptime(str(expires_at), '%Y-%m-%d')
        if datetime.utcnow() > expires_at_dt:
            Admin.clear_reset_token(admin['id'])
            flash('Reset link has expired. Please request a new one.', 'danger')
            return redirect(url_for('admin.admin_forgot_password'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if not password or len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
        elif password != confirm:
            flash('Passwords do not match.', 'danger')
        else:
            Admin.update_password(admin['id'], password)
            Admin.clear_reset_token(admin['id'])
            flash('Admin password reset successfully! Please login.', 'success')
            return redirect(url_for('admin.admin_login'))
    return render_template('admin/reset_password.html', token=token)


# ---------------------------------------------------------------------------
# User security: login history and session management
# ---------------------------------------------------------------------------

def _active_session_for_user(user_id):
    try:
        rows = UserSession.get_active_sessions_for_user(user_id)
        return rows[0] if rows else None
    except Exception:
        return None


def _admin_client_ip():
    if Config.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.remote_addr


@admin_bp.route('/user-security')
@admin_required
def user_security():
    """Overview of every user's login history and all active sessions."""
    page = int(request.args.get('page', 1))
    session_page = int(request.args.get('session_page', 1))
    search = request.args.get('search', '')
    event_type = request.args.get('event_type') or None
    status = request.args.get('status') or None
    date_from = request.args.get('date_from') or None
    date_to = request.args.get('date_to') or None

    history, total = LoginHistory.get_all(
        search=search, event_type=event_type, status=status,
        date_from=date_from, date_to=date_to, page=page, per_page=20,
    )
    sessions, session_total = UserSession.get_active_sessions_admin(
        page=session_page, per_page=20,
    )

    return render_template(
        'admin/user_security.html',
        admin=get_current_admin(),
        history=history, total=total, page=page,
        sessions=sessions, session_total=session_total,
        session_page=session_page,
        search=search, event_type=event_type, status=status,
        date_from=date_from, date_to=date_to,
    )


@admin_bp.route('/users/<int:user_id>/login-history')
@admin_required
def user_login_history(user_id):
    """Login history for one specific student."""
    user = User.get_by_id(user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.user_security'))

    page = int(request.args.get('page', 1))
    event_type = request.args.get('event_type') or None
    date_from = request.args.get('date_from') or None
    date_to = request.args.get('date_to') or None

    history, total = LoginHistory.get_all(
        user_id=user_id, event_type=event_type,
        date_from=date_from, date_to=date_to, page=page, per_page=20,
    )
    active_sessions = UserSession.get_active_sessions_for_user(user_id)
    audit_logs, audit_total = _audit_logs_for_target('user_session', user_id)

    return render_template(
        'admin/user_login_history.html',
        admin=get_current_admin(), user=user,
        history=history, total=total, page=page,
        active_sessions=active_sessions,
        event_type=event_type, date_from=date_from, date_to=date_to,
    )


@admin_bp.route('/users/<int:user_id>/revoke-session/<int:session_id>', methods=['POST'])
@admin_required
@csrf_protect
def revoke_user_session(user_id, session_id):
    """Admin ends one session. The action is written to the audit log."""
    user = User.get_by_id(user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.user_security'))

    row = UserSession.get_by_id(session_id)
    if not row or row.get('user_id') != user_id:
        flash('Session not found or does not belong to this user.', 'danger')
        return redirect(url_for('admin.user_security'))

    try:
        admin = get_current_admin()
        now = datetime.now()
        ip = _admin_client_ip()
        with immediate_transaction() as conn:
            UserSession.revoke(
                conn, session_id,
                'Session revoked by administrator (%s)' % (admin.get('name') if admin else 'unknown'),
                now,
                ended_by_admin=True,
            )
            try:
                LoginHistory.create(
                    conn, user_id, LoginHistory.EVENT_ADMIN_REVOKED,
                    device_description=row.get('device_name'),
                    ip_address=ip,
                    result=LoginHistory.RESULT_INFO,
                    related_session_id=session_id,
                )
            except Exception:
                pass
            try:
                AdminAuditLog.log(
                    conn,
                    admin['id'] if admin else None,
                    'revoke_user_session',
                    'user_session', session_id,
                    'Ended session for user %s (%s)' % (user.get('name'), user.get('id')),
                    ip,
                )
            except Exception:
                pass
        revoked = True
        flash('Session ended and the change was audited.', 'success')
    except Exception:
        revoked = False
        flash('Could not end that session. Please try again.', 'danger')

    # No email notification for individual session revocation.
    # The affected user is notified via an in-browser popup the next time
    # their session-token is checked (see utils/session_status.py and the
    # JS polling in base.html).  Bulk revocation (revoke_all_user_sessions)
    # still sends the standard "session terminated" email.

    return redirect(url_for('admin.user_security'))


@admin_bp.route('/users/<int:user_id>/revoke-all-sessions', methods=['POST'])
@admin_required
@csrf_protect
def revoke_all_user_sessions(user_id):
    user = User.get_by_id(user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin.user_security'))

    try:
        admin = get_current_admin()
        ip = _admin_client_ip()
        now = datetime.now()
        with immediate_transaction() as conn:
            changed = UserSession.revoke_all_for_user(
                conn, user_id,
                'All sessions revoked by administrator (%s)' % (admin.get('name') if admin else 'unknown'),
                when=now,
                ended_by_admin=True,
            )
            try:
                LoginHistory.create(
                    conn, user_id, LoginHistory.EVENT_ADMIN_REVOKED,
                    ip_address=ip, result=LoginHistory.RESULT_INFO,
                    failure_reason='All sessions revoked by administrator',
                )
            except Exception:
                pass
            try:
                AdminAuditLog.log(
                    conn,
                    admin['id'] if admin else None,
                    'revoke_all_user_sessions',
                    'user', user_id,
                    'Ended all sessions for user %s (%s)' % (user.get('name'), user.get('id')),
                    ip,
                )
            except Exception:
                pass
        revoked = True
        flash('%d session(s) ended and the change was audited.' % changed, 'success')
    except Exception:
        revoked = False
        flash('Could not end the sessions. Please try again.', 'danger')

    # No email notification for session revocation — neither individual nor bulk.
    # The affected user is notified via an in-browser popup the next time
    # their session-token is checked (see utils/session_status.py and the
    # JS polling in base.html).  All email channels remain for OTP, password
    # resets, and account-deletion notifications.

    return redirect(url_for('admin.user_security'))


@admin_bp.route('/user-security/audit-log')
@admin_required
def admin_audit_log():
    """Administrative audit trail of security actions."""
    page = int(request.args.get('page', 1))
    action = request.args.get('action') or None
    logs, total = AdminAuditLog.get_all(page=page, per_page=20, action=action)
    return render_template(
        'admin/audit_log.html', admin=get_current_admin(),
        logs=logs, total=total, page=page, action=action,
    )


@admin_bp.route('/user-security/failed-attempts')
@admin_required
def failed_attempts():
    """Recent failed login attempts (passwords are never stored)."""
    page = int(request.args.get('page', 1))
    search = request.args.get('search', '')
    attempts, total = FailedLoginAttempt.get_recent(
        page=page, per_page=25, email=search or None,
    )
    return render_template(
        'admin/failed_attempts.html', admin=get_current_admin(),
        attempts=attempts, total=total, page=page, search=search,
    )


def _audit_logs_for_target(target_type, target_id, limit=25):
    conn = None
    cursor = None
    try:
        from utils.database import get_connection, get_cursor, close_cursor
        conn = get_connection()
        cursor = get_cursor(conn)
        cursor.execute(
            "SELECT * FROM admin_audit_log WHERE target_type = ? AND target_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (target_type, target_id, limit),
        )
        rows = cursor.fetchall()
        return rows, len(rows)
    except Exception:
        return [], 0
    finally:
        close_cursor(cursor)
        conn.close()

