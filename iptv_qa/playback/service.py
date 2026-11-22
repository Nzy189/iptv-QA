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
from urllib.parse import unquote, urljoin, urlsplit, parse_qs, quote

from iptv_qa.common.playlists import parse_playlist

from .constants import (TV_COOKIE, STATIC_FILES, PLAYER_CSP, PLAY_PATH, STREAM_PATH, TRANSCODE_PATH,
                        TSEG_PATH, SEGMENT_NAME, MAX_BODY_BYTES, MAX_PLAYLIST_BYTES,
                        MAX_SEGMENT_BYTES, TRANSCODE_START_TIMEOUT, BOM,
                        TV_PLAYLIST_PATH, TV_RACE_PATH, TV_RSTREAM_PATH, TV_RACE_VIEWS,
                        TV_RACE_CACHE_SECONDS, TV_RACE_CACHE_LIMIT, RACE_BUDGET,
                        RACE_BUDGET_ALL_FAILING)
from .proxy import is_blocked_url, fetch_upstream, rewrite_playlist, NotHls
from . import reqlog
from .linestats import LineStats
from .prefetch import Prefetcher, strip_probe
from .race import race_lines
from .state import Registry, Sessions
from .transcoding import Transcoders


def assets_dir():
    """Return vendored playback assets, including when installed from a wheel."""
    return Path(__file__).resolve().parent / "assets"


