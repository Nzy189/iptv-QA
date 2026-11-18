# 播放器"报错清理"按钮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 观众发现画面内容不对（广告/失效二维码）时，点播放器"报错清理"按钮即可把当前线路移入可恢复清理区，并自动切到该台剩余线路继续播放。

**Architecture:** 复用库现有的线路级 `disable_line` 操作与异步发布/审计/恢复机制。服务端在 `library/api.py` 新增 `report_line` 分支（由 频道+线路序号 解析 `stream_id`，最后一线路被清时顺带频道级隔离）；前端 `app.js` 新增按钮、确认框、`reportCurrentLine()` 上报与跳过已清理线路的续播逻辑。

**Tech Stack:** Python 3.6 兼容（生产为 3.6.8，禁 f-string）；前端 ES5（`var`/`function`，禁箭头函数/模板串）；测试为 `unittest` + Node 内置 `node:vm` 沙箱。

**Spec:** `docs/superpowers/specs/2026-11-18-report-button-design.md`

## Global Constraints

- `iptv_qa/` 内 Python 代码必须兼容 Python 3.6.8：不用 f-string、海象运算符、`dict |` 合并。
- `app.js` 必须保持 ES5 风格（现有代码全部 `var` + `function`，无分号省略）。
- 每次功能改动必须同步更新成对中英文文档（PLAYBACK 对、LIBRARY 对）并更新 `PROGRESS.md`。
- 不部署生产；部署按项目惯例另行隔离打包（与工作区已有的改名/文档待部署项合并）。
- 提交信息用英文祈使句，结尾加 `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`。
- 测试命令：`python -m unittest discover -s tests -v`（Windows 有 12 项平台跳过属基线）、`node tests/player_navigation.test.js`、`python scripts/check_publication.py`。

---

### Task 1: 服务端 `report_line` API

**Files:**
- Modify: `iptv_qa/library/api.py:44-58`（`cleanup` 分支之后插入 `report_line` 分支）
- Test: `tests/test_library.py`（`LibraryHTTPTests` 类内新增两个测试）

**Interfaces:**
- Consumes: `Library.act(op, cid, scope, expected, value=None, sid=None, reason='', publish=False)`（已存在，`iptv_qa/library/store.py:266`）；`Library.view(scope)`（已存在，只返回仍有启用线路的频道）；`player.sessions` / `player.session_token(handler)`（cleanup 分支同款用法）。
- Produces: `POST /api/library/action` 请求体 `{operation:'report_line', source_id, channel_id, slot, reason:'content_mismatch'}`，成功返回 `{ok, revision, state:'pending', channel_id, channel_quarantined}`。错误：401 未登录、409 `stale_player_source`（source_id 不匹配）、400 `unknown_channel`/`invalid_slot`/`invalid_reason`。Task 2 的前端按此契约调用。

- [ ] **Step 1: 写失败测试**

在 `tests/test_library.py` 的 `LibraryHTTPTests` 类内（`test_cleanup_rejects_stale_source_and_can_be_restored` 之后）加入：

