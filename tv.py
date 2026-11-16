#!/usr/bin/env python3
"""Password-gated web TV player mounted inside the dashboard service."""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError
from urllib.parse import unquote, urljoin, urlsplit

from iptv_checker import parse_playlist

TV_COOKIE = 'tvsess'
STATIC_FILES = {'/tv/': ('index.html', 'text/html; charset=utf-8'),
                '/tv/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                '/tv/style.css': ('style.css', 'text/css; charset=utf-8'),
                '/tv/hls.min.js': ('hls.min.js', 'application/javascript; charset=utf-8'),
                '/tv/LICENSE-hlsjs.txt': ('LICENSE-hlsjs.txt', 'text/plain; charset=utf-8')}
PLAYER_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; media-src 'self' blob:; "
              "connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
URI_ATTR = re.compile(r'URI="([^"]*)"')
PLAY_PATH = re.compile(r'^/tv/play/(\d+)(?:/(\d+))?$')
STREAM_PATH = re.compile(r'^/tv/stream/(\d+)$')
TRANSCODE_PATH = re.compile(r'^/tv/transcode/(\d+)(?:/(\d+))?$')
TSEG_PATH = re.compile(r'^/tv/tseg/([0-9-]+)/([A-Za-z0-9._-]+)$')
SEGMENT_NAME = re.compile(r'^[A-Za-z0-9._-]+$')
BLOCKED_HOSTS = ('169.254.169.254', 'metadata.google.internal')
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
MAX_PLAYLIST_BYTES = 32 * 1024 * 1024
MAX_SEGMENT_BYTES = 64 * 1024 * 1024
MAX_BODY_BYTES = 64 * 1024
UPSTREAM_TIMEOUT = 20
TRANSCODE_START_TIMEOUT = 15
BOM = b'\xef\xbb\xbf'


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
        return handle.getcode(), handle.headers, data


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


class Registry:
    """Maps short ids to (url, headers) pairs discovered inside known playlists."""

    def __init__(self, limit=8192):
        self.lock = threading.Lock()
        self.by_rid = OrderedDict()
        self.by_url = {}
        self.counter = 0
        self.limit = limit

    @staticmethod
    def _key(url, headers):
        return (url, json.dumps(headers, sort_keys=True))

    def register(self, url, headers):
        key = self._key(url, headers)
        with self.lock:
            rid = self.by_url.get(key)
            if rid is not None:
                self.by_rid.move_to_end(rid)
                return rid
            self.counter += 1
            rid = self.counter
            self.by_rid[rid] = (url, dict(headers))
            self.by_url[key] = rid
            while len(self.by_rid) > self.limit:
                _, (old_url, old_headers) = self.by_rid.popitem(last=False)
                self.by_url.pop(self._key(old_url, old_headers), None)
            return rid

    def resolve(self, rid):
        with self.lock:
            pair = self.by_rid.get(rid)
            if pair is not None:
                self.by_rid.move_to_end(rid)
            return pair


class Sessions:
    def __init__(self, limit=64):
        self.lock = threading.Lock()
        self.data = OrderedDict()
        self.limit = limit

    def get(self, token):
        with self.lock:
            session = self.data.get(token)
            if session is not None:
                self.data.move_to_end(token)
            return session

    def set_source(self, token, session):
        with self.lock:
            self.data[token] = session
            self.data.move_to_end(token)
            while len(self.data) > self.limit:
                self.data.popitem(last=False)


