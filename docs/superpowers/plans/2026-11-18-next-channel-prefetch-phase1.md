# 下一台预热（服务端）· 第一期：轻量档 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 服务端在播放当前台时后台预竞速"下一个台"，把胜者写入缓存，让浏览器与 APTV 切到下一台时跳过竞速、直接起播。

**Architecture:** 新增 `Prefetcher`（有界、TTL、并发上限），复用现有 `race_lines` 竞速。`TVPlugin._race_winner` 先查预热缓存；token 竞速端点命中某台后触发下一台预热；浏览器新增 `POST /api/tv/prefetch` 按三档开关触发；三档开关在播放器控制栏。

**Tech Stack:** Python 3.6（标准库 threading/OrderedDict）、`http.server`、原生 JS + Hls.js（浏览器端只用按钮与 fetch）。

## Global Constraints

- **必须兼容 Python 3.6.8**（隔离 Linux 回归）：不用 `:=`、`dataclasses`、`capture_output`、`from __future__` 之外的新语法。
- **关闭档（off）必须与现状行为完全一致**：不建任务、不改缓存。
- **每次功能改动同批更新成对中英文文档**：`docs/PLAYBACK.md`+`PLAYBACK.zh-CN.md`，用到新配置项时 `docs/DEPLOYMENT.md`+`DEPLOYMENT.zh-CN.md`。
- 交付前跑 `python -m unittest discover -s tests -v` 与 `python scripts/check_publication.py`。
- 不提交密钥、私有源、凭据；不改检测/发布/总表数据/OBS 行为。
- **一期只实现轻量档**；`full` 档入口接受但行为等同轻量（二期再接字节缓存）。

---

### Task 1: Prefetcher 组件

**Files:**
- Create: `iptv_qa/playback/prefetch.py`
- Test: `tests/test_prefetch.py`

**Interfaces:**
- Produces:
  - `class Prefetcher(race_func=None, mode='light', max_channels=16, max_workers=4, ttl=60)`
  - `VALID_MODES = ('off','light','full')`
  - `Prefetcher.enabled() -> bool`
  - `Prefetcher.get(view, channel_id) -> winner|None`（winner 结构同 `race_lines` 返回值：`{'index','url','headers','final_url'}`）
  - `Prefetcher.warm(view, channel_id, lines, mode=None, wait=False) -> winner|None`
  - `Prefetcher.close() -> None`

- [ ] **Step 1: 写失败测试** `tests/test_prefetch.py`

```python
import time
import unittest
from iptv_qa.playback.prefetch import Prefetcher

WINNER = {'index': 0, 'url': 'http://u/a.m3u8', 'headers': {}, 'final_url': 'http://u/a.m3u8'}
LINES = [{'url': 'http://u/a.m3u8', 'headers': {}}]


class PrefetcherTests(unittest.TestCase):
    def test_off_mode_does_nothing(self):
        calls = []
        p = Prefetcher(race_func=lambda lines: calls.append(1) or WINNER, mode='off')
        self.assertFalse(p.enabled())
        self.assertIsNone(p.warm('main', 'c', LINES, wait=True))
        self.assertEqual(calls, [])
        p.close()

    def test_light_mode_records_winner(self):
        p = Prefetcher(race_func=lambda lines: WINNER, mode='light')
        self.assertEqual(p.warm('main', 'c2', LINES, wait=True), WINNER)
        self.assertEqual(p.get('main', 'c2'), WINNER)
        p.close()

    def test_no_winner_not_cached(self):
        p = Prefetcher(race_func=lambda lines: None, mode='light')
        self.assertIsNone(p.warm('main', 'c', LINES, wait=True))
        self.assertIsNone(p.get('main', 'c'))
        p.close()

    def test_ttl_expiry(self):
        p = Prefetcher(race_func=lambda lines: WINNER, mode='light', ttl=1)
        p.warm('main', 'c', LINES, wait=True)
        self.assertEqual(p.get('main', 'c'), WINNER)
        with p._lock:
            _, w = p._entries[('main', 'c')]
            p._entries[('main', 'c')] = (time.time() - 1, w)
        self.assertIsNone(p.get('main', 'c'))
        p.close()

    def test_max_channels_eviction(self):
        p = Prefetcher(race_func=lambda lines: WINNER, mode='light', max_channels=2)
        for cid in ('a', 'b', 'c'):
            p.warm('main', cid, LINES, wait=True)
        self.assertIsNone(p.get('main', 'a'))
        self.assertEqual(p.get('main', 'c'), WINNER)
        p.close()

    def test_existing_entry_skips_race(self):
        calls = []
        p = Prefetcher(race_func=lambda lines: calls.append(1) or WINNER, mode='light')
        p.warm('main', 'c', LINES, wait=True)
        p.warm('main', 'c', LINES, wait=True)
        self.assertEqual(len(calls), 1)
        p.close()

    def test_full_mode_behaves_as_light_in_phase1(self):
        p = Prefetcher(race_func=lambda lines: WINNER, mode='full')
        p.warm('main', 'c', LINES, wait=True)
        self.assertEqual(p.get('main', 'c'), WINNER)
        p.close()


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest discover -s tests -p "test_prefetch.py" -v`
Expected: FAIL — `ModuleNotFoundError: iptv_qa.playback.prefetch`

