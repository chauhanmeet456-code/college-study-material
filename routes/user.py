from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    jsonify,
)

from config import Config
from models.session import (
    AdminAuditLog,
    LoginHistory,
    UserSession,
    hash_session_token,
)
from models.user import User
from utils.decorators import user_required
from utils.auth import get_current_user
from utils.device import get_request_device_info
from utils.helpers import allowed_image_file, generate_safe_filename
from utils.otp import OTPManager
from utils.security import csrf_protect
import os

user_bp = Blueprint('user', __name__)


@user_bp.route('/profile')
@user_required
def profile():
    user = User.get_by_id(session['user_id'])
    from models.material import Favorite
    _, fav_total = Favorite.get_by_user(session['user_id'], page=1, per_page=1)
    user['favorites_count'] = fav_total
    return render_template('user/profile.html', user=user, current_user=get_current_user())

@user_bp.route('/favorites')
@user_required
def favorites():
    from models.material import Favorite
    page = int(request.args.get('page', 1))
    materials, total = Favorite.get_by_user(session['user_id'], page=page, per_page=12)
    return render_template('user/favorites.html', materials=materials, total=total, page=page, current_user=get_current_user())

@user_bp.route('/profile/update', methods=['POST'])
@user_required
@csrf_protect
def update_profile():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Name is required.', 'danger')
        return redirect(url_for('user.profile'))
    User.update(session['user_id'], name=name)
    session['user_name'] = name
    flash('Profile updated successfully!', 'success')
    return redirect(url_for('user.profile'))

@user_bp.route('/profile/change-password', methods=['POST'])
@user_required
@csrf_protect
def change_password():
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')
    user = User.get_by_id(session['user_id'])
    if not User.verify_password(user, current_password):
        flash('Current password is incorrect.', 'danger')
    elif new_password != confirm_password:
        flash('New passwords do not match.', 'danger')
    elif len(new_password) < 6:
        flash('Password must be at least 6 characters.', 'danger')
    else:
        User.update_password(session['user_id'], new_password)
        flash('Password changed successfully!', 'success')
    return redirect(url_for('user.profile'))

@user_bp.route('/profile/upload-image', methods=['POST'])
@user_required
@csrf_protect
def upload_profile_image():
    if 'profile_image' not in request.files:
        flash('No file selected.', 'danger')
        return redirect(url_for('user.profile'))
    file = request.files['profile_image']
    if file.filename == '':
        flash('No file selected.', 'danger')
        return redirect(url_for('user.profile'))
    if file and allowed_image_file(file.filename):
        filename = generate_safe_filename(file.filename)
        upload_dir = os.path.join(Config.UPLOAD_FOLDER, 'profiles')
        os.makedirs(upload_dir, exist_ok=True)
        file.save(os.path.join(upload_dir, filename))
        User.update(session['user_id'], profile_image=f"profiles/{filename}")
        flash('Profile image updated!', 'success')
    else:
        flash('Invalid file type. Please upload an image.', 'danger')
    return redirect(url_for('user.profile'))

@user_bp.route('/profile/delete-account', methods=['POST'])
@user_required
@csrf_protect
def delete_account():
    password = request.form.get('password', '')
    user = User.get_by_id(session['user_id'])
    
    if not user or not User.verify_password(user, password):
        flash('Incorrect password. Please try again.', 'danger')
        return redirect(url_for('user.profile'))
    
    otp_code = OTPManager.generate_otp()
    OTPManager.store_otp(user['email'], otp_code, 'delete_account')
    
    if Config.MAIL_USERNAME and Config.MAIL_PASSWORD:
        OTPManager.send_otp_email(user['email'], otp_code, 'delete_account')
    
    session['delete_account_email'] = user['email']
    session.pop('user_id', None)
    session.pop('user_name', None)
    session.permanent = True
    flash('OTP sent to your email. Please verify to confirm account deletion.', 'warning')
    return redirect(url_for('user.confirm_delete_account'))

