"""Real FFmpeg regression for audio-only, video progress and live observation."""
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
import time
from urllib.parse import unquote, urlsplit

import iptv_checker as app


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def validate(bin_dir="/usr/bin"):
    cfg = dict(ffmpeg=str(Path(bin_dir)/'ffmpeg'), ffprobe=str(Path(bin_dir)/'ffprobe'),
               video_quality_enabled=True, require_video=True, decode_seconds=2,
               deep_seconds=3, deep_timeout=20, max_startup_seconds=10, max_stall_seconds=5,
               probe_timeout=15, io_timeout=5, decode_timeout=20, recheck_interval_seconds=0)
    with tempfile.TemporaryDirectory(prefix='iptv-quality-validation-') as temp:
        root = Path(temp)
        subprocess.check_call([cfg['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=500',
                               '-t', '8', '-c:a', 'aac', '-f', 'mpegts', str(root / 'audio.ts')])
        producer = subprocess.Popen([cfg['ffmpeg'], '-v', 'error', '-re', '-f', 'lavfi', '-i',
                                     'testsrc=size=160x90:rate=10', '-f', 'lavfi', '-i', 'sine=frequency=600',
                                     '-t', '180', '-c:a', 'aac', '-threads', '1',
                                     '-c:v', 'mpeg2video', '-g', '10', '-f', 'hls', '-hls_time', '1',
                                     '-hls_list_size', '5', '-hls_flags', 'delete_segments+temp_file', str(root / 'live.m3u8')],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        class Handler(SimpleHTTPRequestHandler):
            def translate_path(self, path):
                return str(root / unquote(urlsplit(path).path).lstrip('/'))

            def log_message(self, *args):
                pass

        server = Server(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        origin = 'http://127.0.0.1:{}'.format(server.server_port)
        try:
            deadline = time.monotonic() + 15
            while not (root / 'live.m3u8').exists() and time.monotonic() < deadline:
                time.sleep(.2)
            audio = app.probe(app.endpoint(origin + '/audio.ts', []), app.UA, cfg)
            assert not audio['ok'] and audio['error'] == 'audio_only_rejected', audio
            ep = app.endpoint(origin + '/live.m3u8', [])
            pr = app.probe(ep, app.UA, cfg)
            assert pr['ok'], pr
            short = app.decode(ep, pr, app.UA, cfg)
            assert short['ok'] and short['frames'] >= 4, short
            deep = app.check_video(ep, pr, app.UA, cfg, deep=True)
            assert deep['ok'] and deep['observed_seconds'] >= 3, deep
            # Full publish path with the production quality gates and real media.
            source = ('#EXTM3U\n#EXTINF:-1,TestTV\n'+origin+'/live.m3u8\n#EXTINF:-1,Audio\n'+origin+'/audio.ts\n'
                      '#EXTINF:-1 radio="true",CoverRadio\n'+origin+'/live.m3u8?radio=1\n')
            (root / 'source.m3u').write_text(source, encoding='utf-8')
            cfg.update(data_dir=str(root / 'data'), sources=[{'name': 'fixture', 'url': origin+'/source.m3u'}],
                       deep_validation_enabled=True, deep_workers=1, decode_workers=1, probe_workers=2,
                       summaries={'enabled': False})
            app.write_json(root / 'config.json', cfg)
            assert app.main(['--config', str(root/'config.json')]) == 0
            report = json.loads((root/'data/published/report.json').read_text())
            assert report['channels_available'] == 1 and report['quality']['verified_twice'] == 1, report
            published = (root/'data/published/iptv_top5.m3u').read_text()
            assert 'Audio' not in published and 'CoverRadio' not in published and 'TestTV' in published
            assert report['sources'][0]['excluded_radio_entries'] == 1, report
            # Tiered mode additionally requires independent audio decode and
            # successful retrieval through browser-proxy URL rewriting.
            from iptv_qa.detection import tiers
            tier_cfg = dict(cfg, validation_policy='tiered-v1', browser_proxy_check=True,
                            data_dir=str(root / 'tiered-data'))
            app.write_json(root / 'tiered-config.json', tier_cfg)
            assert app.main(['--config', str(root / 'tiered-config.json')]) == 0
            tier_report = json.loads((root/'tiered-data/published/report.json').read_text())
            assert tier_report['stable_endpoints'] == 1, tier_report
            assert tier_report['validation_policy'] == 'tiered-v1', tier_report
            assert 'TestTV' in (root/'tiered-data/published/iptv_stable.m3u').read_text()
            # The approved QA result must flow through the same library publisher.
            library_root = root / 'library-data'
            library_cfg = dict(tier_cfg, data_dir=str(library_root), channel_library={
                'enabled': True, 'path': str(library_root/'library/catalog.sqlite3'),
                'data_root': str(library_root)})
            app.write_json(root/'library-config.json', library_cfg)
            assert app.main(['--config', str(root/'library-config.json')]) == 0
            from iptv_qa.library.store import Library
            managed = Library(library_root/'library/catalog.sqlite3')
            assert len(managed.view('main')) == 1
            assert managed.view('main')[0]['lines'][0]['evidence'] == 'stable'
            assert 'library_version' in json.loads((library_root/'published/report.json').read_text())
            # A video-only source cannot pass basic A/V checks.
            muted = subprocess.run([cfg['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc=size=160x90:rate=10', '-t', '6', '-threads', '1', '-c:v',
                'mpeg2video', '-f', 'mpegts', str(root/'muted.ts')])
            assert muted.returncode == 0
            muted_ep = app.endpoint(origin + '/muted.ts', [])
            muted_probe = app.probe(muted_ep, app.UA, tier_cfg)
            muted_result = app.decode(muted_ep, muted_probe, app.UA, tier_cfg)
            assert not muted_result['ok'] and muted_result['error'] == 'audio_decode_failed', muted_result
            print('REAL_QUALITY_VALIDATION_OK', json.dumps({'audio_rejected': True,
                  'first_frame_seconds': short['first_frame_seconds'], 'deep_observed_seconds': deep['observed_seconds'],
                  'video_frames': deep['frames'], 'radio_with_video_excluded': True,
                  'two_connection_publication': True}), flush=True)
        finally:
            producer.terminate()
            producer.communicate(timeout=10)
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin-dir', default=os.environ.get('IPTV_FFMPEG_DIR', '/usr/bin'))
    args = parser.parse_args()
    validate(args.bin_dir)