- [ ] **Step 3: 实现** `iptv_qa/playback/prefetch.py`

```python
"""Bounded background pre-race for the next channel (server-side prewarm)."""
import threading
import time
from collections import OrderedDict


class Prefetcher:
    """Race the next channel's lines ahead of time and remember the winner.

    Phase 1 implements light/full identically (race only); Phase 2 adds a
    byte cache to full. 'off' disables all work.
    """

    VALID_MODES = ('off', 'light', 'full')

    def __init__(self, race_func=None, mode='light', max_channels=16, max_workers=4, ttl=60):
        self.race_func = race_func
        self.mode = mode if mode in self.VALID_MODES else 'light'
        self.max_channels = max(1, int(max_channels))
        self.ttl = max(1, int(ttl))
        self._lock = threading.Lock()
        self._entries = OrderedDict()   # (view, channel_id) -> (expires, winner)
        self._sem = threading.BoundedSemaphore(max(1, int(max_workers)))
        self._threads = []
        self._stopped = threading.Event()

    def enabled(self):
        return self.mode != 'off'

    def get(self, view, channel_id):
        key = (view, channel_id)
        now = time.time()
        with self._lock:
            hit = self._entries.get(key)
            if not hit or hit[0] <= now:
                if hit:
                    self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return hit[1]

    def _race(self, lines):
        if self.race_func is None:
            from .race import race_lines
            self.race_func = race_lines
        return self.race_func(lines)

    def _run(self, view, channel_id, lines, mode):
        if self._stopped.is_set() or mode == 'off':
            return None
        winner = self._race(lines)
        if winner is None:
            return None
        now = time.time()
        key = (view, channel_id)
        with self._lock:
            self._entries[key] = (now + self.ttl, winner)
            self._entries.move_to_end(key)
            for old_key in [k for k, v in self._entries.items() if v[0] <= now]:
                self._entries.pop(old_key, None)
            while len(self._entries) > self.max_channels:
                self._entries.popitem(last=False)
        return winner

    def warm(self, view, channel_id, lines, mode=None, wait=False):
        chosen = mode if mode in self.VALID_MODES else self.mode
        if chosen == 'off' or self.get(view, channel_id) is not None:
            return None
        if wait:
            return self._run(view, channel_id, lines, chosen)
        worker = threading.Thread(target=self._warm_guarded,
                                  args=(view, channel_id, lines, chosen), daemon=True)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(worker)
        worker.start()
        return None

    def _warm_guarded(self, view, channel_id, lines, mode):
        if not self._sem.acquire(timeout=1.0):
            return
        try:
            self._run(view, channel_id, lines, mode)
        finally:
            self._sem.release()

    def close(self):
        self._stopped.set()
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=1)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m unittest discover -s tests -p "test_prefetch.py" -v`
Expected: PASS（7 tests）

- [ ] **Step 5: 提交**

```bash
git add iptv_qa/playback/prefetch.py tests/test_prefetch.py
git commit -m "feat(playback): add bounded Prefetcher for next-channel pre-race"
```

