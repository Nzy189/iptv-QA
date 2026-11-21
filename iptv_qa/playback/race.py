"""Concurrent line racing: the first line with a usable manifest and data wins."""
import threading
import time
from urllib.parse import urljoin

from .constants import MAX_PLAYLIST_BYTES
from .proxy import fetch_upstream


def _manifest_is_hls(data):
    if data[:3] == b'\xef\xbb\xbf':
        data = data[3:]
    return data.lstrip(b'\r\n \t')[:7] == b'#EXTM3U'


def _first_media_uri(text):
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith('#'):
            return line
    return None


def _is_master_playlist(text):
    for raw in text.splitlines():
        if raw.strip().startswith('#EXT-X-STREAM-INF'):
            return True
    return False


def _start_media_uri(text):
    """The media URI a live player starts on.

    RFC 8216 6.3.3: a client should not start less than three target
    durations from the end, so AVPlayer and hls.js begin on the
    third-to-last segment. Probing that one also warms the upstream CDN for
    the bytes the player will actually request.
    """
    uris = media_uris(text)
    if not uris:
        return None
    return uris[-3] if len(uris) >= 3 else uris[0]


def media_uris(text):
    return [raw.strip() for raw in text.splitlines()
            if raw.strip() and not raw.strip().startswith('#')]


def _probe(index, line, fetch, segment_cap, state, observe):
    started = time.time()
    ok = False
    try:
        status, headers, data, final_url = fetch(line['url'], line['headers'], MAX_PLAYLIST_BYTES)
        if status < 200 or status >= 300 or not _manifest_is_hls(data):
            return
        probe = {'manifest': {'url': line['url'], 'status': status, 'data': data,
                              'final_url': final_url,
                              'content_type': headers.get('Content-Type') if headers else None}}
        text = data.decode('utf-8', 'replace')
        media_base = final_url
        if _is_master_playlist(text):
            variant_uri = _first_media_uri(text)
            if variant_uri is None:
                return
            variant_url = urljoin(final_url, variant_uri)
            status, headers, data, variant_final = fetch(variant_url, line['headers'], MAX_PLAYLIST_BYTES)
            if status < 200 or status >= 300 or not _manifest_is_hls(data):
                return
            probe['variant'] = {'url': variant_url, 'status': status, 'data': data,
                                'final_url': variant_final,
                                'content_type': headers.get('Content-Type') if headers else None}
            text = data.decode('utf-8', 'replace')
            media_base = variant_final
        segment_uri = _start_media_uri(text)
        if segment_uri is None:
            return
        try:
            _, _, segment, _ = fetch(urljoin(media_base, segment_uri), line['headers'], segment_cap)
            if len(segment) < 1:
                return
        except ValueError:
            pass  # exceeding the probe cap still proves the line delivers data
        ok = True
        with state['lock']:
            if state['winner'] is None:
                state['winner'] = {'index': line.get('slot', index), 'url': line['url'],
                                   'headers': line['headers'], 'final_url': final_url,
                                   'probe': probe}
                state['decided'].set()
    except Exception:
        return
    finally:
        if observe is not None:
            try:
                observe(line['url'], ok, time.time() - started)
            except Exception:
                pass


def race_lines(lines, budget=10.0, fetch=fetch_upstream, segment_cap=65536, observe=None):
    """Race rank-ordered lines; return the first playable one, or None.

    A line qualifies when its manifest is HLS over HTTP 2xx and the media
    object a live player starts on returns data within the overall budget.
    Losing probes are daemon threads and are abandoned once a winner is
    chosen; they still report their outcome to ``observe(url, ok, seconds)``.
    The winner carries the probe's manifest bytes under ``probe`` so the
    caller can serve them without a second upstream round trip; callers
    must drop ``probe`` before caching the winner.
    """
    lines = list(lines or [])
    if not lines:
        return None
    state = {'lock': threading.Lock(), 'winner': None, 'decided': threading.Event()}
    for index, line in enumerate(lines):
        worker = threading.Thread(target=_probe,
                                  args=(index, line, fetch, segment_cap, state, observe),
                                  daemon=True)
        worker.start()
    state['decided'].wait(budget)
    return state['winner']