```python
    def test_report_line_disables_line_and_quarantines_emptied_channel(self):
        self.login();_,_,body=self.client.request('POST','/api/tv/source',{'url':'http://owned.example/iptv_ok.m3u'})
        payload=json.loads(body);cid=payload['channels'][0]['id']
        sid=self.store.view('main')[0]['lines'][2]['stream_id']
        request={'operation':'report_line','channel_id':cid,'source_id':payload['source_id'],
                 'slot':2,'reason':'content_mismatch'}
        status,_,body=self.client.request('POST','/api/library/action',request)
        self.assertEqual(status,200);data=json.loads(body)
        self.assertFalse(data['channel_quarantined'])
        self.assertEqual(len(self.store.view('main')[0]['lines']),4)
        enable={'operation':'enable_line','scope':'main','channel_id':cid,
                'revision':self.store.revision(),'stream_id':sid}
        self.assertEqual(self.client.request('POST','/api/library/action',enable)[0],200)
        self.assertEqual(len(self.store.view('main')[0]['lines']),5)
        for slot in (0,1,2,3,4):
            request['slot']=slot
            status,_,body=self.client.request('POST','/api/library/action',request)
        self.assertEqual(status,200);self.assertTrue(json.loads(body)['channel_quarantined'])
        self.assertEqual(self.store.view('main'),[])
        restore={'operation':'restore','scope':'main','channel_id':cid,'revision':self.store.revision()}
        self.assertEqual(self.client.request('POST','/api/library/action',restore)[0],200)

    def test_report_line_rejects_bad_requests(self):
        from test_tv import Client
        _,_,body=self.client.request('POST','/api/tv/source',{'url':'http://owned.example/iptv_ok.m3u'})
        payload=json.loads(body);cid=payload['channels'][0]['id']
        base={'operation':'report_line','channel_id':cid,'source_id':payload['source_id'],
              'slot':0,'reason':'content_mismatch'}
        anonymous=Client(self.server.server_port)
        self.assertEqual(anonymous.request('POST','/api/library/action',base)[0],401)
        self.login()
        self.assertEqual(self.client.request('POST','/api/library/action',base)[0],200)
        for override in ({'slot':9},{'slot':'2'},{'slot':True},{'reason':'startup_timeout'},
                         {'channel_id':'nope'},{'source_id':'old'}):
            self.assertEqual(self.client.request('POST','/api/library/action',
                dict(base,**override))[0],400 if 'source_id' not in override else 409,
                'rejected: %s'%override)
```

注意：`test_report_line_rejects_bad_requests` 里未登录的 401 检查必须在 `self.login()` 之前（`self.client` 的 cookie 在 login 后才生效）。

- [ ] **Step 2: 运行确认失败**

```bash
python -m unittest tests.test_library -v
```

预期：两个新测试 FAIL（服务端把 `report_line` 当未知操作，走 `act()` 抛 `invalid_operation` → 400，断言 200 失败）。

- [ ] **Step 3: 实现 API 分支**

`iptv_qa/library/api.py` 的 `post()` 中，`cleanup` 分支（`return {'revision':revision,'state':'pending','channel_id':cid}` 之后）插入：

```python
        if operation == 'report_line':
            token=player.session_token(handler)
            source_id=body.get('source_id','')
            session = player.sessions.get(token+':'+source_id) if isinstance(source_id,str) else None
            if not session or not session.get('library_scope') or body.get('source_id') != session.get('source_id'):
                raise Conflict('stale_player_source')
            cid = body.get('channel_id')
            channel = next((c for c in session['channels'] if c['id'] == cid), None)
            if channel is None:
                raise ValueError('unknown_channel')
            slot = body.get('slot')
            if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < len(channel['lines']):
                raise ValueError('invalid_slot')
            if body.get('reason') != 'content_mismatch':
                raise ValueError('invalid_reason')
            sid = channel['lines'][slot]['stream_id']
            revision = self.library.act('disable_line', cid, session['library_scope'],
                                       session['library_revision'], sid=sid,
                                       reason='content_mismatch', publish=True)
            quarantined = not any(e['id'] == cid for e in self.library.view(session['library_scope']))
            if quarantined:
                revision = self.library.act('quarantine', cid, session['library_scope'],
                                          revision, reason='content_mismatch', publish=True)
            session['library_revision'] = revision
            return {'revision':revision,'state':'pending','channel_id':cid,
                    'channel_quarantined':quarantined}
```

原理：`view()` 只返回仍有 `enabled and eligible` 线路的频道（`store.py:202-204`），所以"频道从 view 消失"就是"一线不剩"，此时追加频道级隔离避免发布空频道。审计与 observations 由 `act()` 落库，无需新代码。

- [ ] **Step 4: 运行确认通过**

```bash
python -m unittest tests.test_library -v
```

预期：全部 PASS（含原有测试）。

- [ ] **Step 5: 提交**

