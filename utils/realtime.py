from datetime import datetime, timedelta

from flask import current_app, session
from flask_socketio import SocketIO, join_room

from models.session import UserSession, hash_session_token
from models.user import User

socketio = SocketIO()


@socketio.on('connect')
def authenticate_socket(auth=None):
    user_id = session.get('user_id')
    token = session.get('session_token')
    if not user_id or not token:
        return False

    user = User.get_by_id(user_id)
    if not user or not user.get('is_active'):
        return False

    session_row = UserSession.get_by_token_hash(hash_session_token(token))
    if (
        not session_row
        or session_row.get('status') != UserSession.STATUS_ACTIVE
        or session_row.get('user_id') != user_id
        or _session_expired(session_row)
    ):
        return False

    join_room('user-notifications:{}'.format(user_id))
    return True


def _session_expired(session_row):
    now = datetime.now()
    expires_at = _parse_datetime(session_row.get('expires_at'))
    if expires_at and expires_at < now:
        return True
    last_activity = _parse_datetime(session_row.get('last_activity_at'))
    idle_timeout = current_app.config.get('SESSION_IDLE_TIMEOUT_MINUTES', 60)
    return bool(
        last_activity
        and last_activity < now - timedelta(minutes=idle_timeout)
    )


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def emit_notification(user_id, payload):
    if socketio.server is None:
        return
    socketio.emit(
        'notification:new',
        payload,
        to='user-notifications:{}'.format(user_id),
    )


def init_socketio(app):
    socketio.init_app(app, async_mode='threading', manage_session=False)
    return socketio