class TVPlugin:
    """Serves /tv/ pages and proxies only channels discovered in loaded playlists."""

    def __init__(self, password, static_dir=None, playlists=(), public_base='', ffmpeg=None,
                 transcode_limit=2, transcode_idle_seconds=90, library=None, race_func=None,
                 prefetch_mode='light', prefetch_max_channels=16,
                 prefetch_max_bytes=128 * 1024 * 1024):
        self.library = library
        self.library_api = None
        if library is not None:
            from iptv_qa.library.api import LibraryAPI
            self.library_api = LibraryAPI(library)
        self.password = password.encode('utf-8') if isinstance(password, str) else bytes(password)
        self.static_dir = Path(static_dir) if static_dir is not None else assets_dir()
        self.playlists = tuple(playlists)
        self.public_base = public_base
        self.tv_tokens = [part.strip() for part in
                          (os.environ.get('IPTV_TV_TOKENS') or '').split(',') if part.strip()]
        self.line_stats = LineStats()
        # The default race reports every probe outcome so later races,
        # fallbacks and prewarms prefer lines that actually started fast.
        self._default_race = race_func is None
        if race_func is None:
            race_func = self._race_default
        self.race_func = race_func
        self.race_cache = OrderedDict()
        self.race_lock = threading.Lock()
        self.prefetcher = Prefetcher(race_func=race_func, mode=prefetch_mode,
                                     max_channels=prefetch_max_channels,
                                     ttl=TV_RACE_CACHE_SECONDS,
                                     max_bytes=prefetch_max_bytes)
        self.secret = secrets.token_bytes(32)
        self.token = hmac.new(self.secret, b'iptv-qa-tv-session-v1', hashlib.sha256).hexdigest()
        self.auth_tokens = OrderedDict()
        self.auth_lock = threading.Lock()
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
        self.prefetcher.close()

    def session_token(self, handler):
        for part in handler.headers.get('Cookie', '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == TV_COOKIE:
                return value
        return None

    def authorized(self, handler):
        value = self.session_token(handler)
        with self.auth_lock:
            return bool(value) and self.auth_tokens.get(value,0)>time.time()

    def playback_session(self, handler):
        token=self.session_token(handler)
        source=parse_qs(urlsplit(handler.path).query).get('source_id',[''])[0]
        return self.sessions.get(token+':'+source) if source and token else self.sessions.get(token)

    def save_source(self, token, session):
        self.sessions.set_source(token,session)
        self.sessions.set_source(token+':'+session['source_id'],session)

    def send_media(self, handler, code, body, content_type, extra_headers=None):
        reqlog.note(st=code, b=len(body))
        try:
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
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The player gave up on this request (TV apps routinely cancel a
            # duplicate or a superseded request). Record it instead of
            # printing a traceback for every cancelled segment.
            reqlog.note(abort=True)
            handler.close_connection = True

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

    LOGGED_PATHS = ((TV_RACE_PATH, 'race'), (TV_RSTREAM_PATH, 'rstream'), (TV_PLAYLIST_PATH, 'playlist'),
                    (PLAY_PATH, 'play'), (STREAM_PATH, 'stream'))

    def handle_get(self, handler):
        path = unquote(urlsplit(handler.path).path)
        kind = next((name for pattern, name in self.LOGGED_PATHS if pattern.match(path)), None)
        if kind is None:
            return self._handle_get(handler, path)
        reqlog.begin(kind, ip=handler.client_address[0], m=handler.command,
                     id=path.rsplit('/', 1)[-1][:40])
        try:
            return self._handle_get(handler, path)
        except Exception as error:
            reqlog.note(err=type(error).__name__)
            raise
        finally:
            reqlog.end()

    def _handle_get(self, handler, path):
        if path in ('/api/library/state','/api/library/status'):
            return self.handle_library(handler, False)
        if path in STATIC_FILES:
            return self.serve_static(handler, path)
        if path == '/api/tv/session':
            return self.serve_session_status(handler)
        if path == '/api/tv/playlists':
            return self.serve_playlists(handler)
        if path == '/api/tv/prefetch':
            if not self.authorized(handler):
                self.send_json(handler, {'error': 'unauthorized'}, 401)
                return True
            return self.handle_prefetch_hint(handler)
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
        match = TV_PLAYLIST_PATH.match(path)
        if match:
            return self.serve_token_playlist(handler)
        match = TV_RACE_PATH.match(path)
        if match:
            return self.serve_token_race(handler, match.group(1), match.group(2))
        match = TV_RSTREAM_PATH.match(path)
        if match:
            return self.serve_token_stream(handler, int(match.group(1)))
        return False

    def handle_post(self, handler):
        path = unquote(urlsplit(handler.path).path)
        if path == '/api/library/action':
            return self.handle_library(handler, True)
        if path == '/api/tv/login':
            return self.handle_login(handler)
        if path == '/api/tv/prefetch':
            if not self.authorized(handler):
                self.send_json(handler, {'error': 'unauthorized'}, 401)
                return True
            return self.handle_prefetch(handler)
        if path == '/api/tv/source':
            if not self.authorized(handler):
                self.send_json(handler, {'error': 'unauthorized'}, 401)
                return True
            return self.handle_source(handler)
        return False

    def handle_library(self, handler, mutation):
        if not self.authorized(handler):
            self.send_json(handler, {'error':'unauthorized'}, 401)
            return True
        if self.library_api is None:
            self.send_json(handler, {'error':'library_not_configured'}, 503)
            return True
        from iptv_qa.library.store import Conflict
        try:
            result = self.library_api.post(handler,self) if mutation else self.library_api.get(handler,self)
            self.send_json(handler, dict(result,ok=True))
        except Conflict as error:
            self.send_json(handler, {'error':str(error)}, 409)
        except (ValueError,TypeError,KeyError) as error:
            self.send_json(handler, {'error':str(error)[:100]}, 400)
        except (OSError,Exception) as error:
            self.send_json(handler, {'error':type(error).__name__}, 503)
        return True

    def channel_metadata(self, session):
        if session.get('library_scope'):
            return [{'name':e['name'],'group':e['group_name'],'id':e['id'],
                     'line_count':len(e['lines'])} for e in session['channels']]
        candidates = session.get('candidates')
        return [{'name':e['name'],'group':e['group'],
                 'line_count':len(candidates[i]) if candidates else len(session['chain'])}
                for i,e in enumerate(session['channels'])]

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
        set_names = ['iptv_set1.m3u', 'iptv_set2.m3u', 'iptv_set3.m3u']
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
            channels = self.channel_metadata(session)
            self.send_json(handler, {'ok': True, 'source': session['source'],
                                     'count': len(channels), 'channels': channels,
                                     'sets': max([c.get('line_count',1) for c in channels] or [1]),
                                     'source_id':session.get('source_id',''), 'library_scope':session.get('library_scope','')})
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
        token=self.session_token(handler) if self.authorized(handler) else secrets.token_urlsafe(32)
        with self.auth_lock:
            self.auth_tokens[token]=time.time()+604800
            while len(self.auth_tokens)>64:self.auth_tokens.popitem(last=False)
        cookie = '%s=%s; Path=/; Max-Age=604800; HttpOnly; SameSite=Strict' % (TV_COOKIE, token)
        self.send_json(handler, {'ok': True}, 200, {'Set-Cookie': cookie})
        return True

    def handle_prefetch(self, handler):
        if not self.prefetcher.enabled():
            self.send_json(handler, {'ok': True, 'warmed': False})
            return True
        body = self.read_json(handler) or {}
        mode = str(body.get('mode') or 'off')
        if mode not in Prefetcher.VALID_MODES:
            mode = 'off'
        session = self.playback_session(handler)
        if not session or not session.get('library_scope') or mode == 'off':
            self.send_json(handler, {'ok': True, 'warmed': False})
            return True
        try:
            index = int(body.get('index'))
        except (TypeError, ValueError):
            self.send_json(handler, {'ok': True, 'warmed': False})
            return True
        channels = session.get('channels') or []
        if len(channels) < 2 or index < 0 or index >= len(channels):
            self.send_json(handler, {'ok': True, 'warmed': False})
            return True
        nxt = channels[(index + 1) % len(channels)]
        lines = [{'url': line['url'], 'headers': line['headers']} for line in nxt.get('lines', [])]
        if not lines:
            self.send_json(handler, {'ok': True, 'warmed': False})
            return True
        self.prefetcher.warm(session['library_scope'], nxt['id'], self.line_stats.order(lines),
                             mode=mode)
        self.send_json(handler, {'ok': True, 'warmed': True})
        return True

    def handle_prefetch_hint(self, handler):
        """Return the prewarmed winner's line slot for a browser channel index."""
        try:
            index = int(parse_qs(urlsplit(handler.path).query).get('index', ['-1'])[0])
        except (TypeError, ValueError):
            index = -1
        slot = -1
        session = self.playback_session(handler)
        if session and session.get('library_scope') and self.prefetcher.enabled():
            channels = session.get('channels') or []
            if 0 <= index < len(channels):
                hit = self.prefetcher.get(session['library_scope'], channels[index]['id'])
                if hit:
                    slot = int(hit['index'])
        self.send_json(handler, {'ok': True, 'slot': slot})
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
        scope_map = {'iptv_ok.m3u':'main','iptv_top5.m3u':'main',
                     'iptv_live.m3u':'live','iptv_english.m3u':'english'}
        if self.library is not None:
            scope = next((v for k,v in scope_map.items() if url == self._playlist_url(k,handler.headers.get('Host'))),None)
            if scope:
                snapshot = self.library.snapshot()
                entries = self.library.view_snapshot(snapshot,scope)
                session = {'source':url,'channels':entries,'chain':[url], 'sets':{},
                    'source_id':secrets.token_urlsafe(16), 'library_scope':scope,
                    'library_revision':snapshot['revision']}
                self.save_source(self.session_token(handler),session)
                channels = self.channel_metadata(session)
                self.send_json(handler, {'ok':True,'count':len(channels),'channels':channels,
                    'sets':max([c['line_count'] for c in channels] or [1]),
                    'source_id':session['source_id'],'library_scope':scope})
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
        chain = self._set_chain(url, handler.headers.get('Host'))
        candidates = None
        if urlsplit(url).path.rsplit('/',1)[-1] in ('iptv_ok.m3u','iptv_top5.m3u'):
            grouped = OrderedDict()
            for entry in entries:
                bucket = grouped.setdefault(entry['key'],[])
                if len(bucket)<5 and entry['endpoint']['id'] not in {e['endpoint']['id'] for e in bucket}:
                    bucket.append(entry)
            candidates = list(grouped.values())
            entries = [bucket[0] for bucket in candidates]
        session = {'source':url,'channels':entries,'chain':chain,'sets':{chain[0]:entries},
                   'candidates':candidates,'source_id':secrets.token_urlsafe(16)}
        self.save_source(self.session_token(handler),session)
        channels = self.channel_metadata(session)
        self.send_json(handler, {'ok':True,'count':len(entries),'channels':channels,
            'sets':max([c['line_count'] for c in channels] or [1]),'source_id':session['source_id'], 'library_scope':''})

        return True

    def serve_channel(self, handler, index, slot=0):
        if not self.authorized(handler):
            self.send_json(handler, {'error': 'unauthorized'}, 401)
            return True
        endpoint = self.resolve_channel(handler, index, slot)
        if endpoint is None:
            return True
        return self.serve_url(handler, endpoint['url'], endpoint['headers'], manifest=True)

    def serve_registered(self, handler, rid):
        if not self.authorized(handler):
            self.send_json(handler, {'error': 'unauthorized'}, 401)
            return True
        pair = self.registry.resolve(rid)
        if pair is None:
            self.send_json(handler, {'error': 'unknown_stream'}, 404)
            return True
        return self.serve_url(handler, pair[0], pair[1])

    def token_authorized(self, handler):
        """Returns the presented token string when it matches a configured token."""
        value = parse_qs(urlsplit(handler.path).query).get('token', [''])[0]
        if not value:
            return None
        given = value.encode('utf-8')
        for token in self.tv_tokens:
            if hmac.compare_digest(given, token.encode('utf-8')):
                return value
        return None

    def serve_token_playlist(self, handler):
        if not self.tv_tokens:
            self.send_json(handler, {'error': 'not_found'}, 404)
            return True
        token = self.token_authorized(handler)
        if token is None:
            self.send_json(handler, {'error': 'unauthorized'}, 401)
            return True
        if self.library is None:
            self.send_json(handler, {'error': 'library_not_configured'}, 404)
            return True
        query = parse_qs(urlsplit(handler.path).query)
        view = query.get('view', ['main'])[0]
        if view not in TV_RACE_VIEWS:
            self.send_json(handler, {'error': 'not_found'}, 404)
            return True
        # race endpoints live on this service, never on the published-playlist base
        base = 'http://%s' % (handler.headers.get('Host') or '127.0.0.1')
        lines = ['#EXTM3U']
        for row in self.library.view(view):
            attrs = json.loads(row['metadata'])
            attrs['group-title'] = row['group_name']
            attrs['channel-id'] = row['id']
            prefix = '#EXTINF:-1' + ''.join(
                ' %s="%s"' % (key, str(value).replace('"', '').replace('\n', '').replace('\r', ''))
                for key, value in attrs.items()
                if key in ('tvg-id', 'tvg-name', 'tvg-logo', 'group-title', 'channel-id'))
            lines.append(prefix + ',' + row['name'])
            lines.append('%s/tv/race/%s/%s?token=%s' % (
                base, view, quote(row['id'], safe=''), quote(token, safe='')))
        self.send_media(handler, 200, ('\n'.join(lines) + '\n').encode('utf-8'),
                        'application/vnd.apple.mpegurl')
        return True

    def _race_default(self, lines, budget=RACE_BUDGET):
        return race_lines(lines, budget=budget, observe=self.line_stats.record)

    def _race_winner(self, view, channel_id, lines):
        """Winner for a channel; a fresh race's winner still carries its probe bytes."""
        prefetched = self.prefetcher.get(view, channel_id)
        if prefetched is not None:
            reqlog.note(pick='prewarm', slot=prefetched.get('index'))
            return prefetched
        key = (view, channel_id)
        now = time.time()
        with self.race_lock:
            hit = self.race_cache.get(key)
            if hit and hit[0] > now:
                reqlog.note(pick='cache', slot=hit[1].get('index'))
                return hit[1]
        lines = self.line_stats.order(lines)
        if len(lines) == 1:
            # Nothing to race against: probing first only delays the start.
            line = lines[0]
            reqlog.note(pick='single', slot=line['slot'])
            return {'index': line['slot'], 'url': line['url'], 'headers': line['headers'],
                    'final_url': line['url']}
        race_started = time.monotonic()
        if self._default_race and self.line_stats.all_failing(lines):
            # Every line failed repeatedly in the last minutes: answer fast
            # instead of holding the TV app for the full race budget.
            winner = self._race_default(lines, budget=RACE_BUDGET_ALL_FAILING)
        else:
            winner = self.race_func(lines)
        reqlog.add_ms('race_ms', race_started)
        reqlog.note(pick='race' if winner is not None else 'race_failed',
                    slot=winner.get('index') if winner else None)
        if winner is not None:
            with self.race_lock:
                self.race_cache[key] = (now + TV_RACE_CACHE_SECONDS, strip_probe(winner))
                while len(self.race_cache) > TV_RACE_CACHE_LIMIT:
                    self.race_cache.popitem(last=False)
        return winner

    def _prefetch_next(self, view, channel_id, rows=None):
        """Warm the channel after channel_id in the given view, if enabled."""
        if not self.prefetcher.enabled() or self.library is None:
            return
        if rows is None:
            rows = self.library.view(view)
        ids = [row['id'] for row in rows]
        if channel_id not in ids or len(rows) < 2:
            return
        nxt = rows[(ids.index(channel_id) + 1) % len(rows)]
        lines = [{'url': line['url'], 'headers': line['headers']} for line in nxt['lines']]
        if lines:
            self.prefetcher.warm(view, nxt['id'], self.line_stats.order(lines))

    def serve_token_race(self, handler, view, channel_id):
        if not self.tv_tokens:
            self.send_json(handler, {'error': 'not_found'}, 404)
            return True
        token = self.token_authorized(handler)
        if token is None:
            self.send_json(handler, {'error': 'unauthorized'}, 401)
            return True
        if self.library is None or view not in TV_RACE_VIEWS:
            self.send_json(handler, {'error': 'not_found'}, 404)
            return True
        rows = self.library.view(view)
        channel = next((row for row in rows if row['id'] == channel_id), None)
        if channel is None:
            self.send_json(handler, {'error': 'channel_not_found'}, 404)
            return True
        lines = [{'url': line['url'], 'headers': line['headers']}
                 for line in channel['lines']]
        reqlog.note(view=view, ch=channel_id[:12], name=channel.get('name'), lines=len(lines))
        winner = self._race_winner(view, channel_id, lines)
        if winner is None:
            if not lines:
                self.send_json(handler, {'error': 'channel_or_line_inactive'}, 404)
                return True
            winner = self.line_stats.order(lines)[0]
            reqlog.note(slot=winner.get('slot'), fallback=True)
        elif winner.get('probe'):
            # Serve the manifest the race already downloaded and start
            # fetching the segments the player will ask for next.
            self.prefetcher.adopt_race(winner)
        self._prefetch_next(view, channel_id, rows)
        # the template is %-formatted later, so a literal % in the token must be doubled
        template = '/tv/rstream/%d?token=' + quote(token, safe='').replace('%', '%%')
        return self.serve_url(handler, winner['url'], winner['headers'], template,
                              warm_segments=True,
                              meta={'ch': channel_id[:12], 'name': channel.get('name')})

    def serve_token_stream(self, handler, rid):
        if not self.tv_tokens:
            self.send_json(handler, {'error': 'not_found'}, 404)
            return True
        token = self.token_authorized(handler)
        if token is None:
            self.send_json(handler, {'error': 'unauthorized'}, 401)
            return True
        pair = self.registry.resolve(rid)
        if pair is None:
            self.send_json(handler, {'error': 'unknown_stream'}, 404)
            return True
        meta = self.registry.meta(rid)
        reqlog.note(**meta)
        # the template is %-formatted later, so a literal % in the token must be doubled
        template = '/tv/rstream/%d?token=' + quote(token, safe='').replace('%', '%%')
        child_meta = {k: meta[k] for k in ('ch', 'name') if k in meta} or None
        return self.serve_url(handler, pair[0], pair[1], template, meta=child_meta)

    def serve_playlists(self, handler):
        links = []
        for key, label, name in self.playlists:
            links.append({'key': key, 'label': label,
                          'url': self._playlist_url(name, handler.headers.get('Host'))})
        self.send_json(handler, {'ok': True, 'playlists': links})
        return True

    def resolve_channel(self, handler, index, slot):
        """Returns the endpoint for a channel, trying backup sets by slot."""
        session = self.playback_session(handler)
        if not session or index < 0 or index >= len(session['channels']):
            self.send_json(handler, {'error': 'channel_not_loaded'}, 404)
            return None
        if session.get('library_scope'):
            cid = session['channels'][index]['id']
            current = next((r for r in self.library.view(session['library_scope']) if r['id']==cid),None)
            if not current or slot < 0 or slot >= len(current['lines']):
                self.send_json(handler, {'error':'channel_or_line_inactive'},410)
                return None
            route = current['lines'][slot]
            return {'url':route['url'],'headers':route['headers']}
        if session.get('candidates'):
            candidates = session['candidates'][index]
            if slot < 0 or slot >= len(candidates):
                self.send_json(handler, {'error':'channel_not_loaded'},404)
                return None
            return candidates[slot]['endpoint']
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
        if slot == 0:
            return session['channels'][index]['endpoint']
        target = session['channels'][index]['key']
        match = next((entry for entry in entries if entry['key'] == target), None)
        if match is None:
            self.send_json(handler, {'error': 'channel_not_in_backup'}, 404)
            return None
        return match['endpoint']

    def serve_transcode(self, handler, index, slot=0):
        if not self.authorized(handler):
            self.send_json(handler, {'error': 'unauthorized'}, 401)
            return True
        endpoint = self.resolve_channel(handler, index, slot)
        if endpoint is None:
            return True
        signature=endpoint['url']+json.dumps(endpoint['headers'],sort_keys=True)
        key = str(int(hashlib.sha256(signature.encode()).hexdigest()[:16],16))+'-'+str(slot)
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

    def _send_cached(self, handler, entry, url_template, headers, meta=None):
        data = entry['data']
        if data[:3] == BOM:
            data = data[3:]
        if data.lstrip(b'\r\n \t')[:7] == b'#EXTM3U':
            text = rewrite_playlist(data.decode('utf-8', 'replace'), entry['final_url'], headers,
                                    self.registry.register, url_template, meta)
            self.send_media(handler, 200, text.encode('utf-8'),
                            'application/vnd.apple.mpegurl; charset=utf-8')
            return
        content_type = entry.get('content_type') or 'application/octet-stream'
        self.send_media(handler, entry['status'] if entry['status'] in (200, 206) else 200, data, content_type)

    def serve_url(self, handler, url, headers, url_template=None, manifest=False,
                  warm_segments=False, meta=None):
        range_header = handler.headers.get('Range')
        reqlog.note(host=urlsplit(url).netloc)
        if not range_header:
            cached = self.prefetcher.wait_bytes(url)
            if cached is not None:
                reqlog.note(cache='hit')
                self._send_cached(handler, cached, url_template, headers, meta)
                return True
        reqlog.note(cache='miss')
        upstream_started = time.monotonic()
        try:
            status, response_headers, data, final_url = fetch_upstream(
                url, headers, MAX_PLAYLIST_BYTES if manifest else MAX_SEGMENT_BYTES,
                range_header, require_playlist=manifest)
        except NotHls:
            reqlog.add_ms('up_ms', upstream_started)
            reqlog.note(err='not_hls')
            self.send_json(handler, {'error': 'not_hls'}, 415)
            return True
        except (URLError, OSError, ValueError, ssl.SSLError) as error:
            reqlog.add_ms('up_ms', upstream_started)
            reqlog.note(err=type(error).__name__, detail=str(error)[:80])
            self.send_json(handler, {'error': 'upstream_failed', 'detail': str(error)[:200]}, 502)
            return True
        reqlog.add_ms('up_ms', upstream_started)
        reqlog.note(up_st=status)
        if data[:3] == BOM:
            data = data[3:]
        if data.lstrip(b'\r\n \t')[:7] == b'#EXTM3U':
            if warm_segments and 200 <= status < 300:
                self.prefetcher.adopt_manifest(final_url, data, headers)
            text = rewrite_playlist(data.decode('utf-8', 'replace'), final_url, headers,
                                   self.registry.register, url_template, meta)
            self.send_media(handler, 200, text.encode('utf-8'),
                            'application/vnd.apple.mpegurl; charset=utf-8')
            return True
        content_type = response_headers.get('Content-Type') or 'application/octet-stream'
        extra = {}
        if status == 206 and response_headers.get('Content-Range'):
            extra['Content-Range'] = response_headers.get('Content-Range')
        self.send_media(handler, status if status in (200, 206) else 200, data, content_type, extra)
        return True
