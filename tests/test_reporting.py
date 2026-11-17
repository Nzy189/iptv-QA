import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import reporting
import iptv_checker as app


class ReportingTests(unittest.TestCase):
    def test_quality_panel_uses_sanitized_metrics_only(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            run = self.make_run(data, '20261116T000000Z-11111111', ['News'])
            path = run / 'export/report.json'
            report = json.loads(path.read_text(encoding='utf-8'))
            report['quality'] = dict(enabled=True, deep_tested=10, deep_passed=8,
                                     recheck_tested=7, verified_twice=5, deep_seconds=30,
                                     recheck_interval_seconds=1800, max_startup_seconds=12,
                                     private_url='http://private.invalid/?secret=DO_NOT_PUBLISH')
            report['unverified_endpoints'] = 20
            path.write_text(json.dumps(report), encoding='utf-8')
            target, count = reporting.refresh(data)
            html = (target/'iptv_summary.html').read_text(encoding='utf-8')
            self.assertIn('视频质量复检', html)
            self.assertIn('两次通过 5 条', html)
            self.assertNotIn('DO_NOT_PUBLISH', html)
            self.assertNotIn('private_url', (target/'iptv_history.json').read_text(encoding='utf-8'))
            rows = json.loads((target/'iptv_history.json').read_text(encoding='utf-8'))['runs']
            self.assertEqual(rows[0]['failed_endpoints'], 30)

    def make_run(self, data, identity, names, count=100, state='complete', sample=False):
        run = data / 'runs' / identity
        export = run / 'export'
        export.mkdir(parents=True)
        status = {'run_id': identity, 'state': state, 'started_at': '2026-11-15T00:00:00Z',
                  'finished_at': '2026-11-15T01:30:00Z', 'diagnostic_sample': sample}
        (run / 'status.json').write_text(json.dumps(status), encoding='utf-8')
        if names is not None:
            report = {'run_id': identity, 'tested_endpoints': count, 'unique_endpoints': count,
                      'probe_passed': 60, 'decode_passed': 50, 'channels_total': 10,
                      'channels_available': len(names), 'top3_entries': len(names) * 3,
                      'top5_entries': len(names) * 5, 'published': True,
                      'obs': {'state': 'uploaded'}, 'sources': [{'name': 'seller', 'entries': 120}],
                      'errors': {'timeout': 10}, 'diagnostic_sample': sample,
                      'secret_debug': 'https://secret.test/?token=DO_NOT_PUBLISH'}
            (export / 'report.json').write_text(json.dumps(report), encoding='utf-8')
            (export / 'iptv_top5.m3u').write_text('#EXTM3U\n' + ''.join('#EXTINF:-1,{}\nhttp://private.test/live?password=DO_NOT_PUBLISH\n'.format(n) for n in names), encoding='utf-8')
        return run

    def test_different_validation_policies_do_not_report_channel_loss(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            self.make_run(data, '20261114T210000Z-11111111', ['A', 'B'])
            latest = self.make_run(data, '20261115T000000Z-22222222', ['B'])
            report_path = latest / 'export/report.json'
            report = json.loads(report_path.read_text(encoding='utf-8'))
            report['quality'] = {'enabled': True}
            report_path.write_text(json.dumps(report), encoding='utf-8')
            rows = reporting.collect(data)
            self.assertEqual(rows[-1]['validation_policy'], 'strict-v1')
            self.assertIsNone(rows[-1]['channels_delta'])
            self.assertEqual(rows[-1]['no_longer_available'], [])

    def test_backfill_differences_and_history_survives_removed_run_directory(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            first = self.make_run(data, '20261114T210000Z-11111111', ['A', 'B'])
            self.make_run(data, '20261115T000000Z-22222222', ['B', 'C', 'D'])
            target, count = reporting.refresh(data, public_base='https://reports.example.test')
            self.assertEqual(count, 2)
            self.assertIn('href="https://reports.example.test/iptv_ok.m3u"', (target / 'iptv_summary.html').read_text(encoding='utf-8'))
            rows = json.loads((target / 'iptv_history.json').read_text(encoding='utf-8'))['runs']
            latest = rows[-1]
            self.assertEqual(latest['channels_delta'], 1)
            self.assertEqual(latest['newly_available'], ['C', 'D'])
            self.assertEqual(latest['no_longer_available'], ['A'])
            self.assertEqual(latest['failed_endpoints'], 50)
            self.assertEqual(latest['duration_minutes'], 90)
            self.assertEqual(reporting.local_time(latest['started_at']), '2026-11-15 04:00')
            first.rename(first.with_name('archived-first'))
            _, count = reporting.refresh(data)
            self.assertEqual(count, 2)

    def test_failed_runs_have_unknown_statistics_and_samples_are_excluded(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            self.make_run(data, '20261114T210000Z-11111111', ['A'])
            self.make_run(data, '20261115T000000Z-22222222', None, state='failed')
            self.make_run(data, '20261115T010000Z-33333333', ['A'], sample=True)
            rows = reporting.collect(data)
            self.assertEqual(len(rows), 2)
            self.assertIsNone(rows[-1]['channels_available'])
            self.assertIsNone(rows[-1]['failed_endpoints'])
            self.assertFalse(rows[-1]['has_statistics'])
            self.assertIn('未完成完整统计', reporting.render_html(rows, '2026-11-15T02:00:00Z'))

    def test_public_reports_omit_raw_urls_and_escape_channel_names(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            self.make_run(data, '20261114T210000Z-11111111', ['A'])
            self.make_run(data, '20261115T000000Z-22222222', ['X<script>alert(1)</script>', '=HYPERLINK("x")'])
            target, _ = reporting.refresh(data)
            for filename in reporting.FILES:
                text = (target / filename).read_text(encoding='utf-8-sig')
                self.assertNotIn('DO_NOT_PUBLISH', text)
                self.assertNotIn('private.test', text)
                self.assertNotIn('secret.test', text)
            page = (target / reporting.FILES[0]).read_text(encoding='utf-8')
            self.assertNotIn('<script>alert', page)
            self.assertIn('&lt;script&gt;', page)
            csv_rows = list(csv.reader(io.StringIO((target / reporting.FILES[1]).read_text(encoding='utf-8-sig'))))
            self.assertTrue(csv_rows[1][-3].startswith("'="))

    def test_refresh_updates_existing_row_when_upload_is_retried(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            run = self.make_run(data, '20261115T000000Z-22222222', ['A'])
            reporting.refresh(data)
            path = run / 'export/report.json'
            report = json.loads(path.read_text())
            report['obs']['state'] = 'failed'
            path.write_text(json.dumps(report))
            rows = reporting.collect(data)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['obs_state'], 'failed')

    def test_summary_upload_has_three_explicit_safe_files(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            self.make_run(data, '20261115T000000Z-22222222', ['A'])
            cfg = {'data_dir': str(data), 'obs': {'enabled': True, 'region': 'me-east-1'}}
            with mock.patch.object(app, 'publish_obs', return_value={'state': 'uploaded', 'enabled': True}) as upload:
                self.assertTrue(app.update_run_summaries(cfg))
            self.assertEqual(upload.call_args[1]['filenames'], reporting.FILES)
            self.assertEqual(json.loads((data / 'summaries/status.json').read_text())['state'], 'uploaded')

    @unittest.skipIf(os.name == 'nt', 'Production run locking uses Linux fcntl')
    def test_daily_failure_still_generates_summary_automatically(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            cfg = {'data_dir': str(data), 'ffmpeg': sys.executable, 'ffprobe': sys.executable,
                   'sources': [{'name': 'seller', 'url': 'http://fixture.test'}], 'obs': {'enabled': False}}
            path = data / 'config.json'
            path.write_text(json.dumps(cfg))
            with mock.patch.object(app, 'run_tool', return_value={'code': 0}), mock.patch.object(app, 'download_source', side_effect=RuntimeError('Source unavailable')):
                self.assertEqual(app.main(['--config', str(path)]), 1)
            history = json.loads((data / 'summaries/iptv_history.json').read_text())['runs']
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]['state'], 'failed')
            self.assertIsNone(history[0]['decode_passed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
