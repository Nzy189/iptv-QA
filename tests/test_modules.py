import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from iptv_qa.common.locking import exclusive_lock
from iptv_qa.publishing import cli as publisher
from iptv_qa.reporting import cli as reporter
from iptv_qa.server import http as server


ROOT = Path(__file__).resolve().parents[1]


class ModuleBoundaries(unittest.TestCase):
    def child(self, code):
        return subprocess.check_output([sys.executable, '-c', code], cwd=str(ROOT), universal_newlines=True)

    def test_player_does_not_import_detector_or_publisher(self):
        self.child("import sys; import iptv_qa.playback.service; "
                   "assert 'iptv_checker' not in sys.modules; "
                   "assert 'iptv_qa.detection.checker' not in sys.modules; "
                   "assert 'iptv_qa.publishing.obs' not in sys.modules")

    def test_dashboard_import_does_not_load_player(self):
        self.child("import sys; import iptv_qa.server.http; "
                   "assert 'iptv_qa.playback.service' not in sys.modules; "
                   "assert 'iptv_qa.detection.checker' not in sys.modules")

    def test_legacy_modules_and_shared_parser_preserve_identity(self):
        import iptv_checker
        import tv
        from iptv_qa.detection import checker
        from iptv_qa.playback import service
        from iptv_qa.common.playlists import parse_playlist
        self.assertIs(iptv_checker, checker)
        self.assertIs(tv, service)
        self.assertIs(checker.parse_playlist, parse_playlist)
        self.assertIs(service.parse_playlist, parse_playlist)

    def test_all_cli_modules_can_show_help_without_runtime_setup(self):
        for command in ('run', 'check', 'catalog', 'publish', 'report', 'serve', 'dashboard', 'play'):
            with self.subTest(command=command):
                p = subprocess.run([sys.executable, '-m', 'iptv_qa', command, '--help'],
                                   cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn('usage:', p.stdout)

    def test_report_only_creates_history_without_media_configuration(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp) / 'new-data'
            self.assertEqual(reporter.main(['--data-dir', str(data)]), 0)
            self.assertTrue((data/'summaries/iptv_history.json').is_file())

    def test_publish_existing_export_needs_no_sources_or_ffmpeg(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            export = data/'published/current'
            export.mkdir(parents=True)
            (export/'report.json').write_text(json.dumps({'published':True}), encoding='utf-8')
            config = data/'private.json'
            config.write_text(json.dumps({'data_dir':str(data),'obs':{'enabled':False}}), encoding='utf-8')
            self.assertEqual(publisher.main(['--config',str(config)]), 0)
            self.assertEqual(json.loads((export/'report.json').read_text())['obs']['state'], 'not_configured')

    def test_maintenance_commands_honor_checker_lock(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            with exclusive_lock(data/'run.lock'):
                self.assertEqual(reporter.main(['--data-dir', str(data)]), 2)
            with exclusive_lock(data/'run.lock'):
                pass

    def test_dashboard_mode_ignores_player_password(self):
        with mock.patch.dict(os.environ, {'IPTV_TV_PASSWORD':'test-only-password'}), \
             mock.patch.object(server, 'create_player') as create, \
             mock.patch.object(server, 'Server'):
            server.main(['--mode','dashboard'])
            create.assert_not_called()

    def test_player_close_stops_owned_background_resources(self):
        from iptv_qa.playback.service import TVPlugin
        plugin = TVPlugin('test-only-password')
        plugin.close()
        plugin.close()
        self.assertFalse(plugin._reaper.is_alive())
        with self.assertRaises(OSError):
            plugin.transcoders.start('test', 'http://unused.invalid/live', {})

    def test_player_presets_combine_sets_and_keep_live_and_english_independent(self):
        plugin = server.create_player('test-only-password', 'https://playlists.example')
        try:
            self.assertEqual([p[0] for p in plugin.playlists], ['top5', 'live', 'english'])
            self.assertEqual(plugin._set_chain('https://playlists.example/iptv_live.m3u', ''),
                             ['https://playlists.example/iptv_live.m3u'])
            self.assertEqual(len(plugin._set_chain('https://playlists.example/iptv_set1.m3u', '')), 3)
            self.assertEqual(plugin._set_chain('https://playlists.example/iptv_english.m3u', ''),
                             ['https://playlists.example/iptv_english.m3u'])
        finally:
            plugin.close()

    def test_player_only_runs_without_detection_data_or_reports(self):
        from iptv_qa.playback.service import TVPlugin
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            missing = Path(temp)/'not-created'
            plugin = TVPlugin('test-only-password')
            httpd = server.Server(('127.0.0.1',0), server.make_handler(missing,'',plugin,dashboard_enabled=False))
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                for path,status in (('/',302),('/tv/',200),('/api/status',404),('/iptv_ok.m3u',404)):
                    conn = http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
                    conn.request('GET',path)
                    response = conn.getresponse()
                    self.assertEqual(response.status,status,path)
                    response.read()
                    conn.close()
                self.assertFalse(missing.exists())
            finally:
                httpd.shutdown()
                httpd.server_close()
                plugin.close()


if __name__ == '__main__':
    unittest.main()
