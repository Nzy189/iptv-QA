import http.client
import io
import json
import os
import re
import time
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from unittest import mock
from urllib.parse import quote

import tv
import web

from iptv_qa.playback.service import TVPlugin


class UpstreamServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def __init__(self, address):
        self.hits = {}
        super().__init__(address, make_upstream_handler(self))


def make_upstream_handler(server):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            server.hits[self.path] = self.headers.get('User-Agent', '')
            port = server.server_port
            body = None
            content_type = 'application/octet-stream'
            if self.path == '/list.m3u8':
                content_type = 'application/vnd.apple.mpegurl'
                body = ('#EXTM3U\n'
                        '#EXTINF:-1 group-title="Test",频道一\n'
                        'http://127.0.0.1:%d/one/master.m3u8\n'
                        '#EXTINF:-1 group-title="Test",频道二\n'
                        'http://127.0.0.1:%d/two/master.m3u8\n'
                        '#EXTINF:-1 group-title="Test",频道三\n'
                        '#EXTVLCOPT:http-user-agent=SpecialUA/1.0\n'
                        'http://127.0.0.1:%d/three/master.m3u8\n' % (port, port, port)).encode('utf-8')
            elif self.path in ('/redirect/start.m3u8', '/redirect/hop.m3u8',
                               '/one/redirect-media.m3u8'):
                targets = {'/redirect/start.m3u8': '/redirect/hop.m3u8',
                           '/redirect/hop.m3u8': '/one/master.m3u8',
                           '/one/redirect-media.m3u8': '/one/media.m3u8'}
                self.send_response(302)
                self.send_header('Location', targets[self.path])
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            elif self.path == '/one/master.m3u8':
                content_type = 'application/vnd.apple.mpegurl'
                body = ('#EXTM3U\n'
                        '#EXT-X-STREAM-INF:BANDWIDTH=1000000\n'
                        'redirect-media.m3u8\n'
                        '#EXT-X-STREAM-INF:BANDWIDTH=500000\n'
                        'http://127.0.0.1:%d/two/media.m3u8\n' % port).encode('utf-8')
            elif self.path == '/one/media.m3u8':
                content_type = 'application/vnd.apple.mpegurl'
                body = ('#EXTM3U\n'
                        '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
                        '#EXT-X-MAP:URI="init.mp4"\n'
                        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="audio",URI="audio.m3u8"\n'
                        '#EXTINF:6.0,\n'
                        'seg001.ts?token=abc\n'
                        '#EXTINF:6.0,\n'
                        'seg002.ts\n'
                        '#EXT-X-ENDLIST\n').encode('utf-8')
            elif self.path == '/one/audio.m3u8':
                content_type = 'application/vnd.apple.mpegurl'
                body = ('#EXTM3U\n'
                        '#EXTINF:6.0,\n'
                        'seg002.ts\n'
                        '#EXT-X-ENDLIST\n').encode('utf-8')
            elif self.path == '/three/master.m3u8':
                content_type = 'application/vnd.apple.mpegurl'
                body = ('#EXTM3U\n'
                        '#EXT-X-STREAM-INF:BANDWIDTH=700000\n'
                        'media.m3u8\n').encode('utf-8')
            elif self.path == '/three/media.m3u8':
                content_type = 'application/vnd.apple.mpegurl'
                body = ('#EXTM3U\n'
                        '#EXTINF:6.0,\n'
                        'seg001.ts\n'
                        '#EXT-X-ENDLIST\n').encode('utf-8')
            elif self.path == '/two/master.m3u8':
                content_type = 'application/vnd.apple.mpegurl'
                body = ('#EXTM3U\n'
                        '#EXT-X-STREAM-INF:BANDWIDTH=800000\n'
                        'media.m3u8\n').encode('utf-8')
            elif self.path == '/two/media.m3u8':
                content_type = 'application/vnd.apple.mpegurl'
                body = ('#EXTM3U\n'
                        '#EXTINF:6.0,\n'
                        'seg001.ts\n'
                        '#EXT-X-ENDLIST\n').encode('utf-8')
            elif self.path == '/one/seg001.ts?token=abc':
                body = b'SEGMENT-1A'
            elif self.path == '/one/seg002.ts':
                body = b'SEGMENT-2'
            elif self.path == '/one/key.bin':
                body = b'0123456789abcdef'
            elif self.path == '/one/init.mp4':
                body = b'INITMP4'
            elif self.path == '/two/seg001.ts':
                body = b'SEGMENT-TWO'
            elif self.path == '/three/seg001.ts':
                body = b'SEGMENT-THREE'
            if body is None:
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


class Client:
    def __init__(self, port):
        self.port = port
        self.cookie = None

    def request(self, method, path, payload=None):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if self.cookie:
            headers['Cookie'] = self.cookie
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        set_cookie = response.getheader('Set-Cookie')
        if set_cookie:
            self.cookie = set_cookie.split(';')[0]
        headers = dict(response.getheaders())
        conn.close()
        return response.status, headers, data