@user_bp.route('/profile/confirm-delete-account', methods=['GET', 'POST'])
@csrf_protect
def confirm_delete_account():
    email = session.get('delete_account_email')
    if not email:
        flash('Please request account deletion first.', 'warning')
        return redirect(url_for('user.profile'))

    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()

        if not otp:
            flash('Please enter the OTP.', 'danger')
            return redirect(url_for('user.confirm_delete_account'))

        if OTPManager.verify_otp(email, otp, 'delete_account'):
            user = User.get_by_email(email)
            if user:
                user_id = user['id']
                session.clear()
                deleted = User.delete(user_id)
                # Send confirmation email only after the deletion is committed.
                email_sent = False
                if deleted:
                    try:
                        from utils.notifications import send_template_notification
                        result = send_template_notification(
                            'account_deleted_self',
                            user['email'],
                            related_id=user_id,
                            dedupe_key='account_deleted_self:%d' % user_id,
                        )
                        email_sent = result != 'not_configured' and result != 'queued'
                    except Exception:
                        pass
                if not email_sent:
                    flash(
                        'Your account has been deleted successfully. '
                        'However, we could not send the confirmation email to '
                        + user['email'] + '. '
                        'If you did not receive it, please contact support.',
                        'warning',
                    )
                else:
                    flash('Your account has been deleted successfully.', 'success')
                return redirect(url_for('main.index'))
            else:
                flash('User not found.', 'danger')
                session.pop('delete_account_email', None)
                return redirect(url_for('user.profile'))
        else:
            flash('Invalid or expired OTP.', 'danger')
            return redirect(url_for('user.confirm_delete_account'))

    return render_template('user/confirm_delete_account.html', current_user=get_current_user(), email=email)


# ---------------------------------------------------------------------------
# Login history and active sessions (student side)
# ---------------------------------------------------------------------------

def _current_session_row():
    """The row describing the session this request is using, if any."""
    token = session.get('session_token')
    if not token:
        return None
    try:
        return UserSession.get_by_token_hash(hash_session_token(token))
    except Exception:
        return None


@user_bp.route('/account/login-history')
@user_required
def login_history():
    """A user's own login history, active sessions and revocation controls."""
    user_id = session['user_id']
    page = int(request.args.get('page', 1))
    event_type = request.args.get('event_type') or None

    history, total = LoginHistory.get_for_user(
        user_id, page=page, per_page=15, event_type=event_type,
    )
    active_sessions = UserSession.get_active_sessions_for_user(user_id)
    current_session = _current_session_row()

    # Days covered by the events actually shown, so the retention promise in
    # the template stays truthful.
    oldest_shown = None
    if history:
        oldest_shown = history[-1].get('created_at')

    return render_template(
        'user/login_history.html',
        history=history,
        total=total,
        page=page,
        event_type=event_type,
        active_sessions=active_sessions,
        current_session=current_session,
        retention_days=Config.LOGIN_HISTORY_RETENTION_DAYS,
        current_user=get_current_user(),
    )


@user_bp.route('/account/login-history/revoke/<int:session_id>', methods=['POST'])
@user_required
@csrf_protect
def revoke_session(session_id):
    """Revoke one of this user's own sessions.

    The session in use right now is not revocable here, so a user cannot cut
    themselves off by accident.
    """
    user_id = session['user_id']
    current = _current_session_row()

    if current and current.get('id') == session_id:
        flash('You cannot end the session you are currently using.', 'warning')
        return redirect(url_for('user.login_history'))

    row = UserSession.get_by_id(session_id)
    if not row or row.get('user_id') != user_id:
        flash('Session not found.', 'danger')
        return redirect(url_for('user.login_history'))

    try:
        from utils.database import immediate_transaction

        now = None
        from datetime import datetime as _dt
        now = _dt.now()
        device = get_request_device_info(request)
        with immediate_transaction() as conn:
            UserSession.revoke(
                conn, session_id,
                'Session ended by the user from the active sessions list', now,
            )
            LoginHistory.create(
                conn, user_id, LoginHistory.EVENT_SESSION_REVOKED,
                device_description=device['device_name'],
                ip_address=request.remote_addr,
                result=LoginHistory.RESULT_INFO,
                related_session_id=session_id,
            )
        flash('The selected session was ended.', 'success')
    except Exception:
        flash('Could not end that session. Please try again.', 'danger')

    return redirect(url_for('user.login_history'))