```bash
git add iptv_qa/library/api.py tests/test_library.py
git commit -m "$(cat <<'EOF'
Add player content-mismatch line reporting API

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 前端报错按钮与跳线续播

**Files:**
- Modify: `iptv_qa/playback/assets/index.html:58`（`destruction-btn` 之后加按钮）
- Modify: `iptv_qa/playback/assets/app.js`（多处，见步骤）
- Test: `tests/player_navigation.test.js`（沙箱改造 + 末尾新增异步用例）

**Interfaces:**
- Consumes: Task 1 的 `report_line` HTTP 契约；现有 `api()`、`play()`、`startPlayback()`、`endRace()`、`nextVisible()`、`renderList()`、`showError()`、`cleanupStatus`。
- Produces: `reportCurrentLine()`（点击处理器）；`isReported(channel, slot)` / `firstFreeSlot(channel, after)` 两个纯辅助函数；`channel.reportedSlots` 数组（本地记录已上报 slot）；`state.reporting` 防重入标志；按钮 id `report-btn`。

- [ ] **Step 1: 改造测试沙箱**

`tests/player_navigation.test.js`：

1. `player()` 工厂里 `window` 对象加 confirm（默认允许，测试可覆盖）：

```js
    window: { Hls, innerWidth: 1200, confirm: () => true },
```

2. 文件末尾 `playerTest` 导出行加 `reportCurrentLine`：

```js
  code = code.slice(0, end) + 'globalThis.playerTest = {state, play, advanceAfterFailure, armDestruction, removeFailed, reportCurrentLine, currentVideo: function(){return video;}};\n})();';
```

3. 把最后一行 `console.log('PLAYER_NAVIGATION_OK: ...')` 删除，改为文件末尾追加异步测试段（新用例全部放这里，见 Step 2）。

- [ ] **Step 2: 写失败测试**

文件末尾追加（替换原 console.log 行）：

```js
(async () => {
  function settle() { return new Promise(resolve => setImmediate(resolve)); }
  function reportPlayer() {
    const p = player(3, 5);
    p.state.libraryScope = 'main'; p.state.sourceId = 'snap';
    p.state.channels[0].id = 'ch0'; p.state.channels[0].line_count = 5;
    p.calls = []; p.reply = { revision: 7, channel_quarantined: false };
    p.sandbox.fetch = (path, options) => {
      p.calls.push([path, options.body ? JSON.parse(options.body) : null]);
      return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve(p.reply) });
    };
    return p;
  }
  {
    const p = reportPlayer(); p.play(0);
    p.elements['report-btn'].click();
    await settle();
    assert.equal(p.calls.length, 1, 'accepted confirm sends exactly one report');
    assert.deepEqual(p.calls[0], ['/api/library/action',
      { operation: 'report_line', source_id: 'snap', channel_id: 'ch0', slot: 0, reason: 'content_mismatch' }]);
    assert.deepEqual(p.state.channels[0].reportedSlots, [0]);
    assert.equal(p.state.slot, 1, 'playback resumes at the next free line');
    assert.ok(p.elements['cleanup-status'].textContent.indexOf('已清理 1 条线路') === 0);
  }
  {
    const p = reportPlayer(); p.sandbox.window.confirm = () => false; p.play(0);
    p.elements['report-btn'].click();
    await settle();
    assert.equal(p.calls.length, 0, 'cancelling the confirm sends nothing');
  }
  {
    const p = reportPlayer(); p.reply = { revision: 8, channel_quarantined: true }; p.play(0);
    p.elements['report-btn'].click();
    await settle();
    assert.ok(p.state.channels[0].removed, 'an emptied channel is marked removed');
    assert.equal(p.state.current, 1, 'playback moves to the next channel');
  }
  {
    const p = reportPlayer();
    p.sandbox.fetch = () => Promise.resolve({ status: 409, ok: false, json: () => Promise.resolve({ error: 'stale_revision' }) });
    p.play(0);
    p.elements['report-btn'].click();
    await settle();
    assert.ok(p.elements['overlay-text'].textContent.indexOf('总表已更新') !== -1,
      'a stale revision explains the conflict');
    assert.ok(!p.state.channels[0].reportedSlots, 'a failed report marks nothing locally');
  }
  {
    const p = player(3, 3); p.calls = []; p.play(0);
    p.sandbox.fetch = (path) => { p.calls.push(path); return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({}) }); };
    p.elements['report-btn'].click();
    await settle();
    assert.equal(p.calls.length, 0, 'report does nothing outside managed sources');
  }
  {
    const p = reportPlayer(); p.play(0);
    p.elements['report-btn'].click();
    await settle();
    p.elements['race-btn'].click();
    p.play(0);
    p.fail();
    assert.equal(p.state.slot, 2, 'line advance skips the reported line');
  }
  {
    const p = reportPlayer(); p.play(0);
    p.elements['report-btn'].click();
    await settle();
    p.play(1); p.play(0);
    assert.deepEqual(p.instances.slice(-4).map(h => h.url),
      ['/tv/play/0/1', '/tv/play/0/2', '/tv/play/0/3', '/tv/play/0/4'],
      'returning to the channel races the remaining lines, never the reported one');
  }
})().then(() => console.log('PLAYER_NAVIGATION_OK: backups, sequential scan, random non-repeat, stale events, watchdog, normal mode, hedged racing, report cleanup'))
  .catch(error => { console.error(error); process.exit(1); });
