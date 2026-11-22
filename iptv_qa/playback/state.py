"""Bounded in-memory playback source/session state."""
import json
import threading
from collections import OrderedDict


class Registry:
    """Maps short ids to (url, headers) pairs discovered inside known playlists."""

    def __init__(self, limit=8192):
        self.lock = threading.Lock()
        self.by_rid = OrderedDict()
        self.by_url = {}
        self.meta_by_rid = {}
        self.counter = 0
        self.limit = limit

    @staticmethod
    def _key(url, headers):
        return (url, json.dumps(headers, sort_keys=True))

    def register(self, url, headers, meta=None):
        key = self._key(url, headers)
        with self.lock:
            rid = self.by_url.get(key)
            if rid is not None:
                self.by_rid.move_to_end(rid)
                if meta:
                    self.meta_by_rid[rid] = dict(meta)
                return rid
            self.counter += 1
            rid = self.counter
            self.by_rid[rid] = (url, dict(headers))
            self.by_url[key] = rid
            if meta:
                self.meta_by_rid[rid] = dict(meta)
            while len(self.by_rid) > self.limit:
                old_rid, (old_url, old_headers) = self.by_rid.popitem(last=False)
                self.by_url.pop(self._key(old_url, old_headers), None)
                self.meta_by_rid.pop(old_rid, None)
            return rid

    def meta(self, rid):
        with self.lock:
            return dict(self.meta_by_rid.get(rid) or {})

    def resolve(self, rid):
        with self.lock:
            pair = self.by_rid.get(rid)
            if pair is not None:
                self.by_rid.move_to_end(rid)
            return pair

class Sessions:
    def __init__(self, limit=64):
        self.lock = threading.Lock()
        self.data = OrderedDict()
        self.limit = limit

    def get(self, token):
        with self.lock:
            session = self.data.get(token)
            if session is not None:
                self.data.move_to_end(token)
            return session

    def set_source(self, token, session):
        with self.lock:
            self.data[token] = session
            self.data.move_to_end(token)
            while len(self.data) > self.limit:
                self.data.popitem(last=False)
