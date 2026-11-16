#!/usr/bin/env python3
"""Daily IPTV validation and atomic top-3/top-5 publication (Python >= 3.6)."""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import datetime
import ipaddress
import json
import logging
import os
from pathlib import Path
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen
import uuid

from iptv_qa.common.io import utcnow, atomic_text, write_json
from iptv_qa.common.playlists import normalize, split_extinf, endpoint, parse_playlist, merge_entries
from iptv_qa.publishing.obs import obs_settings, publish_obs

LOG = logging.getLogger('iptv')
UA = 'AppleCoreMedia/1.0.0.20K71 (Apple TV; U; CPU OS 16_1 like Mac OS X; en_us)'
PROTOCOLS = 'http,https,tcp,tls,crypto,httpproxy,udp,rtp,rtsp,rtmp,rtmps,rtmpt,rtmpts,srt,data'


def download_source(source, target, cfg):
    last_error = 'unknown'
    for attempt in range(3):
        try:
            request = Request(source['url'], headers={'User-Agent': source.get('user_agent', 'Mozilla/5.0')})
            with urlopen(request, timeout=cfg.get('download_timeout', 40)) as response:
                limit = cfg.get('max_source_bytes', 32 * 1024 * 1024)
                data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Playlist exceeds configured size limit')
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = data.decode('gb18030')
            entries = parse_playlist(text)
            atomic_text(target, text)
            return entries
        except Exception as exc:
            # Subscription tokens and URL query strings must not reach the journal.
            last_error = type(exc).__name__
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError('Source {} download failed ({})'.format(source['name'], last_error))


def run_tool(args, timeout):
    started = time.monotonic()
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = process.communicate(timeout=timeout)
            code = process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate()
            return {'code': -1, 'error': 'timeout', 'stdout': out.decode('utf-8', 'replace'),
                    'seconds': round(time.monotonic() - started, 3)}
        message = err.decode('utf-8', 'replace')
        if '401' in message or '403' in message:
            error = 'access_denied'
        elif '404' in message:
            error = 'not_found'
        elif 'Connection refused' in message:
            error = 'connection_refused'
        elif 'resolve' in message.lower():
            error = 'dns_error'
        elif code:
            error = 'media_error'
        else:
            error = ''
        return {'code': code, 'error': error, 'stdout': out.decode('utf-8', 'replace'),
                'seconds': round(time.monotonic() - started, 3)}
    except OSError:
        raise RuntimeError('Cannot execute media tool: ' + args[0])


def input_args(ep, ua, cfg):
    args = ['-protocol_whitelist', PROTOCOLS, '-rw_timeout', str(int(cfg.get('io_timeout', 10) * 1000000))]
    if urlsplit(ep['url']).scheme.lower() in ('http', 'https'):
        args += ['-user_agent', ep['headers'].get('user_agent', ua)]
        if ep['headers'].get('referer'):
            args += ['-referer', ep['headers']['referer']]
    return args


def is_placeholder(fmt, duration):
    try:
        seconds = float(duration or 0)
    except (TypeError, ValueError):
        return False
    # A sliding HLS/DASH manifest reports a finite live window, not the programme length.
    return bool(0 < seconds < 300 and not set(fmt.split(',')) & {'hls', 'applehttp', 'dash'})


def probe(ep, ua, cfg):
    if cfg.get('skip_unroutable'):
        try:
            address = ipaddress.ip_address(ep['host'])
        except ValueError:
            address = None
        reason = ''
        if address is not None:
            if address.is_multicast:
                reason = 'multicast_unreachable'
            elif address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
                reason = 'local_network_unreachable'
            elif address.version == 6 and not cfg.get('ipv6_available', True):
                reason = 'ipv6_unavailable'
        if reason:
            return {'id': ep['id'], 'url': ep['url'], 'ok': False, 'seconds': 0, 'error': reason}
    args = [cfg['ffprobe'], '-v', 'error'] + input_args(ep, ua, cfg)
    args += ['-analyzeduration', '4000000', '-probesize', '3000000',
             '-show_entries', 'stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate:stream_disposition=attached_pic:format=duration,format_name',
             '-of', 'json', ep['url']]
    result = run_tool(args, cfg.get('probe_timeout', 25))
    record = {'id': ep['id'], 'url': ep['url'], 'ok': False, 'seconds': result['seconds'],
              'error': result['error'], 'ua': ep['headers'].get('user_agent', ua)}
    try:
        info = json.loads(result['stdout'])
    except (TypeError, ValueError):
        info = {}
    streams = info.get('streams', [])
    video = [s for s in streams if s.get('codec_type') == 'video' and s.get('width') and s.get('height')
             and not s.get('disposition', {}).get('attached_pic')]
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    fmt = info.get('format', {})
    record.update(format=fmt.get('format_name', ''), duration=fmt.get('duration'),
                  audio_only=bool(audio and not video))
    if video:
        record.update(width=video[0]['width'], height=video[0]['height'], codec=video[0].get('codec_name'),
                      video_index=video[0].get('index'), pixel_format=video[0].get('pix_fmt'),
                      frame_rate=video[0].get('avg_frame_rate'))
    if result['code'] == 0 and (video or audio):
        record['ok'] = True
        record['error'] = ''
    if is_placeholder(record['format'], record['duration']):
        record.update(ok=False, error='placeholder_short_file')
    if record['audio_only'] and cfg.get('require_video', True):
        record.update(ok=False, error='audio_only_rejected')
    if not record['ok'] and not record['error']:
        record['error'] = 'no_media_stream'
    return record


