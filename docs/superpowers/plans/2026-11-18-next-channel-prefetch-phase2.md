# 下一台预热（服务端）· 第二期：完整档分片字节缓存 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一期基础上，让"完整档"不只预竞速，还把下一台胜者的清单和前 2 片分片预取进内存字节缓存，媒体中继命中缓存直接回，进一步缩短切换起播。

**Architecture:** 扩展 `Prefetcher` 增加有界字节缓存（TTL + 总字节上限 128MB + LRU）；`TVPlugin.serve_url` 在无 `Range` 请求时先查字节缓存；完整档预热时抓清单（跟一层 master）+ 前 2 片分片写入缓存。

**Tech Stack:** Python 3.6 标准库；复用 `proxy.fetch_upstream`、`proxy.rewrite_playlist`、`race.race_lines`。

## Global Constraints

- **必须先完成并合并第一期**（`Prefetcher`、`_prefetch_next`、`/api/tv/prefetch`、三档开关、配置项已存在）。
- **Python 3.6.8 兼容**：不用 `:=`/`dataclasses` 等新特性。
- **关闭档、轻量档行为不变**；字节缓存默认只在"完整档 + `max_bytes>0`"时启用。
- **内存硬上限**：字节缓存总量不超过 `max_bytes`（默认 128MB），条目 TTL 约 60 秒，LRU 驱逐；不得长跑吃满内存。
- 每次功能改动同批更新成对中英文 `PLAYBACK` 文档；新增配置项更新成对 `DEPLOYMENT` 文档。
- 交付前跑 `python -m unittest discover -s tests -v` 与 `python scripts/check_publication.py`。
- 不提交密钥/私有源/凭据；不改检测/发布/总表数据/OBS 行为。

---

### Task 1: Prefetcher 增加字节缓存与完整档预取

**Files:**
- Modify: `iptv_qa/playback/prefetch.py`
- Test: `tests/test_prefetch.py`

**Interfaces:**
- Produces:
  - `Prefetcher(..., fetch_func=None, max_bytes=0)`
  - `Prefetcher.get_bytes(url) -> {'status','content_type','data','final_url'}|None`
  - `Prefetcher._store_bytes(url, status, content_type, data, final_url)`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_prefetch.py`）

```python
import io


class _Headers:
    def __init__(self, content_type):
        self._ct = content_type

    def get(self, key, default=None):
        return self._ct if key.lower() == 'content-type' else default


MANIFEST = b'#EXTM3U\n#EXTINF:6,\nseg1.ts\n#EXTINF:6,\nseg2.ts\n#EXTINF:6,\nseg3.ts\n'
SEG = b'\x47' * 188


def make_fetch(store):
    def fetch(url, headers, max_bytes):
        body, ctype = store[url]
        return 200, _Headers(ctype), body, url
    return fetch


