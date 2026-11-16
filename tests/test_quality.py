import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import iptv_checker as app
from quality import observe_video, HLSObserver, hls_advanced


class VideoQualityTests(unittest.TestCase):
    def test_hls_requires_sequence_or_segment_advancement(self):
        a = dict(sequence=10, segments=['a.ts'], ended=False)
        self.assertFalse(hls_advanced(a, a))
        self.assertTrue(hls_advanced(a, dict(a, sequence=11)))
        self.assertFalse(hls_advanced(a, dict(a, sequence=11, ended=True)))
        self.assertTrue(hls_advanced(dict(a, sequence=None), dict(a, sequence=None, segments=['b.ts'])))

    def test_hls_master_uses_redirect_base_for_variant(self):
        def response(body, url):
            r = mock.MagicMock()
            r.__enter__.return_value = r
            r.read.return_value = body.encode()
            r.geturl.return_value = url
            return r
        observer = HLSObserver('https://origin.invalid/token', {'User-Agent': 'Test'})
        observer.opener = mock.Mock()
        observer.opener.open.side_effect = [
            response('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nvariant/index.m3u8\n', 'https://edge.invalid/live/master.m3u8'),
            response('#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:41\n#EXTINF:10,\na.ts\n', 'https://edge.invalid/live/variant/index.m3u8')]
        snapshot = observer.snapshot()
        self.assertEqual(snapshot['sequence'], 41)
        self.assertEqual(snapshot['segments'], ['https://edge.invalid/live/variant/a.ts'])
        self.assertEqual(observer.opener.open.call_args_list[1][0][0].full_url,
                         'https://edge.invalid/live/variant/index.m3u8')

    @mock.patch('quality.observe_video')
    @mock.patch('quality.HLSObserver')
    def test_cached_stale_playlist_rejected_even_if_video_decodes(self, observer, observe):
        observer.return_value.snapshot.return_value = dict(sequence=1, segments=['a.ts'], ended=False, target_duration=10)
        observe.return_value = dict(ok=True, error='', frames=900, decoded_seconds=36,
                                    first_frame_seconds=1, seconds=31)
        result = app.check_video(app.endpoint('http://a.invalid/live', []), {'format': 'hls'}, app.UA,
                                 {'ffmpeg': 'unused'}, deep=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'hls_not_advancing')

    def observe(self, script, **options):
        cfg = dict(timeout=3, target=1, startup_limit=2, stall_limit=2, wall_window=0)
        cfg.update(options)
        return observe_video([sys.executable, '-u', '-c', script], **cfg)

    def test_real_process_requires_actual_video_frames(self):
        r = self.observe("print('frame=0\\nout_time_us=1000000\\nprogress=end')")
        self.assertFalse(r['ok'])
        self.assertEqual(r['error'], 'insufficient_video')

    def test_successful_video_progress_measures_startup(self):
        r = self.observe("print('frame=25\\nout_time_us=1000000\\nprogress=end')")
        self.assertTrue(r['ok'], r)
        self.assertIsNotNone(r['first_frame_seconds'])

    def test_startup_timeout(self):
        r = self.observe('import time; time.sleep(3)', startup_limit=.2)
        self.assertEqual(r['error'], 'startup_too_slow')
        self.assertLess(r['seconds'], 2)

    def test_stall_after_first_frame(self):
        r = self.observe("import time; print('frame=1\\nout_time_us=100000'); time.sleep(3)", stall_limit=.2)
        self.assertEqual(r['error'], 'video_stalled')

    def test_segment_download_gap_is_not_stall_when_video_is_buffered(self):
        r = self.observe("import time; print('frame=75\\nout_time_us=3000000'); time.sleep(.5); print('progress=end')", stall_limit=.2)
        self.assertTrue(r['ok'], r)

    def test_cached_window_does_not_satisfy_wall_time(self):
        r = self.observe("print('frame=25\\nout_time_us=1000000\\nprogress=end')", wall_window=2)
        self.assertEqual(r['error'], 'stream_ended_early')

    def test_sustained_video_observation_stops_cleanly(self):
        r = self.observe("import time\nfor i in range(100):\n print('frame=%d\\nout_time_us=%d' % ((i+1)*10,(i+1)*500000)); time.sleep(.1)", wall_window=.4)
        self.assertTrue(r['ok'], r)
        self.assertGreaterEqual(r['observed_seconds'], .4)

    @mock.patch.object(app, 'run_tool')
    def test_tv_rejects_audio_and_album_cover(self, run):
        streams = [{'codec_type': 'audio'}, {'codec_type': 'video', 'width': 600, 'height': 600,
                                             'disposition': {'attached_pic': 1}}]
        run.return_value = dict(code=0, error='', seconds=1, stdout=json.dumps({'streams': streams}))
        r = app.probe(app.endpoint('http://test.invalid/live', []), app.UA, {'ffprobe': 'test'})
        self.assertFalse(r['ok'])
        self.assertEqual(r['error'], 'audio_only_rejected')

    def test_fast_video_beats_slow_full_hd(self):
        entries = app.parse_playlist('#EXTM3U\n#EXTINF:-1,News\nhttp://a.invalid/live\n#EXTINF:-1,News\nhttp://b.invalid/live')
        a, b = [e['endpoint']['id'] for e in entries]
        probes = {a: dict(height=1080, seconds=1), b: dict(height=720, seconds=1)}
        decodes = {a: dict(ok=True, seconds=4, first_frame_seconds=9, startup_class='slow'),
                   b: dict(ok=True, seconds=4, first_frame_seconds=2, startup_class='fast')}
        self.assertEqual(app.choose_lines(entries, probes, decodes, {a: 1, b: .8}, 1)[0]['endpoint']['id'], b)

    def test_tested_user_agent_is_exported_without_losing_provenance(self):
        entries = app.parse_playlist('#EXTM3U\n#EXTINF:-1,News\nhttp://a.invalid/live')
        entries[0]['source'] = 'seller'
        channels, endpoints = app.merge_entries([entries])
        identity = entries[0]['endpoint']['id']
        probes = {identity: dict(ok=True, seconds=1, height=720, ua='RequiredAgent')}
        decodes = {identity: dict(ok=True, seconds=4)}
        outputs, stats = app.render_playlists(channels, probes, decodes, {})
        parsed = app.parse_playlist(outputs[5])
        self.assertEqual(parsed[0]['endpoint']['headers']['user_agent'], 'RequiredAgent')
        comparison = app.source_comparison(channels, endpoints, decodes, outputs)
        self.assertEqual(comparison['sources'][0]['top5_entries'], 1)
        sets, stats = app.split_ranked_playlist(outputs[5])
        self.assertIn('#EXTVLCOPT:http-user-agent=RequiredAgent', sets['iptv_set1.m3u'])

    @mock.patch.object(app, 'check_video')
    def test_second_connection_failure_never_publishes(self, check):
        entries = app.parse_playlist('#EXTM3U\n#EXTINF:-1,News\nhttp://a.invalid/live')
        channels, endpoints = app.merge_entries([entries])
        identity = entries[0]['endpoint']['id']
        probes = {identity: dict(ok=True, height=720, seconds=1)}
        decodes = {identity: dict(ok=True, seconds=4)}
        check.side_effect = [dict(id=identity, ok=True, seconds=30, first_frame_seconds=1, error=''),
                             dict(id=identity, ok=False, seconds=1, error='connection_refused')]
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            report = app.validate_candidates(channels, endpoints, probes, decodes, {}, app.UA,
                                              {'recheck_interval_seconds': 0}, Path(temp), lambda *args: None)
        self.assertEqual(report['verified_twice'], 0)
        self.assertEqual(app.choose_lines(entries, probes, decodes, {}, 5), [])

    @unittest.skipIf(os.name == 'nt', 'Production migration uses Linux symlinks')
    def test_migration_excludes_known_radio_from_baseline_but_keeps_guard(self):
        playlist = '#EXTM3U\n#EXTINF:-1,TV\nhttp://a.invalid/live\n#EXTINF:-1,Radio\nhttp://b.invalid/live'
        entries = app.parse_playlist(playlist)
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            data = Path(temp)
            run = data / 'runs' / 'prior'
            (run/'export').mkdir(parents=True)
            (data/'published').mkdir()
            os.symlink(str(run/'export'), str(data/'published/current'))
            app.atomic_text(run/'export/iptv_top5.m3u', playlist)
            app.atomic_text(run/'probe1.jsonl', '\n'.join(json.dumps(dict(id=e['endpoint']['id'],
                            height=720 if n == 0 else None, audio_only=n == 1)) for n,e in enumerate(entries)))
            prior = dict(channels_available=2, sources=[dict(name='seller', entries=100)])
            baseline = app.video_publication_baseline(data, prior)
            self.assertEqual(baseline['channels_available'], 1)
            self.assertEqual(prior['channels_available'], 2)
            self.assertTrue(app.publish_guard(dict(channels_available=0), baseline, [], .5))
            self.assertTrue(app.publish_guard(dict(channels_available=1), baseline, [dict(name='seller', entries=1)], .5))

    @mock.patch.object(app, 'check_video')
    def test_deep_failure_promotes_next_candidate(self, check):
        entries = app.parse_playlist('#EXTM3U\n#EXTINF:-1,News\nhttp://a.invalid/live\n#EXTINF:-1,News\nhttp://b.invalid/live')
        channels, endpoints = app.merge_entries([entries])
        a, b = [e['endpoint']['id'] for e in entries]
        probes = {i: dict(ok=True, height=720, seconds=n) for n, i in enumerate((a, b))}
        decodes = {i: dict(ok=True, seconds=4) for i in (a, b)}
        def result(ep, *args, **kwargs):
            return dict(id=ep['id'], ok=ep['id'] == b, seconds=30,
                        first_frame_seconds=2, error='' if ep['id'] == b else 'video_stalled')
        check.side_effect = result
        with tempfile.TemporaryDirectory(dir=os.environ.get('IPTV_TEST_TMPDIR')) as temp:
            report = app.validate_candidates(channels, endpoints, probes, decodes, {}, app.UA,
                                              {'deep_candidates_per_channel': 1, 'recheck_interval_seconds': 0},
                                              Path(temp), lambda *args: None)
        self.assertEqual(report['deep_tested'], 2)
        self.assertEqual(report['verified_twice'], 1)
        self.assertEqual(app.choose_lines(entries, probes, decodes, {}, 5)[0]['endpoint']['id'], b)


if __name__ == '__main__':
    unittest.main()
