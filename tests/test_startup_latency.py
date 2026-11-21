"""Startup-latency behaviour: bounded reads, probe reuse, live-edge warm, line stats."""
import threading
import time
import unittest

from iptv_qa.playback import race
from iptv_qa.playback.linestats import LineStats
from iptv_qa.playback.prefetch import Prefetcher, strip_probe
from iptv_qa.playback.proxy import READ_CHUNK, read_capped
from iptv_qa.playback.service import TVPlugin


class _Handle:
    def __init__(self, body):
        self.body = body
        self.asks = []

    def read(self, n=-1):
        self.asks.append(n)
        chunk, self.body = self.body[:n], self.body[n:]
        return chunk


class _Headers(dict):
    def get(self, key, default=None):
        return dict.get(self, key.lower(), default)


def media(count, target=6):
    lines = ['#EXTM3U', '#EXT-X-TARGETDURATION:%d' % target]
    for n in range(1, count + 1):
        lines += ['#EXTINF:%d,' % target, 'seg%d.ts' % n]
    return ('\n'.join(lines) + '\n').encode()


def fetch_from(store, log):
    def fetch(url, headers, cap):
        log.append(url)
        body = store.get(url)
        if body is None:
            raise OSError('missing ' + url)
        if len(body) > cap:
            raise ValueError('upstream object too large')
        return 200, _Headers({'content-type': 'video/mp2t'}), body, url
    return fetch


class ReadCappedTests(unittest.TestCase):
    def test_reads_in_small_chunks_never_preallocating_the_cap(self):
        handle = _Handle(b'x' * (5 * READ_CHUNK + 7))
        data = read_capped(handle, 64 * 1024 * 1024 + 1)
        self.assertEqual(len(data), 5 * READ_CHUNK + 7)
        self.assertLessEqual(max(handle.asks), READ_CHUNK)

    def test_stops_at_limit(self):
        handle = _Handle(b'y' * 1000)
        self.assertEqual(read_capped(handle, 300), b'y' * 300)


class RaceProbeTests(unittest.TestCase):
    def test_winner_carries_probe_manifest_and_probes_live_start_segment(self):
        store = {'http://a/m.m3u8': media(6), 'http://a/seg4.ts': b'\x47' * 188}
        log, seen = [], []
        winner = race.race_lines([{'url': 'http://a/m.m3u8', 'headers': {}}], budget=5,
                                 fetch=fetch_from(store, log),
                                 observe=lambda url, ok, secs: seen.append((url, ok)))
        self.assertEqual(winner['probe']['manifest']['data'], media(6))
        self.assertIn('http://a/seg4.ts', log)          # third-to-last of six
        self.assertNotIn('http://a/seg1.ts', log)
        time.sleep(0.05)
        self.assertEqual(seen, [('http://a/m.m3u8', True)])

    def test_failures_are_observed_and_slot_is_kept(self):
        store = {'http://b/m.m3u8': media(2), 'http://b/seg1.ts': b'\x47'}
        seen = []
        lines = [{'url': 'http://dead/m.m3u8', 'headers': {}, 'slot': 3},
                 {'url': 'http://b/m.m3u8', 'headers': {}, 'slot': 0}]
        winner = race.race_lines(lines, budget=5, fetch=fetch_from(store, []),
                                 observe=lambda url, ok, secs: seen.append((url, ok)))
        self.assertEqual(winner['index'], 0)
        deadline = time.time() + 2
        while len(seen) < 2 and time.time() < deadline:
            time.sleep(0.01)
        self.assertIn(('http://dead/m.m3u8', False), seen)


class LineStatsTests(unittest.TestCase):
    LINES = [{'url': 'a', 'headers': {}}, {'url': 'b', 'headers': {}}, {'url': 'c', 'headers': {}}]

    def test_unknown_lines_keep_library_order_with_slots(self):
        ordered = LineStats().order(self.LINES)
        self.assertEqual([(l['url'], l['slot']) for l in ordered], [('a', 0), ('b', 1), ('c', 2)])

    def test_failing_lines_drop_and_fast_lines_rise(self):
        stats = LineStats()
        stats.record('a', False, 1)
        stats.record('a', False, 1)
        stats.record('c', True, 0.8)
        ordered = [l['url'] for l in stats.order(self.LINES)]
        self.assertEqual(ordered, ['c', 'b', 'a'])

    def test_single_failure_does_not_demote_and_failure_expires(self):
        now = [1000.0]
        stats = LineStats(clock=lambda: now[0])
        stats.record('a', False, 1)
        self.assertFalse(stats.failing('a'))
        stats.record('a', False, 1)
        self.assertTrue(stats.failing('a'))
        now[0] += 601
        self.assertFalse(stats.failing('a'))

    def test_all_failing(self):
        stats = LineStats()
        for url in ('a', 'b'):
            stats.record(url, False, 1)
            stats.record(url, False, 1)
        self.assertTrue(stats.all_failing(self.LINES[:2]))
        self.assertFalse(stats.all_failing(self.LINES))


