from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


COOKIE_NAME = 'studyhub_cookie_consent'
MAX_AGE = 60 * 60 * 24 * 365


def _serializer():
    from flask import current_app

    return URLSafeTimedSerializer(
        current_app.secret_key,
        salt='studyhub-cookie-consent-v1',
    )


def get_cookie_preferences(cookie_value):
    if not cookie_value:
        return None
    try:
        preferences = _serializer().loads(cookie_value, max_age=MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(preferences, dict):
        return None
    return {
        'necessary': True,
        'preferences': bool(preferences.get('preferences', False)),
    }


def serialize_cookie_preferences(preferences):
    return _serializer().dumps({
        'necessary': True,
        'preferences': bool(preferences.get('preferences', False)),
    })