class Transcoders:
    """Keeps at most `limit` on-demand ffmpeg HLS transcodes; stops idle ones."""

    def __init__(self, ffmpeg, limit=2, idle_seconds=90):
        self.lock = threading.Lock()
        self.ffmpeg = ffmpeg
        self.limit = limit
        self.idle = idle_seconds
        self.active = {}
        self.base = None

    def _ensure_base(self):
        if self.base is None:
            self.base = Path(tempfile.mkdtemp(prefix='iptv-tv-'))
        return self.base

    def get(self, index):
        with self.lock:
            entry = self.active.get(index)
            if entry:
                entry['last_used'] = time.time()
            return entry

    def start(self, key, url, headers):
        with self.lock:
            entry = self.active.get(key)
            if entry:
                entry['last_used'] = time.time()
                if entry['process'].poll() is None:
                    return entry
                self._stop_locked(key)
            while len(self.active) >= self.limit:
                oldest = min(self.active, key=lambda name: self.active[name]['last_used'])
                self._stop_locked(oldest)
            work = self._ensure_base() / ('ch%s' % key)
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True)
            args = [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                    '-user_agent', headers.get('user_agent') or USER_AGENT]
            if headers.get('referer'):
                args += ['-referer', headers['referer']]
            args += ['-i', url,
                     '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
                     '-vf', 'scale=-2:min(720\\,ih)', '-b:v', '2M', '-maxrate', '2.4M', '-bufsize', '4M',
                     '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
                     '-f', 'hls', '-hls_time', '2', '-hls_list_size', '6',
                     '-hls_flags', 'delete_segments+append_list+omit_endlist+independent_segments',
                     '-hls_segment_filename', str(work / 'seg%05d.ts'), str(work / 'index.m3u8')]
            log = open(str(work / 'ffmpeg.log'), 'ab')
            process = subprocess.Popen(args, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
            entry = {'process': process, 'dir': work, 'last_used': time.time(), 'log': log}
            self.active[key] = entry
            return entry

    def reap_idle(self):
        now = time.time()
        with self.lock:
            for index in list(self.active):
                entry = self.active[index]
                if entry['process'].poll() is not None:
                    entry['process'].wait()
                    self.active.pop(index, None)
                    try:
                        entry['log'].close()
                    except Exception:
                        pass
                    shutil.rmtree(entry['dir'], ignore_errors=True)
                elif now - entry['last_used'] > self.idle:
                    self._stop_locked(index)

    def _stop_locked(self, index):
        entry = self.active.pop(index, None)
        if not entry:
            return
        try:
            entry['process'].terminate()
            entry['process'].wait(timeout=5)
        except Exception:
            try:
                entry['process'].kill()
                entry['process'].wait(timeout=5)
            except Exception:
                pass
        try:
            entry['log'].close()
        except Exception:
            pass
        shutil.rmtree(entry['dir'], ignore_errors=True)


class TVPlugin:
    """Serves /tv/ pages and proxies only channels discovered in loaded playlists."""

    def __init__(self, password, static_dir, playlists=(), public_base=''):
        self.password = password.encode('utf-8')
        self.static_dir = Path(static_dir)
        self.playlists = tuple(playlists)
        self.public_base = public_base
        self.secret = secrets.token_bytes(32)
        self.token = hmac.new(self.secret, b'iptv-qa-tv-session-v1', hashlib.sha256).hexdigest()
        self.registry = Registry()
        self.sessions = Sessions()
        self.transcoders = Transcoders(os.environ.get('IPTV_TV_FFMPEG', 'ffmpeg'))
        reaper = threading.Thread(target=self._reap_loop, daemon=True)
        reaper.start()

    def _reap_loop(self):
        while True:
            time.sleep(20)
            try:
                self.transcoders.reap_idle()
            except Exception:
                pass

    def session_token(self, handler):
        for part in handler.headers.get('Cookie', '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == TV_COOKIE:
                return value
        return None

    def authorized(self, handler):
        value = self.session_token(handler)
        return bool(value) and hmac.compare_digest(value, self.token)

    def send_media(self, handler, code, body, content_type, extra_headers=None):
        handler.send_response(code)
        handler.send_header('Content-Type', content_type)
        handler.send_header('Content-Length', str(len(body)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (extra_headers or {}).items():
            handler.send_header(key, value)
        handler.end_headers()
        if handler.command != 'HEAD':
            handler.wfile.write(body)

    def send_json(self, handler, value, code=200, extra_headers=None):
        self.send_media(handler, code, json.dumps(value, ensure_ascii=False).encode('utf-8'),
                        'application/json; charset=utf-8', extra_headers)

    def read_json(self, handler):
        try:
            length = int(handler.headers.get('Content-Length') or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            return None
        try:
            return json.loads(handler.rfile.read(length).decode('utf-8'))
        except (OSError, ValueError):
            return None

    def handle_get(self, handler):
        path = unquote(urlsplit(handler.path).path)
        if path in STATIC_FILES:
            return self.serve_static(handler, path)
        if path == '/api/tv/session':
            return self.serve_session_status(handler)
        if path == '/api/tv/playlists':
            return self.serve_playlists(handler)
        match = PLAY_PATH.match(path)
        if match:
            return self.serve_channel(handler, int(match.group(1)), int(match.group(2) or 0))
        match = STREAM_PATH.match(path)
        if match:
            return self.serve_registered(handler, int(match.group(1)))
        match = TRANSCODE_PATH.match(path)
        if match:
            return self.serve_transcode(handler, int(match.group(1)), int(match.group(2) or 0))
        match = TSEG_PATH.match(path)
        if match:
            return self.serve_transcode_segment(handler, match.group(1), match.group(2))
        return False

    def handle_post(self, handler):
        path = unquote(urlsplit(handler.path).path)
        if path == '/api/tv/login':
            return self.handle_login(handler)
        if path == '/api/tv/source':
            if not self.authorized(handler):
                self.send_json(handler, {'error': 'unauthorized'}, 401)
                return True
            return self.handle_source(handler)
        return False

    def serve_static(self, handler, path):
        name, mime = STATIC_FILES[path]
        try:
            body = (self.static_dir / name).read_bytes()
        except OSError:
            self.send_json(handler, {'error': 'player_asset_missing'}, 404)
            return True
        handler.send_response(200)
        handler.send_header('Content-Type', mime)
        handler.send_header('Content-Length', str(len(body)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Referrer-Policy', 'no-referrer')
        handler.send_header('Content-Security-Policy', PLAYER_CSP)
        handler.end_headers()
        if handler.command != 'HEAD':
            handler.wfile.write(body)
        return True

    def _playlist_url(self, name, host):
        if self.public_base:
            return self.public_base.rstrip('/') + '/' + name
        return 'http://%s/%s' % (host or '127.0.0.1', name)

    def _set_chain(self, url, host):
        """Loaded set first, then the remaining aligned backup sets in order."""
        set_names = [name for key, label, name in self.playlists if key.startswith('set')]
        tail = url.rstrip('/').rsplit('/', 1)[-1].lower()
        loaded = next((name for name in set_names if name.lower() == tail), None)
        if loaded is None:
            return [url]
        ordered = [loaded] + [name for name in set_names if name != loaded]
        return [self._playlist_url(name, host) for name in ordered]

    def serve_session_status(self, handler):
        if not self.authorized(handler):
            self.send_json(handler, {'ok': False}, 401)
            return True
        session = self.sessions.get(self.session_token(handler))
        if session:
            channels = [{'name': entry['name'], 'group': entry['group']} for entry in session['channels']]
            self.send_json(handler, {'ok': True, 'source': session['source'],
                                     'count': len(channels), 'channels': channels,
                                     'sets': len(session.get('chain') or [session['source']])})
        else:
            self.send_json(handler, {'ok': True, 'source': '', 'count': 0, 'channels': [], 'sets': 1})
        return True

    def handle_login(self, handler):
        body = self.read_json(handler)
        if body is None:
            self.send_json(handler, {'error': 'bad_request'}, 400)
            return True
        given = str(body.get('password') or '')
        if not hmac.compare_digest(given.encode('utf-8'), self.password):
            time.sleep(1.0)
            self.send_json(handler, {'error': 'wrong_password'}, 401)
            return True
        cookie = '%s=%s; Path=/; Max-Age=604800; HttpOnly; SameSite=Strict' % (TV_COOKIE, self.token)
        self.send_json(handler, {'ok': True}, 200, {'Set-Cookie': cookie})
        return True

    def handle_source(self, handler):
        body = self.read_json(handler)
        if body is None:
            self.send_json(handler, {'error': 'bad_request'}, 400)
            return True
        url = str(body.get('url') or '').strip()
        if urlsplit(url).scheme not in ('http', 'https'):
            self.send_json(handler, {'error': 'invalid_url'}, 400)
            return True
        if is_blocked_url(url):
            self.send_json(handler, {'error': 'blocked_url'}, 400)
            return True
        try:
            _, _, data = fetch_upstream(url, {}, MAX_PLAYLIST_BYTES)
        except (URLError, OSError, ValueError, ssl.SSLError) as error:
            self.send_json(handler, {'error': 'source_fetch_failed', 'detail': str(error)[:200]}, 502)
            return True
        try:
            entries = parse_playlist(data.decode('utf-8', 'replace'))
        except ValueError as error:
            self.send_json(handler, {'error': 'invalid_playlist', 'detail': str(error)[:200]}, 502)
            return True
        channels = [{'name': entry['name'], 'group': entry['group']} for entry in entries]
        chain = self._set_chain(url, handler.headers.get('Host'))
        self.sessions.set_source(self.session_token(handler),
                                 {'source': url, 'channels': entries, 'chain': chain,
                                  'sets': {chain[0]: entries}})
        self.send_json(handler, {'ok': True, 'count': len(entries), 'channels': channels,
                                 'sets': len(chain)})
        return True

    def serve_channel(self, handler, index, slot=0):
        if not self.authorized(handler):
            self.send_json(handler, {'error': 'unauthorized'}, 401)
            return True
        endpoint = self.resolve_channel(handler, index, slot)
        if endpoint is None:
            return True
        return self.serve_url(handler, endpoint['url'], endpoint['headers'])

    def serve_registered(self, handler, rid):
        if not self.authorized(handler):
            self.send_json(handler, {'error': 'unauthorized'}, 401)
            return True
        pair = self.registry.resolve(rid)
        if pair is None:
            self.send_json(handler, {'error': 'unknown_stream'}, 404)
            return True
        return self.serve_url(handler, pair[0], pair[1])

    def serve_playlists(self, handler):
        links = []
        for key, label, name in self.playlists:
            links.append({'key': key, 'label': label,
                          'url': self._playlist_url(name, handler.headers.get('Host'))})
        self.send_json(handler, {'ok': True, 'playlists': links})
        return True

    def resolve_channel(self, handler, index, slot):
        """Returns the endpoint for a channel, trying backup sets by slot."""
        session = self.sessions.get(self.session_token(handler))
        if not session or index < 0 or index >= len(session['channels']):
            self.send_json(handler, {'error': 'channel_not_loaded'}, 404)
            return None
        chain = session.get('chain') or [session['source']]
        if slot < 0 or slot >= len(chain):
            self.send_json(handler, {'error': 'channel_not_loaded'}, 404)
            return None
        entries = session['sets'].get(chain[slot])
        if entries is None:
            try:
                _, _, data = fetch_upstream(chain[slot], {}, MAX_PLAYLIST_BYTES)
                entries = parse_playlist(data.decode('utf-8', 'replace'))
            except (URLError, OSError, ValueError, ssl.SSLError) as error:
                self.send_json(handler, {'error': 'backup_fetch_failed', 'detail': str(error)[:200]}, 502)
                return None
            session['sets'][chain[slot]] = entries
        if index >= len(entries):
            self.send_json(handler, {'error': 'channel_not_loaded'}, 404)
            return None
        return entries[index]['endpoint']

    def serve_transcode(self, handler, index, slot=0):
        if not self.authorized(handler):
            self.send_json(handler, {'error': 'unauthorized'}, 401)
            return True
        endpoint = self.resolve_channel(handler, index, slot)
        if endpoint is None:
            return True
        key = '%d-%d' % (index, slot)
        try:
            entry = self.transcoders.start(key, endpoint['url'], endpoint['headers'])
        except OSError:
            self.send_json(handler, {'error': 'transcoder_unavailable'}, 503)
            return True
        playlist = entry['dir'] / 'index.m3u8'
        deadline = time.time() + TRANSCODE_START_TIMEOUT
        while time.time() < deadline and not playlist.is_file():
            if entry['process'].poll() is not None:
                self.send_json(handler, {'error': 'transcode_failed'}, 502)
                return True
            time.sleep(0.3)
        if not playlist.is_file():
            self.send_json(handler, {'error': 'transcode_timeout'}, 502)
            return True
        lines = []
        for raw in playlist.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if line and not line.startswith('#'):
                lines.append('/tv/tseg/%s/%s' % (key, line))
            else:
                lines.append(raw)
        self.send_media(handler, 200, ('\n'.join(lines) + '\n').encode('utf-8'),
                        'application/vnd.apple.mpegurl; charset=utf-8')
        return True

    def serve_transcode_segment(self, handler, key, name):
        if not self.authorized(handler):
            self.send_json(handler, {'error': 'unauthorized'}, 401)
            return True
        entry = self.transcoders.get(key)
        if not entry or not SEGMENT_NAME.match(name):
            self.send_json(handler, {'error': 'unknown_stream'}, 404)
            return True
        path = entry['dir'] / name
        if not path.is_file():
            self.send_json(handler, {'error': 'segment_not_ready'}, 404)
            return True
        self.send_media(handler, 200, path.read_bytes(), 'video/mp2t')
        return True

    def serve_url(self, handler, url, headers):
        range_header = handler.headers.get('Range')
        try:
            status, response_headers, data = fetch_upstream(url, headers, MAX_SEGMENT_BYTES, range_header)
        except (URLError, OSError, ValueError, ssl.SSLError) as error:
            self.send_json(handler, {'error': 'upstream_failed', 'detail': str(error)[:200]}, 502)
            return True
        if data[:3] == BOM:
            data = data[3:]
        if data.lstrip(b'\r\n \t')[:7] == b'#EXTM3U':
            text = rewrite_playlist(data.decode('utf-8', 'replace'), url, headers, self.registry.register)
            self.send_media(handler, 200, text.encode('utf-8'),
                            'application/vnd.apple.mpegurl; charset=utf-8')
            return True
        content_type = response_headers.get('Content-Type') or 'application/octet-stream'
        extra = {}
        if status == 206 and response_headers.get('Content-Range'):
            extra['Content-Range'] = response_headers.get('Content-Range')
        self.send_media(handler, status if status in (200, 206) else 200, data, content_type, extra)
        return True