```

说明最后两个用例：第六个先关掉竞速（`race-btn` 点击后 `raceOn=false`）再 `play(0)` 重启，使 `p.fail()` 落在单实例的 `advanceAfterFailure` 上（竞速激活时 `fail()` 只会淘汰一个候选，不会推进 slot）；第七个用 `slice(-4)` 是因为沙箱的 `instances` 会累积所有历史实例。

- [ ] **Step 3: 运行确认失败**

```bash
node tests/player_navigation.test.js
```

预期：同步用例全部通过，异步段抛错（`report-btn` 元素无点击行为 / `reportCurrentLine` 未导出 / URL 序列含 slot 0），进程以非零退出。

- [ ] **Step 4: 实现前端**

**`index.html`**：`destruction-btn` 那行之后插入：

```html
    <button id="report-btn" class="ctl-btn" disabled title="画面内容不对（广告/失效提示）时点击：清理当前线路，可在管理页恢复">报错清理</button>
```

**`app.js`**（全部 ES5）：

1. 元素引用（`destructionBtn` 行旁）：

```js
  var reportBtn = document.getElementById('report-btn');
```

2. `state` 初始化对象加 `reporting: false`（加在 `removing: false` 之后）。

3. 辅助函数（放在 `removeFailed` 之前）：

```js
  function isReported(channel, slot) {
    return !!(channel && channel.reportedSlots && channel.reportedSlots.indexOf(slot) !== -1);
  }
  function firstFreeSlot(channel, after) {
    var start = (after === undefined ? 0 : after + 1);
    for (var s = start; s < state.setCount; s += 1) {
      if (!isReported(channel, s)) return s;
    }
    return -1;
  }
