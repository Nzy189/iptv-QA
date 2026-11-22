"""Bounded upstream retrieval and HLS URI rewriting."""
import ssl
from urllib import request as urlrequest
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from .constants import BLOCKED_HOSTS, BOM, USER_AGENT, UPSTREAM_TIMEOUT, URI_ATTR


def is_blocked_url(url):
    return (urlsplit(url).hostname or '').lower() in BLOCKED_HOSTS

READ_CHUNK = 65536


def read_capped(handle, limit):
    """Read at most ``limit`` bytes in small chunks.

    ``HTTPResponse.read(n)`` on Python 3.6 allocates ``bytearray(n)`` up
    front, so asking for the 64 MB segment cap reserved 64 MB per request
    even for a 5 MB segment. Reading in 64 KB chunks keeps memory
    proportional to the bytes actually received.
    """
    parts = []
    total = 0
    while total < limit:
        chunk = handle.read(min(READ_CHUNK, limit - total))
        if not chunk:
            break
        parts.append(chunk)
        total += len(chunk)
    return b''.join(parts)


class NotHls(Exception):
    """The upstream answered with content that is not an HLS playlist."""


def fetch_upstream(url, headers, max_bytes, range_header=None, require_playlist=False):
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
        # Playlist requests sniff the first block and abort immediately on
        # non-HLS content (e.g. endless FLV live streams), instead of
        # buffering the whole segment-size cap into memory.
        if require_playlist:
            data = read_capped(handle, 65537)
            probe = data[3:] if data[:3] == BOM else data
            if probe.lstrip(b'\r\n \t')[:7] != b'#EXTM3U':
                raise NotHls('not_hls')
            data += read_capped(handle, max(0, max_bytes + 1 - len(data)))
        else:
            data = read_capped(handle, max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError('upstream object too large')
        return handle.getcode(), handle.headers, data, handle.geturl()

def rewrite_playlist(text, base_url, headers, register, url_template=None, meta=None):
    """Rewrite child URIs to local ids; ``meta`` (e.g. the channel) is attached
    to every registered child, plus ``dur`` for media segments, so later
    segment requests can be logged against their channel and play length."""
    if url_template is None:
        url_template = '/tv/stream/%d'

    def reg(url, dur=None):
        if meta is None:
            return register(url, headers)
        info = dict(meta)
        if dur is not None:
            info['dur'] = dur
        return register(url, headers, info)

    lines = []
    duration = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            lines.append(raw)
        elif line.startswith('#'):
            if line.startswith('#EXTINF:'):
                try:
                    duration = float(line[8:].split(',')[0])
                except ValueError:
                    duration = None

            def sub(match):
                return 'URI="%s"' % (url_template % reg(urljoin(base_url, match.group(1))))
            lines.append(URI_ATTR.sub(sub, raw))
        else:
            lines.append(url_template % reg(urljoin(base_url, line), duration))
            duration = None
    return '\n'.join(lines) + '\n'
