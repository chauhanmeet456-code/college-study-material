from flask import session, g
from models.user import User
from models.admin import Admin

_cached_user_attr = '_cached_current_user'
_cached_admin_attr = '_cached_current_admin'


def get_current_user():
    """Return the logged-in user, caching the DB lookup for the request."""
    if 'user_id' not in session:
        return None
    user = getattr(g, _cached_user_attr, None)
    if user is None:
        user = User.get_by_id(session['user_id'])
        g._cached_current_user = user
    return user


def get_current_admin():
    """Return the logged-in admin, caching the DB lookup for the request."""
    if 'admin_id' not in session:
        return None
    admin = getattr(g, _cached_admin_attr, None)
    if admin is None:
        admin = Admin.get_by_id(session['admin_id'])
        g._cached_current_admin = admin
    return admin