class RegistryTests(unittest.TestCase):
    def test_register_resolve_and_dedup(self):
        registry = tv.Registry()
        first = registry.register('http://a.test/live', {})
        self.assertEqual(registry.register('http://a.test/live', {}), first)
        url, headers = registry.resolve(first)
        self.assertEqual(url, 'http://a.test/live')
        self.assertEqual(headers, {})
        self.assertIsNone(registry.resolve(first + 1))

    def test_different_headers_get_separate_ids(self):
        registry = tv.Registry()
        plain = registry.register('http://a.test/live', {})
        special = registry.register('http://a.test/live', {'user_agent': 'X/1'})
        self.assertNotEqual(plain, special)
        self.assertEqual(registry.resolve(special)[1], {'user_agent': 'X/1'})

    def test_lru_eviction(self):
        registry = tv.Registry(limit=3)
        ids = [registry.register('http://a.test/%d' % n, {}) for n in range(3)]
        registry.resolve(ids[0])
        registry.register('http://a.test/new', {})
        self.assertIsNone(registry.resolve(ids[1]))
        self.assertIsNotNone(registry.resolve(ids[0]))


class RewriteTests(unittest.TestCase):
    def setUp(self):
        self.registry = tv.Registry()

    def rewritten(self, text, base):
        return tv.rewrite_playlist(text, base, {'user_agent': 'UA'}, self.registry.register)

    def test_master_playlist_rewrites_relative_and_absolute(self):
        text = ('#EXTM3U\n'
                '#EXT-X-STREAM-INF:BANDWIDTH=1000\n'
                'media.m3u8\n'
                '#EXT-X-STREAM-INF:BANDWIDTH=500\n'
                'http://other.test/live.m3u8\n')
        result = self.rewritten(text, 'http://up.test/one/master.m3u8')
        lines = [line for line in result.splitlines() if line and not line.startswith('#')]
        self.assertTrue(all(line.startswith('/tv/stream/') for line in lines))
        self.assertNotIn('other.test', result)
        self.assertNotIn('media.m3u8\n', result)

    def test_media_playlist_rewrites_uri_attributes(self):
        text = ('#EXTM3U\n'
                '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
                '#EXT-X-MAP:URI="init.mp4"\n'
                '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="x",URI="audio.m3u8"\n'
                '#EXTINF:6.0,\n'
                'seg001.ts?token=abc\n')
        result = self.rewritten(text, 'http://up.test/one/media.m3u8')
        self.assertIn('URI="/tv/stream/', result)
        self.assertNotIn('key.bin', result)
        self.assertNotIn('init.mp4', result)
        self.assertNotIn('audio.m3u8', result)
        for line in result.splitlines():
            if line.startswith('#EXT-X-KEY'):
                self.assertIn('URI="/tv/stream/', line)
                self.assertIn('METHOD=AES-128', line)

    def test_registry_entries_resolve_to_absolute_urls(self):
        self.rewritten('seg.ts\n', 'http://up.test/live/media.m3u8')
        rid = 1
        url, headers = self.registry.resolve(rid)
        self.assertEqual(url, 'http://up.test/live/seg.ts')
        self.assertEqual(headers, {'user_agent': 'UA'})


class BlockedUrlTests(unittest.TestCase):
    def test_metadata_hosts_are_blocked(self):
        self.assertTrue(tv.is_blocked_url('http://169.254.169.254/latest/meta-data/'))
        self.assertTrue(tv.is_blocked_url('http://metadata.google.internal/computeMetadata/'))
        self.assertFalse(tv.is_blocked_url('http://example.com/live.m3u8'))


FAKE_FFMPEG = '''import os, sys, time
args = sys.argv[1:]
pattern = args[args.index('-hls_segment_filename') + 1]
out = args[-1]
os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
lines = ['#EXTM3U', '#EXT-X-TARGETDURATION:2']
for n in range(3):
    name = os.path.basename(pattern).replace('%05d', '%05d' % n)
    open(os.path.join(os.path.dirname(out), name), 'wb').write(b'FAKESEG%03d' % n)
    lines += ['#EXTINF:2.0,', name]
open(out, 'w').write('\\n'.join(lines) + '\\n')
end = time.time() + 30
while time.time() < end:
    time.sleep(1)
'''


