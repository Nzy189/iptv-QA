"""Concurrent line racing: the first line with a usable manifest and data wins."""
import threading
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


def _probe(index, line, fetch, segment_cap, state):
    try:
        status, _, data, final_url = fetch(line['url'], line['headers'], MAX_PLAYLIST_BYTES)
        if status < 200 or status >= 300 or not _manifest_is_hls(data):
            return
        text = data.decode('utf-8', 'replace')
        media_base = final_url
        if _is_master_playlist(text):
            variant_uri = _first_media_uri(text)
            if variant_uri is None:
                return
            status, _, data, variant_final = fetch(urljoin(final_url, variant_uri),
                                                   line['headers'], MAX_PLAYLIST_BYTES)
            if status < 200 or status >= 300 or not _manifest_is_hls(data):
                return
            text = data.decode('utf-8', 'replace')
            media_base = variant_final
        segment_uri = _first_media_uri(text)
        if segment_uri is None:
            return
        try:
            _, _, segment, _ = fetch(urljoin(media_base, segment_uri), line['headers'], segment_cap)
            if len(segment) < 1:
                return
        except ValueError:
            pass  # exceeding the probe cap still proves the line delivers data
        with state['lock']:
            if state['winner'] is None:
                state['winner'] = {'index': index, 'url': line['url'],
                                   'headers': line['headers'], 'final_url': final_url}
                state['decided'].set()
    except Exception:
        return


def race_lines(lines, budget=10.0, fetch=fetch_upstream, segment_cap=262144):
    """Race rank-ordered lines; return the first playable one, or None.

    A line qualifies when its manifest is HLS over HTTP 2xx and its first
    media object returns data within the overall budget. Losing probes are
    daemon threads and are abandoned once a winner is chosen.
    """
    lines = list(lines or [])
    if not lines:
        return None
    state = {'lock': threading.Lock(), 'winner': None, 'decided': threading.Event()}
    for index, line in enumerate(lines):
        worker = threading.Thread(target=_probe,
                                  args=(index, line, fetch, segment_cap, state),
                                  daemon=True)
        worker.start()
    state['decided'].wait(budget)
    return state['winner']
