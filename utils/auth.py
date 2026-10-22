from flask import session, flash
from models.user import User
from models.admin import Admin

def get_current_user():
    if 'user_id' in session:
        return User.get_by_id(session['user_id'])
    return None

def get_current_admin():
    if 'admin_id' in session:
        return Admin.get_by_id(session['admin_id'])
    return None
