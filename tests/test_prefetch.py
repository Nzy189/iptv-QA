import time
import unittest
from iptv_qa.playback.prefetch import Prefetcher

WINNER = {'index': 0, 'url': 'http://u/a.m3u8', 'headers': {}, 'final_url': 'http://u/a.m3u8'}
LINES = [{'url': 'http://u/a.m3u8', 'headers': {}}]


class PrefetcherTests(unittest.TestCase):
    def test_off_mode_does_nothing(self):
        calls = []
        p = Prefetcher(race_func=lambda lines: calls.append(1) or WINNER, mode='off')
        self.assertFalse(p.enabled())
        self.assertIsNone(p.warm('main', 'c', LINES, wait=True))
        self.assertEqual(calls, [])
        p.close()

    def test_light_mode_records_winner(self):
        p = Prefetcher(race_func=lambda lines: WINNER, mode='light')
        self.assertEqual(p.warm('main', 'c2', LINES, wait=True), WINNER)
        self.assertEqual(p.get('main', 'c2'), WINNER)
        p.close()

    def test_no_winner_not_cached(self):
        p = Prefetcher(race_func=lambda lines: None, mode='light')
        self.assertIsNone(p.warm('main', 'c', LINES, wait=True))
        self.assertIsNone(p.get('main', 'c'))
        p.close()

    def test_ttl_expiry(self):
        p = Prefetcher(race_func=lambda lines: WINNER, mode='light', ttl=1)
        p.warm('main', 'c', LINES, wait=True)
        self.assertEqual(p.get('main', 'c'), WINNER)
        with p._lock:
            _, w = p._entries[('main', 'c')]
            p._entries[('main', 'c')] = (time.time() - 1, w)
        self.assertIsNone(p.get('main', 'c'))
        p.close()

    def test_max_channels_eviction(self):
        p = Prefetcher(race_func=lambda lines: WINNER, mode='light', max_channels=2)
        for cid in ('a', 'b', 'c'):
            p.warm('main', cid, LINES, wait=True)
        self.assertIsNone(p.get('main', 'a'))
        self.assertEqual(p.get('main', 'c'), WINNER)
        p.close()

    def test_existing_entry_skips_race(self):
        calls = []
        p = Prefetcher(race_func=lambda lines: calls.append(1) or WINNER, mode='light')
        p.warm('main', 'c', LINES, wait=True)
        p.warm('main', 'c', LINES, wait=True)
        self.assertEqual(len(calls), 1)
        p.close()

    def test_full_mode_behaves_as_light_in_phase1(self):
        p = Prefetcher(race_func=lambda lines: WINNER, mode='full')
        p.warm('main', 'c', LINES, wait=True)
        self.assertEqual(p.get('main', 'c'), WINNER)
        p.close()


if __name__ == '__main__':
    unittest.main()
