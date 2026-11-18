"""Bounded background pre-race for the next channel (server-side prewarm)."""
import threading
import time
from collections import OrderedDict
from urllib.parse import urljoin

from .constants import MAX_PLAYLIST_BYTES, MAX_SEGMENT_BYTES
from .proxy import fetch_upstream


class Prefetcher:
    """Race the next channel's lines ahead of time and remember the winner.

    Phase 1 implements light/full identically (race only); Phase 2 adds a
    byte cache to full. 'off' disables all work.
    """

    VALID_MODES = ('off', 'light', 'full')

    def __init__(self, race_func=None, mode='light', max_channels=16, max_workers=4, ttl=60,
                 fetch_func=None, max_bytes=0):
        self.race_func = race_func
        self.mode = mode if mode in self.VALID_MODES else 'light'
        self.max_channels = max(1, int(max_channels))
        self.ttl = max(1, int(ttl))
        self.fetch_func = fetch_func
        self.max_bytes = max(0, int(max_bytes))
        self._lock = threading.Lock()
        self._entries = OrderedDict()   # (view, channel_id) -> (expires, winner)
        self._bytes = OrderedDict()   # url -> (expires, entry)
        self._byte_total = 0
        self._sem = threading.BoundedSemaphore(max(1, int(max_workers)))
        self._threads = []
        self._stopped = threading.Event()

    def enabled(self):
        return self.mode != 'off'

    def get(self, view, channel_id):
        key = (view, channel_id)
        now = time.time()
        with self._lock:
            hit = self._entries.get(key)
            if not hit or hit[0] <= now:
                if hit:
                    self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return hit[1]

    def _race(self, lines):
        if self.race_func is None:
            from .race import race_lines
            self.race_func = race_lines
        return self.race_func(lines)

    def _run(self, view, channel_id, lines, mode):
        if self._stopped.is_set() or mode == 'off':
            return None
        winner = self._race(lines)
        if winner is None:
            return None
        now = time.time()
        key = (view, channel_id)
        with self._lock:
            self._entries[key] = (now + self.ttl, winner)
            self._entries.move_to_end(key)
            for old_key in [k for k, v in self._entries.items() if v[0] <= now]:
                self._entries.pop(old_key, None)
            while len(self._entries) > self.max_channels:
                self._entries.popitem(last=False)
        if mode == 'full' and self.max_bytes > 0:
            try:
                self._warm_bytes(winner)
            except Exception:
                pass
        return winner

    def warm(self, view, channel_id, lines, mode=None, wait=False):
        # A server configured 'off' is a true master switch: never warm,
        # regardless of a per-request mode (e.g. the browser asking 'light').
        if self.mode == 'off':
            return None
        chosen = mode if mode in self.VALID_MODES else self.mode
        if chosen == 'off' or self.get(view, channel_id) is not None:
            return None
        if wait:
            return self._run(view, channel_id, lines, chosen)
        worker = threading.Thread(target=self._warm_guarded,
                                  args=(view, channel_id, lines, chosen), daemon=True)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(worker)
        worker.start()
        return None

    def _warm_guarded(self, view, channel_id, lines, mode):
        if not self._sem.acquire(timeout=1.0):
            return
        try:
            self._run(view, channel_id, lines, mode)
        finally:
            self._sem.release()

    def get_bytes(self, url):
        if self.max_bytes <= 0:
            return None
        now = time.time()
        with self._lock:
            hit = self._bytes.get(url)
            if not hit or hit[0] <= now:
                if hit:
                    self._byte_total -= len(hit[1]['data'])
                    self._bytes.pop(url, None)
                return None
            self._bytes.move_to_end(url)
            return hit[1]

    def _store_bytes(self, url, status, content_type, data, final_url):
        if self.max_bytes <= 0 or not data or len(data) > self.max_bytes:
            return
        entry = {'status': status, 'content_type': content_type, 'data': data, 'final_url': final_url}
        now = time.time()
        with self._lock:
            old = self._bytes.pop(url, None)
            if old:
                self._byte_total -= len(old[1]['data'])
            self._bytes[url] = (now + self.ttl, entry)
            self._byte_total += len(data)
            for k in [k for k, v in self._bytes.items() if v[0] <= now]:
                self._byte_total -= len(self._bytes[k][1]['data'])
                self._bytes.pop(k, None)
            while self._byte_total > self.max_bytes and self._bytes:
                _, (_e, v) = self._bytes.popitem(last=False)
                self._byte_total -= len(v['data'])

    @staticmethod
    def _media_uris(text):
        return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith('#')]

    def _fetch(self, url, headers, cap):
        if self.fetch_func is None:
            return fetch_upstream(url, headers, cap)
        return self.fetch_func(url, headers, cap)

    def _warm_bytes(self, winner):
        status, hdrs, data, final_url = self._fetch(winner['url'], winner['headers'], MAX_PLAYLIST_BYTES)
        if not data or data.lstrip(b'\r\n \t')[:7] != b'#EXTM3U':
            return
        ctype = hdrs.get('Content-Type') or 'application/vnd.apple.mpegurl'
        self._store_bytes(winner['url'], status, ctype, data, final_url)
        text = data.decode('utf-8', 'replace')
        if '#EXT-X-STREAM-INF' in text:
            variants = self._media_uris(text)
            if not variants:
                return
            variant_url = urljoin(final_url, variants[0])
            status, hdrs, data, final_url = self._fetch(variant_url, winner['headers'], MAX_PLAYLIST_BYTES)
            if not data or data.lstrip(b'\r\n \t')[:7] != b'#EXTM3U':
                return
            ctype = hdrs.get('Content-Type') or 'application/vnd.apple.mpegurl'
            self._store_bytes(variant_url, status, ctype, data, final_url)
            text = data.decode('utf-8', 'replace')
        for uri in self._media_uris(text)[:2]:
            seg_url = urljoin(final_url, uri)
            try:
                status, hdrs, seg, seg_final = self._fetch(seg_url, winner['headers'], MAX_SEGMENT_BYTES)
            except Exception:
                continue
            self._store_bytes(seg_url, status, hdrs.get('Content-Type') or 'application/octet-stream', seg, seg_final)

    def close(self):
        self._stopped.set()
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=1)
