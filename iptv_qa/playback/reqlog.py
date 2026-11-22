"""One structured log line per finished TV request, for observing real playback.

Each request handled by the playback service runs on its own thread, so a
thread-local record collects what happened along the way (which channel,
how the line was chosen, cache use, upstream time, bytes, status) and is
written as a single ``TVREQ {json}`` line when the request ends. Background
warm fetches write ``TVWARM {json}`` lines. Tokens and query strings are
never recorded. ``scripts/tv_session_report.py`` turns these lines into a
per-session report.
"""
import json
import logging
import threading
import time

LOG = logging.getLogger('iptv.tv')
_local = threading.local()


def begin(kind, **fields):
    _local.rec = dict(kind=kind, ts=round(time.time(), 3), **fields)
    _local.t0 = time.monotonic()


def active():
    return getattr(_local, 'rec', None) is not None


def note(**fields):
    rec = getattr(_local, 'rec', None)
    if rec is not None:
        rec.update(fields)


def add_ms(key, started):
    """Accumulate milliseconds since ``started`` (a time.monotonic() value) into key."""
    rec = getattr(_local, 'rec', None)
    if rec is not None:
        rec[key] = rec.get(key, 0) + int((time.monotonic() - started) * 1000)


def end():
    rec = getattr(_local, 'rec', None)
    if rec is None:
        return None
    _local.rec = None
    rec['ms'] = int((time.monotonic() - _local.t0) * 1000)
    dur = rec.get('dur')
    if rec.get('kind') == 'rstream' and dur and rec['ms'] > dur * 1000 * 0.8:
        # serving took most of the segment's own play time: a viewer at the
        # live edge freezes when this repeats
        rec['slow'] = True
    LOG.info('TVREQ %s', json.dumps(rec, ensure_ascii=False, separators=(',', ':')))
    return rec


def warm(**fields):
    LOG.info('TVWARM %s', json.dumps(fields, ensure_ascii=False, separators=(',', ':')))
