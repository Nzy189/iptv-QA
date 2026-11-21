"""Runtime startup evidence per line, learned from real playback races.

Scheduled QA ranks lines from the checker's network position at check time.
This in-memory record adds what viewers actually experienced: how long each
line took to deliver its manifest and first media bytes during racing, and
whether it recently failed. It never changes the library; it only reorders
lines for the next race, the all-fail fallback and the prewarm.
"""
import threading
import time
from collections import OrderedDict

FAIL_WINDOW = 600        # a failure keeps a line demoted for 10 minutes
FAIL_STREAK = 2          # consecutive failures before demotion
DEFAULT_BUCKET = 1       # unknown lines sort as a typical 2–4 s start
EWMA_WEIGHT = 0.4


class LineStats:
    def __init__(self, limit=4096, clock=time.time):
        self.limit = max(1, int(limit))
        self.clock = clock
        self._lock = threading.Lock()
        self._rows = OrderedDict()   # url -> dict(ewma, streak, last_fail, last_ok, samples)

    def record(self, url, ok, seconds):
        now = self.clock()
        with self._lock:
            row = self._rows.pop(url, None) or {'ewma': None, 'streak': 0, 'last_fail': 0.0,
                                                'last_ok': 0.0, 'samples': 0}
            if ok:
                seconds = max(0.0, float(seconds))
                row['ewma'] = seconds if row['ewma'] is None else (
                    row['ewma'] * (1 - EWMA_WEIGHT) + seconds * EWMA_WEIGHT)
                row['streak'] = 0
                row['last_ok'] = now
            else:
                row['streak'] += 1
                row['last_fail'] = now
            row['samples'] += 1
            self._rows[url] = row
            while len(self._rows) > self.limit:
                self._rows.popitem(last=False)

    def _get(self, url):
        with self._lock:
            row = self._rows.get(url)
            return dict(row) if row else None

    def failing(self, url):
        row = self._get(url)
        return bool(row and row['streak'] >= FAIL_STREAK
                    and self.clock() - row['last_fail'] < FAIL_WINDOW)

    def _key(self, index, line):
        row = self._get(line['url'])
        failing = bool(row and row['streak'] >= FAIL_STREAK
                       and self.clock() - row['last_fail'] < FAIL_WINDOW)
        bucket = int(row['ewma'] // 2) if row and row['ewma'] is not None else DEFAULT_BUCKET
        return (failing, bucket, index)

    def order(self, lines):
        """Return lines best-first, each tagged with its original ``slot``.

        Recently failing lines go last; lines measured under 2 s move ahead
        of unmeasured ones, lines measured over 4 s move behind them, and the
        library rank breaks every tie.
        """
        tagged = [dict(line, slot=line.get('slot', index)) for index, line in enumerate(lines)]
        return [line for _, line in sorted(
            ((self._key(index, line), line) for index, line in enumerate(tagged)),
            key=lambda pair: pair[0])]

    def all_failing(self, lines):
        return bool(lines) and all(self.failing(line['url']) for line in lines)

    def snapshot(self):
        with self._lock:
            return {url: dict(row) for url, row in self._rows.items()}