---

### Task 2: TVPlugin 接入 Prefetcher 与下一台触发

**Files:**
- Modify: `iptv_qa/playback/service.py`（`__init__`、`_race_winner`、`serve_token_race`、`close`，新增 `_prefetch_next`）
- Test: `tests/test_tv.py`

**Interfaces:**
- Consumes: `Prefetcher`（Task 1）
- Produces:
  - `TVPlugin(..., prefetch_mode='light', prefetch_max_channels=16)`
  - `TVPlugin._prefetch_next(view, channel_id) -> None`
  - `TVPlugin.prefetcher` 实例

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_tv.py` 末尾，独立类）

```python
class PrefetchIntegrationTests(unittest.TestCase):
    WINNER = {'index': 0, 'url': 'http://u/1.m3u8', 'headers': {}, 'final_url': 'http://u/1.m3u8'}

    class _Lib:
        def __init__(self, rows):
            self._rows = rows

        def view(self, scope):
            return self._rows

    def _rows(self):
        return [
            {'id': 'c1', 'name': '1', 'group_name': 'g', 'lines': [{'url': 'http://u/1.m3u8', 'headers': {}}]},
            {'id': 'c2', 'name': '2', 'group_name': 'g', 'lines': [{'url': 'http://u/2.m3u8', 'headers': {}}]},
        ]

    def test_prefetch_next_warms_following_channel(self):
        plugin = TVPlugin(b'pw', library=self._Lib(self._rows()))
        plugin.prefetcher.race_func = lambda lines: self.WINNER
        plugin._prefetch_next('main', 'c1')
        deadline = time.time() + 2
        while time.time() < deadline and plugin.prefetcher.get('main', 'c2') is None:
            time.sleep(0.02)
        self.assertEqual(plugin.prefetcher.get('main', 'c2'), self.WINNER)
        plugin.close()

    def test_race_winner_uses_prefetched_without_racing(self):
        def boom(lines):
            raise AssertionError('should not race')
        plugin = TVPlugin(b'pw', race_func=boom)
        plugin.prefetcher.race_func = lambda lines: self.WINNER
        plugin.prefetcher.warm('main', 'c', [], wait=True)
        got = plugin._race_winner('main', 'c', [{'url': 'http://u/x.m3u8', 'headers': {}}])
        self.assertEqual(got, self.WINNER)
        plugin.close()
```

需在 `tests/test_tv.py` 顶部补 `import time` 与 `from iptv_qa.playback.service import TVPlugin`（若未导入）。

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest tests.test_tv.PrefetchIntegrationTests -v`（若 tests 非包则 `python -m unittest discover -s tests -p "test_tv.py" -v`）
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'prefetch_mode'` 或 `AttributeError: 'TVPlugin' object has no attribute 'prefetcher'`

- [ ] **Step 3: 实现**

在 `service.py` 顶部导入：

```python
from .prefetch import Prefetcher
```

`__init__` 签名与初始化（在 `race_cache`/`race_lock` 之后）：

```python
    def __init__(self, password, static_dir=None, playlists=(), public_base='', ffmpeg=None,
                 transcode_limit=2, transcode_idle_seconds=90, library=None, race_func=None,
                 prefetch_mode='light', prefetch_max_channels=16):
        ...
        self.race_cache = OrderedDict()
        self.race_lock = threading.Lock()
        self.prefetcher = Prefetcher(race_func=race_func, mode=prefetch_mode,
                                     max_channels=prefetch_max_channels,
                                     ttl=TV_RACE_CACHE_SECONDS)
```

`close()` 增加一行（在 `self.transcoders.close()` 之后）：

```python
        self.prefetcher.close()
```

`_race_winner` 开头先查预热缓存：

```python
    def _race_winner(self, view, channel_id, lines):
        prefetched = self.prefetcher.get(view, channel_id)
        if prefetched is not None:
            return prefetched
        key = (view, channel_id)
        now = time.time()
        ... (其余不变)