class ByteCacheTests(unittest.TestCase):
    def _pluginish(self, max_bytes=100000):
        store = {
            'http://u/a.m3u8': (MANIFEST, 'application/vnd.apple.mpegurl'),
            'http://u/seg1.ts': (SEG, 'video/mp2t'),
            'http://u/seg2.ts': (SEG, 'video/mp2t'),
        }
        p = Prefetcher(race_func=lambda lines: WINNER, mode='full',
                       fetch_func=make_fetch(store), max_bytes=max_bytes)
        return p

    def test_full_caches_manifest_and_first_two_segments(self):
        p = self._pluginish()
        p.warm('main', 'c', LINES, wait=True)
        m = p.get_bytes('http://u/a.m3u8')
        self.assertIsNotNone(m)
        self.assertTrue(m['data'].startswith(b'#EXTM3U'))
        self.assertEqual(p.get_bytes('http://u/seg1.ts')['data'], SEG)
        self.assertEqual(p.get_bytes('http://u/seg2.ts')['data'], SEG)
        self.assertIsNone(p.get_bytes('http://u/seg3.ts'))
        p.close()

    def test_light_does_not_cache_bytes(self):
        store = {'http://u/a.m3u8': (MANIFEST, 'application/vnd.apple.mpegurl')}
        p = Prefetcher(race_func=lambda lines: WINNER, mode='light',
                       fetch_func=make_fetch(store), max_bytes=100000)
        p.warm('main', 'c', LINES, wait=True)
        self.assertIsNone(p.get_bytes('http://u/a.m3u8'))
        p.close()

    def test_byte_cap_evicts_oldest(self):
        store = {
            'http://u/a.m3u8': (MANIFEST, 'application/vnd.apple.mpegurl'),
            'http://u/seg1.ts': (SEG, 'video/mp2t'),
            'http://u/seg2.ts': (SEG, 'video/mp2t'),
        }
        p = Prefetcher(race_func=lambda lines: WINNER, mode='full',
                       fetch_func=make_fetch(store), max_bytes=250)
        p.warm('main', 'c', LINES, wait=True)
        # manifest (57B) + seg1 (188B) = 245B fits; adding seg2 (188B) exceeds 250 -> evict oldest
        self.assertLessEqual(p._byte_total, 250)
        self.assertIsNone(p.get_bytes('http://u/a.m3u8'))
        p.close()

    def test_zero_max_bytes_disables_byte_cache(self):
        store = {'http://u/a.m3u8': (MANIFEST, 'application/vnd.apple.mpegurl')}
        p = Prefetcher(race_func=lambda lines: WINNER, mode='full',
                       fetch_func=make_fetch(store), max_bytes=0)
        p.warm('main', 'c', LINES, wait=True)
        self.assertIsNone(p.get_bytes('http://u/a.m3u8'))
        p.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest discover -s tests -p "test_prefetch.py" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'fetch_func'` / `AttributeError: get_bytes`

- [ ] **Step 3: 实现**（修改 `iptv_qa/playback/prefetch.py`）

顶部导入：

```python
from urllib.parse import urljoin

from .constants import MAX_PLAYLIST_BYTES, MAX_SEGMENT_BYTES
from .proxy import fetch_upstream
```

`__init__` 增加参数与字段：

```python
    def __init__(self, race_func=None, mode='light', max_channels=16, max_workers=4, ttl=60,
                 fetch_func=None, max_bytes=0):
        ...
        self.fetch_func = fetch_func
        self.max_bytes = max(0, int(max_bytes))
        self._bytes = OrderedDict()   # url -> (expires, entry)
        self._byte_total = 0
```

`_run` 末尾、返回前加入完整档字节预取：

```python
        if mode == 'full' and self.max_bytes > 0:
            try:
                self._warm_bytes(winner)
            except Exception:
                pass
        return winner
