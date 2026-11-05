import os
import html
import ipaddress
import re
from urllib.parse import urlsplit

from flask import current_app, request, url_for


PUBLIC_ENDPOINTS = {
    'main.index',
    'main.subjects',
    'main.subject_details',
    'main.material_details',
    'main.notes_page',
    'main.assignments_page',
    'main.books_page',
    'main.practicals_page',
    'main.question_papers_page',
    'main.important_questions_page',
    'main.syllabus_page',
    'main.search',
    'main.about',
    'main.cookie_policy',
    'main.contact',
}

PAGE_DESCRIPTIONS = {
    'main.index': (
        'Access notes, assignments, books, practicals, question papers, '
        'and other study materials for your course.'
    ),
    'main.subjects': 'Browse StudyHub subjects and find relevant course materials.',
    'main.notes_page': 'Browse notes and study resources available on StudyHub.',
    'main.assignments_page': 'Find assignments and coursework resources on StudyHub.',
    'main.books_page': 'Browse books and recommended reading available on StudyHub.',
    'main.practicals_page': 'Find practicals and lab resources on StudyHub.',
    'main.question_papers_page': 'Browse question papers and exam preparation resources.',
    'main.important_questions_page': 'Review important questions and exam preparation materials.',
    'main.syllabus_page': 'Browse course syllabus and curriculum resources on StudyHub.',
    'main.search': 'Search StudyHub for notes, books, assignments, and other study materials.',
    'main.about': 'Learn about StudyHub and its college study material resources.',
    'main.cookie_policy': 'Learn how StudyHub uses necessary and optional cookies.',
    'main.contact': 'Contact StudyHub for help with study materials and your account.',
}


def _public_origin():
    configured = (current_app.config.get('PUBLIC_BASE_URL') or '').strip()
    if not configured:
        return ''

    try:
        parsed = urlsplit(configured)
        port = parsed.port
    except ValueError:
        return ''
    if (
        not parsed.netloc
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ('', '/')
    ):
        return ''

    hostname = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None

    if parsed.scheme == 'http':
        is_loopback_origin = hostname == 'localhost' or (
            address is not None and address.is_loopback
        )
        try:
            request_address = ipaddress.ip_address(request.remote_addr or '')
            is_local_request = request_address.is_loopback
        except ValueError:
            is_local_request = False
        request_hostname = ''
        try:
            request_hostname = urlsplit(
                '//' + request.host
            ).hostname or ''
            request_address = ipaddress.ip_address(request_hostname)
            is_local_host = request_address.is_loopback
        except ValueError:
            is_local_host = request_hostname.lower() == 'localhost'
        if (
            not is_loopback_origin
            or not (
                current_app.testing
                or current_app.debug
                or (is_local_request and is_local_host)
            )
        ):
            return ''
    elif parsed.scheme != 'https':
        return ''

    if parsed.scheme == 'https' and (
        hostname == 'localhost'
        or hostname.endswith((
            '.localhost', '.local', '.test', '.invalid', '.internal',
            '.intranet', '.lan', '.home', '.corp', '.private', '.intra',
        ))
        or (hostname.endswith('.example') and not current_app.testing)
        or (address is not None and (
            address.is_private or address.is_loopback or address.is_reserved
        ))
        or (address is None and '.' not in hostname)
    ):
        return ''

    normalized_host = f'[{hostname}]' if ':' in hostname else hostname
    return parsed.scheme + '://' + normalized_host + (
        f':{port}' if port is not None else ''
    )


def absolute_public_url(path):
    origin = _public_origin()
    if not origin or not isinstance(path, str) or not path.startswith('/'):
        return ''
    return origin + path


def build_seo_context():
    endpoint = request.endpoint or ''
    is_public = endpoint in PUBLIC_ENDPOINTS

    if is_public:
        try:
            path = url_for(endpoint, **(request.view_args or {}))
        except (TypeError, ValueError):
            path = url_for('main.index')
    else:
        path = url_for('main.index')

    origin = _public_origin()
    image_filename = 'images/og-studyhub.png'
    image_path = url_for('static', filename=image_filename)
    image_file = os.path.join(
        current_app.static_folder or '',
        image_filename.replace('/', os.sep),
    )
    return {
        'is_public_page': is_public,
        'canonical_url': absolute_public_url(path),
        'og_image_url': (
            absolute_public_url(image_path)
            if os.path.isfile(image_file)
            else ''
        ),
        'default_meta_description': PAGE_DESCRIPTIONS.get(
            endpoint,
            'Explore college study materials and course resources on StudyHub.',
        ),
    }


def clean_meta_description(value, limit=300):
    text = html.unescape(str(value or ''))
    text = re.sub(r'<[^>]*>', ' ', text)
    text = ' '.join(text.split())
    return text[:limit]