```

新增方法（放在 `serve_token_race` 之前）：

```python
    def _prefetch_next(self, view, channel_id):
        """Warm the channel after channel_id in the given view, if enabled."""
        if not self.prefetcher.enabled() or self.library is None:
            return
        rows = self.library.view(view)
        ids = [row['id'] for row in rows]
        if channel_id not in ids or len(rows) < 2:
            return
        nxt = rows[(ids.index(channel_id) + 1) % len(rows)]
        lines = [{'url': line['url'], 'headers': line['headers']} for line in nxt['lines']]
        if lines:
            self.prefetcher.warm(view, nxt['id'], lines)
```

`serve_token_race` 在 `winner = self._race_winner(...)` 之后、返回前，加：

```python
        self._prefetch_next(view, channel_id)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m unittest discover -s tests -p "test_tv.py" -v`
Expected: PASS（含新类 2 tests，原有测试仍通过）

- [ ] **Step 5: 提交**

```bash
git add iptv_qa/playback/service.py tests/test_tv.py
git commit -m "feat(playback): warm next channel from token race via Prefetcher"
```

---

### Task 3: 浏览器预热接口 `POST /api/tv/prefetch`

**Files:**
- Modify: `iptv_qa/playback/service.py`（`handle_post` 新增分支；新增 `handle_prefetch`）
- Test: `tests/test_tv.py`

**Interfaces:**
- Consumes: `Prefetcher`、`TVPlugin.playback_session`、`session['channels']`（元素含 `id`/`lines`）
- Produces: HTTP `POST /api/tv/prefetch`，请求体 `{mode, index, source_id?}`，返回 `{ok, warmed}`

- [ ] **Step 1: 写失败测试**（追加到 PrefetchIntegrationTests）

```python
    class _Handler:
        def __init__(self, path, body):
            self.path = path
            self.command = 'POST'
            self.headers = {}
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.status = None

        def send_response(self, code):
            self.status = code

        def send_header(self, key, value):
            pass

        def end_headers(self):
            pass

    def test_prefetch_endpoint_warms_next_with_given_mode(self):
        rows = self._rows()
        plugin = TVPlugin(b'pw')
        plugin.prefetcher.race_func = lambda lines: self.WINNER
        session = {'source': 'x', 'library_scope': 'main', 'channels': rows,
                   'source_id': 'sid', 'chain': ['x'], 'sets': {}}
        plugin.playback_session = lambda handler: session
        plugin.authorized = lambda handler: True
        body = json.dumps({'mode': 'light', 'index': 0}).encode('utf-8')
        handler = self._Handler('/api/tv/prefetch', body)
        self.assertTrue(plugin.handle_prefetch(handler))
        deadline = time.time() + 2
        while time.time() < deadline and plugin.prefetcher.get('main', 'c2') is None:
            time.sleep(0.02)
        self.assertEqual(plugin.prefetcher.get('main', 'c2'), self.WINNER)
        plugin.close()

    def test_prefetch_endpoint_off_does_not_warm(self):
        rows = self._rows()
        plugin = TVPlugin(b'pw')
        plugin.prefetcher.race_func = lambda lines: self.WINNER
        session = {'source': 'x', 'library_scope': 'main', 'channels': rows,
                   'source_id': 'sid', 'chain': ['x'], 'sets': {}}
        plugin.playback_session = lambda handler: session
        plugin.authorized = lambda handler: True
        handler = self._Handler('/api/tv/prefetch', json.dumps({'mode': 'off', 'index': 0}).encode('utf-8'))
        plugin.handle_prefetch(handler)
        self.assertIsNone(plugin.prefetcher.get('main', 'c2'))
        plugin.close()
```

需在 `tests/test_tv.py` 顶部补 `import io`。

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest discover -s tests -p "test_tv.py" -v`
Expected: FAIL — `AttributeError: 'TVPlugin' object has no attribute 'handle_prefetch'`

- [ ] **Step 3: 实现**

`handle_post` 新增分支（放在 `/api/tv/source` 之前）：

```python
        if path == '/api/tv/prefetch':
            if not self.authorized(handler):
                self.send_json(handler, {'error': 'unauthorized'}, 401)
                return True
            return self.handle_prefetch(handler)
```

新增方法：

