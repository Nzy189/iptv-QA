#!/usr/bin/env python3
"""Benchmark next-channel prefetch modes against a private deployment.

Usage:
  python scripts/prefetch_bench.py --base http://HOST:PORT --token TOKEN \
      --view main --channel CHANNEL_ID --prev PREV_CHANNEL_ID
Times a cold /tv/race request for CHANNEL_ID after optionally warming it via
POST /api/tv/prefetch of PREV_CHANNEL_ID. Repeat --rounds times.

Note: the benchmark uses the token racing path (APTV-style), where the server
prewarms the next channel after each race hit, so the "warm" group measures
serve_token_race hitting the prewarmed cache. POST /api/tv/prefetch requires
a browser session cookie (tvsess); when run without one the warm group still
exercises the automatic prewarm triggered by the previous race request.
"""
import argparse
import json
import time
from urllib import request as urlrequest


def timed_race(base, token, view, channel_id):
    url = '%s/tv/race/%s/%s?token=%s' % (base.rstrip('/'), view, channel_id, token)
    start = time.time()
    with urlrequest.urlopen(url, timeout=30) as response:
        response.read(1)
    return time.time() - start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--view', default='main')
    parser.add_argument('--channel', required=True)
    parser.add_argument('--prev', required=True)
    parser.add_argument('--rounds', type=int, default=5)
    args = parser.parse_args()
    cold = []
    for _ in range(args.rounds):
        cold.append(timed_race(args.base, args.token, args.view, args.channel))
        time.sleep(61)  # let the ~60s winner cache expire for a true cold sample
    warm = []
    for _ in range(args.rounds):
        # warming the previous channel triggers server-side prewarm of this one
        body = json.dumps({'mode': 'light', 'index': 0}).encode('utf-8')
        req = urlrequest.Request(args.base.rstrip('/') + '/api/tv/prefetch', data=body,
                                 headers={'Content-Type': 'application/json'})
        try:
            urlrequest.urlopen(req, timeout=10).read()
        except Exception:
            pass
        time.sleep(1.0)
        warm.append(timed_race(args.base, args.token, args.view, args.channel))
    print('cold avg %.3fs  warm avg %.3fs' % (sum(cold) / len(cold), sum(warm) / len(warm)))
    print('cold', ['%.3f' % x for x in cold])
    print('warm', ['%.3f' % x for x in warm])


if __name__ == '__main__':
    main()
