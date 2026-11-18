"""Durable, sanitized per-run history and readable static reports; Python 3.6+."""
import collections
import csv
import datetime
import html
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

FILES = ('iptv_summary.html', 'iptv_history.csv', 'iptv_history.json')
NUMBERS = ('unique_endpoints', 'tested_endpoints', 'probe_passed', 'decode_passed',
           'channels_total', 'channels_available', 'top3_entries', 'top5_entries',
           'short_decode_passed', 'unverified_endpoints', 'stable_endpoints',
           'basic_endpoints', 'pending_review_endpoints', 'stable_channels', 'basic_channels', 'pending_review_channels')
STATES = {'complete': '检测完成', 'upload_failed': '上传失败', 'failed': '检测失败',
          'publication_blocked': '保留上一版', 'interrupted': '运行中断'}
OBS_STATES = {'uploaded': '已上传', 'failed': '上传失败', 'not_configured': '尚未配置',
              'skipped': '未上传', 'pending': '等待上传'}
ERRORS = {'timeout': '超时', 'access_denied': '拒绝访问', 'not_found': '地址不存在',
          'connection_refused': '连接被拒绝', 'dns_error': '域名解析失败',
          'media_error': '媒体读取或解码错误', 'no_media_stream': '没有媒体流',
          'placeholder_short_file': '短视频占位', 'insufficient_decoded_media': '有效解码不足 4 秒',
          'local_network_unreachable': '内网地址在 ECS 不可达', 'multicast_unreachable': '运营商组播在 ECS 不可达',
          'ipv6_unavailable': 'ECS 暂无 IPv6 路由', 'audio_only_rejected': '纯音频未纳入电视订阅',
          'startup_too_slow': '视频起播超时', 'video_stalled': '视频帧停止推进',
          'insufficient_video': '有效视频帧或时长不足', 'stream_ended_early': '持续观察前直播结束',
          'not_deep_verified': '未通过两次深检', 'slow_video_delivery': '视频到达速度不足以连续播放',
          'hls_not_live': 'HLS 已结束或为点播', 'hls_not_advancing': 'HLS 直播分片不再更新',
          'hls_manifest_unavailable': 'HLS 播放列表无法读取',
          'audio_decode_failed': '音频解码未通过', 'proxy_delivery_failed': '网页代理取流未通过',
          'proxy_invalid_playlist': '网页代理清单无效', 'proxy_empty_media': '网页代理取得空媒体'}


def atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex[:8])
    try:
        with temp.open('w', encoding='utf-8', newline='') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp), str(path))
    finally:
        if temp.exists():
            temp.unlink()


