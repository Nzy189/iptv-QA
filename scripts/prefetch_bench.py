#!/usr/bin/env python3
"""Benchmark next-channel prefetch modes against a private deployment.

Usage:
  python scripts/prefetch_bench.py --base http://HOST:PORT --token TOKEN \
      --view main --channel CHANNEL_ID --prev PREV_CHANNEL_ID
Times a cold /tv/race request for CHANNEL_ID after warming it by first racing
PREV_CHANNEL_ID (which triggers the server-side prewarm of CHANNEL_ID).
Repeat --rounds times.

The benchmark uses only the token racing path (APTV-style): the server
prewarms the channel AFTER the one raced (in view order), so the warm group
first races --prev (triggering server-side prewarm of --channel), sleeps
~1s, then times a race request for --channel that should hit the prewarmed
cache. --prev must therefore be the channel immediately BEFORE --channel in
the same view; otherwise the prewarm targets a different channel and the
warm group measures cold timing. No browser session cookie is needed.
"""
import argparse
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
    for i in range(args.rounds):
        cold.append(timed_race(args.base, args.token, args.view, args.channel))
        if i + 1 < args.rounds:
            time.sleep(61)  # let the ~60s winner cache expire for a true cold sample
    warm = []
    for _ in range(args.rounds):
        # racing the previous channel triggers server-side prewarm of this one
        timed_race(args.base, args.token, args.view, args.prev)
        time.sleep(1.0)
        warm.append(timed_race(args.base, args.token, args.view, args.channel))
    print('cold avg %.3fs  warm avg %.3fs' % (sum(cold) / len(cold), sum(warm) / len(warm)))
    print('cold', ['%.3f' % x for x in cold])
    print('warm', ['%.3f' % x for x in warm])


if __name__ == '__main__':
    main()