class TVServiceTests(unittest.TestCase):
    PASSWORD = 'test-password-123'
    PLAYLISTS = (('top5', '综合频道 · Top5', 'iptv_ok.m3u'),
                 ('live', '直播频道', 'iptv_live.m3u'),
                 ('english', '英语频道', 'iptv_english.m3u'))

    @classmethod
    def setUpClass(cls):
        cls.tool_temp = tempfile.TemporaryDirectory()
        tool_base = Path(cls.tool_temp.name)
        (tool_base / 'fake_ffmpeg.py').write_text(FAKE_FFMPEG, encoding='utf-8')
        if os.name == 'nt':
            wrapper = tool_base / 'fake_ffmpeg.bat'
            wrapper.write_text('@python "%~dp0fake_ffmpeg.py" %*\n', encoding='utf-8')
        else:
            wrapper = tool_base / 'fake_ffmpeg.sh'
            wrapper.write_text('#!/bin/sh\nexec python3 "$(dirname "$0")/fake_ffmpeg.py" "$@"\n',
                               encoding='utf-8')
            wrapper.chmod(0o755)
        os.environ['IPTV_TV_FFMPEG'] = str(wrapper)
        cls.upstream = UpstreamServer(('127.0.0.1', 0))
        cls.upstream_thread = threading.Thread(target=cls.upstream.serve_forever)
        cls.upstream_thread.daemon = True
        cls.upstream_thread.start()
        cls.temp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.temp.name)
        (cls.data / 'summaries').mkdir()
        (cls.data / 'summaries' / 'iptv_history.json').write_text(
            json.dumps({'runs': [], 'generated_at': '2026-11-16T00:00:00Z'}), encoding='utf-8')
        (cls.data / 'status.json').write_text(json.dumps({'state': 'complete'}), encoding='utf-8')
        (cls.data / 'published').mkdir()
        port = cls.upstream.server_port
        (cls.data / 'published' / 'iptv_set1.m3u').write_text(
            '#EXTM3U\n'
            '#EXTINF:-1 group-title="Test",频道一\n'
            'http://127.0.0.1:%d/dead/master.m3u8\n'
            '#EXTINF:-1 group-title="Test",频道二\n'
            'http://127.0.0.1:%d/one/master.m3u8\n'
            '#EXTINF:-1 group-title="Test",频道三\n'
            '#EXTVLCOPT:http-user-agent=SpecialUA/1.0\n'
            'http://127.0.0.1:%d/three/master.m3u8\n' % (port, port, port), encoding='utf-8')
        (cls.data / 'published' / 'iptv_set2.m3u').write_text(
            '#EXTM3U\n'
            '#EXTINF:-1 group-title="Test",频道一\n'
            'http://127.0.0.1:%d/two/master.m3u8\n'
            '#EXTINF:-1 group-title="Test",频道二\n'
            'http://127.0.0.1:%d/one/master.m3u8\n'
            '#EXTINF:-1 group-title="Test",频道三\n'
            'http://127.0.0.1:%d/two/master.m3u8\n' % (port, port, port), encoding='utf-8')
        (cls.data / 'published' / 'iptv_set3.m3u').write_text(
            '#EXTM3U\n'
            '#EXTINF:-1 group-title="Test",频道一\n'
            'http://127.0.0.1:%d/one/master.m3u8\n'
            '#EXTINF:-1 group-title="Test",频道二\n'
            'http://127.0.0.1:%d/two/master.m3u8\n'
            '#EXTINF:-1 group-title="Test",频道三\n'
            'http://127.0.0.1:%d/one/master.m3u8\n' % (port, port, port), encoding='utf-8')
        cls.plugin = tv.TVPlugin(cls.PASSWORD, tv.assets_dir(),
                                 cls.PLAYLISTS, '')
        cls.server = web.Server(('127.0.0.1', 0), web.make_handler(cls.data, '', cls.plugin))
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.plugin.close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.temp.cleanup()
        cls.tool_temp.cleanup()
        os.environ.pop('IPTV_TV_FFMPEG', None)

    def client(self):
        return Client(self.server.server_port)

    def logged_in_client(self):
        client = self.client()
        with mock.patch.object(tv.time, 'sleep'):
            status, _, _ = client.request('POST', '/api/tv/login', {'password': self.PASSWORD})
        self.assertEqual(status, 200)
        return client

    def test_player_page_is_served_with_strict_csp(self):
        client = self.client()
        status, headers, data = client.request('GET', '/tv/')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/html'))
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertIn('网页电视'.encode('utf-8'), data)
        status, headers, _ = client.request('GET', '/tv/app.js')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/javascript'))
        status, _, _ = client.request('GET', '/tv/hls.min.js')
        self.assertEqual(status, 200)

    def test_requests_without_login_are_rejected(self):
        client = self.client()
        self.assertEqual(client.request('GET', '/tv/play/0')[0], 401)
        self.assertEqual(client.request('GET', '/tv/stream/1')[0], 401)
        self.assertEqual(client.request('GET', '/api/tv/session')[0], 401)
        status, _, _ = client.request('POST', '/api/tv/source', {'url': 'http://example.com/x.m3u8'})
        self.assertEqual(status, 401)

    def test_wrong_password_is_rejected(self):
        client = self.client()
        with mock.patch.object(tv.time, 'sleep') as sleeper:
            status, _, _ = client.request('POST', '/api/tv/login', {'password': 'nope'})
        self.assertEqual(status, 401)
        sleeper.assert_called_once()

    def test_redirected_manifests_resolve_children_from_final_url(self):
        client = self.logged_in_client()
        port = self.upstream.server_port
        rid = self.plugin.registry.register(
            'http://127.0.0.1:%d/redirect/start.m3u8' % port,
            {'user_agent': 'RedirectUA/1.0'})
        status, _, data = client.request('GET', '/tv/stream/%d' % rid)
        self.assertEqual(status, 200)
        variant = next(line for line in data.decode().splitlines()
                       if line.startswith('/tv/stream/'))
        status, _, data = client.request('GET', variant)
        self.assertEqual(status, 200)
        import re
        children = re.findall(r'/tv/stream/\d+', data.decode())
        expected = [b'0123456789abcdef', b'INITMP4', None,
                    b'SEGMENT-1A', b'SEGMENT-2']
        self.assertEqual(len(children), len(expected))
        for child, body in zip(children, expected):
            status, _, payload = client.request('GET', child)
            self.assertEqual(status, 200)
            if body is None:
                audio_segment = next(line for line in payload.decode().splitlines()
                                     if line.startswith('/tv/stream/'))
                self.assertEqual(client.request('GET', audio_segment)[2], b'SEGMENT-2')
            else:
                self.assertEqual(payload, body)
        self.assertEqual(self.upstream.hits['/one/seg001.ts?token=abc'],
                         'RedirectUA/1.0')

    def test_full_proxy_flow(self):
        client = self.logged_in_client()
        source = 'http://127.0.0.1:%d/list.m3u8' % self.upstream.server_port
        status, _, data = client.request('POST', '/api/tv/source', {'url': source})
        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertEqual(payload['count'], 3)
        self.assertEqual(payload['channels'][0]['name'], '频道一')
        self.assertNotIn(b'output_url', data)

        status, _, data = client.request('GET', '/api/tv/session')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['count'], 3)

        status, headers, data = client.request('GET', '/tv/play/0')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/vnd.apple.mpegurl'))
        master = data.decode('utf-8')
        self.assertNotIn('127.0.0.1', master)
        variant_ids = [line.split('/')[-1] for line in master.splitlines()
                       if line.startswith('/tv/stream/')]
        self.assertEqual(len(variant_ids), 2)

        status, _, data = client.request('GET', '/tv/stream/' + variant_ids[0])
        self.assertEqual(status, 200)
        media = data.decode('utf-8')
        self.assertNotIn('127.0.0.1', media)
        child_ids = [line.split('/')[-1].rstrip('"') for line in media.splitlines()
                     if '/tv/stream/' in line]
        self.assertEqual(len(child_ids), 5)

        segment_id = [line.split('/')[-1] for line in media.splitlines()
                      if line.startswith('/tv/stream/')][0]
        status, headers, data = client.request('GET', '/tv/stream/' + segment_id)
        self.assertEqual(status, 200)
        self.assertEqual(data, b'SEGMENT-1A')

        key_line = [line for line in media.splitlines() if line.startswith('#EXT-X-KEY')][0]
        key_id = key_line.split('/tv/stream/')[1].rstrip('"')
        status, _, data = client.request('GET', '/tv/stream/' + key_id)
        self.assertEqual(status, 200)
        self.assertEqual(data, b'0123456789abcdef')

        status, _, data = client.request('GET', '/tv/stream/' + variant_ids[1])
        self.assertEqual(status, 200)
        self.assertIn(b'#EXTINF', data)

        self.assertEqual(client.request('GET', '/tv/stream/99999')[0], 404)
        self.assertEqual(client.request('GET', '/tv/play/7')[0], 404)

    def test_channel_specific_user_agent_is_forwarded(self):
        client = self.logged_in_client()
        source = 'http://127.0.0.1:%d/list.m3u8' % self.upstream.server_port
        client.request('POST', '/api/tv/source', {'url': source})
        status, _, _ = client.request('GET', '/tv/play/0')
        self.assertEqual(status, 200)
        status, _, _ = client.request('GET', '/tv/play/2')
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream.hits['/three/master.m3u8'], 'SpecialUA/1.0')
        self.assertNotEqual(self.upstream.hits['/one/master.m3u8'], 'SpecialUA/1.0')

    def test_blocked_and_invalid_sources_are_refused(self):
        client = self.logged_in_client()
        status, _, data = client.request('POST', '/api/tv/source',
                                        {'url': 'http://169.254.169.254/latest/meta-data/'})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(data)['error'], 'blocked_url')
        status, _, _ = client.request('POST', '/api/tv/source', {'url': 'ftp://example.com/x'})
        self.assertEqual(status, 400)
        status, _, data = client.request('POST', '/api/tv/source',
                                        {'url': 'http://127.0.0.1:%d/missing.m3u8' % self.upstream.server_port})
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(data)['error'], 'source_fetch_failed')

    def test_dashboard_links_to_player(self):
        client = self.client()
        status, _, data = client.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'href="/tv/"', data)

    def test_playlists_endpoint_lists_published_sources(self):
        client = self.client()
        status, _, data = client.request('GET', '/api/tv/playlists')
        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertEqual([item['key'] for item in payload['playlists']],
                         ['top5', 'live', 'english'])
        self.assertEqual(payload['playlists'][0]['url'],
                         'http://127.0.0.1:%d/iptv_ok.m3u' % self.server.server_port)

    def test_set_chain_falls_back_to_backup_sets(self):
        client = self.logged_in_client()
        source = 'http://127.0.0.1:%d/iptv_set1.m3u' % self.server.server_port
        status, _, data = client.request('POST', '/api/tv/source', {'url': source})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['sets'], 3)
        status, _, data = client.request('GET', '/api/tv/session')
        self.assertEqual(json.loads(data)['sets'], 3)

        self.assertEqual(client.request('GET', '/tv/play/0')[0], 502)
        self.assertEqual(client.request('GET', '/tv/play/0/0')[0], 502)
        status, headers, data = client.request('GET', '/tv/play/0/1')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/vnd.apple.mpegurl'))
        self.assertIn(b'BANDWIDTH=800000', data)
        status, _, data = client.request('GET', '/tv/play/0/2')
        self.assertEqual(status, 200)
        self.assertIn(b'BANDWIDTH=1000000', data)
        self.assertEqual(client.request('GET', '/tv/play/0/3')[0], 404)

    def test_backup_matches_channel_identity_after_reordering_or_removal(self):
        from iptv_qa.common.playlists import parse_playlist
        client = self.logged_in_client()
        source = 'http://127.0.0.1:%d/iptv_set1.m3u' % self.server.server_port
        self.assertEqual(client.request('POST', '/api/tv/source', {'url': source})[0], 200)
        session = self.plugin.sessions.get(client.cookie.split('=', 1)[1])
        port = self.upstream.server_port
        reordered = parse_playlist('#EXTM3U\n#EXTINF:-1,频道二\n'
            'http://127.0.0.1:%d/one/master.m3u8\n#EXTINF:-1,频道一\n'
            'http://127.0.0.1:%d/two/master.m3u8\n' % (port, port))
        session['sets'][session['chain'][1]] = reordered
        status, _, data = client.request('GET', '/tv/play/0/1')
        self.assertEqual(status, 200)
        self.assertIn(b'BANDWIDTH=800000', data)
        session['sets'][session['chain'][1]] = reordered[:1]
        status, _, data = client.request('GET', '/tv/play/0/1')
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(data)['error'], 'channel_not_in_backup')

    def test_english_preset_is_independent(self):
        target = self.data / 'english/published'
        target.mkdir(parents=True, exist_ok=True)
        (target / 'iptv_top3.m3u').write_text('#EXTM3U\n#EXTINF:-1,English News\n'
            'http://127.0.0.1:%d/one/master.m3u8\n' % self.upstream.server_port, encoding='utf-8')
        client = self.logged_in_client()
        source = 'http://127.0.0.1:%d/iptv_english.m3u' % self.server.server_port
        status, _, data = client.request('POST', '/api/tv/source', {'url': source})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['sets'], 1)
        self.assertEqual(client.request('GET', '/tv/play/0')[0], 200)
        self.assertEqual(client.request('GET', '/tv/play/0/1')[0], 404)

    def test_custom_source_has_no_backup_chain(self):
        client = self.logged_in_client()
        source = 'http://127.0.0.1:%d/list.m3u8' % self.upstream.server_port
        status, _, data = client.request('POST', '/api/tv/source', {'url': source})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['sets'], 1)
        self.assertEqual(client.request('GET', '/tv/play/0/1')[0], 404)

    def test_live_view_is_independent_and_served_outside_scheduled_publication(self):
        extras = self.data / 'extras'
        extras.mkdir(exist_ok=True)
        content = ('#EXTM3U\n#EXTINF:-1,独立频道\n'
                   'http://127.0.0.1:%d/one/master.m3u8\n' % self.upstream.server_port)
        (extras / 'iptv_live.m3u').write_text(content, encoding='utf-8')
        client = self.logged_in_client()
        status, _, data = client.request('GET', '/iptv_live.m3u')
        self.assertEqual(status, 200)
        self.assertEqual(data, (extras / 'iptv_live.m3u').read_bytes())
        source = 'http://127.0.0.1:%d/iptv_live.m3u' % self.server.server_port
        status, _, data = client.request('POST', '/api/tv/source', {'url': source})
        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertEqual(payload['sets'], 1)
        self.assertEqual([c['name'] for c in payload['channels']], ['独立频道'])
        self.assertEqual(client.request('GET', '/tv/play/0')[0], 200)
        self.assertEqual(client.request('GET', '/tv/play/0/1')[0], 404)

    def test_transcode_flow_serves_local_segments(self):
        client = self.logged_in_client()
        source = 'http://127.0.0.1:%d/list.m3u8' % self.upstream.server_port
        client.request('POST', '/api/tv/source', {'url': source})
        status, headers, data = client.request('GET', '/tv/transcode/0')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/vnd.apple.mpegurl'))
        text = data.decode('utf-8')
        segment_path=next(line for line in text.splitlines() if line.startswith('/tv/tseg/'))
        self.assertTrue(segment_path.endswith('/seg00000.ts'))
        self.assertNotIn('seg%05d', text)
        status, _, data = client.request('GET', segment_path)
        self.assertEqual(status, 200)
        self.assertEqual(data, b'FAKESEG000')
        self.assertEqual(client.request('GET', segment_path.rsplit('/',1)[0]+'/missing.ts')[0], 404)
        self.assertEqual(client.request('GET', segment_path.rsplit('/',1)[0]+'/..')[0], 404)
        self.assertEqual(client.request('GET', '/tv/transcode/99')[0], 404)

    def test_unknown_post_returns_404(self):
        client = self.logged_in_client()
        self.assertEqual(client.request('POST', '/api/unknown')[0], 404)


class TokenRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = UpstreamServer(('127.0.0.1', 0))
        cls.upstream_thread = threading.Thread(target=cls.upstream.serve_forever)
        cls.upstream_thread.daemon = True
        cls.upstream_thread.start()
        port = cls.upstream.server_port
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        (root / 'summaries').mkdir()
        (root / 'summaries' / 'iptv_history.json').write_text(
            json.dumps({'runs': [], 'generated_at': '2026-11-18T00:00:00Z'}), encoding='utf-8')
        (root / 'status.json').write_text(json.dumps({'state': 'complete'}), encoding='utf-8')
        from iptv_qa.library.store import Library
        cls.library = Library(root / 'library' / 'catalog.sqlite3')
        content = ('#EXTM3U\n'
                   '#EXTINF:-1 tvg-id="race.test" tvg-logo="http://logo.test/race.png" '
                   'group-title="Race",竞速频道\n'
                   'http://127.0.0.1:%d/two/master.m3u8\n'
                   '#EXTINF:-1 tvg-id="race.test" group-title="Race",竞速频道\n'
                   'http://127.0.0.1:%d/one/master.m3u8\n' % (port, port))
        cls.library.ingest('main', content, 'token-tests')
        cls.channel = cls.library.view('main')[0]
        cls.race_state = {'fail': False, 'calls': 0}

        def fake_race(lines):
            cls.race_state['calls'] += 1
            if cls.race_state['fail']:
                return None
            return {'index': 1, 'url': lines[1]['url'], 'headers': lines[1]['headers'],
                    'final_url': lines[1]['url']}

        cls.off_plugin = tv.TVPlugin('pw', static_dir=tv.assets_dir(), library=cls.library)
        os.environ['IPTV_TV_TOKENS'] = 'tok-alpha, tok-beta'
        try:
            cls.plugin = tv.TVPlugin('pw', static_dir=tv.assets_dir(), library=cls.library,
                                     race_func=fake_race)
            cls.nolib_plugin = tv.TVPlugin('pw', static_dir=tv.assets_dir())
            cls.base_plugin = tv.TVPlugin('pw', static_dir=tv.assets_dir(), library=cls.library,
                                          public_base='https://bucket.example.test',
                                          race_func=fake_race)
        finally:
            os.environ.pop('IPTV_TV_TOKENS', None)
        cls.server = web.Server(('127.0.0.1', 0), web.make_handler(root, '', cls.plugin))
        cls.off_server = web.Server(('127.0.0.1', 0), web.make_handler(root, '', cls.off_plugin))
        cls.nolib_server = web.Server(('127.0.0.1', 0), web.make_handler(root, '', cls.nolib_plugin))
        cls.base_server = web.Server(('127.0.0.1', 0), web.make_handler(root, '', cls.base_plugin))
        cls.threads = []
        for server in (cls.server, cls.off_server, cls.nolib_server, cls.base_server):
            thread = threading.Thread(target=server.serve_forever)
            thread.daemon = True
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.server, cls.off_server, cls.nolib_server, cls.base_server):
            server.shutdown()
            server.server_close()
        cls.plugin.close()
        cls.off_plugin.close()
        cls.nolib_plugin.close()
        cls.base_plugin.close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.temp.cleanup()

    def client(self):
        return Client(self.server.server_port)

    def channel_path(self, view='main'):
        return '/tv/race/%s/%s' % (view, quote(self.channel['id'], safe=''))

    def clear_race_cache(self):
        with self.plugin.race_lock:
            self.plugin.race_cache.clear()

    def test_endpoints_are_404_without_configured_tokens(self):
        client = Client(self.off_server.server_port)
        self.assertEqual(client.request('GET', '/tv/playlist.m3u?token=tok-alpha')[0], 404)
        self.assertEqual(client.request('GET', self.channel_path() + '?token=tok-alpha')[0], 404)
        self.assertEqual(client.request('GET', '/tv/rstream/1?token=tok-alpha')[0], 404)

    def test_playlist_rejects_missing_wrong_token_or_missing_library(self):
        client = self.client()
        self.assertEqual(client.request('GET', '/tv/playlist.m3u')[0], 401)
        self.assertEqual(client.request('GET', '/tv/playlist.m3u?token=wrong')[0], 401)
        nolib = Client(self.nolib_server.server_port)
        self.assertEqual(nolib.request('GET', '/tv/playlist.m3u?token=tok-alpha')[0], 404)

    def test_playlist_race_urls_use_request_host_not_public_base(self):
        client = Client(self.base_server.server_port)
        status, _, data = client.request('GET', '/tv/playlist.m3u?token=tok-beta')
        self.assertEqual(status, 200)
        text = data.decode('utf-8')
        self.assertIn('http://127.0.0.1:%d/tv/race/main/' % self.base_server.server_port, text)
        self.assertNotIn('bucket.example.test', text)

    def test_playlist_renders_one_race_url_per_channel(self):
        client = self.client()
        status, headers, data = client.request('GET', '/tv/playlist.m3u?token=tok-beta')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/vnd.apple.mpegurl'))
        text = data.decode('utf-8')
        self.assertEqual(text.count('#EXTINF:'), 1)
        self.assertIn('tvg-id="race.test"', text)
        self.assertIn('tvg-logo="http://logo.test/race.png"', text)
        self.assertIn('group-title="Race"', text)
        self.assertIn('竞速频道', text)
        expected = 'http://127.0.0.1:%d%s?token=tok-beta' % (
            self.server.server_port, self.channel_path())
        self.assertIn(expected, text)
        status, _, _ = client.request('GET', '/tv/playlist.m3u?token=tok-beta&view=bogus')
        self.assertEqual(status, 404)

    def test_race_requires_valid_token_and_known_channel(self):
        client = self.client()
        self.assertEqual(client.request('GET', self.channel_path())[0], 401)
        self.assertEqual(client.request('GET', self.channel_path() + '?token=wrong')[0], 401)
        self.assertEqual(
            client.request('GET', self.channel_path() + '?token=tok-alpha&x=1')[0], 200)
        self.assertEqual(
            client.request('GET', '/tv/race/main/missing-id?token=tok-alpha')[0], 404)
        self.assertEqual(
            client.request('GET', self.channel_path('bogus') + '?token=tok-alpha')[0], 404)

    def test_race_relays_winner_and_rewrites_children_with_token(self):
        self.clear_race_cache()
        client = self.client()
        status, headers, data = client.request(
            'GET', self.channel_path() + '?token=tok-alpha')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/vnd.apple.mpegurl'))
        text = data.decode('utf-8')
        self.assertIn('BANDWIDTH=1000000', text)
        children = re.findall(r'/tv/rstream/(\d+)\?token=tok-alpha', text)
        self.assertEqual(len(children), 2)
        status, _, data = client.request('GET', '/tv/rstream/%s?token=tok-alpha' % children[0])
        self.assertEqual(status, 200)
        media = data.decode('utf-8')
        self.assertIn('/tv/rstream/', media)
        self.assertIn('token=tok-alpha', media)
        self.assertNotIn('/tv/stream/', media)
        self.assertEqual(client.request('GET', '/tv/rstream/%s' % children[0])[0], 401)
        self.assertEqual(
            client.request('GET', '/tv/rstream/%s?token=wrong' % children[0])[0], 401)
        self.assertEqual(client.request('GET', '/tv/rstream/99999?token=tok-alpha')[0], 404)

    def test_race_falls_back_to_line_zero_when_no_winner(self):
        self.clear_race_cache()
        self.race_state['fail'] = True
        try:
            client = self.client()
            status, _, data = client.request(
                'GET', self.channel_path() + '?token=tok-alpha')
            self.assertEqual(status, 200)
            self.assertIn('BANDWIDTH=800000', data.decode('utf-8'))
        finally:
            self.race_state['fail'] = False

    def test_race_winner_is_cached_across_requests(self):
        self.clear_race_cache()
        client = self.client()
        before = self.race_state['calls']
        status, _, first = client.request('GET', self.channel_path() + '?token=tok-alpha')
        self.assertEqual(status, 200)
        status, _, second = client.request('GET', self.channel_path() + '?token=tok-alpha')
        self.assertEqual(status, 200)
        self.assertEqual(self.race_state['calls'], before + 1)
        self.assertEqual(first, second)

    def test_token_does_not_unlock_management_or_browser_endpoints(self):
        client = self.client()
        self.assertEqual(client.request('GET', '/api/library/state?token=tok-alpha')[0], 401)
        self.assertEqual(client.request('GET', '/api/tv/session?token=tok-alpha')[0], 401)
        self.assertEqual(client.request('GET', '/tv/play/0?token=tok-alpha')[0], 401)
        self.assertEqual(client.request('GET', '/tv/stream/1?token=tok-alpha')[0], 401)