```

新增方法：

```python
    def get_bytes(self, url):
        if self.max_bytes <= 0:
            return None
        now = time.time()
        with self._lock:
            hit = self._bytes.get(url)
            if not hit or hit[0] <= now:
                if hit:
                    self._byte_total -= len(hit[1]['data'])
                    self._bytes.pop(url, None)
                return None
            self._bytes.move_to_end(url)
            return hit[1]

    def _store_bytes(self, url, status, content_type, data, final_url):
        if self.max_bytes <= 0 or not data or len(data) > self.max_bytes:
            return
        entry = {'status': status, 'content_type': content_type, 'data': data, 'final_url': final_url}
        now = time.time()
        with self._lock:
            old = self._bytes.pop(url, None)
            if old:
                self._byte_total -= len(old[1]['data'])
            self._bytes[url] = (now + self.ttl, entry)
            self._byte_total += len(data)
            for k in [k for k, v in self._bytes.items() if v[0] <= now]:
                self._byte_total -= len(self._bytes[k][1]['data'])
                self._bytes.pop(k, None)
            while self._byte_total > self.max_bytes and self._bytes:
                _, (_e, v) = self._bytes.popitem(last=False)
                self._byte_total -= len(v['data'])

    @staticmethod
    def _media_uris(text):
        return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith('#')]

    def _fetch(self, url, headers, cap):
        if self.fetch_func is None:
            return fetch_upstream(url, headers, cap)
        return self.fetch_func(url, headers, cap)

    def _warm_bytes(self, winner):
        status, hdrs, data, final_url = self._fetch(winner['url'], winner['headers'], MAX_PLAYLIST_BYTES)
        if not data or data.lstrip(b'\r\n \t')[:7] != b'#EXTM3U':
            return
        ctype = hdrs.get('Content-Type') or 'application/vnd.apple.mpegurl'
        self._store_bytes(winner['url'], status, ctype, data, final_url)
        text = data.decode('utf-8', 'replace')
        if '#EXT-X-STREAM-INF' in text:
            variants = self._media_uris(text)
            if not variants:
                return
            variant_url = urljoin(final_url, variants[0])
            status, hdrs, data, final_url = self._fetch(variant_url, winner['headers'], MAX_PLAYLIST_BYTES)
            if not data or data.lstrip(b'\r\n \t')[:7] != b'#EXTM3U':
                return
            ctype = hdrs.get('Content-Type') or 'application/vnd.apple.mpegurl'
            self._store_bytes(variant_url, status, ctype, data, final_url)
            text = data.decode('utf-8', 'replace')
        for uri in self._media_uris(text)[:2]:
            seg_url = urljoin(final_url, uri)
            try:
                status, hdrs, seg, seg_final = self._fetch(seg_url, winner['headers'], MAX_SEGMENT_BYTES)
            except Exception:
                continue
            self._store_bytes(seg_url, status, hdrs.get('Content-Type') or 'application/octet-stream', seg, seg_final)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m unittest discover -s tests -p "test_prefetch.py" -v`
Expected: PASS（一期 7 + 二期 4 tests）

- [ ] **Step 5: 提交**

```bash
git add iptv_qa/playback/prefetch.py tests/test_prefetch.py
git commit -m "feat(playback): full-mode byte cache for next-channel prewarm"
```

---

### Task 2: 媒体中继命中字节缓存

**Files:**
- Modify: `iptv_qa/playback/service.py`（`serve_url` 新增缓存分支；新增 `_send_cached`）
- Test: `tests/test_tv.py`

**Interfaces:**
- Consumes: `Prefetcher.get_bytes`
- Produces: `TVPlugin._send_cached(handler, entry, url_template, headers)`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_tv.py`）

```python
class ServeCacheTests(unittest.TestCase):
    class _Handler:
        def __init__(self, range_header=None):
            self.command = 'GET'
            self.headers = {} if range_header is None else {'Range': range_header}
            self.wfile = io.BytesIO()

        def send_response(self, code):
            self.status = code

        def send_header(self, key, value):
            pass

        def end_headers(self):
            pass

    def test_serves_cached_segment_without_upstream(self):
        plugin = TVPlugin(b'pw')
        plugin.prefetcher.max_bytes = 100000
        plugin.prefetcher._store_bytes('http://u/seg.ts', 200, 'video/mp2t', b'\x47' * 188, 'http://u/seg.ts')
        handler = self._Handler()
        with mock.patch('iptv_qa.playback.service.fetch_upstream',
                        side_effect=AssertionError('no upstream on cache hit')):
            self.assertTrue(plugin.serve_url(handler, 'http://u/seg.ts', {}))
        self.assertEqual(handler.wfile.getvalue(), b'\x47' * 188)
        plugin.close()

    def test_range_request_bypasses_cache(self):
        plugin = TVPlugin(b'pw')
        plugin.prefetcher.max_bytes = 100000
        plugin.prefetcher._store_bytes('http://u/seg.ts', 200, 'video/mp2t', b'\x47' * 188, 'http://u/seg.ts')
        handler = self._Handler(range_header='bytes=0-10')
        calls = []

        def fake_fetch(url, headers, cap, range_header=None):
            calls.append(url)
            return 206, {}, b'\x47' * 11, url

        with mock.patch('iptv_qa.playback.service.fetch_upstream', side_effect=fake_fetch):
            plugin.serve_url(handler, 'http://u/seg.ts', {})
        self.assertEqual(calls, ['http://u/seg.ts'])
        plugin.close()
```

