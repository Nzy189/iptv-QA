"""Observe decoded video, rather than HTTP response time; Python 3.6+."""
import collections
from http.cookiejar import CookieJar
import subprocess
import threading
import time
from urllib.parse import urljoin
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request


class HLSObserver:
    """Compare actual media playlists, including master-playlist redirects."""
    def __init__(self, url, headers, timeout=10):
        self.url, self.headers, self.timeout = url, headers, timeout
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def snapshot(self):
        url = self.url
        for _ in range(4):
            with self.opener.open(Request(url, headers=self.headers), timeout=self.timeout) as response:
                data = response.read(512 * 1024 + 1)
                base = response.geturl()
            if len(data) > 512 * 1024:
                raise ValueError('Oversized HLS manifest')
            lines = [line.strip() for line in data.decode('utf-8-sig').splitlines() if line.strip()]
            if not lines or lines[0] != '#EXTM3U':
                raise ValueError('Not HLS')
            if any(line.startswith('#EXT-X-STREAM-INF:') for line in lines):
                variant = None
                for index, line in enumerate(lines):
                    if line.startswith('#EXT-X-STREAM-INF:'):
                        variant = next((x for x in lines[index + 1:] if not x.startswith('#')), None)
                        break
                if not variant:
                    raise ValueError('Empty HLS master')
                url = urljoin(base, variant)
                continue
            segments = [urljoin(base, line) for line in lines if not line.startswith('#')]
            if not segments or not any(line.startswith('#EXTINF:') for line in lines):
                raise ValueError('Empty media playlist')
            sequence = next((int(line.split(':', 1)[1]) for line in lines if line.startswith('#EXT-X-MEDIA-SEQUENCE:')), None)
            duration = next((float(line.split(':', 1)[1]) for line in lines if line.startswith('#EXT-X-TARGETDURATION:')), 0)
            if not 0 <= duration <= 60:
                raise ValueError('Unreasonable HLS segment duration')
            return {'sequence': sequence, 'segments': segments, 'target_duration': duration,
                    'ended': '#EXT-X-ENDLIST' in lines}
        raise ValueError('Nested HLS master limit')


def hls_advanced(first, last):
    if first['ended'] or last['ended']:
        return False
    a, b = first['sequence'], last['sequence']
    if a is not None and b is not None:
        return b > a
    return bool(set(last['segments']) - set(first['segments']))


def media_error(message, code):
    message = message.lower()
    if '401' in message or '403' in message:
        return 'access_denied'
    if '404' in message:
        return 'not_found'
    if 'connection refused' in message:
        return 'connection_refused'
    if 'resolve' in message:
        return 'dns_error'
    return 'media_error' if code else ''


def observe_video(args, timeout, target, startup_limit, stall_limit, wall_window=0):
    """Only video is mapped by the caller. First-frame timing is a progress sample
    upper bound, not network RTT. Long checks must advance for real wall time,
    so downloading a cached HLS window quickly cannot satisfy the observation.
    """
    started = time.monotonic()
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lock = threading.Lock()
    state = {'frames': 0, 'decoded_seconds': 0.0, 'first_frame_seconds': None,
             'last_frame_at': None, 'max_frame_gap': 0.0}
    errors = collections.deque(maxlen=20)

    def read_progress():
        for raw in iter(process.stdout.readline, b''):
            key, _, value = raw.decode('utf-8', 'replace').strip().partition('=')
            try:
                now = time.monotonic() - started
                with lock:
                    if key == 'frame' and int(value) > state['frames']:
                        if state['first_frame_seconds'] is None:
                            state['first_frame_seconds'] = now
                        if state['last_frame_at'] is not None:
                            state['max_frame_gap'] = max(state['max_frame_gap'], now - state['last_frame_at'])
                        state['last_frame_at'] = now
                        state['frames'] = int(value)
                    elif key == 'out_time_us':
                        state['decoded_seconds'] = max(state['decoded_seconds'], float(value) / 1000000)
            except ValueError:
                pass

    def read_errors():
        for line in iter(process.stderr.readline, b''):
            errors.append(line.decode('utf-8', 'replace')[:2048])

    readers = [threading.Thread(target=read_progress), threading.Thread(target=read_errors)]
    for reader in readers:
        reader.daemon = True
        reader.start()
    stopped, failure = False, ''
    try:
        while True:
            elapsed = time.monotonic() - started
            with lock:
                current = dict(state)
            first, last = current['first_frame_seconds'], current['last_frame_at']
            if first is None and elapsed > startup_limit:
                failure = 'startup_too_slow'
                break
            # HLS arrives in segment-sized bursts. Silence between downloads
            # is not a playback stall while decoded video still covers playout.
            if (first is not None and elapsed - last > stall_limit
                    and elapsed - first - current['decoded_seconds'] > stall_limit):
                failure = 'video_stalled'
                break
            if wall_window and first is not None and elapsed - first >= wall_window and current['decoded_seconds'] >= target - .2:
                stopped = True
                break
            if process.poll() is not None:
                break
            if elapsed >= timeout:
                failure = 'timeout'
                break
            time.sleep(.05)
    finally:
        observation_ended = time.monotonic()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        for reader in readers:
            reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()
    elapsed = time.monotonic() - started
    with lock:
        result = dict(state)
    first = result['first_frame_seconds']
    observed = max(0, observation_ended - started - first) if first is not None else 0
    result.pop('last_frame_at')
    result.update(seconds=round(elapsed, 3), observed_seconds=round(observed, 3),
                  code=process.returncode)
    if not failure and first is not None and first > startup_limit:
        failure = 'startup_too_slow'
    if not failure and not stopped and process.returncode:
        failure = media_error(''.join(errors), process.returncode)
    if not failure and (not result['frames'] or result['decoded_seconds'] < target - .2):
        failure = 'insufficient_video'
    if not failure and wall_window and (not stopped or observed < wall_window):
        failure = 'stream_ended_early'
    if (not failure and wall_window and result['decoded_seconds'] < observed * .9
            and observed - result['decoded_seconds'] > 3):
        failure = 'slow_video_delivery'
    # A few video frames plus a large timestamp jump must not pass as live TV.
    if not failure and result['frames'] < max(2, target * 2):
        failure = 'insufficient_video'
    result.update(ok=not failure, error=failure)
    for key in ('decoded_seconds', 'first_frame_seconds', 'max_frame_gap'):
        if result[key] is not None:
            result[key] = round(result[key], 3)
    return result
