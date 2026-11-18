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


class _Headers:
    def __init__(self, content_type):
        self._ct = content_type

    def get(self, key, default=None):
        return self._ct if key.lower() == 'content-type' else default


MANIFEST = b'#EXTM3U\n#EXTINF:6,\nseg1.ts\n#EXTINF:6,\nseg2.ts\n#EXTINF:6,\nseg3.ts\n'
SEG = b'\x47' * 188


def make_fetch(store):
    def fetch(url, headers, max_bytes):
        body, ctype = store[url]
        return 200, _Headers(ctype), body, url
    return fetch


class ByteCacheTests(unittest.TestCase):
    def _pluginish(self, max_bytes=100000):
        store = {
            'http://u/a.m3u8': (MANIFEST, 'application/vnd.apple.mpegurl'),
            'http://u/seg1.ts': (SEG, 'video/mp2t'),
            'http://u/seg2.ts': (SEG, 'video/mp2t'),
        }
        p = Prefetcher(race_func=lambda lines: WINNER, mode='full',
                       fetch_func=make_fetch(store), max_bytes=max_bytes)
        return p

    def test_full_caches_manifest_and_first_two_segments(self):
        p = self._pluginish()
        p.warm('main', 'c', LINES, wait=True)
        m = p.get_bytes('http://u/a.m3u8')
        self.assertIsNotNone(m)
        self.assertTrue(m['data'].startswith(b'#EXTM3U'))
        self.assertEqual(p.get_bytes('http://u/seg1.ts')['data'], SEG)
        self.assertEqual(p.get_bytes('http://u/seg2.ts')['data'], SEG)
        self.assertIsNone(p.get_bytes('http://u/seg3.ts'))
        p.close()

    def test_light_does_not_cache_bytes(self):
        store = {'http://u/a.m3u8': (MANIFEST, 'application/vnd.apple.mpegurl')}
        p = Prefetcher(race_func=lambda lines: WINNER, mode='light',
                       fetch_func=make_fetch(store), max_bytes=100000)
        p.warm('main', 'c', LINES, wait=True)
        self.assertIsNone(p.get_bytes('http://u/a.m3u8'))
        p.close()

    def test_byte_cap_evicts_oldest(self):
        store = {
            'http://u/a.m3u8': (MANIFEST, 'application/vnd.apple.mpegurl'),
            'http://u/seg1.ts': (SEG, 'video/mp2t'),
            'http://u/seg2.ts': (SEG, 'video/mp2t'),
        }
        p = Prefetcher(race_func=lambda lines: WINNER, mode='full',
                       fetch_func=make_fetch(store), max_bytes=250)
        p.warm('main', 'c', LINES, wait=True)
        # manifest (57B) + seg1 (188B) = 245B fits; adding seg2 (188B) exceeds 250 -> evict oldest
        self.assertLessEqual(p._byte_total, 250)
        self.assertIsNone(p.get_bytes('http://u/a.m3u8'))
        p.close()

    def test_zero_max_bytes_disables_byte_cache(self):
        store = {'http://u/a.m3u8': (MANIFEST, 'application/vnd.apple.mpegurl')}
        p = Prefetcher(race_func=lambda lines: WINNER, mode='full',
                       fetch_func=make_fetch(store), max_bytes=0)
        p.warm('main', 'c', LINES, wait=True)
        self.assertIsNone(p.get_bytes('http://u/a.m3u8'))
        p.close()


if __name__ == '__main__':
    unittest.main()