```

4. `reportCurrentLine`（放在辅助函数之后）：

```js
  function reportCurrentLine() {
    if (!state.libraryScope || state.current < 0 || state.reporting) return;
    var channel = state.channels[state.current];
    if (!channel || channel.removed) return;
    if (!window.confirm('当前线路播放的不是该频道内容？确认后此线路将被清理，可在管理页恢复')) return;
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      showError('网络已断开，未清理', true); return;
    }
    var slot = state.slot, index = state.current, generation = state.generation, source = state.sourceId;
    state.reporting = true;
    clearWatchdog(); clearDestruction(); endRace(null);
    api('/api/library/action', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({operation:'report_line', source_id:source, channel_id:channel.id,
                            slot:slot, reason:'content_mismatch'})})
      .then(function (response) { return response.json().then(function (data) {
        if (!response.ok) throw Error(response.status === 409
          ? '总表已更新，请重新加载分组后再清理' : (data.error || '清理未保存'));
        if (state.sourceId !== source) return;
        channel.reportedSlots = (channel.reportedSlots || []).concat([slot]);
        state.cleanupRevision = data.revision;
        if (data.channel_quarantined) {
          channel.removed = true;
          cleanupStatus.textContent = '已清理 1 条线路 · 该频道已无可播线路，移入清理区 · 可在管理页恢复';
          renderList();
          if (state.generation === generation) {
            var next = nextVisible(index);
            if (next < 0) showError('当前分组已无可播放频道，可到管理页恢复', false);
            else play(next);
          }
          return;
        }
        cleanupStatus.textContent = '已清理 1 条线路 · 可在管理页恢复';
        if (state.generation === generation && state.current === index) play(index);
      }); })
      .catch(function (error) { showError(error.message || '清理失败，未删除记录', true); })
      .finally(function () { state.reporting = false; });
  }
```

5. `play()`：删除 `state.slot = 0;`（原 431 行），在 `state.setCount=channel.line_count || state.setCount;` 之后加：

```js
    state.slot = firstFreeSlot(channel);
