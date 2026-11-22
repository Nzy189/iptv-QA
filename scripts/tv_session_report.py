#!/usr/bin/env python3
"""Summarise real TV playback from the player's structured request log.

Reads ``TVREQ`` / ``TVWARM`` lines written by the playback service (see
``iptv_qa/playback/reqlog.py``), either from a file/stdin or straight from the
systemd journal, and reports per channel switch: how the line was chosen,
how long the playlist took, when the first segment was delivered, and which
segments were slow, failed or cancelled. A segment counts as slow when
serving it took more than 80% of its own play time; at the live edge that is
what a viewer sees as a freeze.

Examples:
  python3 scripts/tv_session_report.py --since "2026-11-22 13:00" [--unit iptv-web]
  journalctl -u iptv-web -o cat | python3 scripts/tv_session_report.py -
"""
import argparse
import json
import statistics
import subprocess
import sys
import time

REOPEN_GAP = 30.0   # a race request for the same channel within this gap is a playlist reload


def read_lines(args):
    if args.source == '-':
        return sys.stdin.read().splitlines()
    if args.source:
        with open(args.source, encoding='utf-8', errors='replace') as handle:
            return handle.read().splitlines()
    cmd = ['journalctl', '-u', args.unit, '--no-pager', '-o', 'cat']
    if args.since:
        cmd += ['--since', args.since]
    if args.until:
        cmd += ['--until', args.until]
    out = subprocess.run(cmd, stdout=subprocess.PIPE).stdout
    return out.decode('utf-8', 'replace').splitlines()


def parse(lines):
    reqs, warms = [], []
    for line in lines:
        for tag, bucket in (('TVREQ ', reqs), ('TVWARM ', warms)):
            pos = line.find(tag)
            if pos >= 0:
                try:
                    bucket.append(json.loads(line[pos + len(tag):]))
                except ValueError:
                    pass
    return reqs, warms


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else 0


def describe(values, unit='ms'):
    if not values:
        return 'n=0'
    return 'n=%d median=%d p90=%d max=%d %s' % (len(values), statistics.median(values),
                                               pct(values, .9), max(values), unit)


def build_opens(reqs):
    """Group requests into channel opens per client."""
    opens, last_race = [], {}
    current = {}
    for rec in sorted(reqs, key=lambda r: r.get('ts', 0)):
        ip = rec.get('ip')
        if rec.get('kind') == 'race' and rec.get('m') == 'GET':
            key = (ip, rec.get('ch'))
            prev = last_race.get(key)
            last_race[key] = rec['ts']
            if prev is not None and rec['ts'] - prev < REOPEN_GAP and current.get(ip) \
                    and current[ip]['ch'] == rec.get('ch'):
                current[ip]['reloads'].append(rec)
                continue
            entry = {'open': rec, 'ch': rec.get('ch'), 'reloads': [], 'segments': []}
            opens.append(entry)
            current[ip] = entry
        elif rec.get('kind') == 'rstream' and rec.get('dur') is not None:
            entry = current.get(ip)
            if entry is not None and rec.get('ch') == entry['ch']:
                entry['segments'].append(rec)
    return opens


def fmt_time(ts):
    return time.strftime('%H:%M:%S', time.localtime(ts))


def report(reqs, warms, out=sys.stdout, limit=200):
    tv = [r for r in reqs if r.get('kind') in ('race', 'rstream', 'playlist')]
    heads = sum(1 for r in tv if r.get('m') == 'HEAD')
    aborts = sum(1 for r in tv if r.get('abort'))
    errors = [r for r in tv if r.get('st', 200) >= 400 or r.get('err')]
    print('TV requests %d (HEAD %d, cancelled by player %d, errors %d)'
          % (len(tv), heads, aborts, len(errors)), file=out)
    opens = build_opens(tv)
    segs = [r for r in tv if r.get('kind') == 'rstream' and r.get('dur') is not None
            and not r.get('abort')]
    slow = [r for r in segs if r.get('slow')]
    print('channel opens %d, segments served %d, slow segments %d (%.0f%%)'
          % (len(opens), len(segs), len(slow), 100.0 * len(slow) / max(1, len(segs))), file=out)
    print('open -> playlist   ' + describe([o['open']['ms'] for o in opens if 'ms' in o['open']]), file=out)
    firsts = []
    for o in opens:
        if o['segments']:
            s = o['segments'][0]
            firsts.append(int((s['ts'] - o['open']['ts']) * 1000 + s.get('ms', 0)))
    print('open -> 1st segment delivered ' + describe(firsts), file=out)
    print('segment serve time ' + describe([r['ms'] for r in segs]), file=out)
    print('segment upstream   ' + describe([r['up_ms'] for r in segs if 'up_ms' in r]), file=out)
    hits = sum(1 for r in segs if r.get('cache') == 'hit')
    waits = [r for r in segs if r.get('wait_ms')]
    print('segment cache hits %d, waited on background fetch %d (timed out %d)'
          % (hits, len(waits), sum(1 for r in waits if r.get('wait_timeout'))), file=out)
    picks = {}
    for o in opens:
        picks[o['open'].get('pick', '?')] = picks.get(o['open'].get('pick', '?'), 0) + 1
    print('line choice: ' + ', '.join('%s %d' % kv for kv in sorted(picks.items())), file=out)
    if warms:
        print('background warms ' + describe([w['ms'] for w in warms if 'ms' in w])
              + ', failed %d' % sum(1 for w in warms if w.get('err')), file=out)
    print('', file=out)
    print('%-8s %-16s %-7s %6s %6s %5s %4s %5s %7s  %s' % (
        'time', 'channel', 'pick', 'list', '1st', 'segs', 'slow', 'err', 'maxseg', 'notes'), file=out)
    for o in opens[-limit:]:
        rec = o['open']
        seg_ms = [s['ms'] for s in o['segments'] if 'ms' in s]
        first = ''
        if o['segments']:
            s = o['segments'][0]
            first = '%.1f' % ((s['ts'] - rec['ts']) + s.get('ms', 0) / 1000.0)
        bad = [s for s in o['segments'] if s.get('st', 200) >= 400 or s.get('err')]
        notes = []
        if rec.get('err') or rec.get('st', 200) >= 400:
            notes.append('playlist %s %s' % (rec.get('st'), rec.get('err', '')))
        if rec.get('abort'):
            notes.append('playlist cancelled')
        if rec.get('fallback'):
            notes.append('all lines failed race')
        if not o['segments'] and not rec.get('abort'):
            notes.append('no segment requested')
        if o['reloads']:
            notes.append('%d reloads' % len(o['reloads']))
        print('%-8s %-16s %-7s %6.1f %6s %5d %4d %5d %7s  %s' % (
            fmt_time(rec['ts']), str(rec.get('name') or rec.get('ch'))[:16], rec.get('pick', '?'),
            rec.get('ms', 0) / 1000.0, first, len(o['segments']),
            sum(1 for s in o['segments'] if s.get('slow')), len(bad),
            '%.1f' % (max(seg_ms) / 1000.0) if seg_ms else '', '; '.join(notes)), file=out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('source', nargs='?', help="log file, or '-' for stdin; default reads the journal")
    parser.add_argument('--unit', default='iptv-web')
    parser.add_argument('--since')
    parser.add_argument('--until')
    parser.add_argument('--limit', type=int, default=200, help='rows in the per-open table')
    args = parser.parse_args(argv)
    reqs, warms = parse(read_lines(args))
    report(reqs, warms, limit=args.limit)


if __name__ == '__main__':
    main()