```python
    def handle_prefetch(self, handler):
        body = self.read_json(handler) or {}
        mode = str(body.get('mode') or 'off')
        if mode not in Prefetcher.VALID_MODES:
            mode = 'off'
        session = self.playback_session(handler)
        if not session or not session.get('library_scope') or mode == 'off':
            self.send_json(handler, {'ok': True, 'warmed': False})
            return True
        try:
            index = int(body.get('index'))
        except (TypeError, ValueError):
            self.send_json(handler, {'ok': True, 'warmed': False})
            return True
        channels = session.get('channels') or []
        if len(channels) < 2 or index < 0 or index >= len(channels):
            self.send_json(handler, {'ok': True, 'warmed': False})
            return True
        nxt = channels[(index + 1) % len(channels)]
        lines = [{'url': line['url'], 'headers': line['headers']} for line in nxt.get('lines', [])]
        if not lines:
            self.send_json(handler, {'ok': True, 'warmed': False})
            return True
        self.prefetcher.warm(session['library_scope'], nxt['id'], lines, mode=mode)
        self.send_json(handler, {'ok': True, 'warmed': True})
        return True
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m unittest discover -s tests -p "test_tv.py" -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add iptv_qa/playback/service.py tests/test_tv.py
git commit -m "feat(playback): POST /api/tv/prefetch to warm next channel by mode"
```

---

### Task 4: 播放器三档开关与触发

**Files:**
- Modify: `iptv_qa/playback/assets/index.html`（控制栏加按钮）
- Modify: `iptv_qa/playback/assets/app.js`（状态、按钮、`maybePrefetch`、`playing` 触发）
- Test: `tests/player_navigation.test.js`

**Interfaces:**
- Consumes: `POST /api/tv/prefetch`
- Produces: `state.prefetchMode ∈ {'off','light','full'}`，`maybePrefetch()`，`localStorage['tv-prefetch']`

- [ ] **Step 1: 写失败测试**（追加到 `tests/player_navigation.test.js` 末尾）

先把测试 harness 的导出行扩展（`code = code.slice(0, end) + 'globalThis.playerTest = {...'`）加入 `maybePrefetch, cyclePrefetch`：

```javascript
  code = code.slice(0, end) + 'globalThis.playerTest = {state, play, advanceAfterFailure, armDestruction, removeFailed, reportCurrentLine, maybePrefetch, cyclePrefetch, currentVideo: function(){return video;}};\n})();';
```

追加用例：

```javascript
{
  const p = player(); p.state.libraryScope = 'main';
  const calls = [];
  p.sandbox.fetch = (url, opts) => { calls.push([url, opts]); return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) }); };
  p.play(0);
  assert.equal(p.state.prefetchMode, 'light');
  p.elements['prefetch-btn'].click();
  assert.equal(p.state.prefetchMode, 'full');
  assert.equal(p.storage.get('tv-prefetch'), 'full');
  const last = calls.filter(c => c[0] === '/api/tv/prefetch').pop();
  assert.ok(last, 'toggling prefetch posts to the endpoint');
  assert.equal(JSON.parse(last[1].body).mode, 'full');
  assert.equal(JSON.parse(last[1].body).index, 0);
}
{
  const p = player(); p.state.libraryScope = 'main'; p.state.prefetchMode = 'off';
  const calls = [];
  p.sandbox.fetch = (url) => { calls.push(url); return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); };
  p.play(0); p.maybePrefetch();
  assert.equal(calls.filter(u => u === '/api/tv/prefetch').length, 0, 'off mode never posts');
}
{
  const p = player(); p.state.libraryScope = 'main'; p.state.order = 'random';
  const calls = [];
  p.sandbox.fetch = (url) => { calls.push(url); return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); };
  p.play(0); p.maybePrefetch();
  assert.equal(calls.filter(u => u === '/api/tv/prefetch').length, 0, 'random order never prefetches');
}
```

- [ ] **Step 2: 运行确认失败**

Run: `node tests/player_navigation.test.js`
Expected: FAIL — `state.prefetchMode` undefined / `p.maybePrefetch is not a function`

- [ ] **Step 3: 实现**

`index.html` 在 `race-btn`（第 61 行）后加：

