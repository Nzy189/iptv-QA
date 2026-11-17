"""Basic playback evidence and optional stability classification; Python 3.6+."""
import collections
import re

POLICY = 'tiered-v1'
HARD_FAILURES = {'audio_only_rejected', 'no_media_stream', 'placeholder_short_file',
                 'hls_not_live'}


def enabled(cfg):
    return cfg.get('validation_policy', 'strict-v1') == POLICY


def check_audio(ep, ua, cfg, run_tool, input_args):
    target = cfg.get('decode_seconds', 4)
    args = [cfg['ffmpeg'], '-hide_banner', '-nostdin', '-v', 'error', '-threads', '1']
    args += input_args(ep, ua, cfg)
    args += ['-i', ep['url'], '-t', str(target), '-map', '0:a:0', '-vn', '-sn', '-dn',
             '-progress', 'pipe:1', '-nostats', '-f', 'null', '-']
    result = run_tool(args, cfg.get('decode_timeout', 40))
    seconds = 0.0
    for line in result['stdout'].splitlines():
        if line.startswith('out_time_us='):
            try:
                seconds = max(seconds, float(line.split('=', 1)[1]) / 1000000)
            except ValueError:
                pass
    return {'ok': result['code'] == 0 and seconds >= target - .2,
            'decoded_seconds': round(seconds, 3),
            'error': result['error'] or ('insufficient_audio' if seconds < target - .2 else '')}


def check_proxy(ep, ua):
    # Exercise the same retrieval/rewriting helpers as the browser proxy. This is
    # delivery evidence, not a claim of codec support on every receiver.
    from iptv_qa.playback.proxy import fetch_upstream, rewrite_playlist
    from iptv_qa.playback.state import Registry
    headers = dict(ep['headers'])
    headers.setdefault('user_agent', ua)
    registry = Registry()
    url = ep['url']
    for _ in range(5):
        _, _, data, final = fetch_upstream(url, headers, 32 * 1024 * 1024)
        text = data.decode('utf-8-sig', 'replace')
        if not text.lstrip().startswith('#EXTM3U'):
            return {'ok': False, 'error': 'proxy_invalid_playlist'}
        if '#EXT-X-ENDLIST' in text:
            return {'ok': False, 'error': 'hls_not_live'}
        rewritten = rewrite_playlist(text, final, headers, registry.register)
        if '#EXT-X-STREAM-INF:' in text:
            child = next(line for line in rewritten.splitlines()
                         if line.startswith('/tv/stream/'))
            url, _ = registry.resolve(int(child.rsplit('/', 1)[1]))
            continue
        # Check keys, initialization maps and one media segment through the
        # rewritten registry, including redirected relative addresses.
        paths = []
        for line in rewritten.splitlines():
            if line.startswith(('#EXT-X-KEY:', '#EXT-X-MAP:')):
                paths.extend(re.findall(r'/tv/stream/([0-9]+)', line))
        segment = next(line for line in rewritten.splitlines()
                       if line.startswith('/tv/stream/'))
        paths.append(segment.rsplit('/', 1)[1])
        for rid in paths:
            child, child_headers = registry.resolve(int(rid))
            _, _, content, _ = fetch_upstream(child, child_headers, 32 * 1024 * 1024)
            if not content:
                return {'ok': False, 'error': 'proxy_empty_media'}
        return {'ok': True, 'error': ''}
    return {'ok': False, 'error': 'proxy_nested_playlist_limit'}


def classify(basic, final):
    """Keep this run's A/V evidence; do not resurrect historical successes."""
    result = dict(final)
    result['stability_error'] = final.get('error', '')
    result['basic_ok'] = bool(basic.get('ok'))
    for key in ('audio_check', 'proxy_check'):
        if key in basic:
            result[key] = basic[key]
    if not basic.get('ok') or final.get('error') in HARD_FAILURES:
        result.update(ok=False, validation_tier='unavailable')
    elif final.get('ok') and final.get('verified_twice'):
        result.update(ok=True, validation_tier='stable', error='')
    else:
        # Preserve short-test timings, not a failed deep test's empty metrics.
        result = dict(basic, stability_error=final.get('error', ''),
                      stability_checked=bool(final.get('deep')),
                      basic_ok=True, verified_twice=False,
                      validation_tier=('basic' if final.get('error') == 'not_deep_verified'
                                       else 'pending_review'))
    return result


def summary(channels, decodes):
    counts = collections.Counter(r.get('validation_tier', 'unavailable') for r in decodes.values())
    result = {'validation_policy': POLICY, 'stable_endpoints': counts['stable'],
              'basic_endpoints': counts['basic'], 'pending_review_endpoints': counts['pending_review']}
    for tier in ('stable', 'basic', 'pending_review'):
        result[tier + '_channels'] = sum(any(decodes.get(i, {}).get('validation_tier') == tier
                                            for i in c['entries']) for c in channels.values())
    return result


def comparable_baseline(stats, prior):
    """Never compare legacy video-only counts with A/V-and-proxy counts."""
    if not prior or (stats.get('validation_policy') == prior.get('validation_policy')
                     and stats.get('basic_check_settings') == prior.get('basic_check_settings')):
        return prior, 'same_policy' if prior else 'first_publication'
    return dict(prior, channels_available=0), 'policy_changed_channel_comparison_skipped'