需在 `tests/test_tv.py` 顶部确保 `import io` 与 `from unittest import mock`（后者通常已导入）。

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest discover -s tests -p "test_tv.py" -v`
Expected: FAIL — 缓存未接入，`fetch_upstream` 被调用（抛 AssertionError）

- [ ] **Step 3: 实现**

`serve_url` 开头：

```python
    def serve_url(self, handler, url, headers, url_template=None):
        range_header = handler.headers.get('Range')
        if not range_header:
            cached = self.prefetcher.get_bytes(url)
            if cached is not None:
                self._send_cached(handler, cached, url_template, headers)
                return True
        try:
            status, response_headers, data, final_url = fetch_upstream(url, headers, MAX_SEGMENT_BYTES, range_header)
        ...
```

新增方法：

```python
    def _send_cached(self, handler, entry, url_template, headers):
        data = entry['data']
        if data[:3] == BOM:
            data = data[3:]
        if data.lstrip(b'\r\n \t')[:7] == b'#EXTM3U':
            text = rewrite_playlist(data.decode('utf-8', 'replace'), entry['final_url'], headers,
                                    self.registry.register, url_template)
            self.send_media(handler, 200, text.encode('utf-8'),
                            'application/vnd.apple.mpegurl; charset=utf-8')
            return
        content_type = entry.get('content_type') or 'application/octet-stream'
        self.send_media(handler, entry['status'] if entry['status'] in (200, 206) else 200, data, content_type)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m unittest discover -s tests -p "test_tv.py" -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add iptv_qa/playback/service.py tests/test_tv.py
git commit -m "feat(playback): serve prefetched manifests/segments from byte cache"
```

---

### Task 3: 字节缓存容量配置

**Files:**
- Modify: `iptv_qa/playback/service.py`（`__init__` 增加 `prefetch_max_bytes`，传给 `Prefetcher`）
- Modify: `iptv_qa/server/http.py`（`create_player` 与 `main` 增加 `--prefetch-max-bytes`）
- Test: `tests/test_tv.py`

**Interfaces:**
- Produces: `TVPlugin(..., prefetch_max_bytes=128*1024*1024)`；CLI `--prefetch-max-bytes`（默认 `134217728`）

- [ ] **Step 1: 写失败测试**

```python
class PrefetchBytesConfigTests(unittest.TestCase):
    def test_max_bytes_default_and_override(self):
        from iptv_qa.server.http import create_player
        a = create_player('pw')
        self.assertEqual(a.prefetcher.max_bytes, 128 * 1024 * 1024)
        a.close()
        b = create_player('pw', prefetch_max_bytes=1000)
        self.assertEqual(b.prefetcher.max_bytes, 1000)
        b.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest discover -s tests -p "test_tv.py" -v`
Expected: FAIL — `prefetcher.max_bytes` 不存在或不为默认值

- [ ] **Step 3: 实现**

`TVPlugin.__init__` 追加参数并把 `max_bytes` 传给 `Prefetcher`：

```python
    def __init__(self, password, static_dir=None, playlists=(), public_base='', ffmpeg=None,
                 transcode_limit=2, transcode_idle_seconds=90, library=None, race_func=None,
                 prefetch_mode='light', prefetch_max_channels=16, prefetch_max_bytes=128 * 1024 * 1024):
        ...
        self.prefetcher = Prefetcher(race_func=race_func, mode=prefetch_mode,
                                     max_channels=prefetch_max_channels,
                                     ttl=TV_RACE_CACHE_SECONDS, max_bytes=prefetch_max_bytes)
```

`create_player` 增加参数并透传：

```python
def create_player(password, public_base='', ffmpeg=None, transcode_limit=2, transcode_idle=90,
                  library=None, prefetch_mode='light', prefetch_max_channels=16,
                  prefetch_max_bytes=128 * 1024 * 1024):
    ...
    return TVPlugin(..., prefetch_mode=prefetch_mode,
                    prefetch_max_channels=prefetch_max_channels,
                    prefetch_max_bytes=prefetch_max_bytes)
