import ipaddress
from urllib.parse import urlsplit, urlunsplit

from authlib.integrations.flask_client import OAuth
from flask import current_app, url_for

from utils.seo import absolute_public_url


def init_google_oauth(app):
    oauth = OAuth(app)
    client_id = (app.config.get('GOOGLE_CLIENT_ID') or '').strip()
    client_secret = (app.config.get('GOOGLE_CLIENT_SECRET') or '').strip()

    if client_id and client_secret:
        oauth.register(
            name='google',
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url=(
                'https://accounts.google.com/.well-known/openid-configuration'
            ),
            client_kwargs={'scope': 'openid email profile'},
        )
        app.extensions['google_oauth_client'] = oauth.create_client('google')
    else:
        app.extensions['google_oauth_client'] = None


def google_callback_uri():
    callback_path = url_for('auth.google_callback')
    configured_origin = (
        current_app.config.get('PUBLIC_BASE_URL') or ''
    ).strip().rstrip('/')

    if configured_origin:
        try:
            public_callback = absolute_public_url(callback_path)
        except ValueError:
            public_callback = ''
        if public_callback:
            return public_callback

        try:
            parts = urlsplit(configured_origin)
            port = parts.port
        except ValueError:
            return ''
        hostname = (parts.hostname or '').lower()
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = hostname == 'localhost'

        if (
            parts.scheme == 'http'
            and is_loopback
            and hostname
            and not parts.username
            and not parts.password
            and parts.path in ('', '/')
            and not parts.query
            and not parts.fragment
        ):
            host = f'[{hostname}]' if ':' in hostname else hostname
            netloc = host + (f':{port}' if port is not None else '')
            return urlunsplit((
                parts.scheme,
                netloc,
                callback_path,
                '',
                '',
            ))
        return ''

    if current_app.testing or current_app.debug:
        return url_for('auth.google_callback', _external=True)
    return ''


def google_oauth_enabled():
    return bool(
        current_app.extensions.get('google_oauth_client')
        and (current_app.config.get('GOOGLE_CLIENT_ID') or '').strip()
        and (current_app.config.get('GOOGLE_CLIENT_SECRET') or '').strip()
        and google_callback_uri()
    )