@user_bp.route('/account/login-history/revoke-all', methods=['POST'])
@user_required
@csrf_protect
def revoke_all_sessions():
    """End every active session except the one being used now."""
    user_id = session['user_id']
    current = _current_session_row()

    try:
        from utils.database import immediate_transaction
        from datetime import datetime as _dt

        now = _dt.now()
        device = get_request_device_info(request)
        with immediate_transaction() as conn:
            changed = UserSession.revoke_all_for_user(
                conn, user_id,
                'All other sessions ended by the user',
                exclude_id=current.get('id') if current else None,
                when=now,
            )
            LoginHistory.create(
                conn, user_id, LoginHistory.EVENT_SESSION_REVOKED,
                device_description=device['device_name'],
                ip_address=request.remote_addr,
                result=LoginHistory.RESULT_INFO,
                failure_reason='All other sessions ended by user',
            )
        if changed:
            flash('%d other session(s) ended.' % changed, 'success')
        else:
            flash('There were no other active sessions.', 'info')
    except Exception:
        flash('Could not end the other sessions. Please try again.', 'danger')

    return redirect(url_for('user.login_history'))


# ---------------------------------------------------------------------------
# Session-status polling endpoint (used by the browser-side popup mechanism)
# ---------------------------------------------------------------------------

@user_bp.route('/account/session-status')
def session_status():
    """Return JSON describing whether the current browser session is still valid.

    This endpoint is polled by client-side JavaScript (every few seconds) so
    that a browser tab detects, in near-real-time, when an administrator has
    revoked *this* user's session.  When that happens the frontend shows a
    modal popup with the message:

        "Admin has ended your session. Please log in again."

    Only the affected user (the one whose ``session_token`` was revoked)
    receives ``valid: false`` — every other browser either has a different
    token or no token at all and is unaffected.

    The check is server-side: it reads the signed Flask ``session_token``
    cookie and looks the corresponding row up in ``user_sessions``.  This
    satisfies the requirement not to rely solely on flash messages (the
    session is already invalidated server-side *before* the redirect, so a
    flash message may never be rendered).
    """
    token = session.get('session_token')
    if not token:
        # Not a logged-in user — nothing to poll.  Returning valid:true keeps
        # the JS idle; returning false would cause an infinite redirect loop
        # for anonymous visitors.
        return jsonify({'valid': True, 'reason': 'no_session'})

    row = UserSession.get_by_token_hash(hash_session_token(token))
    if not row:
        return jsonify({'valid': False, 'reason': 'missing'})

    status = row.get('status')
    if status == UserSession.STATUS_ACTIVE:
        return jsonify({'valid': True, 'reason': 'active'})

    if status == UserSession.STATUS_REVOKED:
        ended_by_admin = row.get('ended_by_admin')
        if ended_by_admin == 1 or ended_by_admin is True:
            return jsonify({'valid': False, 'reason': 'revoked_by_admin'})
        return jsonify({'valid': False, 'reason': 'revoked'})

    if status == UserSession.STATUS_EXPIRED:
        return jsonify({'valid': False, 'reason': 'expired'})

    if status == UserSession.STATUS_LOGGED_OUT:
        return jsonify({'valid': False, 'reason': 'logged_out'})

    return jsonify({'valid': False, 'reason': status or 'unknown'})