```

6. `advanceAfterFailure()`：把

```js
    if (state.slot + 1 < state.setCount) {
      state.slot += 1;
```

改为：

```js
    var next = firstFreeSlot(state.channels[state.current], state.slot);
    if (next >= 0) {
      state.slot = next;
```

（后续 `state.transcoding = false;` 等行不动。）

7. `startRace()`：把开头

```js
    var channel = state.channels[index];
    var lines = Math.min(5, channel.line_count || 1);
    if (state.slot !== 0 || !state.raceOn || lines < 2) return;
```

改为（候选从当前 slot 起收集、跳过已上报线路、上限仍 5 条）：

```js
    var channel = state.channels[index];
    var total = channel.line_count || state.setCount;
    if (state.slot !== firstFreeSlot(channel) || !state.raceOn || total < 2) return;
    var slots = [];
    for (var s = state.slot; s < total && slots.length < 5; s += 1) {
      if (!isReported(channel, s)) slots.push(s);
    }
    if (slots.length < 2) return;
```

主候选 `var main = { slot: 0, ... }` 改为 `var main = { slot: slots[0], ... }`；候选循环 `for (var s = 1; s < lines; s += 1) { (function (slot) {` 改为 `for (var ci = 1; ci < slots.length; ci += 1) { (function (slot) {`，循环体末尾的 `})(s);` 改为 `})(slots[ci]);`。循环体内部不变（`slot` 形参继续用于 URL）。

8. `dropRaceCandidate()` 全部失败路径：`state.slot = race.candidates.length - 1;` 改为 `state.slot = race.candidates[race.candidates.length - 1].slot;`；纯编码失败路径 `state.slot = 0; startPlayback(true);` 改为 `state.slot = firstFreeSlot(state.channels[state.current]); startPlayback(true);`

9. 按钮接线：三处 `destructionBtn.disabled=!state.libraryScope`（约 526、554、725 行）之后各加一行 `reportBtn.disabled=!state.libraryScope;`；`destructionBtn.addEventListener` 附近加：

```js
  reportBtn.addEventListener('click', reportCurrentLine);
```

- [ ] **Step 5: 运行确认通过**

```bash
node tests/player_navigation.test.js
```

预期：`PLAYER_NAVIGATION_OK: ... report cleanup`。

- [ ] **Step 6: 提交**

```bash
git add iptv_qa/playback/assets/index.html iptv_qa/playback/assets/app.js tests/player_navigation.test.js
git commit -m "$(cat <<'EOF'
Add player report button to clean mismatched lines

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 成对文档与 PROGRESS

**Files:**
- Modify: `docs/PLAYBACK.md` / `docs/PLAYBACK.zh-CN.md`
- Modify: `docs/LIBRARY.md` / `docs/LIBRARY.zh-CN.md`
- Modify: `PROGRESS.md`

**Interfaces:**
- Consumes: Task 1/2 已落地的行为。
- Produces: 无代码接口；文档描述必须与实现一致。

- [ ] **Step 1: PLAYBACK 对更新**

两份文档在"失效清理"说明附近各加一段（中英文对应）：

- 报错清理按钮：观看中发现画面内容与频道不符（广告、失效二维码等）时点击；弹确认框后清理**当前线路**（不是整台），播放器自动切到该台剩余线路并重新竞速。
- 该台一线不剩时整台移入清理区并自动切到下一台。
- 清理可在管理页恢复（启用线路/恢复频道）；QA 检测不会自动恢复人工清理的线路。
- 与失效清理的区别：失效清理是技术性起播失败的自动判定；报错清理是观众对内容的主动上报。
- 按钮仅受管（总表）会话可用。

- [ ] **Step 2: LIBRARY 对更新**

两份文档在操作/证据说明处补充：`report_line` 上报以 `content_mismatch` 为由禁用单条线路（`disable_line`），留审计与观察记录；频道线路耗尽时追加频道级隔离；人工禁用不被 QA 轮次自动恢复。

- [ ] **Step 3: PROGRESS.md 更新**

新增一节（中英对照，风格与既有条目一致）：

- Implemented：`report_line` API（线路级禁用 + 空台兜底隔离 + 审计/异步发布）与播放器报错清理按钮（确认框、跳过已清理线路续播、整台耗尽切台）。
- Verified：引用 Task 4 的实际命令与结果（先写"待验证"，Task 4 完成后回填真实数字）。
- Not deployed：与既有待部署项（失效清理改名、文档修正）合并为同一隔离包，等 Apple TV 令牌订阅一起部署的决定不变。

- [ ] **Step 4: 提交**

```bash
git add docs/PLAYBACK.md docs/PLAYBACK.zh-CN.md docs/LIBRARY.md docs/LIBRARY.zh-CN.md PROGRESS.md
git commit -m "$(cat <<'EOF'
Document player content-mismatch reporting

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 全量回归与 PROGRESS 回填

**Files:**
- Modify: `PROGRESS.md`（回填验证数字）

**Interfaces:**
- Consumes: Task 1–3 全部完成。
- Produces: 验证证据，写入 PROGRESS.md。

- [ ] **Step 1: 全量测试**

```bash
python -m unittest discover -s tests -v
node tests/player_navigation.test.js
python scripts/check_publication.py
```

预期：unittest 全过（Windows 12 项平台跳过与基线一致，总数应为 150）；node 输出 `PLAYER_NAVIGATION_OK: ... report cleanup`；发布审计 `PUBLICATION_AUDIT_OK`（文件数比之前 +1，即新增的设计文档与计划文档计入后无秘密、无断链）。

- [ ] **Step 2: 回填 PROGRESS.md**

把 Task 3 写的"待验证"替换为真实命令输出数字（测试总数、通过/跳过数、审计文件数）。

- [ ] **Step 3: 提交**

```bash
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
Record report-button verification evidence

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review 结论

- **Spec 覆盖**：spec 的前端（按钮/确认框/上报/续播/整台兜底提示/409/断网）、服务端（校验/禁用/兜底隔离/审计）、持久性、文档、测试、部署边界分别落在 Task 1–4；Apple TV 范围外已在 spec 记录，无需任务。
- **占位符**：无 TBD/TODO；所有代码步骤给出完整代码。
- **类型/命名一致性**：`report_line` 请求体与返回体在 Task 1（服务端）与 Task 2（前端）逐字段一致；`firstFreeSlot(channel, after)`、`isReported(channel, slot)`、`reportCurrentLine`、`report-btn`、`state.reporting`、`channel.reportedSlots` 全计划统一。
- **已知取舍**：`startRace` 守卫由 `state.slot !== 0` 改为 `state.slot !== firstFreeSlot(channel)`，无上报时行为与原版完全一致（play() 起播 slot 恒为 firstFreeSlot=0）；有上报时从首个可用线路竞速。
