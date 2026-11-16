"""On-demand transcoder lifecycle; independent of batch QA."""
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from .constants import USER_AGENT


class Transcoders:
    """Keeps at most `limit` on-demand ffmpeg HLS transcodes; stops idle ones."""

    def __init__(self, ffmpeg, limit=2, idle_seconds=90):
        self.lock = threading.Lock()
        self.ffmpeg = ffmpeg
        self.limit = limit
        self.idle = idle_seconds
        self.active = {}
        self.base = None
        self.closed = False

    def _ensure_base(self):
        if self.base is None:
            self.base = Path(tempfile.mkdtemp(prefix='iptv-tv-'))
        return self.base

    def get(self, index):
        with self.lock:
            entry = self.active.get(index)
            if entry:
                entry['last_used'] = time.time()
            return entry

    def start(self, key, url, headers):
        with self.lock:
            if self.closed:
                raise OSError('Transcoder manager is closed')
            entry = self.active.get(key)
            if entry:
                entry['last_used'] = time.time()
                if entry['process'].poll() is None:
                    return entry
                self._stop_locked(key)
            while len(self.active) >= self.limit:
                oldest = min(self.active, key=lambda name: self.active[name]['last_used'])
                self._stop_locked(oldest)
            work = self._ensure_base() / ('ch%s' % key)
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True)
            args = [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                    '-user_agent', headers.get('user_agent') or USER_AGENT]
            if headers.get('referer'):
                args += ['-referer', headers['referer']]
            args += ['-i', url,
                     '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
                     '-vf', 'scale=-2:min(720\\,ih)', '-b:v', '2M', '-maxrate', '2.4M', '-bufsize', '4M',
                     '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
                     '-f', 'hls', '-hls_time', '2', '-hls_list_size', '6',
                     '-hls_flags', 'delete_segments+append_list+omit_endlist+independent_segments',
                     '-hls_segment_filename', str(work / 'seg%05d.ts'), str(work / 'index.m3u8')]
            log = open(str(work / 'ffmpeg.log'), 'ab')
            process = subprocess.Popen(args, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
            entry = {'process': process, 'dir': work, 'last_used': time.time(), 'log': log}
            self.active[key] = entry
            return entry

    def reap_idle(self):
        now = time.time()
        with self.lock:
            for index in list(self.active):
                entry = self.active[index]
                if entry['process'].poll() is not None:
                    entry['process'].wait()
                    self.active.pop(index, None)
                    try:
                        entry['log'].close()
                    except Exception:
                        pass
                    shutil.rmtree(entry['dir'], ignore_errors=True)
                elif now - entry['last_used'] > self.idle:
                    self._stop_locked(index)

    def _stop_locked(self, index):
        entry = self.active.pop(index, None)
        if not entry:
            return
        try:
            entry['process'].terminate()
            entry['process'].wait(timeout=5)
        except Exception:
            try:
                entry['process'].kill()
                entry['process'].wait(timeout=5)
            except Exception:
                pass
        try:
            entry['log'].close()
        except Exception:
            pass
        shutil.rmtree(entry['dir'], ignore_errors=True)

    def close(self):
        with self.lock:
            self.closed = True
            for key in list(self.active):
                self._stop_locked(key)
            if self.base is not None:
                shutil.rmtree(self.base, ignore_errors=True)
                self.base = None