class PrefetchIntegrationTests(unittest.TestCase):
    WINNER = {'index': 0, 'url': 'http://u/1.m3u8', 'headers': {}, 'final_url': 'http://u/1.m3u8'}

    class _Lib:
        def __init__(self, rows):
            self._rows = rows

        def view(self, scope):
            return self._rows

    def _rows(self):
        return [
            {'id': 'c1', 'name': '1', 'group_name': 'g', 'lines': [{'url': 'http://u/1.m3u8', 'headers': {}}]},
            {'id': 'c2', 'name': '2', 'group_name': 'g', 'lines': [{'url': 'http://u/2.m3u8', 'headers': {}}]},
        ]

    def test_prefetch_next_warms_following_channel(self):
        plugin = TVPlugin(b'pw', library=self._Lib(self._rows()))
        plugin.prefetcher.race_func = lambda lines: self.WINNER
        plugin._prefetch_next('main', 'c1')
        deadline = time.time() + 2
        while time.time() < deadline and plugin.prefetcher.get('main', 'c2') is None:
            time.sleep(0.02)
        self.assertEqual(plugin.prefetcher.get('main', 'c2'), self.WINNER)
        plugin.close()

    def test_race_winner_uses_prefetched_without_racing(self):
        def boom(lines):
            raise AssertionError('should not race')
        plugin = TVPlugin(b'pw', race_func=boom)
        plugin.prefetcher.race_func = lambda lines: self.WINNER
        plugin.prefetcher.warm('main', 'c', [], wait=True)
        got = plugin._race_winner('main', 'c', [{'url': 'http://u/x.m3u8', 'headers': {}}])
        self.assertEqual(got, self.WINNER)
        plugin.close()

    class _Handler:
        def __init__(self, path, body):
            self.path = path
            self.command = 'POST'
            self.headers = {'Content-Length': str(len(body))}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.status = None

        def send_response(self, code):
            self.status = code

        def send_header(self, key, value):
            pass

        def end_headers(self):
            pass

    def test_prefetch_endpoint_warms_next_with_given_mode(self):
        rows = self._rows()
        plugin = TVPlugin(b'pw')
        plugin.prefetcher.race_func = lambda lines: self.WINNER
        session = {'source': 'x', 'library_scope': 'main', 'channels': rows,
                   'source_id': 'sid', 'chain': ['x'], 'sets': {}}
        plugin.playback_session = lambda handler: session
        plugin.authorized = lambda handler: True
        body = json.dumps({'mode': 'light', 'index': 0}).encode('utf-8')
        handler = self._Handler('/api/tv/prefetch', body)
        self.assertTrue(plugin.handle_prefetch(handler))
        deadline = time.time() + 2
        while time.time() < deadline and plugin.prefetcher.get('main', 'c2') is None:
            time.sleep(0.02)
        self.assertEqual(plugin.prefetcher.get('main', 'c2'), self.WINNER)
        plugin.close()

    def test_prefetch_endpoint_off_does_not_warm(self):
        rows = self._rows()
        plugin = TVPlugin(b'pw')
        plugin.prefetcher.race_func = lambda lines: self.WINNER
        session = {'source': 'x', 'library_scope': 'main', 'channels': rows,
                   'source_id': 'sid', 'chain': ['x'], 'sets': {}}
        plugin.playback_session = lambda handler: session
        plugin.authorized = lambda handler: True
        handler = self._Handler('/api/tv/prefetch', json.dumps({'mode': 'off', 'index': 0}).encode('utf-8'))
        plugin.handle_prefetch(handler)
        self.assertIsNone(plugin.prefetcher.get('main', 'c2'))
        plugin.close()


class PrefetchConfigTests(unittest.TestCase):
    def test_create_player_passes_prefetch_mode(self):
        from iptv_qa.server.http import create_player
        plugin = create_player('pw', prefetch_mode='off')
        self.assertEqual(plugin.prefetcher.mode, 'off')
        plugin.close()

    def test_invalid_mode_falls_back_to_light(self):
        from iptv_qa.server.http import create_player
        plugin = create_player('pw', prefetch_mode='bogus')
        self.assertEqual(plugin.prefetcher.mode, 'light')
        plugin.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