def decode(ep, probe_record, ua, cfg):
    if cfg.get('video_quality_enabled', False):
        return check_video(ep, probe_record, ua, cfg)
    target = cfg.get('decode_seconds', 4)
    args = [cfg['ffmpeg'], '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
            '-threads', '1'] + input_args(ep, ua, cfg)
    args += ['-i', ep['url'], '-t', str(target)]
    if probe_record.get('audio_only'):
        args += ['-map', '0:a:0', '-vn']
    else:
        args += ['-map', '0:v:0', '-map', '0:a:0?']
    args += ['-sn', '-dn', '-threads', '1', '-progress', 'pipe:1', '-nostats', '-f', 'null', '-']
    result = run_tool(args, cfg.get('decode_timeout', 40))
    frames, seconds = 0, 0.0
    for line in result['stdout'].splitlines():
        key, sep, value = line.partition('=')
        try:
            if key == 'out_time_us':
                seconds = max(seconds, float(value) / 1000000)
            elif key == 'frame':
                frames = max(frames, int(value))
        except ValueError:
            pass
    duration_ok = seconds >= target - 0.2
    video_ok = bool(probe_record.get('audio_only') or frames > 0)
    ok = result['code'] == 0 and duration_ok and video_ok
    return {'id': ep['id'], 'url': ep['url'], 'ok': ok, 'seconds': result['seconds'],
            'decoded_seconds': round(seconds, 3), 'frames': frames,
            'error': '' if ok else result['error'] or 'insufficient_decoded_media'}


def check_video(ep, probe_record, ua, cfg, deep=False):
    from iptv_qa.detection.quality import observe_video, HLSObserver, hls_advanced
    started = time.monotonic()
    if probe_record.get('audio_only'):
        return {'id': ep['id'], 'url': ep['url'], 'ok': False, 'seconds': 0,
                'error': 'audio_only_rejected'}
    target = cfg.get('deep_seconds', 30) if deep else cfg.get('decode_seconds', 4)
    hls, first_manifest = None, None
    if deep and set(probe_record.get('format', '').split(',')) & {'hls', 'applehttp'}:
        headers = {'User-Agent': ep['headers'].get('user_agent', ua)}
        if ep['headers'].get('referer'):
            headers['Referer'] = ep['headers']['referer']
        try:
            hls = HLSObserver(ep['url'], headers, cfg.get('io_timeout', 10))
            first_manifest = hls.snapshot()
            if first_manifest['ended']:
                return {'id': ep['id'], 'url': ep['url'], 'ok': False, 'seconds': round(time.monotonic()-started, 3),
                        'deep': True, 'error': 'hls_not_live'}
            # Two segment periods allow slow but valid manifests to advance.
            target = max(target, first_manifest['target_duration'] * 2 + 2)
        except Exception:
            return {'id': ep['id'], 'url': ep['url'], 'ok': False, 'seconds': round(time.monotonic()-started, 3),
                    'deep': True, 'error': 'hls_manifest_unavailable'}
    stream = probe_record.get('video_index')
    mapping = '0:{}'.format(stream) if stream is not None else '0:v:0'
    args = [cfg['ffmpeg'], '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
            '-threads', '1'] + input_args(ep, ua, cfg)
    args += ['-i', ep['url']]
    if not deep:
        args += ['-t', str(target)]
    # Video-only output prevents audio timestamps from disguising missing video.
    # Passthrough avoids counting frames invented by frame-rate conversion.
    args += ['-map', mapping, '-an', '-sn', '-dn', '-threads', '1', '-fps_mode', 'passthrough',
             '-stats_period', '0.25', '-progress', 'pipe:1', '-nostats', '-f', 'null', '-']
    timeout = max(cfg.get('deep_timeout', 90), target + cfg.get('max_startup_seconds', 12) + 15) if deep else cfg.get('decode_timeout', 40)
    result = observe_video(args, timeout,
                           target, cfg.get('max_startup_seconds', 12), cfg.get('max_stall_seconds', 8),
                           wall_window=target if deep else 0)
    if result['ok'] and hls is not None:
        try:
            last_manifest = hls.snapshot()
            result['hls_advanced'] = hls_advanced(first_manifest, last_manifest)
            if not result['hls_advanced']:
                result.update(ok=False, error='hls_not_advancing')
        except Exception:
            result.update(ok=False, error='hls_manifest_unavailable')
    result['seconds'] = round(time.monotonic() - started, 3)
    result.update(id=ep['id'], url=ep['url'], deep=deep,
                  startup_class=('fast' if (result.get('first_frame_seconds') or 999) <= cfg.get('fast_startup_seconds', 5) else 'slow'))
    return result


