#!/usr/bin/env python3
"""HTTP host for dashboard-only, player-only, or combined operation."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
from pathlib import Path
from socketserver import ThreadingMixIn
import threading
from urllib.parse import unquote, urlsplit

from iptv_qa.reporting.history import render_html

PUBLIC_FILES = {'/iptv_history.csv': ('iptv_history.csv', 'text/csv; charset=utf-8'),
                '/iptv_history.json': ('iptv_history.json', 'application/json; charset=utf-8'),
                '/api/history': ('iptv_history.json', 'application/json; charset=utf-8')}
PLAYLISTS = ('iptv_ok.m3u', 'iptv_top3.m3u', 'iptv_set1.m3u', 'iptv_set2.m3u', 'iptv_set3.m3u', 'iptv_english.m3u')
STATUS_FIELDS = ('run_id', 'state', 'stage', 'completed', 'total', 'passed', 'updated_at', 'started_at',
                 'finished_at', 'channels_available', 'top3_entries', 'top5_entries', 'error_type')


def make_handler(data_dir, public_base, tv_plugin=None, dashboard_enabled=True):
    data_dir = Path(data_dir)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'IPTV-Dashboard'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(60)

        def log_message(self, fmt, *args):
            logging.info('%s %s %r', self.client_address[0], self.command, urlsplit(self.path).path)

        def send_body(self, code, body, content_type, download=False):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="iptv_history.csv"')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def json_body(self, value, code=200):
            self.send_body(code, json.dumps(value, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            if tv_plugin is not None and tv_plugin.handle_post(self):
                return
            self.json_body({'error': 'not_found'}, 404)

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            try:
                if tv_plugin is not None and tv_plugin.handle_get(self):
                    return
                if path == '/healthz':
                    return self.json_body({'ok': True})
                if not dashboard_enabled:
                    if path == '/':
                        self.send_response(302)
                        self.send_header('Location', '/tv/')
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    if path.lstrip('/') not in PLAYLISTS:
                        return self.json_body({'error': 'not_found'}, 404)
                if path in ('/', '/iptv_summary.html'):
                    history = json.loads((data_dir / 'summaries/iptv_history.json').read_text(encoding='utf-8'))
                    page = render_html(history['runs'], history['generated_at'])
                    banner = '<section class="card" style="margin-top:18px"><b id="live-title">读取最新进度…</b><small id="live-detail">页面每 30 秒自动更新</small><div id="progress-wrap" hidden style="background:#e2e7ef;margin-top:10px;border-radius:5px;overflow:hidden"><div id="progress" style="height:6px;background:#155acc;width:0"></div></div></section>'
                    if tv_plugin is not None:
                        banner = banner.replace('</section>',
                                                '<div style="margin-top:10px"><a href="/tv/" style="color:#155acc;font-weight:600">网页电视</a>'
                                                '<small style="color:#57606a"> · 换台播放已验证频道</small></div></section>', 1)
                    page = page.replace('<section class="cards">', banner + '<section class="cards">', 1)
                    initial = json.dumps(history['generated_at']).replace('<', '\\u003c')
                    script = '''<script>
let generation=INITIAL;
const stages={catalog_download:'下载仓库与语言资料',catalog_classify:'中文、英语频道分流',download:'下载直播源',probe1:'初次探测',probe2:'复查失败地址',probe3:'复查恢复的服务器',decode1:'实际解码验证',decode2:'重试解码',render:'生成订阅文件',finished:'检测结束',failed:'检测失败'};
const stageLabel=stage=>stage&&stage.startsWith('deep')?'候选源持续视频深检':stage&&stage.startsWith('recheck')?(stage==='recheck_wait'?'等待间隔后重新连接':'重新连接复检'):(stages[stage]||stage||'准备中');
const states={complete:'最近一次检测已完成',running:'检测进行中',upload_failed:'检测完成，上传失败',failed:'最近一次检测失败',publication_blocked:'检测结果异常，保留上一版'};
async function refresh(){try{const response=await fetch('/api/status',{cache:'no-store'});if(!response.ok)throw Error();const x=await response.json();if(x.summary_generated_at&&x.summary_generated_at!==generation){location.reload();return;}const s=x.run||{};document.getElementById('live-title').textContent=states[s.state]||'等待检测';let detail='每周日、一、三迪拜 04:00 / 北京 08:00 开始 · 每 30 秒自动更新';if(s.state==='running'){detail=stageLabel(s.stage)+'：'+(s.completed||0)+' / '+(s.total||0)+'，本阶段通过 '+(s.passed||0);if(s.updated_at&&Date.now()-Date.parse(s.updated_at)>900000)detail+=' · 进度暂未更新';}if(x.summary_state==='failed')detail+=' · 汇总上传失败';document.getElementById('live-detail').textContent=detail;const active=s.state==='running'&&s.total>0;document.getElementById('progress-wrap').hidden=!active;document.getElementById('progress').style.width=(active?Math.max(0,Math.min(100,100*s.completed/s.total)):0)+'%';}catch(e){document.getElementById('live-detail').textContent='暂时无法读取进度，30 秒后重试';}}
const originalRefresh=refresh;
refresh=async function(){await originalRefresh();try{const r=await fetch('/api/status',{cache:'no-store'});const x=await r.json();const label=x.scope==='english'?'英语频道':x.scope==='chinese'?'中文频道':'';if(label&&x.run&&x.run.state==='running')document.getElementById('live-title').textContent=label+'检测进行中';if(x.catalog_counts){const c=x.catalog_counts;document.getElementById('live-detail').textContent+=' · 新库中文 '+(c.zh_unique_channels||0)+' 台 / 英语 '+(c.en_unique_channels||0)+' 台';}if(x.english_channels)document.getElementById('live-detail').textContent+=' · 英语已生成 '+x.english_channels+' 台';}catch(e){}};
refresh();setInterval(refresh,30000);
</script>'''.replace('INITIAL', initial)
                    page = page.replace('</body>', script + '</body>')
                    return self.send_body(200, page.encode('utf-8'), 'text/html; charset=utf-8')
                if path == '/api/status':
                    raw = json.loads((data_dir / 'status.json').read_text(encoding='utf-8'))
                    history = json.loads((data_dir / 'summaries/iptv_history.json').read_text(encoding='utf-8'))
                    pipeline = {}
                    try:
                        pipeline = json.loads((data_dir / 'pipeline.json').read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        pass
                    if pipeline.get('state') == 'running':
                        phase = pipeline.get('phase')
                        if phase == 'english':
                            try:
                                raw = json.loads((data_dir / 'english/status.json').read_text(encoding='utf-8'))
                            except (OSError, ValueError):
                                raw = {'state': 'running', 'stage': 'download'}
                        elif phase in ('catalog_download', 'catalog_classify'):
                            raw = {'state': 'running', 'stage': phase, 'updated_at': pipeline.get('updated_at')}
                    english_channels = None
                    try:
                        er = json.loads((data_dir / 'english/published/report.json').read_text(encoding='utf-8'))
                        if er.get('obs', {}).get('state') == 'uploaded':
                            english_channels = er.get('channels_available')
                    except (OSError, ValueError):
                        pass
                    try:
                        summary = json.loads((data_dir / 'summaries/status.json').read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        summary = {}
                    return self.json_body({'run': {k: raw[k] for k in STATUS_FIELDS if k in raw},
                                           'summary_generated_at': history.get('generated_at'),
                                           'summary_state': summary.get('state'), 'scope': pipeline.get('phase'),
                                           'catalog_counts': pipeline.get('catalog_counts'), 'english_channels': english_channels})
                if path in PUBLIC_FILES:
                    name, mime = PUBLIC_FILES[path]
                    return self.send_body(200, (data_dir / 'summaries' / name).read_bytes(), mime, path.endswith('.csv'))
                if path.lstrip('/') in PLAYLISTS and path.count('/') == 1:
                    if not public_base:
                        name = path.lstrip('/')
                        if name == 'iptv_english.m3u':
                            target = data_dir / 'english/published/iptv_top3.m3u'
                        else:
                            target = data_dir / 'published' / ('iptv_top5.m3u' if name == 'iptv_ok.m3u' else name)
                        if not target.is_file():
                            return self.json_body({'error': 'playlist_not_published'}, 404)
                        return self.send_body(200, target.read_bytes(), 'audio/x-mpegurl; charset=utf-8')
                    self.send_response(302)
                    self.send_header('Location', public_base.rstrip('/') + path)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                return self.json_body({'error': 'not_found'}, 404)
            except (OSError, ValueError, KeyError):
                return self.json_body({'error': 'report_temporarily_unavailable'}, 503)

    return Handler


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        self.slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def create_player(password, public_base='', ffmpeg=None, transcode_limit=2, transcode_idle=90):
    """Playback is optional; dashboard-only startup never imports the player."""
    from iptv_qa.playback.service import TVPlugin, assets_dir
    playlists = (('set1', 'Set1', 'iptv_set1.m3u'), ('set2', 'Set2', 'iptv_set2.m3u'),
                 ('set3', 'Set3', 'iptv_set3.m3u'), ('top3', 'Top3', 'iptv_top3.m3u'))
    return TVPlugin(password, assets_dir(), playlists, public_base, ffmpeg=ffmpeg,
                    transcode_limit=transcode_limit, transcode_idle_seconds=transcode_idle)


def main(argv=None, default_mode='all'):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8088, type=int)
    parser.add_argument('--data-dir', default='/var/lib/iptv-qa')
    parser.add_argument('--public-base', default='')
    parser.add_argument('--mode', choices=('all', 'dashboard', 'player'), default=default_mode,
                        help='Run both interfaces, reports only, or standalone playback')
    parser.add_argument('--ffmpeg', default=None, help='Optional player transcoder binary')
    parser.add_argument('--transcode-limit', type=int, default=2)
    parser.add_argument('--transcode-idle', type=int, default=90, help='Stop idle transcodes after these seconds')
    args = parser.parse_args(argv)
    if args.transcode_limit < 1 or args.transcode_idle < 1:
        parser.error('Transcode limit and idle seconds must be positive')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    tv_plugin = None
    password = os.environ.get('IPTV_TV_PASSWORD', '')
    if args.mode == 'player' and not password:
        parser.error('Set IPTV_TV_PASSWORD to run the standalone player')
    if password and args.mode != 'dashboard':
        tv_plugin = create_player(password, args.public_base, args.ffmpeg,
                                  args.transcode_limit, args.transcode_idle)
        logging.info('Web TV player enabled at /tv/')
    elif args.mode == 'all':
        logging.warning('IPTV_TV_PASSWORD not set; web TV player disabled')
    server = Server((args.host, args.port), make_handler(args.data_dir, args.public_base, tv_plugin,
                                                       dashboard_enabled=args.mode != 'player'))
    logging.info('IPTV %s service listening on %s:%d', args.mode, args.host, args.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if tv_plugin is not None:
            tv_plugin.close()


if __name__ == '__main__':
    main()
