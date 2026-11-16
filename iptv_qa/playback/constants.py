"""Playback protocol paths and bounded runtime defaults."""
import re

TV_COOKIE = 'tvsess'
STATIC_FILES = {'/tv/': ('index.html', 'text/html; charset=utf-8'),
                '/tv/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                '/tv/style.css': ('style.css', 'text/css; charset=utf-8'),
                '/tv/hls.min.js': ('hls.min.js', 'application/javascript; charset=utf-8'),
                '/tv/LICENSE-hlsjs.txt': ('LICENSE-hlsjs.txt', 'text/plain; charset=utf-8')}
PLAYER_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; media-src 'self' blob:; "
              "connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
URI_ATTR = re.compile(r'URI="([^"]*)"')
PLAY_PATH = re.compile(r'^/tv/play/(\d+)(?:/(\d+))?$')
STREAM_PATH = re.compile(r'^/tv/stream/(\d+)$')
TRANSCODE_PATH = re.compile(r'^/tv/transcode/(\d+)(?:/(\d+))?$')
TSEG_PATH = re.compile(r'^/tv/tseg/([0-9-]+)/([A-Za-z0-9._-]+)$')
SEGMENT_NAME = re.compile(r'^[A-Za-z0-9._-]+$')
BLOCKED_HOSTS = ('169.254.169.254', 'metadata.google.internal')
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
MAX_PLAYLIST_BYTES = 32 * 1024 * 1024
MAX_SEGMENT_BYTES = 64 * 1024 * 1024
MAX_BODY_BYTES = 64 * 1024
UPSTREAM_TIMEOUT = 20
TRANSCODE_START_TIMEOUT = 15
BOM = b'\xef\xbb\xbf'