def batch(stage, eps, worker, workers, host_limit, run_dir, status_callback):
    if not eps:
        return {}
    if workers < 1 or host_limit < 1:
        raise ValueError('Worker and per-host connection counts must be positive')
    groups = collections.defaultdict(list)
    for ep in eps:
        groups[ep['host']].append(ep)
    for items in groups.values():
        items.sort(key=lambda ep: bool(ep.get('published_priority')))
    # Only submit a host when it has a free connection slot. Waiting for a busy
    # host must not consume worker threads that can check other servers.
    hosts = list(groups)
    random.Random(42).shuffle(hosts)
    hosts.sort(key=lambda host: not any(ep.get('published_priority') for ep in groups[host]))
    ready = collections.deque(hosts)
    queued = set(hosts)
    active = collections.Counter()

    records, completed, passed = {}, 0, 0
    last_log = time.monotonic()
    status_callback(stage, 0, len(eps), 0)
    LOG.info('%s started: %d addresses, concurrency=%d, per_host=%d', stage, len(eps), workers, host_limit)
    with (run_dir / (stage + '.jsonl')).open('a', encoding='utf-8') as log_file:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {}
            while ready or pending:
                while ready and len(pending) < workers:
                    host = ready.popleft()
                    queued.remove(host)
                    ep = groups[host].pop()
                    pending[pool.submit(worker, ep)] = host
                    active[host] += 1
                    if groups[host] and active[host] < host_limit:
                        ready.append(host)
                        queued.add(host)
                done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    host = pending.pop(future)
                    active[host] -= 1
                    if groups[host] and active[host] < host_limit and host not in queued:
                        ready.append(host)
                        queued.add(host)
                    record = future.result()
                    record['checked_at'] = utcnow()
                    records[record['id']] = record
                    log_file.write(json.dumps(record, ensure_ascii=False) + '\n')
                    log_file.flush()
                    completed += 1
                    passed += bool(record['ok'])
                    if completed % 50 == 0 or time.monotonic() - last_log > 25 or completed == len(eps):
                        LOG.info('%s %d/%d passed=%d', stage, completed, len(eps), passed)
                        status_callback(stage, completed, len(eps), passed)
                        last_log = time.monotonic()
    return records


class History:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.execute('CREATE TABLE IF NOT EXISTS checks '
                        '(id TEXT PRIMARY KEY, total INTEGER, successes INTEGER, reliability REAL, last_seen TEXT)')
        self.db.commit()

    def record(self, outcomes):
        for identity, ok in outcomes.items():
            prior = self.db.execute('SELECT total,successes,reliability FROM checks WHERE id=?', (identity,)).fetchone()
            if prior:
                total, successes, reliability = prior
                reliability = reliability * 0.75 + float(ok) * 0.25
            else:
                total, successes, reliability = 0, 0, float(ok)
            self.db.execute('INSERT OR REPLACE INTO checks VALUES (?,?,?,?,?)',
                            (identity, total + 1, successes + int(ok), reliability, utcnow()))
        self.db.commit()
        return {row[0]: row[1] for row in self.db.execute('SELECT id,reliability FROM checks')}

    def close(self):
        self.db.close()

    def snapshot(self):
        return {row[0]: row[1] for row in self.db.execute('SELECT id,reliability FROM checks')}


def choose_lines(entries, probes, decodes, reliability, limit):
    eligible = [entry for entry in entries if decodes.get(entry['endpoint']['id'], {}).get('ok')]

    def rank(entry):
        identity = entry['endpoint']['id']
        dec, pr = decodes[identity], probes[identity]
        # Check duration is measured end-to-end, not mislabelled as startup latency.
        startup = dec.get('first_frame_seconds')
        # Slow starts must not win simply because a source advertises 1080p.
        return (dec.get('startup_class') == 'slow',
                bool(dec.get('retried') or pr.get('retried')),
                -reliability.get(identity, 0),
                round(startup / 2) if startup is not None else 999,
                -min(pr.get('height', 0), 1080),
                pr['seconds'] + dec['seconds'], identity)

    eligible.sort(key=rank)
    unique, requests = [], set()
    for entry in eligible:
        ep = entry['endpoint']
        signature = (ep['url'], ep['headers'].get('user_agent', probes[ep['id']].get('ua', '')),
                     ep['headers'].get('referer', ''))
        if signature not in requests:
            unique.append(entry)
            requests.add(signature)
    eligible = unique
    selected, hosts = [], set()
    for entry in eligible:
        host = entry['endpoint']['host']
        if host not in hosts and len(selected) < limit:
            selected.append(entry)
            hosts.add(host)
    for entry in eligible:
        if len(selected) == limit:
            break
        if entry not in selected:
            selected.append(entry)
    return selected


