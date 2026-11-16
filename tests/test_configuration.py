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
import web


class ConfigurationTests(unittest.TestCase):
    def test_example_runs_without_obs_credentials(self):
        path = Path(__file__).resolve().parents[1] / 'config/config.example.json'
        cfg = json.loads(path.read_text(encoding='utf-8'))
        self.assertFalse(cfg['obs']['enabled'])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app.publish_obs(Path('/unused'), cfg['obs'])['state'], 'not_configured')

    def test_region_derives_endpoint_but_explicit_endpoint_wins(self):
        result = app.obs_settings({'enabled': True, 'region': 'me-east-1'})
        self.assertEqual(result['endpoint'], 'https://obs.me-east-1.myhuaweicloud.com')
        self.assertEqual(app.obs_settings(dict(result, endpoint='https://custom.example.test'))['endpoint'], 'https://custom.example.test')
        with self.assertRaises(ValueError):
            app.obs_settings({'enabled': True, 'region': '../bad'})

    def test_cache_policy_and_retry_count_are_configurable(self):
        client = mock.Mock()
        client.putFile.return_value.status = 503
        sdk = types.SimpleNamespace(ObsClient=mock.Mock(return_value=client), PutObjectHeader=types.SimpleNamespace)
        with mock.patch.dict('sys.modules', {'obs': sdk, 'certifi': types.SimpleNamespace(where=lambda: 'test-ca')}), \
             mock.patch.dict(os.environ, {'OBS_ACCESS_KEY_ID': 'test-ak', 'OBS_SECRET_ACCESS_KEY': 'test-sk'}), \
             mock.patch.object(app.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                app.publish_obs(Path('/unused'), {'enabled': True, 'region': 'me-east-1', 'bucket': 'example-bucket',
                                                 'upload_retries': 2, 'cache_control': 'no-cache'})
        self.assertEqual(client.putFile.call_count, 2)
        self.assertEqual(client.putFile.call_args[1]['extensionHeaders']['Cache-Control'], 'no-cache')

    @unittest.skipIf(os.name == 'nt', 'HTTP integration is verified on Linux')
    def test_local_playlist_without_obs_does_not_redirect_to_itself(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            (data/'published').mkdir()
            (data/'published/iptv_top5.m3u').write_text('#EXTM3U\n', encoding='utf-8')
            server = web.Server(('127.0.0.1', 0), web.make_handler(data, ''))
            thread = threading.Thread(target=server.serve_forever)
            thread.daemon = True
            thread.start()
            try:
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                conn.request('GET', '/iptv_ok.m3u')
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b'#EXTM3U\n')
                self.assertIsNone(response.getheader('Location'))
                conn.request('GET', '/iptv_english.m3u')
                response = conn.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
                conn.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