```html
    <button id="prefetch-btn" class="ctl-btn" aria-pressed="false" title="看当前台时后台预热下一个台：关 / 轻量 / 完整">下一台预热：轻量</button>
```

`app.js`：

① 元素引用（第 28 行 `raceBtn` 附近）：

```javascript
  var prefetchBtn = document.getElementById('prefetch-btn');
```

② `state` 增加字段（第 46 行 `raceOn` 后）：

```javascript
                raceOn: localStorage.getItem('tv-source-race') !== '0',
                prefetchMode: (localStorage.getItem('tv-prefetch') || 'light') };
```

③ 按钮逻辑（放在 `raceBtn` 监听附近）：

```javascript
  var PREFETCH_MODES = ['off', 'light', 'full'];
  function updatePrefetchBtn() {
    var label = { off: '关', light: '轻量', full: '完整' }[state.prefetchMode] || '轻量';
    prefetchBtn.textContent = '下一台预热：' + label;
    prefetchBtn.setAttribute('aria-pressed', String(state.prefetchMode !== 'off'));
  }
  function maybePrefetch() {
    if (state.prefetchMode === 'off' || state.order !== 'sequential' || state.current < 0 || !state.libraryScope) return;
    api('/api/tv/prefetch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: state.prefetchMode, index: state.current, source_id: state.sourceId })
    }).catch(function () {});
  }
  function cyclePrefetch() {
    var i = PREFETCH_MODES.indexOf(state.prefetchMode);
    state.prefetchMode = PREFETCH_MODES[(i + 1) % PREFETCH_MODES.length];
    localStorage.setItem('tv-prefetch', state.prefetchMode);
    updatePrefetchBtn();
    maybePrefetch();
  }
  prefetchBtn.addEventListener('click', cyclePrefetch);
  updatePrefetchBtn();
```

④ 在 `bindVideoEvents` 的 `playing` 回调里触发（第 714-717 行）：

```javascript
    el.addEventListener('playing', function () {
      clearDestruction();
      if (autoplayNotice) hideOverlay();
      maybePrefetch();
    });
```

⑤ 顺序模式切回时也触发（`orderBtn` 监听末尾，第 672 行前）：

```javascript
    if (state.order === 'sequential') maybePrefetch();
```

- [ ] **Step 4: 运行确认通过**

Run: `node tests/player_navigation.test.js`
Expected: PASS（原用例 + 新用例）

- [ ] **Step 5: 提交**

```bash
git add iptv_qa/playback/assets/index.html iptv_qa/playback/assets/app.js tests/player_navigation.test.js
git commit -m "feat(player): prefetch toggle (off/light/full) calls /api/tv/prefetch"
```

---

### Task 5: 服务配置项与装配

**Files:**
- Modify: `iptv_qa/server/http.py`（`create_player` 与 `main` 参数）
- Test: `tests/test_tv.py`

**Interfaces:**
- Consumes: `TVPlugin(prefetch_mode, prefetch_max_channels)`
- Produces: CLI `--prefetch-mode`（默认 `IPTV_TV_PREFETCH` 或 `light`）、`--prefetch-max-channels`（默认 16）；`create_player(..., prefetch_mode='light', prefetch_max_channels=16)`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_tv.py`）

```python
class PrefetchConfigTests(unittest.TestCase):
    def test_create_player_passes_prefetch_mode(self):
        from iptv_qa.server.http import create_player
        plugin = create_player('pw', prefetch_mode='off')
        self.assertEqual(plugin.prefetcher.mode, 'off')
        plugin.close()

    def test_invalid_mode_falls_back_to_light(self):
        from iptv_qa.server.http import create_player
        plugin = create_player('pw', prefetch_mode='bogus')
        self.assertEqual(plugin.prefetcher.mode, 'light')
        plugin.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m unittest discover -s tests -p "test_tv.py" -v`
Expected: FAIL — `create_player() got an unexpected keyword argument 'prefetch_mode'`

- [ ] **Step 3: 实现**

`create_player`：

```python
def create_player(password, public_base='', ffmpeg=None, transcode_limit=2, transcode_idle=90,
                  library=None, prefetch_mode='light', prefetch_max_channels=16):
    from iptv_qa.playback.service import TVPlugin, assets_dir
    playlists = (('top5', '综合频道', 'iptv_ok.m3u'),
                 ('live', '直播频道', 'iptv_live.m3u'),
                 ('english', '英语频道', 'iptv_english.m3u'))
    return TVPlugin(password, assets_dir(), playlists, public_base, ffmpeg=ffmpeg,
                    transcode_limit=transcode_limit, transcode_idle_seconds=transcode_idle, library=library,
                    prefetch_mode=prefetch_mode, prefetch_max_channels=prefetch_max_channels)