def validate_candidates(channels, endpoints, probes, decodes, reliability, ua, cfg, run_dir, status):
    """Deep-test candidates, then require a separate successful connection before publication."""
    checked, first_checks = set(), {}
    limit = cfg.get('deep_candidates_per_channel', 10)
    round_number = 0
    while True:
        pending = {}
        for channel in channels.values():
            entries = list(channel['entries'].values())
            already_good = sum(first_checks.get(e['endpoint']['id'], {}).get('ok', False) for e in entries)
            if already_good >= 5:
                continue
            candidates = [e for e in entries if e['endpoint']['id'] not in checked]
            for entry in choose_lines(candidates, probes, decodes, reliability, limit):
                ep = entry['endpoint']
                pending[ep['id']] = endpoints[ep['id']]
        if not pending:
            break
        round_number += 1
        records = batch('deep{}'.format(round_number), list(pending.values()),
                        lambda ep: check_video(ep, probes[ep['id']], probes[ep['id']].get('ua', ua), cfg, deep=True),
                        cfg.get('deep_workers', 6), cfg.get('deep_per_host', 1), run_dir, status)
        for identity, result in records.items():
            result['retried'] = bool(decodes[identity].get('retried') or probes[identity].get('retried'))
            decodes[identity] = result
        checked.update(records)
        first_checks.update(records)
    survivors = {i: r for i, r in first_checks.items() if r['ok']}
    second_checks = {}
    if survivors:
        # Live caches can make a single connection look healthy. Reopen later.
        last_first_check = max(datetime.datetime.strptime(r['checked_at'], '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=datetime.timezone.utc).timestamp() for r in survivors.values())
        due = last_first_check + cfg.get('recheck_interval_seconds', 1800)
        total = max(0, int(due - time.time()))
        while time.time() < due:
            remaining = max(0, int(due - time.time()))
            status('recheck_wait', total - remaining, total, len(survivors))
            time.sleep(min(30, max(.1, due - time.time())))
        round_number = 0
        while True:
            pending = {}
            for channel in channels.values():
                entries = list(channel['entries'].values())
                already_good = sum(second_checks.get(e['endpoint']['id'], {}).get('ok', False) for e in entries)
                if already_good >= 5:
                    continue
                remaining = [e for e in entries if e['endpoint']['id'] in survivors and e['endpoint']['id'] not in second_checks]
                for entry in choose_lines(remaining, probes, decodes, reliability, 5 - already_good):
                    pending[entry['endpoint']['id']] = endpoints[entry['endpoint']['id']]
            if not pending:
                break
            round_number += 1
            records = batch('recheck{}'.format(round_number), list(pending.values()),
                            lambda ep: check_video(ep, probes[ep['id']], probes[ep['id']].get('ua', ua), cfg, deep=True),
                            cfg.get('deep_workers', 6), cfg.get('deep_per_host', 1), run_dir, status)
            for identity, result in records.items():
                first = survivors[identity]
                result['first_check'] = {k: first.get(k) for k in ('checked_at', 'first_frame_seconds', 'max_frame_gap', 'observed_seconds')}
                result['first_frame_seconds'] = max(first.get('first_frame_seconds') or 0, result.get('first_frame_seconds') or 0)
                result['startup_class'] = ('fast' if result['first_frame_seconds'] <= cfg.get('fast_startup_seconds', 5) else 'slow')
                result['retried'] = first.get('retried', False)
                result['verified_twice'] = bool(result['ok'])
                decodes[identity] = result
            second_checks.update(records)
    # Untested and one-pass-only candidates can never leak into published Top5.
    for identity, result in decodes.items():
        if result['ok'] and not second_checks.get(identity, {}).get('ok'):
            result.update(ok=False, error='not_deep_verified')
    errors = collections.Counter(r['error'] for r in list(first_checks.values()) + list(second_checks.values()) if not r['ok'])
    return {'enabled': True, 'deep_tested': len(first_checks), 'deep_passed': len(survivors),
            'recheck_tested': len(second_checks), 'verified_twice': sum(r['ok'] for r in second_checks.values()),
            'deep_seconds': cfg.get('deep_seconds', 30), 'recheck_interval_seconds': cfg.get('recheck_interval_seconds', 1800),
            'max_startup_seconds': cfg.get('max_startup_seconds', 12), 'errors': dict(errors)}


def render_playlists(channels, probes, decodes, reliability, epg_url=''):
    header = '#EXTM3U'
    if epg_url:
        header += ' x-tvg-url="{}"'.format(epg_url.replace('"', ''))
    outputs = {3: [header], 5: [header]}
    stats = {'channels_total': len(channels), 'channels_available': 0,
             'top3_entries': 0, 'top5_entries': 0, 'unavailable_channels': []}
    for channel in channels.values():
        best = choose_lines(list(channel['entries'].values()), probes, decodes, reliability, 5)
        if best:
            stats['channels_available'] += 1
        else:
            stats['unavailable_channels'].append(channel['name'])
        for count in (3, 5):
            for entry in best[:count]:
                outputs[count].append(channel['info'])
                extra = list(entry['extra'])
                tested_ua = probes[entry['endpoint']['id']].get('ua')
                if tested_ua and not entry['endpoint']['headers'].get('user_agent'):
                    extra.append('#EXTVLCOPT:http-user-agent=' + tested_ua)
                entry['published_endpoint_id'] = endpoint(entry['output_url'], extra)['id']
                outputs[count].extend(extra)
                outputs[count].append(entry['output_url'])
                stats['top{}_entries'.format(count)] += 1
    return {count: '\n'.join(lines) + '\n' for count, lines in outputs.items()}, stats


def split_ranked_playlist(text):
    """Split ranked Top5 into three equal channel sets without probing again."""
    header = text.splitlines()[0] if text.splitlines() else '#EXTM3U'
    channels = collections.OrderedDict()
    if '#EXTINF:' in text:
        for entry in parse_playlist(text):
            entries = channels.setdefault(entry['key'], [])
            if all(e['endpoint']['id'] != entry['endpoint']['id'] for e in entries):
                entries.append(entry)
    sets = [[header] for _ in range(3)]
    distribution = collections.Counter()
    for entries in channels.values():
        choices = entries[:3]
        distribution[len(choices)] += 1
        for index in range(3):
            chosen = choices[min(index, len(choices) - 1)]
            sets[index].append(entries[0]['info'])
            sets[index].extend(chosen['extra'])
            sets[index].append(chosen['output_url'])
    outputs = {'iptv_set{}.m3u'.format(i + 1): '\n'.join(lines) + '\n' for i, lines in enumerate(sets)}
    stats = {'channels_per_set': len(channels),
             'line_distribution': {str(i): distribution[i] for i in (1, 2, 3)},
             'channels_with_backup': distribution[2] + distribution[3],
             'channels_with_three_lines': distribution[3], 'generated_at': utcnow()}
    return outputs, stats


def source_comparison(channels, endpoints, decodes, outputs):
    sources = sorted({s for ep in endpoints.values() for s in ep.get('sources', [])})
    selected = {n: parse_playlist(text) if '#EXTINF:' in text else [] for n, text in outputs.items()}
    # Exported requests include the UA actually used in validation. Its identity
    # may therefore differ from the raw input, while provenance stays the same.
    exported = {}
    for key, channel in channels.items():
        exported[key] = {}
        for identity, entry in channel['entries'].items():
            output_id = entry.get('published_endpoint_id', identity)
            prior = exported[key].get(output_id, {})
            exported[key][output_id] = dict(entry, sources=list(dict.fromkeys(
                prior.get('sources', []) + entry.get('sources', []))))
    rows = []
    for source in sources:
        ids = {i for i, ep in endpoints.items() if source in ep.get('sources', [])}
        available = {key for key, c in channels.items() if any(source in e.get('sources', []) and decodes.get(i, {}).get('ok') for i, e in c['entries'].items())}
        row = {'name': source, 'candidate_addresses': len(ids),
               'passed_addresses': sum(bool(decodes.get(i, {}).get('ok')) for i in ids),
               'available_channels': len(available)}
        for limit in (3, 5):
            row['top{}_entries'.format(limit)] = sum(source in exported[e['key']][e['endpoint']['id']].get('sources', []) for e in selected[limit])
        rows.append(row)
    new_keys, old_keys = set(), set()
    for key, c in channels.items():
        if any(any(s.startswith('tvlist') for s in e.get('sources', [])) for e in c['entries'].values()):
            new_keys.add(key)
        if any(any(not s.startswith('tvlist') for s in e.get('sources', [])) for e in c['entries'].values()):
            old_keys.add(key)
    available_keys = {e['key'] for e in selected[5]}
    best = {}
    for e in selected[5]:
        best.setdefault(e['key'], exported[e['key']][e['endpoint']['id']])
    improved = 0
    restored = 0
    for key in new_keys & old_keys:
        entry = best.get(key)
        if not entry or not any(s.startswith('tvlist') for s in entry.get('sources', [])):
            continue
        old_passes = any(decodes.get(i, {}).get('ok') and any(not s.startswith('tvlist') for s in e.get('sources', [])) for i, e in channels[key]['entries'].items())
        if not old_passes:
            restored += 1
        elif all(s.startswith('tvlist') for s in entry.get('sources', [])):
            improved += 1
    return {'sources': rows, 'overlap_channels': len(new_keys & old_keys),
            'new_candidate_channels': len(new_keys - old_keys),
            'new_playable_channels': len((new_keys - old_keys) & available_keys),
            'repository_best_channels': improved, 'restored_channels': restored}


def publish_guard(stats, prior, source_stats, minimum_ratio):
    if stats['channels_available'] == 0:
        return 'No playable channels; previous publication retained'
    if prior:
        old_count = prior.get('channels_available', 0)
        if old_count and stats['channels_available'] < old_count * minimum_ratio:
            return 'Playable channel count fell below publication safety threshold'
        previous_sources = {s['name']: s['entries'] for s in prior.get('sources', [])}
        for source in source_stats:
            old_size = previous_sources.get(source['name'], 0)
            if old_size and source['entries'] < old_size * minimum_ratio:
                return 'Source {} shrank below publication safety threshold'.format(source['name'])
    return ''


def video_publication_baseline(data_dir, prior):
    """On the first video-only release, do not count known radio channels in
    the old TV baseline. Preserve all existing guards if evidence is missing.
    """
    if not prior or prior.get('quality', {}).get('enabled'):
        return prior
    current = data_dir / 'published' / 'current'
    if not current.exists():
        return prior
    try:
        run = current.resolve().parent
        probes = {}
        for stage in ('probe1', 'probe2', 'probe3'):
            path = run / (stage + '.jsonl')
            if path.exists():
                with path.open(encoding='utf-8') as handle:
                    for line in handle:
                        row = json.loads(line)
                        probes[row['id']] = row
        entries = parse_playlist((current / 'iptv_top5.m3u').read_text(encoding='utf-8'))
        if not all(e['endpoint']['id'] in probes for e in entries):
            return prior
        keys = {e['key'] for e in entries if probes[e['endpoint']['id']].get('height')
                and not probes[e['endpoint']['id']].get('audio_only')}
        if not keys:
            return prior
        return dict(prior, channels_available=len(keys))
    except (OSError, ValueError, KeyError):
        return prior


def publish_local(data_dir, export_dir):
    published = data_dir / 'published'
    published.mkdir(exist_ok=True)
    # One symlink switch changes the complete pair and its report as a unit.
    tmp = published / ('.current-' + uuid.uuid4().hex[:8])
    os.symlink(str(export_dir.resolve()), str(tmp))
    os.replace(str(tmp), str(published / 'current'))
    for filename, target in (('iptv_top3.m3u', 'iptv_top3.m3u'),
                             ('iptv_top5.m3u', 'iptv_top5.m3u'),
                             ('iptv_ok.m3u', 'iptv_top5.m3u'), ('report.json', 'report.json'),
                             ('iptv_set1.m3u', 'iptv_set1.m3u'), ('iptv_set2.m3u', 'iptv_set2.m3u'),
                             ('iptv_set3.m3u', 'iptv_set3.m3u')):
        if not (export_dir / target).is_file():
            continue
        link = published / filename
        if not link.is_symlink():
            if link.exists():
                raise RuntimeError('Refusing to overwrite unmanaged publication file')
            os.symlink('current/' + target, str(link))


def update_run_summaries(cfg):
    if cfg.get('summaries', {}).get('enabled') is False:
        return True
    target = Path(cfg['data_dir']) / 'summaries'
    try:
        from iptv_qa.reporting.history import refresh, FILES
        obs = obs_settings(cfg.get('obs', {}))
        public_base = obs.get('public_base_url', '')
        if not public_base and obs.get('enabled') and obs.get('bucket') and urlsplit(obs.get('endpoint', '')).hostname:
            public_base = 'https://{}.{}/{}'.format(obs['bucket'], urlsplit(obs['endpoint']).hostname,
                                                   obs.get('prefix', '').strip('/')).rstrip('/')
        target, count = refresh(cfg['data_dir'], public_base=public_base)
        result = publish_obs(target, cfg.get('obs', {}), filenames=FILES)
        write_json(target / 'status.json', dict(result, updated_at=utcnow(), run_count=count))
        LOG.info('Run summaries refreshed: %d records, OBS=%s', count, result['state'])
        return True
    except Exception as exc:
        LOG.error('Run summary refresh failed (%s)', type(exc).__name__)
        try:
            write_json(target / 'status.json', {'state': 'failed', 'error_type': type(exc).__name__, 'updated_at': utcnow()})
        except OSError:
            pass
        return False


def generate_current_sets(cfg):
    data_dir = Path(cfg['data_dir'])
    export = (data_dir / 'published/current').resolve()
    report = json.loads((export / 'report.json').read_text(encoding='utf-8'))
    if not report.get('published') or report.get('diagnostic_sample'):
        raise RuntimeError('No complete validated publication is available')
    files, stats = split_ranked_playlist((export / 'iptv_top5.m3u').read_text(encoding='utf-8'))
    if stats['channels_per_set'] != report['channels_available']:
        raise RuntimeError('Set channel count differs from validated publication')
    for filename, text in files.items():
        atomic_text(export / filename, text)
    report['sets'] = stats
    publish_local(data_dir, export)
    try:
        result = publish_obs(export, cfg.get('obs', {}), filenames=tuple(files))
        report['sets_upload'] = result
        if result['state'] == 'uploaded':
            old = report.get('obs', {}).get('objects', [])
            report['obs'] = dict(result, objects=list(dict.fromkeys(old + result['objects'])))
    except Exception as exc:
        report['sets_upload'] = {'state': 'failed', 'error_type': type(exc).__name__}
        write_json(export / 'report.json', report)
        raise
    write_json(export / 'report.json', report)
    LOG.info('Three sets generated from existing checks: %d channels per set, backup coverage=%d',
             stats['channels_per_set'], stats['channels_with_backup'])


def prune_runs(data_dir, days):
    current = (data_dir / 'published' / 'current').resolve()
    cutoff = time.time() - days * 86400
    runs = data_dir / 'runs'
    for path in runs.iterdir():
        if (path.is_dir() and not path.is_symlink() and re.match(r'^\d{8}T\d{6}Z-[a-f0-9]{8}$', path.name)
                and path.stat().st_mtime < cutoff and current.parent != path.resolve()):
            shutil.rmtree(str(path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/iptv-qa/config.json')
    parser.add_argument('--limit', type=int, default=0, help='Diagnostic sample only; NEVER publishes')
    parser.add_argument('--check-config', action='store_true')
    parser.add_argument('--upload-only', action='store_true', help='Upload the current publication without rerunning stream checks')
    parser.add_argument('--refresh-reports', action='store_true', help='Backfill and upload run summaries without checking streams')
    parser.add_argument('--generate-sets', action='store_true', help='Generate and upload three sets from existing validated Top5')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if not cfg.get('sources') or any(not re.match(r'^[A-Za-z0-9_-]+$', s['name']) for s in cfg['sources']):
        raise ValueError('Configure named playlist sources')
    for tool in ('ffmpeg', 'ffprobe'):
        if not Path(cfg[tool]).is_file():
            raise ValueError('Missing executable: ' + cfg[tool])
        result = run_tool([cfg[tool], '-version'], 10)
        if result['code'] != 0:
            raise ValueError('Media executable failed self-check: ' + cfg[tool])
    if args.check_config:
        LOG.info('Configuration valid; %d sources; OBS enabled=%s', len(cfg['sources']), cfg.get('obs', {}).get('enabled', False))
        return 0
    data_dir = Path(cfg['data_dir'])
    data_dir.mkdir(parents=True, exist_ok=True)
    import fcntl
    lock = (data_dir / 'run.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        LOG.warning('Another IPTV run is already active; skipped')
        return 0
    if args.refresh_reports:
        try:
            return 0 if update_run_summaries(cfg) else 2
        finally:
            lock.close()
    if args.generate_sets:
        try:
            generate_current_sets(cfg)
            return 0
        except Exception as exc:
            LOG.error('Three-set generation failed (%s)', type(exc).__name__)
            return 2
        finally:
            update_run_summaries(cfg)
            lock.close()
    if args.upload_only:
        report, report_path = None, None
        try:
            if not cfg.get('obs', {}).get('enabled'):
                raise RuntimeError('OBS is disabled')
            export_dir = (data_dir / 'published' / 'current').resolve()
            report_path = export_dir / 'report.json'
            report = json.loads(report_path.read_text(encoding='utf-8'))
            if not report.get('published') or report.get('diagnostic_sample'):
                raise RuntimeError('No complete publication is available')
            report['obs'] = publish_obs(export_dir, cfg['obs'])
            write_json(report_path, report)
            LOG.info('OBS upload complete for current publication')
            return 0
        except Exception as exc:
            if report is not None:
                report['obs'] = {'enabled': True, 'state': 'failed', 'error': type(exc).__name__}
                write_json(report_path, report)
            LOG.error('OBS upload-only failed (%s)', type(exc).__name__)
            return 2
        finally:
            update_run_summaries(cfg)
            lock.close()
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
    run_dir = data_dir / 'runs' / run_id
    run_dir.mkdir(parents=True)
    state = {'run_id': run_id, 'started_at': utcnow(), 'state': 'running', 'diagnostic_sample': bool(args.limit)}

    def status(stage, completed=0, total=0, passed=0):
        state.update(stage=stage, completed=completed, total=total, passed=passed, updated_at=utcnow())
        write_json(data_dir / 'status.json', state)
        write_json(run_dir / 'status.json', state)

    try:
        status('download')
        playlists, source_stats = [], []
        language_db = {}
        if cfg.get('language_scope'):
            from iptv_qa.catalog.sources import classify
            metadata_path = Path(cfg.get('language_metadata_path', Path(cfg['data_dir']) / 'catalog-cache/language-metadata.json'))
            if metadata_path.exists():
                language_db = {c['id']: c for c in json.loads(metadata_path.read_text(encoding='utf-8')) if c.get('id')}
        for source in cfg['sources']:
            try:
                entries = download_source(source, run_dir / (source['name'] + '.m3u'), cfg)
            except RuntimeError:
                if source.get('optional'):
                    source_stats.append({'name': source['name'], 'entries': 0, 'download_failed': True})
                    continue
                raise
            raw_count = len(entries)
            radio_count = 0
            if cfg.get('exclude_radio', True):
                from iptv_qa.catalog.sources import is_radio
                radio_count = sum(is_radio(e) for e in entries)
                entries = [e for e in entries if not is_radio(e)]
            if cfg.get('language_scope'):
                entries = [e for e in entries if classify(e, language_db) == cfg['language_scope']]
            for entry in entries:
                entry['source'] = source['name']
            playlists.append(entries)
            source_stats.append({'name': source['name'], 'entries': len(entries), 'raw_entries': raw_count,
                                 'excluded_radio_entries': radio_count})
            LOG.info('Downloaded source %s: %d entries', source['name'], len(entries))
        channels, endpoints = merge_entries(playlists)
        eps = list(endpoints.values())
        previous_playlist = data_dir / 'published' / 'iptv_top5.m3u'
        if previous_playlist.exists():
            try:
                previous_ids = {e['endpoint']['url'] for e in parse_playlist(previous_playlist.read_text(encoding='utf-8'))}
                for ep in eps:
                    ep['published_priority'] = ep['url'] in previous_ids
            except ValueError:
                pass
        if args.limit:
            eps = random.Random(7).sample(eps, min(args.limit, len(eps)))
        LOG.info('Merged: %d channels, %d unique endpoints, testing=%d', len(channels), len(endpoints), len(eps))
        ua, retry_ua = cfg.get('user_agent', UA), cfg.get('retry_user_agent', 'Mozilla/5.0')
        workers = cfg.get('probe_workers', 24)
        probes = batch('probe1', eps, lambda ep: probe(ep, ua, cfg), workers,
                       cfg.get('probe_per_host', 3), run_dir, status)
        live_hosts = {endpoints[i]['host'] for i, r in probes.items() if r['ok']}
        failed_hosts = collections.defaultdict(list)
        for ep in eps:
            if not probes[ep['id']]['ok'] and probes[ep['id']]['error'] not in ('placeholder_short_file', 'audio_only_rejected', 'local_network_unreachable', 'multicast_unreachable', 'ipv6_unavailable'):
                failed_hosts[ep['host']].append(ep)
        retry_eps, deferred = [], collections.defaultdict(list)
        for host, failures in failed_hosts.items():
            if host in live_hosts:
                retry_eps.extend(failures)
            else:
                retry_eps.extend(failures[:cfg.get('dead_host_retry_sample', 2)])
                deferred[host].extend(failures[cfg.get('dead_host_retry_sample', 2):])
        retry_records = batch('probe2', retry_eps, lambda ep: probe(ep, retry_ua, cfg), cfg.get('probe_retry_workers', workers),
                              cfg.get('probe_retry_per_host', 1), run_dir, status)
        revived = {endpoints[i]['host'] for i, r in retry_records.items() if r['ok']}
        rest = [ep for host, items in deferred.items() if host in revived for ep in items]
        retry_records.update(batch('probe3', rest, lambda ep: probe(ep, retry_ua, cfg), cfg.get('probe_retry_workers', workers),
                                   cfg.get('probe_retry_per_host', 1), run_dir, status))
        for identity, record in retry_records.items():
            record['retried'] = True
            record['ua'] = retry_ua
            probes[identity] = record
        good = [ep for ep in eps if probes[ep['id']]['ok']]
        decodes = batch('decode1', good,
                        lambda ep: decode(ep, probes[ep['id']], probes[ep['id']].get('ua', ua), cfg),
                        cfg.get('decode_workers', 6), cfg.get('decode_per_host', 2), run_dir, status)
        retry_decode = [ep for ep in good if not decodes[ep['id']]['ok']]
        for identity, record in batch('decode2', retry_decode,
                                      lambda ep: decode(ep, probes[ep['id']], probes[ep['id']].get('ua', ua), cfg),
                                      cfg.get('decode_retry_workers', cfg.get('decode_workers', 6)),
                                      cfg.get('decode_retry_per_host', 1), run_dir, status).items():
            record['retried'] = True
            decodes[identity] = record
        short_decode_passed = sum(bool(r['ok']) for r in decodes.values())
        quality_report = {'enabled': False}
        if cfg.get('deep_validation_enabled', False):
            history = History(data_dir / 'history.sqlite3')
            try:
                prior_reliability = history.snapshot()
            finally:
                history.close()
            quality_report = validate_candidates(channels, endpoints, probes, decodes, prior_reliability,
                                                 ua, cfg, run_dir, status)
            atomic_text(run_dir / 'final_decodes.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in decodes.values()))
        status('render')
        if args.limit:
            reliability = {}
        else:
            history = History(data_dir / 'history.sqlite3')
            try:
                reliability = history.record({ep['id']: bool(decodes.get(ep['id'], {}).get('ok')) for ep in eps
                                              if decodes.get(ep['id'], {}).get('error') != 'not_deep_verified'})
            finally:
                history.close()
        outputs, stats = render_playlists(channels, probes, decodes, reliability, cfg.get('epg_url', ''))
        set_files, set_stats = split_ranked_playlist(outputs[5])
        report = dict(stats, run_id=run_id, started_at=state['started_at'], finished_at=utcnow(),
                      sources=source_stats, unique_endpoints=len(endpoints), tested_endpoints=len(eps),
                      probe_passed=len(good), decode_passed=sum(bool(r['ok']) for r in decodes.values()),
                      short_decode_passed=short_decode_passed,
                      unverified_endpoints=sum(r.get('error') == 'not_deep_verified' for r in decodes.values()),
                      errors=dict(collections.Counter(r['error'] for r in list(probes.values()) + list(decodes.values())
                                  if not r['ok'] and r['error'] != 'not_deep_verified')),
                      diagnostic_sample=bool(args.limit), published=False, sets=set_stats, quality=quality_report,
                      concurrency={key: cfg.get(key) for key in ('probe_workers', 'probe_per_host', 'probe_retry_workers',
                                   'probe_retry_per_host', 'decode_workers', 'decode_per_host', 'decode_retry_workers', 'decode_retry_per_host')})
        report['source_comparison'] = source_comparison(channels, endpoints, decodes, outputs)
        export_dir = run_dir / 'export'
        export_dir.mkdir()
        for count, text in outputs.items():
            atomic_text(export_dir / 'iptv_top{}.m3u'.format(count), text)
        for filename, text in set_files.items():
            atomic_text(export_dir / filename, text)
        prior = {}
        prior_report = data_dir / 'published' / 'report.json'
        if prior_report.exists():
            prior = json.loads(prior_report.read_text(encoding='utf-8'))
        baseline = video_publication_baseline(data_dir, prior) if cfg.get('deep_validation_enabled') else prior
        report['publication_baseline_channels'] = baseline.get('channels_available')
        reason = 'Diagnostic samples do not publish' if args.limit else publish_guard(stats, baseline, source_stats, cfg.get('minimum_publish_ratio', 0.5))
        report['publication_blocked_reason'] = reason
        if reason:
            report['obs'] = {'enabled': cfg.get('obs', {}).get('enabled', False), 'state': 'skipped'}
            write_json(export_dir / 'report.json', report)
            state['state'] = 'sample_complete' if args.limit else 'publication_blocked'
            LOG.warning('%s', reason)
        else:
            report['published'] = True
            report['obs'] = {'enabled': cfg.get('obs', {}).get('enabled', False), 'state': 'pending' if cfg.get('obs', {}).get('enabled') else 'not_configured'}
            write_json(export_dir / 'report.json', report)
            publish_local(data_dir, export_dir)
            try:
                report['obs'] = publish_obs(export_dir, cfg.get('obs', {}))
            except Exception as exc:
                report['obs'] = {'enabled': True, 'state': 'failed', 'error': type(exc).__name__}
                state['state'] = 'upload_failed'
                LOG.error('OBS upload failed (%s); local files retained for retry', type(exc).__name__)
            write_json(export_dir / 'report.json', report)
            state.setdefault('state', 'complete')
            if state['state'] == 'running':
                state['state'] = 'complete'
            prune_runs(data_dir, cfg.get('retention_days', 14))
        state.update(channels_available=stats['channels_available'], top3_entries=stats['top3_entries'],
                     top5_entries=stats['top5_entries'], finished_at=utcnow())
        status('finished')
        LOG.info('Finished state=%s channels=%d top3=%d top5=%d', state['state'],
                 stats['channels_available'], stats['top3_entries'], stats['top5_entries'])
        return 0 if state['state'] in ('complete', 'sample_complete') else 2
    except Exception as exc:
        state.update(state='failed', error_type=type(exc).__name__, finished_at=utcnow())
        status('failed')
        LOG.error('Run failed (%s); previous publication retained', type(exc).__name__)
        return 1
    finally:
        if not args.limit:
            update_run_summaries(cfg)
        lock.close()


if __name__ == '__main__':
    sys.exit(main())
