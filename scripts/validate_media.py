"""Real ffmpeg + HTTP/HLS + complete publication regression, executed on the ECS."""
import argparse
import os
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
from pathlib import Path
from socketserver import ThreadingMixIn
import subprocess
import tempfile
import threading
from urllib.parse import unquote, urlsplit

import iptv_checker as app


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def validate(bin_dir):
    cfg = {'ffmpeg': str(Path(bin_dir) / 'ffmpeg'), 'ffprobe': str(Path(bin_dir) / 'ffprobe'),
           'probe_workers': 8, 'decode_workers': 3, 'probe_timeout': 10, 'decode_timeout': 20,
           'io_timeout': 3, 'decode_seconds': 4, 'minimum_publish_ratio': .5}
    with tempfile.TemporaryDirectory(prefix='iptv-media-validation-') as temp:
        base = Path(temp)
        for name, seconds in [('full', 8), ('short', 1)]:
            subprocess.check_call([cfg['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
                                   'testsrc=size=160x90:rate=10', '-t', str(seconds), '-threads', '1',
                                   '-c:v', 'mpeg2video', '-g', '10', '-f', 'mpegts', str(base / (name + '.ts'))])
        subprocess.check_call([cfg['ffmpeg'], '-v', 'error', '-i', str(base / 'short.ts'), '-threads', '1',
                               '-c:v', 'mpeg4', '-movflags', '+faststart', str(base / 'short.mp4')])
        subprocess.check_call([cfg['ffmpeg'], '-v', 'error', '-i', str(base / 'full.ts'), '-c', 'copy',
                               '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0', str(base / 'live.m3u8')])

        class Handler(SimpleHTTPRequestHandler):
            def translate_path(self, path):
                return str(base / unquote(urlsplit(path).path).lstrip('/'))

            def log_message(self, *args):
                pass

        server = ThreadedHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        origin = 'http://127.0.0.1:{}'.format(server.server_port)
        try:
            full = app.endpoint(origin + '/live.m3u8', [])
            probed = app.probe(full, app.UA, cfg)
            assert probed['ok'], ('HLS probe failed', probed)
            assert 0 < float(probed['duration']) < 300
            decoded = app.decode(full, probed, app.UA, cfg)
            assert decoded['ok'] and decoded['frames'] > 0, ('HLS decode failed', decoded)
            short = app.endpoint(origin + '/short.mp4', [])
            assert not app.probe(short, app.UA, cfg)['ok'], 'Short placeholder was retained'
            short_decoded = app.decode(short, {}, app.UA, cfg)
            assert not short_decoded['ok'], 'Early EOF was incorrectly counted as four seconds'
            print('REAL_MEDIA_CHECKS_OK', json.dumps({'hls_frames': decoded['frames'],
                  'hls_seconds': decoded['decoded_seconds'], 'short_seconds': short_decoded['decoded_seconds']}), flush=True)

            lines = ['#EXTM3U']
            for index in range(7):
                lines.extend(['#EXTINF:-1 group-title="Test",News', origin + '/live.m3u8?line=' + str(index)])
            lines.extend(['#EXTINF:-1 group-title="Test",ShortPlaceholder', origin + '/short.mp4',
                          '#EXTINF:-1 group-title="Test",Unavailable', origin + '/missing.m3u8'])
            (base / 'source1.m3u').write_text('\n'.join(lines), encoding='utf-8')
            (base / 'source2.m3u').write_text('#EXTM3U\n#EXTINF:-1 group-title="Other",News\n' + origin + '/live.m3u8?line=0\n', encoding='utf-8')
            cfg.update(data_dir=str(base / 'data'), sources=[
                {'name': 'one', 'url': origin + '/source1.m3u'},
                {'name': 'two', 'url': origin + '/source2.m3u'}])
            config_path = base / 'config.json'
            config_path.write_text(json.dumps(cfg), encoding='utf-8')
            assert app.main(['--config', str(config_path)]) == 0, 'End-to-end job failed'
            published = base / 'data' / 'published'
            report = json.loads((published / 'report.json').read_text(encoding='utf-8'))
            assert report['top5_entries'] == 5 and report['top3_entries'] == 3, report
            assert report['channels_available'] == 1, report
            history = app.History(base / 'data' / 'history.sqlite3')
            assert history.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            history.close()
            first_version = (published / 'current').resolve()
            for name in ('source1.m3u', 'source2.m3u'):
                (base / name).write_text('#EXTM3U\n#EXTINF:-1,Unavailable\n' + origin + '/missing.m3u8\n', encoding='utf-8')
            assert app.main(['--config', str(config_path)]) == 2, 'Zero-result job should block publication'
            assert (published / 'current').resolve() == first_version, 'Last good publication was overwritten'
            print('END_TO_END_PUBLICATION_OK top5=5 top3=3; ZERO_RESULT_RETAINED_PREVIOUS', flush=True)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin-dir', default=os.environ.get('IPTV_FFMPEG_DIR', '/usr/bin'))
    args = parser.parse_args()
    validate(args.bin_dir)
