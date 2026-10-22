"""Device description helpers for login session tracking.

The User-Agent header is parsed to produce a coarse, best-effort description
of the browser, operating system and device class.  Device identification is
never treated as a reliable identity: it is only used to help a user recognise
their own sessions, and the values can be absent or wrong for unusual or
spoofed user agents.  No extra fingerprinting data is collected.
"""

import re


def parse_browser(user_agent):
    """Return a short browser name from a User-Agent string."""
    if not user_agent:
        return 'Unknown Browser'

    ua = user_agent.lower()

    if 'edgios/' in ua or 'edga/' in ua or 'edge/' in ua:
        return 'Edge'
    if 'opr/' in ua or 'opios' in ua:
        return 'Opera'
    if 'opera mini' in ua:
        return 'Opera Mini'
    if 'samsungbrowser' in ua:
        return 'Samsung Browser'
    if 'ucbrowser' in ua:
        return 'UC Browser'
    if 'crios/' in ua:
        return 'Chrome'
    if 'fxios/' in ua:
        return 'Firefox'
    if 'firefox/' in ua or 'focus/' in ua:
        return 'Firefox'
    if 'chrome/' in ua or 'chromium/' in ua:
        return 'Chrome'
    if 'safari/' in ua:
        return 'Safari'
    if 'msie' in ua or 'trident/' in ua:
        return 'Internet Explorer'
    if 'bot' in ua or 'crawl' in ua or 'spider' in ua or 'headless' in ua:
        return 'Automated Agent'
    return 'Unknown Browser'


def parse_os(user_agent):
    """Return a short operating system description."""
    if not user_agent:
        return 'Unknown OS'

    ua = user_agent.lower()

    if 'bot' in ua or 'crawl' in ua or 'spider' in ua:
        return 'Automated Agent'

    if 'iphone os' in ua or 'ios/' in ua or 'iphone' in ua:
        return 'iOS'
    if 'like mac os' in ua and 'android' not in ua:
        return 'iOS'
    if 'ipad' in ua:
        return 'iPadOS'
    if 'android' in ua:
        match = re.search(r'android\s?([\d.]+)', ua)
        if match:
            return f'Android {match.group(1)}'
        return 'Android'
    if 'windows nt 10.0' in ua or 'windows nt 11' in ua:
        return 'Windows 10/11'
    if 'windows nt 6.3' in ua:
        return 'Windows 8.1'
    if 'windows nt 6.2' in ua:
        return 'Windows 8'
    if 'windows nt 6.1' in ua:
        return 'Windows 7'
    if 'windows' in ua:
        return 'Windows'
    if 'mac os x' in ua or 'macos' in ua:
        return 'macOS'
    if 'cros' in ua:
        return 'ChromeOS'
    if 'linux' in ua or 'x11' in ua:
        return 'Linux'
    return 'Unknown OS'


def parse_device_type(user_agent):
    """Return a coarse device class: Mobile, Tablet, Desktop or Automated."""
    if not user_agent:
        return 'Unknown Device'

    ua = user_agent.lower()

    if 'bot' in ua or 'crawl' in ua or 'spider' in ua:
        return 'Automated Agent'
    if 'ipad' in ua or ('android' in ua and 'mobile' not in ua) or 'tablet' in ua:
        return 'Tablet'
    if 'mobi' in ua or 'iphone' in ua or 'android' in ua or 'windows phone' in ua:
        return 'Mobile'
    return 'Desktop'


def describe_device(user_agent):
    """Return a human readable device label such as "Chrome on Windows 10/11"."""
    if not user_agent:
        return 'Unknown device'
    return f'{parse_browser(user_agent)} on {parse_os(user_agent)}'


def get_request_device_info(request):
    """Collect coarse device information from a Flask request object.

    Returns a dict with ``device_name``, ``browser_name``, ``operating_system``
    and ``device_type``.  Only data already available in the request headers is
    used; no fingerprints are built.
    """
    user_agent = request.headers.get('User-Agent', '') or ''
    return {
        'device_name': describe_device(user_agent),
        'browser_name': parse_browser(user_agent),
        'operating_system': parse_os(user_agent),
        'device_type': parse_device_type(user_agent),
    }