```

`main()` 增加参数并透传：

```python
    parser.add_argument('--prefetch-max-bytes', type=int, default=128 * 1024 * 1024,
                        help='Memory cap for prefetched manifest/segment bytes (full mode)')
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m unittest discover -s tests -p "test_tv.py" -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add iptv_qa/playback/service.py iptv_qa/server/http.py tests/test_tv.py
git commit -m "feat(playback): configure prefetch byte-cache memory cap"
```

---

### Task 4: 真实源实测与证据

**Files:**
- Create: `scripts/prefetch_bench.py`
- Modify: `PROGRESS.md`

**Interfaces:**
- Produces: 命令行基准，输出"关 / 轻量 / 完整"切换耗时对照，供证据留档（不进入产品路径）。

- [ ] **Step 1: 写基准脚本** `scripts/prefetch_bench.py`

```python
#!/usr/bin/env python3
"""Benchmark next-channel prefetch modes against a private deployment.

Usage:
  python scripts/prefetch_bench.py --base http://HOST:PORT --token TOKEN \
      --view main --channel CHANNEL_ID --prev PREV_CHANNEL_ID
Times a cold /tv/race request for CHANNEL_ID after optionally warming it via
POST /api/tv/prefetch of PREV_CHANNEL_ID. Repeat --rounds times.
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
```

> 说明：`/api/tv/prefetch` 需浏览器会话 Cookie；真实基准用 token 竞速路径（APTV 路径）时，服务端在命中上一台后会自动预热下一台（Task 2 一期），因此"warm"组直接测 `serve_token_race` 命中预热缓存的耗时。若脚本走浏览器端点，需带上有效 `tvsess` Cookie（额外说明写在脚本 docstring）。

- [ ] **Step 2: 在私有环境实测并记录**

Run（用私有部署参数，不入库）：`python scripts/prefetch_bench.py --base http://HOST:PORT --token TOKEN --view main --channel CH_ID --prev PREV_ID`
Expected: 打印冷/热平均耗时；把数字与并发下出口带宽、是否触及源站限流一并写入 `PROGRESS.md`。**未跑真实源前不得声称"更快"。**

- [ ] **Step 3: 提交**

```bash
git add scripts/prefetch_bench.py PROGRESS.md
git commit -m "test(playback): add prefetch benchmark and record phase-2 evidence"
```

---

### Task 5: 文档（完整档）

**Files:**
- Modify: `docs/PLAYBACK.md`、`docs/PLAYBACK.zh-CN.md`（完整档 = 预取清单+前 2 片，128MB 上限，Range 请求绕过缓存）
- Modify: `docs/DEPLOYMENT.md`、`docs/DEPLOYMENT.zh-CN.md`（`--prefetch-max-bytes`）
- Modify: `PROGRESS.md`

- [ ] **Step 1: 更新 PLAYBACK 中英各一段**（在一期段落上补充完整档说明与内存上限、Range 绕过）
- [ ] **Step 2: 更新 DEPLOYMENT 中英配置表**（`--prefetch-max-bytes` 默认 134217728）
- [ ] **Step 3: 审计与提交**

```bash
python scripts/check_publication.py
git add docs/PLAYBACK.md docs/PLAYBACK.zh-CN.md docs/DEPLOYMENT.md docs/DEPLOYMENT.zh-CN.md PROGRESS.md
git commit -m "docs: document full prefetch mode and byte-cache cap"
```

---

## Self-Review

- **Spec coverage：** 字节缓存（Task 1）✅；中继命中（Task 2）✅；128MB/2 片（Task 1/3）✅；Range 绕过（Task 2 测试）✅；实测证据（Task 4）✅；文档（Task 5）✅。
- **Placeholder scan：** 无 TBD；代码块均可落地。
- **Type consistency：** 缓存 entry 统一 `{'status','content_type','data','final_url'}`，Task 1 产出、Task 2 消费一致；`max_bytes` 在 `Prefetcher`/`TVPlugin`/`create_player` 三处同名透传。
- **依赖：** 本计划假设一期已合并；若一期尚未落地，先执行一期计划。
