# 播放器"报错清理"按钮 · 设计文档（2026-11-18）

状态：已实施并随最终审查修复（2026-11-18）——slot 解析统一为实时 view（见"前端/服务端"两节），会话级跳线机制已按审查结论移除。

## 背景与动机

现有"失效清理"只覆盖技术性失败：起播超时、全部线路报错。但存在一类机器判不了的问题——**画面能正常播放，内容却是广告或"频道已失效"二维码**。这类问题只有看的人能发现。用户需要一个"报错按钮"：观看时发现内容不对，点一下就把当前这条线路自动清理掉。

关键事实（已核实）：

- 库 `store.act()` 已支持线路级 `disable_line`/`enable_line`（带审计、观察记录、异步发布），见 `iptv_qa/library/store.py:266`。
- QA 检测的 `ingest()` 只更新线路的 rank/eligible/evidence/last_seen，**不碰 enabled**（`store.py:154`）——人工禁用的线路不会被下轮检测自动恢复，清理是持久的。
- 播放器通过 `/tv/play/<频道序号>/<线路序号>` 播放，服务端能由线路序号解析出具体 `stream_id`（`service.py:465-471`），前端无需暴露内部标识。
- 竞速胜出后 `state.slot` 即为当前实际播放的线路序号（`app.js:282`），报错清理的正是用户眼前这条。

## 用户确认的决策

1. **清理粒度：只清当前线路**（不清整个频道；频道剩余线路自动接续）。广告/二维码通常是单条源的问题。
2. **误触保护：弹确认框**再执行。
3. **方案 A**：复用现有 `disable_line` 通路，不做独立"用户上报"状态，不做服务端内容自动识别。

## 设计

### 前端（`iptv_qa/playback/assets/app.js` / `index.html`）

- 控制栏在"失效清理"旁新增"报错清理"按钮；仅总表会话（`libraryScope`）可用，否则置灰（与失效清理按钮同一禁用逻辑）。
- 点击弹原生 `confirm`："当前线路播放的不是该频道内容？确认后此线路将被清理，可在管理页恢复。"
- 确认后 `POST /api/library/action`，请求体 `{operation:'report_line', source_id, channel_id, slot, reason:'content_mismatch'}`，其中 `slot = state.slot`（竞速胜者序号）。slot 与 `/tv/play` 共用同一索引空间：服务端按实时 view 解析，前端不做会话级换算。
- 成功后：直接 `play(index)` 从该台剩余线路的第一条重新竞速接续播放（竞速关闭时顺序重试）。被清理线路已从实时 view 消失，无需会话级跳过列表；状态栏提示"已清理 1 条线路 · 可在管理页恢复"；更新会话 `library_revision`。
- 服务端返回 `channel_quarantined=true` 时（最后一线路被清），提示"该频道已无可清理线路，已移入清理区"并自动切到下一个可见频道（复用 `nextVisible`）。
- 失败处理：409 版本冲突提示"总表已更新，请重新加载分组"；断网提示"网络已断开，未清理"；请求进行中防重复提交。

### 服务端（`iptv_qa/library/api.py`）

- `post()` 新增 `report_line` 分支，与现有 `cleanup` 分支并列：
  1. 校验会话（同 `cleanup`：`library_scope` 存在、`source_id` 匹配会话）。
  2. 校验 `channel_id` 在该 scope 的实时 view 内（与 `/tv/play` 同一解析空间），不在则 400 `unknown_channel`；`slot` 在实时 view 该频道线路范围内，否则 400。
  3. 从实时 view 的频道数据取 `lines[slot]['stream_id']`——保证"报的就是正在播的那条"，禁一条后序号前移也始终对应当前实际线路。
  4. `library.act('disable_line', cid, scope, session['library_revision'], sid, reason='content_mismatch', publish=True)`；审计与观察记录由 `act()` 自动落库。
  5. 兜底：禁用后检查该频道在该 scope 下剩余 `enabled=1` 的线路数，为 0 则追加 `act('quarantine', ...)`（同 reason），避免发布出空频道；返回体带 `channel_quarantined` 标记。
  6. 更新 `session['library_revision']`，返回 `{revision, state:'pending', channel_id, channel_quarantined}`。
- `reason` 仅接受 `content_mismatch`（与 `cleanup` 的 `startup_timeout`/`all_lines_failed` 集合分开）。
- 令牌端点（Apple TV 订阅）不涉及本操作；管理 API 不受影响。

### 持久性与恢复

- 清理的线路 `enabled=0`，QA 下轮检测不恢复；管理页现有"启用线路"操作即可还原，无需新界面。
- 审计记录含操作者上下文（reason `content_mismatch`），管理页可查。

## 文档

- `docs/PLAYBACK.md` / `PLAYBACK.zh-CN.md`：按钮行为、确认框、清理后切线、恢复方式、与失效清理的区别（人工判断内容 vs 技术失败）。
- `docs/LIBRARY.md` / `LIBRARY.zh-CN.md`：新增 `content_mismatch` 人工上报语义、线路级清理不被 QA 自动恢复的说明。
- `PROGRESS.md` 按规则记录实现/验证/部署状态。

## 测试

- 服务端（`tests/`）：上报成功禁用指定线路并发布排队；slot 越界 400；非总表会话拒绝；最后一线路触发频道级隔离且返回标记；版本冲突 409；`content_mismatch` 之外的 reason 拒绝。
- 前端（`tests/player_navigation.test.js`）：点击→confirm→请求体正确；成功后剔除线路并切线；`channel_quarantined` 时切下一台；409 与断网提示；非总表会话按钮置灰。
- 回归：`python -m unittest discover -s tests -v` 全量、`node tests/player_navigation.test.js`、`python scripts/check_publication.py`。

## 部署

按项目老规矩：隔离目录测试后激活，仅重启 `iptv-web`，检测进程不动；与工作区已有的"失效清理"改名、文档修正等待部署项合并为同一个隔离包。

## 范围外

- Apple TV 令牌订阅端按钮（第三方应用无法嵌入；上报落在库层，将来 TV 端有入口可直接复用）。
- 服务端内容自动识别（OCR/截图比对）。
- 独立"用户上报"状态与排序沉底（方案 B，已否决）。
