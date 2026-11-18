import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from iptv_qa.playback import race


def make_upstream(server):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def reply(self, body, ctype='application/vnd.apple.mpegurl', status=200):
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/dead/media.m3u8':
                self.reply(b'gone', status=404)
            elif path == '/slow/media.m3u8':
                time.sleep(3)
                self.reply(FAST_MEDIA)
            elif path == '/slowseg/media.m3u8':
                self.reply(FAST_MEDIA)
            elif path == '/slowseg/seg.ts':
                time.sleep(3)
                self.reply(b'\x47' * 188, 'video/mp2t')
            elif path == '/ua/media.m3u8':
                if self.headers.get('User-Agent') != 'SpecialUA/1.0':
                    self.reply(b'blocked', status=404)
                    return
                self.reply(FAST_MEDIA)
            elif path == '/ua/seg.ts':
                if self.headers.get('User-Agent') != 'SpecialUA/1.0':
                    self.reply(b'blocked', status=404)
                    return
                self.reply(b'\x47' * 188, 'video/mp2t')
            elif path == '/redirect/start.m3u8':
                self.send_response(302)
                self.send_header('Location', '/redirect/final/media.m3u8')
                self.send_header('Content-Length', '0')
                self.end_headers()
            elif path == '/redirect/final/media.m3u8':
                self.reply(FAST_MEDIA)
            elif path == '/redirect/final/seg.ts':
                self.reply(b'\x47' * 188, 'video/mp2t')
            elif path == '/master/master.m3u8':
                self.reply(b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nvariant.m3u8\n')
            elif path == '/master/variant.m3u8':
                self.reply(FAST_MEDIA)
            elif path == '/master/seg.ts':
                self.reply(b'\x47' * 188, 'video/mp2t')
            elif path == '/bigseg/media.m3u8':
                self.reply(FAST_MEDIA)
            elif path == '/bigseg/seg.ts':
                self.reply(b'\x47' * (1024 * 1024), 'video/mp2t')
            elif path == '/empty/media.m3u8':
                self.reply(FAST_MEDIA)
            elif path == '/empty/seg.ts':
                self.reply(b'', 'video/mp2t')
            elif path == '/fast/media.m3u8':
                self.reply(FAST_MEDIA)
            elif path == '/fast/seg.ts':
                self.reply(b'\x47' * 188, 'video/mp2t')
            else:
                self.reply(b'not found', status=404)

    return Handler


FAST_MEDIA = (b'#EXTM3U\n'
              b'#EXTINF:6.0,\n'
              b'seg.ts\n'
              b'#EXT-X-ENDLIST\n')


def line(url, headers=None):
    return {'url': url, 'headers': headers or {}}


class RaceLinesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), make_upstream(cls))
        cls.server.daemon_threads = True
        import threading
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def url(self, path):
        return 'http://127.0.0.1:%d%s' % (self.port, path)

    def test_fast_line_beats_slow_manifest(self):
        winner = race.race_lines([line(self.url('/slow/media.m3u8')),
                                  line(self.url('/fast/media.m3u8'))], budget=10.0)
        self.assertEqual(winner['index'], 1)
        self.assertEqual(winner['url'], self.url('/fast/media.m3u8'))
        self.assertEqual(winner['final_url'], self.url('/fast/media.m3u8'))
        self.assertEqual(winner['headers'], {})

    def test_fast_line_beats_slow_segment(self):
        winner = race.race_lines([line(self.url('/slowseg/media.m3u8')),
                                  line(self.url('/fast/media.m3u8'))], budget=10.0)
        self.assertEqual(winner['index'], 1)

    def test_dead_lines_are_ignored(self):
        winner = race.race_lines([line(self.url('/dead/media.m3u8')),
                                  line(self.url('/dead/media.m3u8')),
                                  line(self.url('/fast/media.m3u8'))], budget=10.0)
        self.assertEqual(winner['index'], 2)

    def test_user_agent_header_is_forwarded(self):
        headers = {'user_agent': 'SpecialUA/1.0'}
        winner = race.race_lines([line(self.url('/ua/media.m3u8'), headers)], budget=10.0)
        self.assertEqual(winner['index'], 0)
        self.assertEqual(winner['headers'], headers)
        self.assertIsNone(race.race_lines([line(self.url('/ua/media.m3u8'))], budget=2.0))

    def test_redirect_resolves_against_final_url(self):
        winner = race.race_lines([line(self.url('/redirect/start.m3u8'))], budget=10.0)
        self.assertEqual(winner['index'], 0)
        self.assertEqual(winner['final_url'], self.url('/redirect/final/media.m3u8'))

    def test_master_playlist_variant_path(self):
        winner = race.race_lines([line(self.url('/master/master.m3u8'))], budget=10.0)
        self.assertEqual(winner['index'], 0)
        self.assertEqual(winner['final_url'], self.url('/master/master.m3u8'))

    def test_all_failures_return_none(self):
        self.assertIsNone(race.race_lines([line(self.url('/dead/media.m3u8'))], budget=2.0))
        self.assertIsNone(race.race_lines([], budget=2.0))

    def test_budget_expires_without_hanging(self):
        started = time.time()
        self.assertIsNone(race.race_lines([line(self.url('/slow/media.m3u8'))], budget=1.0))
        self.assertLess(time.time() - started, 3.0)

    def test_oversized_segment_still_qualifies(self):
        winner = race.race_lines([line(self.url('/bigseg/media.m3u8'))],
                                 budget=10.0, segment_cap=262144)
        self.assertEqual(winner['index'], 0)

    def test_empty_segment_does_not_qualify(self):
        self.assertIsNone(race.race_lines([line(self.url('/empty/media.m3u8'))], budget=2.0))


if __name__ == '__main__':
    unittest.main()
