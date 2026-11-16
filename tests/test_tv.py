import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from unittest import mock

import tv
import web


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
            elif self.path == '/one/master.m3u8':
                content_type = 'application/vnd.apple.mpegurl'
                body = ('#EXTM3U\n'
                        '#EXT-X-STREAM-INF:BANDWIDTH=1000000\n'
                        'media.m3u8\n'
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
    PLAYLISTS = (('set1', 'Set1', 'iptv_set1.m3u'), ('set2', 'Set2', 'iptv_set2.m3u'),
                 ('set3', 'Set3', 'iptv_set3.m3u'), ('top3', 'Top3', 'iptv_top3.m3u'))

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
        cls.plugin = tv.TVPlugin(cls.PASSWORD, Path(__file__).resolve().parent.parent / 'webtv',
                                 cls.PLAYLISTS, 'https://public.example.test')
        cls.server = web.Server(('127.0.0.1', 0), web.make_handler(cls.data, '', cls.plugin))
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
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
                         ['set1', 'set2', 'set3', 'top3'])
        self.assertEqual(payload['playlists'][0]['url'],
                         'https://public.example.test/iptv_set1.m3u')

    def test_transcode_flow_serves_local_segments(self):
        client = self.logged_in_client()
        source = 'http://127.0.0.1:%d/list.m3u8' % self.upstream.server_port
        client.request('POST', '/api/tv/source', {'url': source})
        status, headers, data = client.request('GET', '/tv/transcode/0')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/vnd.apple.mpegurl'))
        text = data.decode('utf-8')
        self.assertIn('/tv/tseg/0/seg00000.ts', text)
        self.assertNotIn('seg%05d', text)
        status, _, data = client.request('GET', '/tv/tseg/0/seg00000.ts')
        self.assertEqual(status, 200)
        self.assertEqual(data, b'FAKESEG000')
        self.assertEqual(client.request('GET', '/tv/tseg/0/missing.ts')[0], 404)
        self.assertEqual(client.request('GET', '/tv/tseg/0/..')[0], 404)
        self.assertEqual(client.request('GET', '/tv/transcode/99')[0], 404)

    def test_unknown_post_returns_404(self):
        client = self.logged_in_client()
        self.assertEqual(client.request('POST', '/api/unknown')[0], 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
