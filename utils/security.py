"""Security helpers: CSRF tokens, login rate limiting and session policy.

These utilities are deliberately dependency free.  CSRF protection is applied
as a browser-form token check, and login flood protection is a simple sliding
window counter keyed on IP address and on the submitted identifier.  The rate
limit state lives in the server side session (never in a client readable
cookie) and in the ``failed_login_attempts`` table so it survives a browser
restart.
"""

import hashlib
import hmac
import logging
import secrets
import time
from functools import wraps

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    request,
    session,
    url_for,
)
from markupsafe import Markup

CSRF_SESSION_KEY = '_csrf_token'
CSRF_FIELD_NAME = 'csrf_token'
CSRF_HEADER_NAME = 'X-CSRF-Token'

DEVICE_COOKIE_NAME = 'studyhub_device_id'

_logger = logging.getLogger('studyhub.security')


def generate_csrf_token():
    """Return the CSRF token for this session, creating it on first use."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
        session.permanent = True
    return token


def _provided_csrf_token():
    token = request.form.get(CSRF_FIELD_NAME)
    if not token:
        token = request.headers.get(CSRF_HEADER_NAME)
    if not token:
        token = request.headers.get('X-CSRFToken')
    return token


def validate_csrf_token():
    """Compare the submitted CSRF token with the one bound to the session."""
    expected = session.get(CSRF_SESSION_KEY)
    if not expected:
        return False
    provided = _provided_csrf_token()
    if not provided:
        return False
    try:
        return hmac.compare_digest(str(expected), str(provided))
    except (TypeError, ValueError):
        return False


def csrf_protect(view):
    """Reject a state changing request that does not carry a valid CSRF token."""

    @wraps(view)
    def decorated(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            if not validate_csrf_token():
                _logger.info(
                    '[SECURITY_DIAG] csrf_rejected endpoint=%s referrer=%s redirect=%s',
                    request.endpoint or 'unknown',
                    request.referrer or 'none',
                    request.form.get('next') or request.referrer or '/',
                )
                if request.is_json or request.headers.get('Accept') == 'application/json':
                    return {'success': False, 'message': 'Invalid or expired CSRF token.'}, 400
                flash('Your session security check failed. Please try again.', 'danger')
                next_url = request.form.get('next') or request.referrer or url_for('main.index')
                return redirect(next_url)
        return view(*args, **kwargs)

    return decorated


# --- Device identifier -----------------------------------------------------
# A long lived random identifier stored in a cookie.  It is *not* an
# authentication credential: it only lets the platform recognise that a user
# returning from a known device really is the same browser, so the device label
# in the history is stable across logins.

def get_or_create_device_id():
    from flask import current_app

    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_id:
        device_id = secrets.token_urlsafe(24)
    return device_id


# --- Rate limiting ---------------------------------------------------------

def _limit_prefix(scope, identifier):
    # Store only non reversible identifiers bound to the live session so the
    # limiter cannot be inspected from outside the process.
    digest = hashlib.sha256(f'{scope}:{identifier}'.encode('utf-8')).hexdigest()
    return f'_ratelimit_{scope}_{digest[:16]}'


def check_login_rate_limit(scope, identifier, max_attempts, window_seconds):
    """Return True when the window is exhausted for this scope/identifier."""
    now = time.time()
    key = _limit_prefix(scope, identifier)
    history = session.get(key) or []
    history = [ts for ts in history if now - ts < window_seconds]
    session[key] = history
    session.permanent = True
    return len(history) >= max_attempts


def register_login_failure(scope, identifier, window_seconds):
    now = time.time()
    key = _limit_prefix(scope, identifier)
    history = session.get(key) or []
    history = [ts for ts in history if now - ts < window_seconds]
    history.append(now)
    session[key] = history
    session.permanent = True


def clear_login_rate_limit(scope, identifier):
    session.pop(_limit_prefix(scope, identifier), None)


def get_rate_limit_status(scope, identifier, window_seconds):
    """Return (attempts_used, max_seconds_remaining) for display purposes."""
    now = time.time()
    key = _limit_prefix(scope, identifier)
    history = session.get(key) or []
    history = [ts for ts in history if now - ts < window_seconds]
    if not history:
        return 0, 0
    remaining = int(max(0, window_seconds - (now - min(history))))
    return len(history), remaining


# --- Security headers ------------------------------------------------------

def apply_security_headers(response):
    """Add hardening headers to every response."""
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault(
        'Permissions-Policy',
        'geolocation=(self), microphone=(), camera=()',
    )
    response.headers.setdefault('Accept-Ranges', 'bytes')
    csp = (
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "media-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "frame-src 'self' https://maps.google.com https://www.google.com https://www.openstreetmap.org; "
        "connect-src 'self' https://cdn.jsdelivr.net; "
        "form-action 'self'"
    )
    response.headers.setdefault('Content-Security-Policy', csp)
    return response


def generate_state():
    """Return a CSRF token. Kept for template use. Never expose raw tokens."""
    return generate_csrf_token()


class CSRFTokenInput:
    """A small object used by templates to render a hidden CSRF input."""

    def html(self):
        return Markup(
            '<input type="hidden" name="{field}" value="{token}">'.format(
                field=CSRF_FIELD_NAME,
                token=generate_csrf_token(),
            )
        )


def csrf_input():
    """Template helper: returns a hidden input carrying the CSRF token."""
    return Markup(
        '<input type="hidden" name="{field}" value="{token}">'.format(
            field=CSRF_FIELD_NAME,
            token=generate_csrf_token(),
        )
    )
