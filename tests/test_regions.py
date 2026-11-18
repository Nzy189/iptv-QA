import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from iptv_qa.server import http as dashboard, regions


class RegionsTest(unittest.TestCase):
    def test_disabled_and_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'regions.json'
            p.write_text('{"enabled": false}', encoding='utf-8')
            self.assertIsNone(regions.load_regions(str(p)))
            p.write_text(json.dumps({'enabled': True, 'nodes': [{'name': 'A', 'url': 'http://user:pass@example.test'}]}), encoding='utf-8')
            with self.assertRaises(ValueError):
                regions.load_regions(str(p))

    def test_combined_page_and_regional_progress(self):
        with tempfile.TemporaryDirectory() as d:
            summary = Path(d) / 'summaries'
            summary.mkdir()
            (summary / 'iptv_history.json').write_text(json.dumps({'runs': [], 'generated_at': '2026-01-01T00:00:00Z'}), encoding='utf-8')
            config = {'local_name': 'Riyadh', 'nodes': [{'name': 'Guangzhou', 'url': 'http://example.test', 'audit': True}]}
            def remote(node, path):
                if path == '/api/history':
                    return {'runs': [], 'generated_at': '2026-01-01T00:00:00Z'}
                if path == '/api/guangzhou-audit':
                    return {'state': 'running', 'stage': 'decode1', 'completed': 2, 'total': 5}
                raise OSError('first scheduled run has not started')
            with mock.patch.object(regions, 'fetch_json', side_effect=remote):
                srv = dashboard.Server(('127.0.0.1', 0), dashboard.make_handler(d, '', regions=config))
                thread = threading.Thread(target=srv.serve_forever, daemon=True)
                thread.start()
                try:
                    conn = http.client.HTTPConnection('127.0.0.1', srv.server_port)
                    conn.request('GET', '/')
                    res = conn.getresponse()
                    page = res.read().decode('utf-8')
                    self.assertEqual(res.status, 200)
                    self.assertLess(page.index('Riyadh检测结果'), page.index('Guangzhou检测结果'))
                    self.assertNotIn('<iframe', page)
                    self.assertIn("frame-ancestors 'none'", res.getheader('Content-Security-Policy'))
                    conn.request('GET', '/api/regions/0')
                    res = conn.getresponse()
                    payload = json.loads(res.read().decode('utf-8'))
                    self.assertEqual(res.status, 200)
                    self.assertEqual(payload['audit']['completed'], 2)
                finally:
                    srv.shutdown()
                    srv.server_close()
                    thread.join()
