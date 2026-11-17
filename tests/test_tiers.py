import tempfile
import unittest
from pathlib import Path
from unittest import mock

import iptv_checker as app
from iptv_qa.detection import tiers


class TierTests(unittest.TestCase):
    def fixture(self):
        entries = app.parse_playlist('#EXTM3U\n#EXTINF:-1,News\nhttp://a.invalid/live')
        channels, endpoints = app.merge_entries([entries])
        identity = entries[0]['endpoint']['id']
        return entries, channels, endpoints, identity

    def test_transient_recheck_failure_keeps_current_basic_evidence(self):
        basic = dict(ok=True, seconds=4, audio_check={'ok': True}, first_frame_seconds=2)
        result = tiers.classify(basic, dict(ok=False, deep=True, error='startup_too_slow'))
        self.assertTrue(result['ok'])
        self.assertEqual(result['validation_tier'], 'pending_review')
        self.assertEqual(result['stability_error'], 'startup_too_slow')
        self.assertEqual(result['first_frame_seconds'], 2)
        self.assertEqual(basic.get('validation_tier'), None)

    def test_hard_failure_and_missing_basic_evidence_never_leak(self):
        for error in tiers.HARD_FAILURES:
            result = tiers.classify(dict(ok=True), dict(ok=False, error=error))
            self.assertFalse(result['ok'])
        self.assertFalse(tiers.classify(dict(ok=False), dict(ok=True, verified_twice=True))['ok'])

    def test_untested_candidate_is_basic_not_stable(self):
        result = tiers.classify(dict(ok=True), dict(ok=False, error='not_deep_verified'))
        self.assertEqual(result['validation_tier'], 'basic')
        self.assertFalse(result['verified_twice'])

    @mock.patch('iptv_qa.playback.proxy.fetch_upstream')
    def test_proxy_checks_redirected_key_map_and_segment(self, fetch):
        fetch.side_effect = [
            (200, {}, b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nchild.m3u8\n',
             'http://edge.invalid/live/master.m3u8'),
            (200, {}, b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
             b'#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6,\nseg.ts?q=1\n',
             'http://cdn.invalid/actual/media.m3u8'),
            (200, {}, b'key', 'http://cdn.invalid/actual/key.bin'),
            (200, {}, b'init', 'http://cdn.invalid/actual/init.mp4'),
            (200, {}, b'segment', 'http://cdn.invalid/actual/seg.ts?q=1')]
        ep = app.endpoint('http://origin.invalid/token', [])
        self.assertTrue(tiers.check_proxy(ep, app.UA)['ok'])
        self.assertEqual([c[0][0] for c in fetch.call_args_list], [
            'http://origin.invalid/token', 'http://edge.invalid/live/child.m3u8',
            'http://cdn.invalid/actual/key.bin', 'http://cdn.invalid/actual/init.mp4',
            'http://cdn.invalid/actual/seg.ts?q=1'])

    def test_audio_needs_successful_decode_and_duration(self):
        ep = app.endpoint('http://a.invalid/live', [])
        for code, stdout, expected in [(0, 'out_time_us=4000000', True),
                                       (0, 'out_time_us=1000000', False),
                                       (1, 'out_time_us=4000000', False)]:
            run = mock.Mock(return_value=dict(code=code, stdout=stdout, error=''))
            result = tiers.check_audio(ep, app.UA, {'ffmpeg': 'unused'}, run, lambda *a: [])
            self.assertEqual(result['ok'], expected)
            self.assertIn('0:a:0', run.call_args[0][0])

    @mock.patch.object(app, 'run_tool')
    @mock.patch.object(app, 'check_video')
    def test_video_success_cannot_hide_audio_failure(self, video, run):
        video.return_value = dict(ok=True, error='')
        run.return_value = dict(code=1, stdout='', error='media_error')
        ep = app.endpoint('http://a.invalid/live', [])
        result = app.decode(ep, {'format': 'hls'}, app.UA,
                            {'ffmpeg': 'unused', 'validation_policy': tiers.POLICY})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'audio_decode_failed')

    def test_policy_migration_keeps_source_and_zero_guards(self):
        prior = dict(channels_available=1000, sources=[dict(name='source', entries=100)])
        baseline, note = tiers.comparable_baseline({'validation_policy': tiers.POLICY}, prior)
        self.assertIn('skipped', note)
        self.assertEqual(prior['channels_available'], 1000)
        self.assertFalse(app.publish_guard(dict(channels_available=10), baseline, [], .5))
        self.assertTrue(app.publish_guard(dict(channels_available=0), baseline, [], .5))
        self.assertTrue(app.publish_guard(dict(channels_available=10), baseline,
                                         [dict(name='source', entries=1)], .5))
        same = dict(prior, validation_policy=tiers.POLICY)
        baseline, note = tiers.comparable_baseline({'validation_policy': tiers.POLICY}, same)
        self.assertTrue(app.publish_guard(dict(channels_available=10), baseline, [], .5))

    @mock.patch('iptv_qa.detection.tiers.check_proxy', side_effect=OSError)
    @mock.patch('iptv_qa.detection.tiers.check_audio', return_value={'ok': True})
    @mock.patch.object(app, 'check_video', return_value={'ok': True, 'error': ''})
    def test_video_and_audio_cannot_hide_proxy_failure(self, video, audio, proxy):
        result = app.decode(app.endpoint('http://a.invalid/live', []), {'format': 'hls'},
            app.UA, {'validation_policy': tiers.POLICY})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'proxy_delivery_failed')

    @mock.patch.object(app, 'check_video')
    def test_failed_second_connection_is_pending_not_stable(self, check):
        entries, channels, endpoints, identity = self.fixture()
        probes = {identity: dict(ok=True, height=720, seconds=1)}
        decodes = {identity: dict(ok=True, seconds=4, audio_check={'ok': True})}
        check.side_effect = [dict(id=identity, ok=True, deep=True, seconds=30,
                                 first_frame_seconds=1, error=''),
                             dict(id=identity, ok=False, deep=True, seconds=1,
                                  error='startup_too_slow')]
        with tempfile.TemporaryDirectory() as temp:
            report = app.validate_candidates(channels, endpoints, probes, decodes, {}, app.UA,
                {'validation_policy': tiers.POLICY, 'recheck_interval_seconds': 0},
                Path(temp), lambda *args: None)
        self.assertEqual(report['verified_twice'], 0)
        self.assertEqual(decodes[identity]['validation_tier'], 'pending_review')
        self.assertTrue(app.choose_lines(entries, probes, decodes, {}, 5))

    def test_stable_lines_beat_faster_and_more_diverse_fallbacks(self):
        entries = app.parse_playlist('#EXTM3U\n#EXTINF:-1,News\nhttp://a.invalid/1\n'
            '#EXTINF:-1,News\nhttp://a.invalid/2\n#EXTINF:-1,News\nhttp://b.invalid/3')
        ids = [e['endpoint']['id'] for e in entries]
        probes = {i: dict(height=720, seconds=1) for i in ids}
        decodes = {i: dict(ok=True, seconds=4, validation_tier='stable' if n<2 else 'basic',
                           startup_class='slow' if n<2 else 'fast') for n,i in enumerate(ids)}
        chosen = app.choose_lines(entries, probes, decodes, {}, 2)
        self.assertEqual({e['endpoint']['id'] for e in chosen}, set(ids[:2]))


if __name__ == '__main__':
    unittest.main()