class LiveEdgeWarmTests(unittest.TestCase):
    def store(self):
        store = {'http://u/a.m3u8': media(6)}
        for n in range(1, 7):
            store['http://u/seg%d.ts' % n] = b'\x47' * 188
        return store

    def test_warm_takes_the_three_segments_a_live_player_starts_on(self):
        log = []
        p = Prefetcher(race_func=lambda lines: {'index': 0, 'url': 'http://u/a.m3u8', 'headers': {},
                                                'final_url': 'http://u/a.m3u8'},
                       mode='full', fetch_func=fetch_from(self.store(), log), max_bytes=10 ** 6)
        p.warm('main', 'c', [{'url': 'http://u/a.m3u8', 'headers': {}}], wait=True)
        for n in (4, 5, 6):
            self.assertIsNotNone(p.get_bytes('http://u/seg%d.ts' % n))
        self.assertIsNone(p.get_bytes('http://u/seg1.ts'))
        p.close()

    def test_adopt_race_reuses_probe_manifest_without_refetch(self):
        log = []
        p = Prefetcher(mode='light', fetch_func=fetch_from(self.store(), log), max_bytes=10 ** 6)
        winner = {'index': 0, 'url': 'http://u/a.m3u8', 'headers': {}, 'final_url': 'http://u/a.m3u8',
                  'probe': {'manifest': {'url': 'http://u/a.m3u8', 'status': 200, 'data': media(6),
                                         'final_url': 'http://u/a.m3u8', 'content_type': None}}}
        p.adopt_race(winner)
        self.assertEqual(p.get_bytes('http://u/a.m3u8')['data'], media(6))
        self.assertIsNotNone(p.wait_bytes('http://u/seg4.ts', timeout=2))
        self.assertNotIn('http://u/a.m3u8', log)
        p.close()

    def test_off_mode_adopts_nothing(self):
        p = Prefetcher(mode='off', fetch_func=fetch_from(self.store(), []), max_bytes=10 ** 6)
        p.adopt_race({'url': 'http://u/a.m3u8', 'headers': {}, 'probe': {
            'manifest': {'status': 200, 'data': media(6), 'final_url': 'http://u/a.m3u8'}}})
        self.assertIsNone(p.get_bytes('http://u/a.m3u8'))

    def test_cached_playlist_is_served_once_then_goes_upstream(self):
        p = Prefetcher(mode='light', fetch_func=fetch_from(self.store(), []), max_bytes=10 ** 6)
        p.adopt_race({'url': 'http://u/a.m3u8', 'headers': {}, 'probe': {
            'manifest': {'status': 200, 'data': media(6), 'final_url': 'http://u/a.m3u8'}}})
        self.assertIsNotNone(p.wait_bytes('http://u/a.m3u8', timeout=0))
        self.assertIsNone(p.wait_bytes('http://u/a.m3u8', timeout=0))
        self.assertIsNotNone(p.wait_bytes('http://u/seg4.ts', timeout=2))
        self.assertIsNotNone(p.wait_bytes('http://u/seg4.ts', timeout=0))   # segments are reusable
        p.close()

    def test_live_manifest_expires_after_two_target_durations(self):
        p = Prefetcher(mode='full', max_bytes=10 ** 6, ttl=60)
        self.assertEqual(p._manifest_ttl(media(3, target=4)), 8)
        self.assertEqual(p._manifest_ttl(b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n'), 60)

    def test_wait_bytes_waits_for_inflight_segment(self):
        gate = threading.Event()
        store = self.store()

        def slow_fetch(url, headers, cap):
            gate.wait(2)
            return 200, _Headers({}), store[url], url
        p = Prefetcher(mode='full', fetch_func=slow_fetch, max_bytes=10 ** 6)
        p.adopt_manifest('http://u/a.m3u8', media(6), {})
        time.sleep(0.05)
        threading.Timer(0.2, gate.set).start()
        self.assertIsNotNone(p.wait_bytes('http://u/seg4.ts', timeout=3))
        p.close()


class ServiceRaceTests(unittest.TestCase):
    TWO = [{'url': 'http://x/1.m3u8', 'headers': {}}, {'url': 'http://x/2.m3u8', 'headers': {}}]

    def test_single_line_channel_skips_racing(self):
        calls = []
        plugin = TVPlugin(b'pw', race_func=lambda lines: calls.append(1))
        try:
            winner = plugin._race_winner('main', 'c', self.TWO[:1])
            self.assertEqual(calls, [])
            self.assertEqual((winner['index'], winner['url']), (0, 'http://x/1.m3u8'))
        finally:
            plugin.close()

    def test_probe_bytes_are_returned_once_but_never_cached(self):
        probe = {'manifest': {'status': 200, 'data': media(3), 'final_url': 'http://x/2.m3u8'}}
        plugin = TVPlugin(b'pw', race_func=lambda lines: {'index': 1, 'url': 'http://x/2.m3u8',
                                                          'headers': {}, 'final_url': 'http://x/2.m3u8',
                                                          'probe': probe})
        try:
            self.assertIn('probe', plugin._race_winner('main', 'c', self.TWO))
            self.assertNotIn('probe', plugin._race_winner('main', 'c', self.TWO))
        finally:
            plugin.close()

    def test_all_failing_lines_get_short_budget(self):
        plugin = TVPlugin(b'pw')
        budgets = []
        plugin._race_default = lambda lines, budget=10.0: budgets.append(budget)
        try:
            for line in self.TWO:
                plugin.line_stats.record(line['url'], False, 1)
                plugin.line_stats.record(line['url'], False, 1)
            plugin._race_winner('main', 'c', self.TWO)
            self.assertEqual(budgets, [3.0])
        finally:
            plugin.close()

    def test_strip_probe(self):
        self.assertEqual(strip_probe({'url': 'u', 'probe': {}}), {'url': 'u'})
        self.assertIsNone(strip_probe(None))


if __name__ == '__main__':
    unittest.main()
