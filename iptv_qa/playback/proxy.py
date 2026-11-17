"""Bounded upstream retrieval and HLS URI rewriting."""
import ssl
from urllib import request as urlrequest
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from .constants import BLOCKED_HOSTS, USER_AGENT, UPSTREAM_TIMEOUT, URI_ATTR


def is_blocked_url(url):
    return (urlsplit(url).hostname or '').lower() in BLOCKED_HOSTS

def fetch_upstream(url, headers, max_bytes, range_header=None):
    request_headers = {'User-Agent': headers.get('user_agent') or USER_AGENT, 'Accept': '*/*'}
    if headers.get('referer'):
        request_headers['Referer'] = headers['referer']
    if range_header:
        request_headers['Range'] = range_header
    request = urlrequest.Request(url, headers=request_headers)
    context = None
    if urlsplit(url).scheme == 'https':
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = ssl.create_default_context()
    try:
        response = urlrequest.urlopen(request, timeout=UPSTREAM_TIMEOUT, context=context)
    except URLError as error:
        if hasattr(error, 'close'):
            error.close()
        raise
    with response as handle:
        data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError('upstream object too large')
        return handle.getcode(), handle.headers, data, handle.geturl()

def rewrite_playlist(text, base_url, headers, register):
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            lines.append(raw)
        elif line.startswith('#'):
            def sub(match):
                return 'URI="/tv/stream/%d"' % register(urljoin(base_url, match.group(1)), headers)
            lines.append(URI_ATTR.sub(sub, raw))
        else:
            lines.append('/tv/stream/%d' % register(urljoin(base_url, line), headers))
    return '\n'.join(lines) + '\n'
