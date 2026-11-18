"""Bounded background pre-race for the next channel (server-side prewarm)."""
import threading
import time
from collections import OrderedDict


class Prefetcher:
    """Race the next channel's lines ahead of time and remember the winner.

    Phase 1 implements light/full identically (race only); Phase 2 adds a
    byte cache to full. 'off' disables all work.
    """

    VALID_MODES = ('off', 'light', 'full')

    def __init__(self, race_func=None, mode='light', max_channels=16, max_workers=4, ttl=60):
        self.race_func = race_func
        self.mode = mode if mode in self.VALID_MODES else 'light'
        self.max_channels = max(1, int(max_channels))
        self.ttl = max(1, int(ttl))
        self._lock = threading.Lock()
        self._entries = OrderedDict()   # (view, channel_id) -> (expires, winner)
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
        return winner

    def warm(self, view, channel_id, lines, mode=None, wait=False):
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

    def close(self):
        self._stopped.set()
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=1)