def parsed_time(value):
    try:
        return datetime.datetime.strptime(value[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def local_time(value, offset=4):
    dt = parsed_time(value)
    return dt.astimezone(datetime.timezone(datetime.timedelta(hours=offset))).strftime('%Y-%m-%d %H:%M') if dt else '—'


def channel_names(path):
    names = set()
    if not path.exists():
        return None
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.startswith('#EXTINF:'):
            continue
        quoted = False
        for i, character in enumerate(line):
            if character == '"':
                quoted = not quoted
            elif character == ',' and not quoted:
                names.add(line[i + 1:].strip())
                break
    return sorted(names)


def number(value):
    return value if type(value) is int and value >= 0 else None


def read_run(run_dir, active_id):
    status_path, report_path = run_dir / 'status.json', run_dir / 'export/report.json'
    try:
        status = json.loads(status_path.read_text(encoding='utf-8'))
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
    except (OSError, ValueError):
        return None
    if status.get('diagnostic_sample') or report.get('diagnostic_sample'):
        return None
    state = status.get('state')
    if state == 'running':
        if run_dir.name == active_id:
            return None
        state = 'interrupted'
    if state not in STATES:
        return None
    row = {'run_id': run_dir.name, 'state': state,
           'started_at': status.get('started_at') or report.get('started_at'),
           'finished_at': status.get('finished_at') or report.get('finished_at'),
           'published': bool(report.get('published')),
           'obs_state': report.get('obs', {}).get('state', 'skipped'),
           'error_type': status.get('error_type', ''),
           'validation_policy': report.get('validation_policy',
               'strict-v1' if report.get('quality', {}).get('enabled') else 'legacy-short'),
           'sources': [{'name': s.get('name', ''), 'entries': number(s.get('entries'))}
                       for s in report.get('sources', [])],
           'errors': {k: number(v) for k, v in report.get('errors', {}).items() if k in ERRORS},
           'available_names': channel_names(run_dir / 'export/iptv_top5.m3u')}
    for key in NUMBERS:
        row[key] = number(report.get(key))
    row['has_statistics'] = bool(report and row['tested_endpoints'] is not None)
    if not row['has_statistics']:
        row['available_names'] = None
    tested, passed = row['tested_endpoints'], row['decode_passed']
    row['failed_endpoints'] = max(tested - passed - (row.get('unverified_endpoints') or 0), 0) if tested is not None and passed is not None else None
    row['success_percent'] = round(passed / tested * 100, 2) if tested and passed is not None else None
    start, finish = parsed_time(row['started_at']), parsed_time(row['finished_at'])
    row['duration_minutes'] = round((finish - start).total_seconds() / 60, 1) if start and finish else None
    raw_sets = report.get('sets', {})
    raw_quality = report.get('quality', {})
    row['quality'] = {k: number(raw_quality.get(k)) for k in ('deep_tested', 'deep_passed', 'recheck_tested', 'verified_twice',
                      'deep_seconds', 'recheck_interval_seconds', 'max_startup_seconds')} if raw_quality.get('enabled') else None
    row['sets'] = ({'channels_per_set': number(raw_sets.get('channels_per_set')),
                    'channels_with_backup': number(raw_sets.get('channels_with_backup')),
                    'channels_with_three_lines': number(raw_sets.get('channels_with_three_lines')),
                    'line_distribution': {str(i): number(raw_sets.get('line_distribution', {}).get(str(i))) for i in (1, 2, 3)}}
                   if raw_sets else None)
    row['concurrency'] = {k: number(v) for k, v in report.get('concurrency', {}).items()
                          if k in ('probe_workers', 'probe_per_host', 'probe_retry_workers', 'probe_retry_per_host',
                                   'decode_workers', 'decode_per_host', 'decode_retry_workers', 'decode_retry_per_host')}
    raw_comparison = report.get('source_comparison', {})
    row['source_comparison'] = {'sources': [{k: (r.get(k, '') if k == 'name' else number(r.get(k))) for k in ('name', 'candidate_addresses', 'passed_addresses', 'available_channels', 'top3_entries', 'top5_entries')} for r in raw_comparison.get('sources', [])],
                                **{k: number(raw_comparison.get(k)) for k in ('overlap_channels', 'new_candidate_channels', 'new_playable_channels', 'repository_best_channels', 'restored_channels')}} if raw_comparison else None
    return row


def collect(data_dir):
    data_dir = Path(data_dir)
    db = sqlite3.connect(str(data_dir / 'history.sqlite3'))
    try:
        db.execute('CREATE TABLE IF NOT EXISTS run_summaries (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        try:
            active_id = json.loads((data_dir / 'status.json').read_text(encoding='utf-8')).get('run_id')
        except (OSError, ValueError):
            active_id = None
        for path in sorted((data_dir / 'runs').glob('*')):
            if not path.is_dir() or not re.match(r'^\d{8}T\d{6}Z-[a-f0-9]{8}$', path.name):
                continue
            row = read_run(path, active_id)
            if row:
                db.execute('INSERT OR REPLACE INTO run_summaries VALUES (?,?)',
                           (row['run_id'], json.dumps(row, ensure_ascii=False)))
        db.commit()
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM run_summaries ORDER BY run_id')]
    finally:
        db.close()
    previous = None
    for row in rows:
        row.update(channels_delta=None, top3_delta=None, top5_delta=None,
                   newly_available=[], no_longer_available=[], comparison_run_id=None)
        if row['has_statistics'] and row['channels_available'] is not None:
            if previous and row.get('validation_policy', 'legacy') == previous.get('validation_policy', 'legacy'):
                row['comparison_run_id'] = previous['run_id']
                for field, output in [('channels_available', 'channels_delta'), ('top3_entries', 'top3_delta'), ('top5_entries', 'top5_delta')]:
                    if row.get(field) is not None and previous.get(field) is not None:
                        row[output] = row[field] - previous[field]
                if row['available_names'] is not None and previous['available_names'] is not None:
                    row['newly_available'] = sorted(set(row['available_names']) - set(previous['available_names']))
                    row['no_longer_available'] = sorted(set(previous['available_names']) - set(row['available_names']))
            previous = row
    return rows


def shown(value):
    return '—' if value is None else str(value)


def delta(value):
    return '—' if value is None else '{:+d}'.format(value)


def render_csv(rows):
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    def safe_row(values):
        writer.writerow([("'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else v) for v in values])
    safe_row(['运行ID', '开始时间(迪拜UTC+4)', '结束时间(迪拜UTC+4)', '开始时间(北京UTC+8)',
                     '状态', '输入记录数', '去重地址', '探测通过', '解码通过', '未通过地址', '最终通过比例(%)',
                     '频道总数', '可用频道', '频道变化', 'Top5线路', 'Top5变化', 'Top3线路', 'Top3变化',
                     '耗时(分钟)', 'OBS上传', '检测策略', '稳定地址', '基础地址', '待复查地址',
                     '新增可用频道', '减少的可用频道', '失败阶段原因'])
    for row in reversed(rows):
        source_total = sum(s['entries'] or 0 for s in row['sources']) if row['sources'] else None
        safe_row([row['run_id'], local_time(row['started_at']), local_time(row['finished_at']),
                         local_time(row['started_at'], 8), STATES[row['state']], source_total,
                         row['unique_endpoints'], row['probe_passed'], row['decode_passed'], row['failed_endpoints'], row['success_percent'],
                         row['channels_total'], row['channels_available'], row['channels_delta'], row['top5_entries'], row['top5_delta'],
                         row['top3_entries'], row['top3_delta'], row['duration_minutes'], OBS_STATES.get(row['obs_state'], '未知'),
                         row.get('validation_policy', 'legacy'), row.get('stable_endpoints'),
                         row.get('basic_endpoints'), row.get('pending_review_endpoints'),
                         '；'.join(row['newly_available']), '；'.join(row['no_longer_available']),
                         '；'.join('{}:{}'.format(ERRORS[k], v) for k, v in row['errors'].items())])
    # UTF-8 BOM makes the Chinese CSV open correctly in desktop spreadsheet apps.
    return '\ufeff' + buffer.getvalue()


def render_html(rows, generated_at, public_base='', catalog_state=None):
    esc = lambda value: html.escape(str(value), quote=True)
    latest = rows[-1] if rows else None
    latest_full = next((r for r in reversed(rows) if r['has_statistics']), None)
    cards = []
    if latest_full:
        for label, value, change in [('可用频道', latest_full['channels_available'], latest_full['channels_delta']),
                                     ('Top5 线路', latest_full['top5_entries'], latest_full['top5_delta']),
                                     ('Top3 线路', latest_full['top3_entries'], latest_full['top3_delta'])]:
            cards.append('<div class="card"><span>{}</span><strong>{}</strong><small>较上次完整检测 {}</small></div>'.format(esc(label), esc(shown(value)), esc(delta(change))))
        cards.append('<div class="card"><span>最终通过比例</span><strong>{}%</strong><small>{} / {} 个地址解码通过</small></div>'.format(
            esc(shown(latest_full['success_percent'])), esc(shown(latest_full['decode_passed'])), esc(shown(latest_full['tested_endpoints']))))
    table = []
    for row in reversed(rows[-90:]):
        details = []
        if row.get('validation_policy') == 'tiered-v1':
            details.append('分层音视频检测：稳定优选 {} 条，基础可播 {} 条，待复查 {} 条；待复查仍有本轮短检成功证据，不等于失效。'.format(
                shown(row.get('stable_endpoints')), shown(row.get('basic_endpoints')),
                shown(row.get('pending_review_endpoints'))))
        if row['errors']:
            details.append('失败阶段：' + '、'.join('{} {}'.format(ERRORS[k], v) for k, v in row['errors'].items()))
        if row['newly_available']:
            details.append('新增可用：' + '、'.join(row['newly_available']))
        if row['no_longer_available']:
            details.append('减少的可用频道：' + '、'.join(row['no_longer_available']))
        if row['error_type']:
            details.append('异常类型：' + row['error_type'])
        sources = '、'.join('{} {}'.format(s['name'], shown(s['entries'])) for s in row['sources'])
        if sources:
            details.insert(0, '输入记录：' + sources)
        if row.get('concurrency'):
            c = row['concurrency']
            details.append('并发设置：探测 {}，解码 {}；单服务器探测 {}，解码 {}'.format(
                shown(c.get('probe_workers')), shown(c.get('decode_workers')),
                shown(c.get('probe_per_host')), shown(c.get('decode_per_host'))))
        state_label = STATES[row['state']]
        ok = row['state'] == 'complete' and row['obs_state'] in ('uploaded', 'not_configured')
        values = [local_time(row['started_at']), shown(row['tested_endpoints']), shown(row['decode_passed']),
                  shown(row['failed_endpoints']), shown(row['channels_available']), delta(row['channels_delta']),
                  shown(row['top5_entries']), shown(row['top3_entries']), shown(row['duration_minutes'])]
        cells = ''.join('<td>{}</td>'.format(esc(v)) for v in values)
        cells += '<td><span class="badge {}">{}</span><small>{}</small></td>'.format('ok' if ok else 'warn', esc(state_label), esc(OBS_STATES.get(row['obs_state'], '未知')))
        table.append('<tr>{}</tr>'.format(cells))
        if details:
            table.append('<tr class="detail"><td colspan="10"><details><summary>本轮详情</summary>{}</details></td></tr>'.format(''.join('<p>{}</p>'.format(esc(v)) for v in details)))
    latest_status = '暂无检测记录' if not latest else '{} · {} · OBS {}'.format(local_time(latest['started_at']), STATES[latest['state']], OBS_STATES.get(latest['obs_state'], '未知'))
    latest_note = '' if not latest_full or latest is latest_full else '<p class="notice">最近一轮未完成完整统计，下方指标显示上一次完整检测。请查看历史表中的失败状态。</p>'
    links = [('iptv_top3.m3u', '兼容 Top3'),('iptv_set1.m3u', '兼容 Set1'),
             ('iptv_set2.m3u', '兼容 Set2'),('iptv_set3.m3u', '兼容 Set3'),
             ('iptv_history.csv', '下载 CSV'),('iptv_history.json', 'JSON 历史')]
    advanced = ''.join('<a href="{}">{}</a>'.format(esc(public_base.rstrip('/')+'/'+name if public_base else name),label) for name,label in links)
    nav = '<a class="primary" href="/tv/">播放频道</a><a href="/tv/manage/">管理频道</a>'
    nav += '<details class="advanced"><summary>历史与兼容文件</summary><nav>'+advanced+'</nav></details>'
    sets_panel = ''
    if latest_full and latest_full.get('sets'):
        s = latest_full['sets']
        sets_panel = '<section class="card" style="margin-top:18px"><b>兼容套表 · 自动生成</b><p>每套 {} 个频道，每个频道只出现一次。第 1 套主用，第 2、3 套备用。</p><small>{} 个频道有至少 2 条可用线路，{} 个有 3 条。缺少备用线路时复用最后一条，三套频道及顺序一致。</small></section>'.format(
            esc(shown(s['channels_per_set'])), esc(shown(s['channels_with_backup'])), esc(shown(s['channels_with_three_lines'])))
    comparison_panel = ''
    quality_panel = ''
    if latest_full and latest_full.get('quality'):
        q = latest_full['quality']
        quality_panel = '<section class="card" style="margin-top:18px"><b>视频质量复检</b><p>深检 {} 条，通过 {} 条；重新连接复检 {} 条，两次通过 {} 条。</p><small>必须有实际视频帧；每次持续观察至少 {} 秒，复检间隔至少 {} 分钟，首批视频帧等待上限 {} 秒。起播时间为 ECS 解码进度采样，不代表家庭网络实测。</small></section>'.format(
            *[esc(shown(q[k])) for k in ('deep_tested', 'deep_passed', 'recheck_tested', 'verified_twice', 'deep_seconds')],
            esc(shown(round(q['recheck_interval_seconds'] / 60, 1) if q['recheck_interval_seconds'] is not None else None)),
            esc(shown(q['max_startup_seconds'])))
    if latest_full and latest_full.get('validation_policy') == 'tiered-v1':
        quality_panel += '<section class="card"><b>可播与稳定分层</b><p>稳定 {} 条 · 基础 {} 条 · 待复查 {} 条</p><small>待复查有本轮音视频短检成功证据，作为备用；下轮重新检测。稳定精选仅包含两次深检通过的源。</small></section>'.format(
            esc(shown(latest_full.get('stable_endpoints'))), esc(shown(latest_full.get('basic_endpoints'))),
            esc(shown(latest_full.get('pending_review_endpoints'))))
    if latest_full and latest_full.get('source_comparison'):
        c = latest_full['source_comparison']
        cells = ''.join('<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
            esc(r['name']), esc(shown(r['candidate_addresses'])), esc(shown(r['passed_addresses'])),
            esc(shown(r['available_channels'])), esc(shown(r['top5_entries'])), esc(shown(r['top3_entries']))) for r in c['sources'])
        comparison_panel = '<h2>来源横向对比</h2><p class="muted">重复频道 {} 个；新增可用频道 {} 个；新库优选线路胜出 {} 个频道；补齐原本失效频道 {} 个。</p><div class="table-wrap"><table><thead><tr><th>来源</th><th>候选地址</th><th>本轮可播地址</th><th>可用频道</th><th>入选 Top5</th><th>入选 Top3</th></tr></thead><tbody>{}</tbody></table></div><small>同一线路来自多个来源时分别计入来源贡献。</small>'.format(
            esc(shown(c['overlap_channels'])), esc(shown(c['new_playable_channels'])), esc(shown(c['repository_best_channels'])), esc(shown(c['restored_channels'])), cells)
    if catalog_state:
        cards = []
        for key,label,tip in [('main','综合频道','每台最多五条主备线路'),('supplement','今日补充','新增来源，独立分组'),('english','英语频道','新闻与英语收听'),('removed','清理区','可在频道管理中恢复')]:
            cards.append('<div class="card"><span>{}</span><strong>{}</strong><small>{}</small></div>'.format(label,esc(shown(catalog_state.get(key))),tip))
        publication = catalog_state.get('publication') or {}
        current_note = '<section class="card current"><b>当前总表版本 {} · 已发布版本 {}</b><p>同步状态：{}。最近检测未发布时，继续使用已发布内容。</p></section>'.format(
            esc(catalog_state['revision']),esc(catalog_state['published_revision']),
            esc({'synced':'已同步','sync_failed':'同步失败，可在管理页重试','local_only':'仅本地','pending':'同步中'}.get(publication.get('state'),'尚未发布')))
    else:
        current_note = '<p class="notice">下方统计属于最近完成的检测，可能尚未发布，不代表当前在线频道数量。</p>'
    if sets_panel:
        sets_panel = '<details class="compat"><summary>兼容套表说明</summary>'+sets_panel+'</details>'
    return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>频道中心 · iptv-QA</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:#18243a;font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;font-size:15px;line-height:1.6}main{max-width:1320px;margin:auto;padding:36px 24px}h1{margin:0;font-size:30px}h2{font-size:20px;margin:30px 0 14px}.muted,small{color:#63738a}small{display:block;font-size:12px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap}a{color:#155acc;text-decoration:none}nav{display:flex;gap:12px;flex-wrap:wrap}nav a{background:white;border:1px solid #d9e1ee;padding:8px 12px;border-radius:8px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:24px}.card{background:white;padding:20px;border-radius:12px;border:1px solid #e2e7ef}.card strong{display:block;font-size:34px;margin:5px 0}.table-wrap{overflow:auto;background:white;border:1px solid #e2e7ef;border-radius:12px}table{border-collapse:collapse;width:100%;white-space:nowrap;text-align:left}th,td{padding:12px 14px;border-bottom:1px solid #edf0f5}th{font-weight:600;font-size:13px;color:#63738a;background:#fbfcfe}.badge{padding:3px 8px;border-radius:5px;font-size:12px}.ok{background:#e1f4e8;color:#176937}.warn{background:#fff0da;color:#8b5200}.detail td{padding:0 14px 10px}.detail details{white-space:normal;font-size:13px;color:#63738a}.detail p{margin:6px 0;overflow-wrap:anywhere}.detail summary{cursor:pointer}.notice{padding:12px;background:#fff0da;border-radius:8px}.foot{font-size:13px;margin-top:18px;color:#63738a}@media(max-width:760px){main{padding:24px 14px}.cards{grid-template-columns:repeat(2,1fr)}.card{padding:14px}.card strong{font-size:27px}}
.primary{background:#155acc;color:white}.advanced{position:relative}.advanced summary{padding:10px 14px;border:1px solid #dce4f0;border-radius:9px;cursor:pointer;color:#155acc}.advanced[open] nav{padding:14px;max-width:650px;gap:10px}.compat{margin:18px 0}.compat summary{cursor:pointer;color:#63738a}.current{margin:20px 0}.current p{color:#63738a}</style></head><body><main>
<div class="top"><div><h1>频道中心</h1><div class="muted">统一管理频道 · 自动切换备用线路 · 可恢复清理与私有备份</div></div>
<nav>''' + nav + '''</nav></div>
<p class="muted">最近一轮：''' + esc(latest_status) + '''</p>''' + latest_note + '''
''' + current_note + '''<section class="cards">''' + ''.join(cards) + '''</section>''' + sets_panel + quality_panel + '''
''' + comparison_panel + '''<h2>检测历史 <small>页面展示最近 90 次；CSV 和 JSON 包含全部汇总记录。时间为迪拜 UTC+4。</small></h2>
<div class="table-wrap"><table><thead><tr><th>检测开始</th><th>检测地址</th><th>本轮可播地址</th><th>未通过地址</th><th>可用频道</th><th>频道变化</th><th>Top5</th><th>Top3</th><th>分钟</th><th>状态 / OBS</th></tr></thead><tbody>''' + ''.join(table) + '''</tbody></table></div>
<p class="foot">按服务器定时计划执行，检测结束后更新。分层策略的可播地址包含稳定优选、基础可播和待复查；旧策略为严格深检口径，不同策略不计算增减。失败原因按检测阶段记录统计；增减对比上一次完成统计的检测。失败或中断时未知指标显示“—”。详细日志保留 14 天，汇总长期保留。</p>
<p class="foot">汇总生成：''' + esc(local_time(generated_at)) + '''（迪拜） / ''' + esc(local_time(generated_at, 8)) + '''（北京）。报告随检测自动更新；播放与管理入口使用当前总表；历史统计保留当轮检测口径。</p>
</main></body></html>'''


def refresh(data_dir, public_base=''):
    data_dir = Path(data_dir)
    rows = collect(data_dir)
    generated_at = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    target = data_dir / 'summaries'
    target.mkdir(exist_ok=True)
    catalog_state = None
    library_path = data_dir / 'library/catalog.sqlite3'
    if library_path.is_file():
        from iptv_qa.library.store import Library
        catalog_state = Library(library_path).summary()
    atomic(target / FILES[0], render_html(rows, generated_at, public_base, catalog_state))
    atomic(target / FILES[1], render_csv(rows))
    atomic(target / FILES[2], json.dumps({'generated_at': generated_at, 'display_timezone': 'Asia/Dubai', 'runs': rows}, ensure_ascii=False, indent=2) + '\n')
    return target, len(rows)