```

`main()` 增加参数与校验：

```python
    parser.add_argument('--prefetch-mode', default=os.environ.get('IPTV_TV_PREFETCH', 'light'),
                        choices=('off', 'light', 'full'), help='Next-channel prewarm mode')
    parser.add_argument('--prefetch-max-channels', type=int, default=16,
                        help='Maximum channels kept warm at once')
```

并在构造 player 的调用处传入这两个参数（找到 `create_player(` 调用，补 `prefetch_mode=args.prefetch_mode, prefetch_max_channels=args.prefetch_max_channels`）。

> 注：`TVPlugin.__init__` 已把非法 `mode` 归一为 `'light'`（Task 2），故 `test_invalid_mode_falls_back_to_light` 成立。

- [ ] **Step 4: 运行确认通过**

Run: `python -m unittest discover -s tests -p "test_tv.py" -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add iptv_qa/server/http.py tests/test_tv.py
git commit -m "feat(playback): configure prefetch mode and capacity via CLI/env"
```

---

### Task 6: 文档与进度

**Files:**
- Modify: `docs/PLAYBACK.md`、`docs/PLAYBACK.zh-CN.md`（三档开关、服务端预热、APTV 受益、带宽取舍、转码不预热）
- Modify: `docs/DEPLOYMENT.md`、`docs/DEPLOYMENT.zh-CN.md`（`--prefetch-mode`、`--prefetch-max-channels` 默认值）
- Modify: `PROGRESS.md`（记录一期实现与验证证据）

- [ ] **Step 1: 更新 PLAYBACK 中英各一段**

英文（加在"Switching and media delivery"或"Token racing subscription"附近）：

```markdown
- **Next-channel prewarm (server-side):** a bounded `Prefetcher` races the next channel's lines while you watch the current one and remembers the winner for about 60 seconds. The browser control toggles **off / light / full** (default light); APTV and other token players use `--prefetch-mode`. Light races only; full (Phase 2) also caches the first segments. Only the next channel is warmed, never transcode lines. Prewarming spends upstream bandwidth and source connections; CPU/RAM are not the limit.
```

中文对应一段（同步 `PLAYBACK.zh-CN.md`）。DEPLOYMENT 两语言各加配置项表格行。

- [ ] **Step 2: 更新 PROGRESS.md**（"Current work" 顶部加一期条目，写实测命令与结果；未验证部分明确标注）

- [ ] **Step 3: 运行发布审计**

Run: `python scripts/check_publication.py`
Expected: `PUBLICATION_AUDIT_OK`

- [ ] **Step 4: 提交**

```bash
git add docs/PLAYBACK.md docs/PLAYBACK.zh-CN.md docs/DEPLOYMENT.md docs/DEPLOYMENT.zh-CN.md PROGRESS.md
git commit -m "docs: document next-channel prewarm (phase 1)"
```

---

## Self-Review

- **Spec coverage：** 开关（Task 4）✅；服务端预竞速缓存（Task 1/2）✅；APTV 触发（Task 2）✅；浏览器触发（Task 3/4）✅；配置项（Task 5）✅；文档（Task 6）✅；转码不预热（设计约束，`_prefetch_next` 只用 view 的 lines、不含转码逻辑）✅。
- **Placeholder scan：** 无 TBD/TODO；所有代码块为可落地内容。
- **Type consistency：** winner 结构 `{'index','url','headers','final_url'}` 全程一致；`Prefetcher.warm(view, channel_id, lines, mode=None, wait=False)` 在 service 与测试中签名一致；`handle_prefetch` 使用 `Prefetcher.VALID_MODES`。
