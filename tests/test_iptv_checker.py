import json
import os
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import iptv_checker as app


@mock.patch.dict('sys.modules', {'certifi': types.SimpleNamespace(where=lambda: 'test-ca-bundle.pem')})
class CheckerTests(unittest.TestCase):
    def entry(self, name, url, group='TV'):
        return app.parse_playlist('#EXTM3U\n#EXTINF:-1 group-title="{}",{}\n{}\n'.format(group, name, url))[0]

    def test_channel_normalization_keeps_4k_plus_and_regional_editions(self):
        self.assertEqual(app.normalize('CCTV-5 体育 HD'), app.normalize('CCTV5'))
        self.assertNotEqual(app.normalize('CCTV-4K'), app.normalize('CCTV4'))
        self.assertNotEqual(app.normalize('CCTV5+'), app.normalize('CCTV5'))
        self.assertNotEqual(app.normalize('CCTV4欧洲'), app.normalize('CCTV4美洲'))

    def test_playlist_handles_bom_blanks_and_commas_in_attributes(self):
        text = '\ufeff#EXTM3U\n\n#EXTINF:-1 group-title="TV, Radio",News\n#EXTVLCOPT:http-user-agent=CustomTV\nhttps://example.com/live\n'
        entry = app.parse_playlist(text)[0]
        self.assertEqual(entry['name'], 'News')
        self.assertEqual(entry['group'], 'TV, Radio')
        self.assertEqual(entry['endpoint']['headers']['user_agent'], 'CustomTV')
        with self.assertRaises(ValueError):
            app.parse_playlist('<html>Subscription expired</html>')

    def test_merge_caps_one_channel_across_all_source_groups(self):
        first = [self.entry('CCTV-5 体育', 'http://one.test/live', 'Sports'),
                 self.entry('CCTV5', 'http://one.test/live', 'National')]
        second = [self.entry('CCTV5 HD', 'http://two.test/live')]
        channels, endpoints = app.merge_entries([first, second])
        self.assertEqual(len(channels), 1)
        self.assertEqual(len(endpoints), 2)
        self.assertEqual(len(channels['CCTV5']['entries']), 2)

    def test_request_headers_are_part_of_endpoint_identity(self):
        a = app.endpoint('https://one.test/live|User-Agent=A', [])
        b = app.endpoint('https://one.test/live|User-Agent=B', [])
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(a['url'], b['url'])

    def test_sliding_hls_window_is_not_a_short_placeholder(self):
        self.assertFalse(app.is_placeholder('hls', '24'))
        self.assertFalse(app.is_placeholder('dash', '120'))
        self.assertTrue(app.is_placeholder('mov,mp4,m4a,3gp,3g2,mj2', '7'))
        self.assertFalse(app.is_placeholder('mpegts', None))

    @mock.patch.object(app, 'run_tool')
    def test_probe_rejects_nonzero_exit_even_with_video_metadata(self, run):
        run.return_value = {'code': 1, 'error': 'media_error', 'seconds': 1,
                            'stdout': json.dumps({'streams': [{'codec_type': 'video', 'width': 1920, 'height': 1080}]})}
        result = app.probe(app.endpoint('http://one.test/live', []), app.UA, {'ffprobe': 'ffprobe'})
        self.assertFalse(result['ok'])

    @mock.patch.object(app, 'run_tool')
    def test_decode_requires_four_seconds_and_actual_video_frames(self, run):
        ep, cfg = app.endpoint('http://one.test/live', []), {'ffmpeg': 'ffmpeg'}
        run.return_value = {'code': 0, 'error': '', 'seconds': 1, 'stdout': 'frame=20\nout_time_us=1000000\nprogress=end\n'}
        self.assertFalse(app.decode(ep, {}, app.UA, cfg)['ok'])
        run.return_value['stdout'] = 'frame=0\nout_time_us=4000000\nprogress=end\n'
        self.assertFalse(app.decode(ep, {}, app.UA, cfg)['ok'])
        self.assertTrue(app.decode(ep, {'audio_only': True}, app.UA, cfg)['ok'])
        run.return_value['stdout'] = 'frame=100\nout_time_us=4000000\nprogress=end\n'
        self.assertTrue(app.decode(ep, {}, app.UA, cfg)['ok'])

    def test_top3_is_prefix_of_top5_and_prefers_separate_hosts(self):
        entries = [self.entry('News', 'http://{}.test:{}/live'.format(host, port))
                   for host, port in [('one', 80), ('one', 81), ('two', 80), ('three', 80), ('four', 80), ('five', 80), ('six', 80)]]
        channels, endpoints = app.merge_entries([entries])
        probes = {i: {'height': 1080, 'seconds': k} for k, i in enumerate(endpoints)}
        decodes = {i: {'ok': True, 'seconds': 4} for i in endpoints}
        texts, stats = app.render_playlists(channels, probes, decodes, {})
        three = [x for x in texts[3].splitlines() if not x.startswith('#')]
        five = [x for x in texts[5].splitlines() if not x.startswith('#')]
        self.assertEqual(three, five[:3])
        self.assertEqual(stats['top3_entries'], 3)
        self.assertEqual(stats['top5_entries'], 5)
        self.assertEqual(len(set(app.urlsplit(u).hostname for u in five)), 5)

    def test_current_failures_never_survive_on_historical_reliability(self):
        entry = self.entry('News', 'http://one.test/live')
        identity = entry['endpoint']['id']
        self.assertEqual(app.choose_lines([entry], {}, {identity: {'ok': False}}, {identity: 1}, 5), [])

    def test_publication_guard_preserves_last_good_when_zero_or_large_drop(self):
        self.assertTrue(app.publish_guard({'channels_available': 0}, {}, [], .5))
        self.assertTrue(app.publish_guard({'channels_available': 49}, {'channels_available': 100}, [], .5))
        self.assertFalse(app.publish_guard({'channels_available': 80}, {'channels_available': 100}, [], .5))
        prior = {'channels_available': 100, 'sources': [{'name': 'seller', 'entries': 5000}]}
        self.assertTrue(app.publish_guard({'channels_available': 100}, prior, [{'name': 'seller', 'entries': 10}], .5))

    def test_history_records_failures_and_recovers(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            hist = app.History(Path(temp) / 'history.sqlite3')
            self.assertEqual(hist.record({'line': True})['line'], 1)
            self.assertEqual(hist.record({'line': False})['line'], .75)
            self.assertGreater(hist.record({'line': True})['line'], .75)
            hist.close()

    @unittest.skipIf(os.name == 'nt', 'Production symlink behaviour is validated on Linux')
    def test_pair_publication_changes_current_directory_atomically(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            for version in ('old', 'new'):
                export = data / version
                export.mkdir()
                for name in ('iptv_top3.m3u', 'iptv_top5.m3u', 'report.json'):
                    (export / name).write_text(version)
                app.publish_local(data, export)
                self.assertEqual((data / 'published' / 'iptv_top3.m3u').read_text(), version)
                self.assertEqual((data / 'published' / 'iptv_top5.m3u').read_text(), version)
                self.assertEqual((data / 'published' / 'iptv_ok.m3u').read_text(), version)

    def test_batch_enforces_per_host_limit(self):
        eps = [app.endpoint('http://one.test/live/{}'.format(i), []) for i in range(6)]
        mutex, active, maximum = threading.Lock(), [0], [0]

        def worker(ep):
            with mutex:
                active[0] += 1
                maximum[0] = max(maximum[0], active[0])
            time.sleep(.01)
            with mutex:
                active[0] -= 1
            return {'id': ep['id'], 'ok': True}

        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            results = app.batch('test', eps, worker, 6, 2, Path(temp), lambda *args: None)
        self.assertEqual(len(results), 6)
        self.assertLessEqual(maximum[0], 2)

    def test_slow_host_does_not_occupy_workers_needed_by_other_hosts(self):
        eps = [app.endpoint('http://{}.test/live/{}'.format(host, i), []) for i in range(8) for host in ('slow', 'ready')]
        release = threading.Event()
        ready_completed = [0]
        mutex = threading.Lock()

        def worker(ep):
            if ep['host'] == 'slow.test':
                released = release.wait(1)
            else:
                with mutex:
                    ready_completed[0] += 1
                    if ready_completed[0] >= 5:
                        release.set()
                released = True
            return {'id': ep['id'], 'ok': released}

        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            records = app.batch('fair', eps, worker, 4, 1, Path(temp), lambda *args: None)
        self.assertEqual(len(records), len(eps))
        self.assertTrue(all(r['ok'] for r in records.values()))

    def test_obs_is_disabled_without_importing_sdk_or_accessing_network(self):
        self.assertEqual(app.publish_obs(Path('/unused'), {'enabled': False})['state'], 'not_configured')

    def test_obs_upload_order_fixed_keys_and_report_commit_marker(self):
        client = mock.Mock()
        client.putFile.return_value.status = 200
        sdk = types.SimpleNamespace(ObsClient=mock.Mock(return_value=client), PutObjectHeader=types.SimpleNamespace)
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            export = Path(temp)
            app.write_json(export / 'report.json', {'published': True, 'obs': {'state': 'pending'}})
            with mock.patch.dict('sys.modules', {'obs': sdk}), mock.patch.dict(os.environ, {'OBS_ACCESS_KEY_ID': 'test-ak', 'OBS_SECRET_ACCESS_KEY': 'test-sk'}):
                result = app.publish_obs(export, {'enabled': True, 'endpoint': 'https://obs.example.test', 'bucket': 'example', 'prefix': '/iptv/'})
            keys = [call[0][1] for call in client.putFile.call_args_list]
            self.assertEqual(keys, ['iptv/iptv_top5.m3u', 'iptv/iptv_top3.m3u', 'iptv/report.json'])
            self.assertEqual(result['state'], 'uploaded')
            self.assertEqual(json.loads((export / 'report.json').read_text())['obs']['state'], 'uploaded')
            self.assertEqual(client.putFile.call_args_list[0][1]['extensionHeaders']['Cache-Control'], 'max-age=300')
            client.close.assert_called_once()

    def test_obs_user_object_names_upload_exactly_two_files_at_bucket_root(self):
        client = mock.Mock()
        client.putFile.return_value.status = 200
        sdk = types.SimpleNamespace(ObsClient=mock.Mock(return_value=client), PutObjectHeader=types.SimpleNamespace)
        with mock.patch.dict('sys.modules', {'obs': sdk}), mock.patch.dict(os.environ, {'OBS_ACCESS_KEY_ID': 'test-ak', 'OBS_SECRET_ACCESS_KEY': 'test-sk'}):
            result = app.publish_obs(Path('/unused'), {'enabled': True, 'endpoint': 'https://obs.example.test',
                'bucket': 'example-bucket', 'prefix': '', 'upload_report': False,
                'object_names': {'iptv_top5.m3u': 'iptv_ok.m3u'}})
        self.assertEqual([c[0][1] for c in client.putFile.call_args_list], ['iptv_ok.m3u', 'iptv_top3.m3u'])
        self.assertEqual(result['objects'], ['iptv_ok.m3u', 'iptv_top3.m3u'])

    def test_obs_failure_retries_and_stops_before_later_files(self):
        client = mock.Mock()
        client.putFile.return_value.status = 503
        sdk = types.SimpleNamespace(ObsClient=mock.Mock(return_value=client), PutObjectHeader=types.SimpleNamespace)
        with mock.patch.dict('sys.modules', {'obs': sdk}), mock.patch.dict(os.environ, {'OBS_ACCESS_KEY_ID': 'test-ak', 'OBS_SECRET_ACCESS_KEY': 'test-sk'}), mock.patch.object(app.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                app.publish_obs(Path('/unused'), {'enabled': True, 'endpoint': 'https://obs.example.test', 'bucket': 'example'})
        self.assertEqual(client.putFile.call_count, 3)
        client.close.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
