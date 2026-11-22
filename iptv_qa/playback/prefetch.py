"""Bounded background pre-race for the next channel (server-side prewarm)."""
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urljoin

from .constants import BOM, MAX_PLAYLIST_BYTES, MAX_SEGMENT_BYTES
from . import reqlog
from .proxy import fetch_upstream

TARGET_DURATION = re.compile(rb'#EXT-X-TARGETDURATION:\s*([0-9.]+)')
WARM_SEGMENTS = 3          # a live player starts on the third-to-last segment
# A request for a segment being warmed waits at most this long, then
# fetches it itself: a stuck background download must never hold the
# viewer for longer than a short head start is worth.
INFLIGHT_WAIT = 3.0


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
        self._seg_sem = threading.BoundedSemaphore(max(1, int(max_workers)))
        self._inflight = {}   # url -> Event set once the warm fetch finished
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
        raced = self._race(lines)
        if raced is None:
            return None
        winner = strip_probe(raced)
        now = time.time()
        key = (view, channel_id)
        with self._lock:
            self._entries[key] = (now + self.ttl, winner)
            self._entries.move_to_end(key)
            for old_key in [k for k, v in self._entries.items() if v[0] <= now]:
                self._entries.pop(old_key, None)
            while len(self._entries) > self.max_channels:
                self._entries.popitem(last=False)
        if mode == 'full' and self.mode == 'full' and self.max_bytes > 0:
            try:
                self._warm_bytes(raced)
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

    def get_bytes(self, url, consume=False):
        """Cached bytes for url; ``consume`` hands out a single-use playlist once.

        A cached live playlist is only good for the player's first load:
        later reloads must reach upstream to see new segments, otherwise the
        player keeps receiving the same window and stalls.
        """
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
            if consume and hit[1].get('once'):
                self._byte_total -= len(hit[1]['data'])
                self._bytes.pop(url, None)
                return hit[1]
            self._bytes.move_to_end(url)
            return hit[1]

    def wait_bytes(self, url, timeout=INFLIGHT_WAIT):
        """get_bytes, but first wait for a warm fetch of this URL still in flight.

        Without this, a player asking for a segment the server is already
        downloading would start a second, competing upstream download.
        """
        with self._lock:
            event = self._inflight.get(url)
        if event is not None:
            started = time.monotonic()
            done = event.wait(timeout)
            reqlog.add_ms('wait_ms', started)
            if not done:
                reqlog.note(wait_timeout=True)
        return self.get_bytes(url, consume=True)

    def _manifest_ttl(self, data):
        # A live media playlist goes stale after a couple of target
        # durations; serving an older copy starts the player behind the
        # window the CDN still holds.
        match = TARGET_DURATION.search(data[:4096])
        if not match:
            return self.ttl
        try:
            return min(self.ttl, max(2.0, 2 * float(match.group(1))))
        except ValueError:
            return self.ttl

    def _store_bytes(self, url, status, content_type, data, final_url, ttl=None, once=False):
        if self.max_bytes <= 0 or not data or len(data) > self.max_bytes:
            return
        entry = {'status': status, 'content_type': content_type, 'data': data, 'final_url': final_url,
                 'once': once}
        now = time.time()
        with self._lock:
            old = self._bytes.pop(url, None)
            if old:
                self._byte_total -= len(old[1]['data'])
            self._bytes[url] = (now + (self.ttl if ttl is None else ttl), entry)
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

    def _store_manifest(self, url, status, ctype, data, final_url):
        if data[:3] == BOM:
            data = data[3:]
        if not data or data.lstrip(b'\r\n \t')[:7] != b'#EXTM3U':
            return None
        if 200 <= status < 300:
            self._store_bytes(url, status, ctype or 'application/vnd.apple.mpegurl', data, final_url,
                              ttl=self._manifest_ttl(data), once=True)
        return data

    def _manifest_from(self, part, url, headers):
        """Probe bytes from the race when present, otherwise a fresh fetch."""
        if part is not None:
            return part['status'], part.get('content_type'), part['data'], part['final_url']
        status, hdrs, data, final_url = self._fetch(url, headers, MAX_PLAYLIST_BYTES)
        return status, hdrs.get('Content-Type'), data, final_url

    def _warm_bytes(self, winner, segments=True):
        probe = winner.get('probe') or {}
        status, ctype, data, final_url = self._manifest_from(probe.get('manifest'), winner['url'],
                                                             winner['headers'])
        data = self._store_manifest(winner['url'], status, ctype, data, final_url)
        if data is None:
            return
        text = data.decode('utf-8', 'replace')
        if '#EXT-X-STREAM-INF' in text:
            variants = self._media_uris(text)
            if not variants:
                return
            variant_url = urljoin(final_url, variants[0])
            part = probe.get('variant')
            if part is not None and part.get('url') != variant_url:
                part = None
            status, ctype, data, final_url = self._manifest_from(part, variant_url, winner['headers'])
            data = self._store_manifest(variant_url, status, ctype, data, final_url)
            if data is None:
                return
            text = data.decode('utf-8', 'replace')
        if segments:
            self._warm_segments(final_url, text, winner['headers'])

    def _segment_urls(self, base_url, text):
        return [urljoin(base_url, uri) for uri in self._media_uris(text)[-WARM_SEGMENTS:]]

    def _warm_segments(self, base_url, text, headers):
        urls = [u for u in self._segment_urls(base_url, text) if self.get_bytes(u) is None]
        events = {}
        with self._lock:
            for url in urls:
                if url not in self._inflight:
                    events[url] = self._inflight[url] = threading.Event()
        try:
            for url in urls:
                if url not in events or self._stopped.is_set():
                    continue
                started = time.monotonic()
                try:
                    status, hdrs, seg, seg_final = self._fetch(url, headers, MAX_SEGMENT_BYTES)
                    if 200 <= status < 300:
                        self._store_bytes(url, status,
                                          hdrs.get('Content-Type') or 'application/octet-stream',
                                          seg, seg_final)
                    reqlog.warm(host=_host(url), st=status, b=len(seg),
                                ms=int((time.monotonic() - started) * 1000))
                except Exception as error:
                    reqlog.warm(host=_host(url), err=type(error).__name__,
                                ms=int((time.monotonic() - started) * 1000))
                finally:
                    with self._lock:
                        if self._inflight.get(url) is events[url]:
                            self._inflight.pop(url, None)
                    events[url].set()
        finally:
            with self._lock:
                for url, event in events.items():
                    if self._inflight.get(url) is event:
                        self._inflight.pop(url, None)
            for event in events.values():
                event.set()

    def _spawn_segments(self, base_url, text, headers):
        if not self._seg_sem.acquire(blocking=False):
            return  # busy: the player simply fetches the segments itself

        def run():
            try:
                self._warm_segments(base_url, text, headers)
            finally:
                self._seg_sem.release()
        worker = threading.Thread(target=run, daemon=True)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(worker)
        worker.start()

    def adopt_race(self, winner):
        """Reuse a fresh foreground race's probe bytes for the channel being opened.

        The manifest (and variant) the probe already downloaded is cached at
        once, so the request that triggered the race is answered without a
        second upstream round trip; the segments a live player starts on are
        then fetched in the background. These are the bytes the player is
        about to request anyway, so this is not speculative prefetch.
        """
        if self.mode == 'off' or self.max_bytes <= 0 or not winner or not winner.get('probe'):
            return
        try:
            self._warm_bytes(winner, segments=False)
        except Exception:
            return
        probe = winner['probe']
        part = probe.get('variant') or probe.get('manifest')
        data = part['data'][3:] if part['data'][:3] == BOM else part['data']
        self.adopt_manifest(part['final_url'], data, winner['headers'])

    def adopt_manifest(self, final_url, data, headers):
        """Start warming the start segments of a media playlist just fetched."""
        if self.mode == 'off' or self.max_bytes <= 0:
            return
        text = data.decode('utf-8', 'replace')
        if '#EXT-X-STREAM-INF' in text or '#EXTINF' not in text:
            return
        self._spawn_segments(final_url, text, headers)

    def close(self):
        self._stopped.set()
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=1)


def strip_probe(winner):
    """A winner without its one-shot probe bytes, safe to cache and reuse."""
    if winner is None or 'probe' not in winner:
        return winner
    return {key: value for key, value in winner.items() if key != 'probe'}


def _host(url):
    from urllib.parse import urlsplit
    return urlsplit(url).netloc
