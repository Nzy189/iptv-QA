import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest import mock

import iptv_checker as app
import reporting
import web


class SetTests(unittest.TestCase):
    def playlist(self):
        lines = ['#EXTM3U x-tvg-url="https://epg.test/xml"']
        for name, count in [('One', 1), ('Two', 2), ('Three', 3)]:
            for n in range(count):
                lines += ['#EXTINF:-1 group-title="TV",' + name,
                          'http://host{}.test/{}/live'.format(n, name)]
        return '\n'.join(lines) + '\n'

    def test_three_sets_equal_channels_and_fallback_to_last_available(self):
        files, stats = app.split_ranked_playlist(self.playlist())
        parsed = [app.parse_playlist(files['iptv_set{}.m3u'.format(i)]) for i in (1, 2, 3)]
        for entries in parsed:
            self.assertEqual([e['name'] for e in entries], ['One', 'Two', 'Three'])
            self.assertEqual(len({e['name'] for e in entries}), 3)
        self.assertEqual(parsed[0][0]['output_url'], parsed[2][0]['output_url'])
        self.assertNotEqual(parsed[0][1]['output_url'], parsed[1][1]['output_url'])
        self.assertEqual(parsed[1][1]['output_url'], parsed[2][1]['output_url'])
        self.assertEqual(len({p[2]['output_url'] for p in parsed}), 3)
        self.assertEqual(stats['line_distribution'], {'1': 1, '2': 1, '3': 1})
        self.assertEqual(stats['channels_with_backup'], 2)

    def test_sets_merge_repeated_channel_groups_and_duplicate_urls(self):
        text = '#EXTM3U\n#EXTINF:-1 group-title="First",CCTV5\nhttp://one.test/live\n#EXTINF:-1 group-title="Second",CCTV-5 体育\nhttp://two.test/live\n#EXTINF:-1 group-title="First",CCTV5\nhttp://one.test/live\n'
        files, stats = app.split_ranked_playlist(text)
        self.assertEqual(stats['channels_per_set'], 1)
        self.assertEqual(stats['line_distribution']['2'], 1)
        for value in files.values():
            entries = app.parse_playlist(value)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['group'], 'First')

    def test_empty_results_produce_empty_sets(self):
        files, stats = app.split_ranked_playlist('#EXTM3U\n')
        self.assertEqual(stats['channels_per_set'], 0)
        self.assertTrue(all(text == '#EXTM3U\n' for text in files.values()))

    def test_daily_upload_publishes_existing_lists_and_three_new_sets(self):
        client = mock.Mock()
        client.putFile.return_value.status = 200
        sdk = types.SimpleNamespace(ObsClient=mock.Mock(return_value=client), PutObjectHeader=types.SimpleNamespace)
        modules = {'obs': sdk, 'certifi': types.SimpleNamespace(where=lambda: 'test-ca.pem')}
        cfg = {'enabled': True, 'endpoint': 'https://obs.example.test', 'bucket': 'example-bucket', 'prefix': '',
               'upload_report': False, 'publish_sets': True, 'object_names': {'iptv_top5.m3u': 'iptv_ok.m3u'}}
        with mock.patch.dict('sys.modules', modules), mock.patch.dict(os.environ, {'OBS_ACCESS_KEY_ID': 'test-ak', 'OBS_SECRET_ACCESS_KEY': 'test-sk'}):
            result = app.publish_obs(Path('/unused'), cfg)
        self.assertEqual(result['objects'], ['iptv_ok.m3u', 'iptv_top3.m3u', 'iptv_set1.m3u', 'iptv_set2.m3u', 'iptv_set3.m3u'])

    @unittest.skipIf(os.name == 'nt', 'Production publication uses Linux symlinks')
    def test_generate_existing_sets_does_not_retest_or_create_a_new_run(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            export = data / 'runs/20261115T000000Z-11111111/export'
            export.mkdir(parents=True)
            (export / 'iptv_top5.m3u').write_text(self.playlist(), encoding='utf-8')
            (export / 'report.json').write_text(json.dumps({'published': True, 'channels_available': 3}))
            app.publish_local(data, export)
            with mock.patch.object(app, 'probe') as probe, mock.patch.object(app, 'decode') as decode:
                app.generate_current_sets({'data_dir': str(data), 'obs': {'enabled': False}})
                probe.assert_not_called()
                decode.assert_not_called()
            self.assertEqual(len(list((data / 'runs').iterdir())), 1)
            self.assertTrue((data / 'published/iptv_set3.m3u').is_file())


@unittest.skipIf(os.name == 'nt', 'HTTP service integration is validated on the ECS')
class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.temp.name)
        (cls.data / 'runs').mkdir()
        reporting.refresh(cls.data)
        (cls.data / 'status.json').write_text(json.dumps({'state': 'complete', 'stage': 'finished', 'secret': 'DO_NOT_EXPOSE'}))
        cls.server = web.Server(('127.0.0.1', 0), web.make_handler(cls.data, 'https://public.example.test'))
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def get(self, path, method='GET'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        conn.request(method, path)
        response = conn.getresponse()
        data, status, headers = response.read(), response.status, dict(response.getheaders())
        conn.close()
        return status, headers, data

    def test_page_is_inline_live_and_readable(self):
        status, headers, data = self.get('/')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/html'))
        self.assertNotIn('Content-Disposition', headers)
        self.assertIn('IPTV 检测汇总'.encode('utf-8'), data)
        self.assertIn(b'/api/status', data)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.get('/', 'HEAD')[2], b'')

    def test_only_sanitized_status_fields_are_public(self):
        status, _, data = self.get('/api/status')
        self.assertEqual(status, 200)
        self.assertNotIn(b'DO_NOT_EXPOSE', data)
        self.assertEqual(json.loads(data)['run']['state'], 'complete')

    def test_private_files_and_traversal_are_never_served(self):
        for path in ['/../history.sqlite3', '/%2e%2e/config.json', '/obs.env', '/status.json', '/runs/', '/api/config']:
            self.assertEqual(self.get(path)[0], 404, path)

    def test_playlist_links_redirect_to_obs_and_csv_downloads(self):
        status, headers, _ = self.get('/iptv_set2.m3u')
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], 'https://public.example.test/iptv_set2.m3u')
        status, headers, _ = self.get('/iptv_history.csv')
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
