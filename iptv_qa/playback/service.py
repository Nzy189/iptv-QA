#!/usr/bin/env python3
"""Password-gated browser playback API, independent of the QA pipeline."""
import hashlib
import hmac
import json
import os
import secrets
import ssl
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError
from urllib.parse import unquote, urljoin, urlsplit

from iptv_qa.common.playlists import parse_playlist

from .constants import (TV_COOKIE, STATIC_FILES, PLAYER_CSP, PLAY_PATH, STREAM_PATH, TRANSCODE_PATH,
                        TSEG_PATH, SEGMENT_NAME, MAX_BODY_BYTES, MAX_PLAYLIST_BYTES,
                        MAX_SEGMENT_BYTES, TRANSCODE_START_TIMEOUT, BOM)
from .proxy import is_blocked_url, fetch_upstream, rewrite_playlist
from .state import Registry, Sessions
from .transcoding import Transcoders


def assets_dir():
    """Return vendored playback assets, including when installed from a wheel."""
    return Path(__file__).resolve().parent / "assets"


class TVPlugin:
    """Serves /tv/ pages and proxies only channels discovered in loaded playlists."""

    def __init__(self, password, static_dir=None, playlists=(), public_base='', ffmpeg=None,
                 transcode_limit=2, transcode_idle_seconds=90):
        self.password = password.encode('utf-8')
        self.static_dir = Path(static_dir) if static_dir is not None else assets_dir()
        self.playlists = tuple(playlists)
        self.public_base = public_base
        self.secret = secrets.token_bytes(32)
        self.token = hmac.new(self.secret, b'iptv-qa-tv-session-v1', hashlib.sha256).hexdigest()
        self.registry = Registry()
        self.sessions = Sessions()
        self.transcoders = Transcoders(ffmpeg or os.environ.get('IPTV_TV_FFMPEG', 'ffmpeg'),
                                       limit=transcode_limit, idle_seconds=transcode_idle_seconds)
        self._stopped = threading.Event()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._reaper.start()

    def _reap_loop(self):
        while not self._stopped.wait(20):
            try:
                self.transcoders.reap_idle()
            except Exception:
                pass

    def close(self):
        """Release owned workers when an embedded or standalone service stops."""
        self._stopped.set()
        self._reaper.join(timeout=1)
        self.transcoders.close()

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
            _, _, data, _ = fetch_upstream(url, {}, MAX_PLAYLIST_BYTES)
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
                _, _, data, _ = fetch_upstream(chain[slot], {}, MAX_PLAYLIST_BYTES)
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
            status, response_headers, data, final_url = fetch_upstream(url, headers, MAX_SEGMENT_BYTES, range_header)
        except (URLError, OSError, ValueError, ssl.SSLError) as error:
            self.send_json(handler, {'error': 'upstream_failed', 'detail': str(error)[:200]}, 502)
            return True
        if data[:3] == BOM:
            data = data[3:]
        if data.lstrip(b'\r\n \t')[:7] == b'#EXTM3U':
            text = rewrite_playlist(data.decode('utf-8', 'replace'), final_url, headers, self.registry.register)
            self.send_media(handler, 200, text.encode('utf-8'),
                            'application/vnd.apple.mpegurl; charset=utf-8')
            return True
        content_type = response_headers.get('Content-Type') or 'application/octet-stream'
        extra = {}
        if status == 206 and response_headers.get('Content-Range'):
            extra['Content-Range'] = response_headers.get('Content-Range')
        self.send_media(handler, status if status in (200, 206) else 200, data, content_type, extra)
        return True
