# 新用户上手易用性改进 · 实施计划（2026-11-18）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让有自己 M3U 的陌生人在 README 首页 1 分钟内找到导入路径并知道要等多久；任何人 1 分钟内看懂项目能做/不能做什么。

**Architecture:** 纯文档重构为主（README 双语快速上手改为两条路径 + 功能清单 + 时间预期，DEPLOYMENT 同步），加一处小代码改动（`scripts/configure.py` 的 `--source` 接受本地文件路径，自动转 `file://` URI）及其单测。规格见 `docs/superpowers/specs/2026-11-18-onboarding-usability-design.md`。

**Tech Stack:** Python 3 标准库（argparse/pathlib/urllib），unittest（项目不用 pytest），GitHub Flavored Markdown。

## Global Constraints

- 中英文文档必须成对同步，语言切换链接保留（项目规则，AGENTS.md 第 7 条）。
- 不改检测规则、排名逻辑、发布流程；不动生产部署。
- 公共文档不写私有部署细节、硬件信息（AGENTS.md 第 9 条）。
- `--source` 传既非 URL 也非存在文件的值时：报错退出（exit code 2，argparse `parser.error`）。
- 测试命令：`python3 -m unittest discover -s tests -v`（Windows 上用 `python`）。
- 提交信息末尾加 `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`。

---

### Task 1: configure.py `--source` 本地文件路径支持（TDD）

**Files:**
- Modify: `scripts/configure.py`
- Create: `tests/test_configure_script.py`

**Interfaces:**
- Consumes: 现有 `configure.py` 的 `--source`（action='append'）与 `config/config.example.json` 结构。
- Produces: `normalize_source(value) -> str`（`scripts/configure.py` 内函数）：含 `://` 的值原样返回；存在的本地文件返回 `Path.resolve().as_uri()`；否则抛 `ValueError`。`main()` 捕获后调 `parser.error` 退出。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_configure_script.py`：

```python
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/configure.py'


def run_configure(output, data_dir, *sources):
    argv = [sys.executable, str(SCRIPT), '--output', str(output), '--data-dir', str(data_dir)]
    for source in sources:
        argv += ['--source', source]
    return subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT))


class ConfigureSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)

    def read_config(self):
        return json.loads((self.base / 'config.local.json').read_text(encoding='utf-8'))

    def test_local_playlist_path_becomes_file_uri(self):
        playlist = self.base / 'my.m3u'
        playlist.write_text('#EXTM3U\n', encoding='utf-8')
        result = run_configure(self.base / 'config.local.json', self.base / 'data', str(playlist))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_config()['sources'][0]['url'], playlist.resolve().as_uri())

    def test_http_url_passes_through(self):
        result = run_configure(self.base / 'config.local.json', self.base / 'data',
                               'https://example.test/list.m3u')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_config()['sources'][0]['url'], 'https://example.test/list.m3u')

    def test_invalid_source_is_rejected(self):
        result = run_configure(self.base / 'config.local.json', self.base / 'data',
                               str(self.base / 'missing.m3u'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--source', result.stderr)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m unittest tests.test_configure_script -v
```

预期：`test_local_playlist_path_becomes_file_uri` FAIL（sources[0].url 是原始路径而非 file:// URI）；`test_invalid_source_is_rejected` FAIL（当前无效值不报错，returncode 为 0）；`test_http_url_passes_through` PASS。

- [ ] **Step 3: 最小实现**

修改 `scripts/configure.py`。在 `main()` 前加函数：

```python
def normalize_source(value):
    """Pass URLs through; convert an existing local playlist file to a file:// URI."""
    if '://' in value:
        return value
    path = Path(value).expanduser()
    if path.is_file():
        return path.resolve().as_uri()
    raise ValueError('not a URL or an existing local playlist file: {}'.format(value))
```

把 `main()` 中现有的：

```python
    if args.source:
        cfg['sources'] = [{'name': 'subscription_{}'.format(i+1), 'url': url} for i,url in enumerate(args.source)]
```

替换为：

```python
    if args.source:
        try:
            cfg['sources'] = [{'name': 'subscription_{}'.format(i+1), 'url': normalize_source(url)}
                              for i, url in enumerate(args.source)]
        except ValueError as exc:
            parser.error(str(exc))
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m unittest tests.test_configure_script -v
```

预期：3 个测试全 PASS。

- [ ] **Step 5: 跑全量测试防回归**

```bash
python -m unittest discover -s tests -v
```

预期：与改动前相同的通过/跳过数 + 新增 3 项全过（Windows 上原有平台跳过照旧）。

- [ ] **Step 6: 提交**

```bash
git add scripts/configure.py tests/test_configure_script.py
git commit -m "$(cat <<'EOF'
Accept local playlist files in configure --source

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: README.md 快速上手重构 + 功能清单（英文）

**Files:**
- Modify: `README.md`（替换 `## Quick start` 整节；在 `## Why iptv-QA?` 前插入 `## What it does` 节）

**Interfaces:**
- Consumes: Task 1 的 `--source` 本地文件支持（文档描述的行为依赖它已实现）。
- Produces: 锚点 `#check-your-own-playlist`（DEPLOYMENT.md 在 Task 4 引用）。

- [ ] **Step 1: 替换 `## Quick start` 整节**

删除现有 `## Quick start` 节（从 `## Quick start` 标题到 `## Documentation` 标题之前的全部内容，即当前第 101–127 行区域），替换为：

```markdown
## Quick start

**Requirements:** Linux, Python 3, FFmpeg and ffprobe. FFmpeg 7.x is the reference version. OBS and cloud storage are optional.

### Check your own playlist

Have an M3U subscription or file? Point iptv-QA at it and check only that:

```bash
git clone https://github.com/Nzy189/iptv-QA.git
cd iptv-QA
python3 scripts/configure.py --output config.local.json --data-dir ./data --source /path/to/your.m3u
python3 -m iptv_qa check --config config.local.json
```

- `--source` accepts an `http(s)` URL or a local playlist file, and can be repeated for several subscriptions.
- `check` skips catalogue discovery and tests only the sources you configured.
- Expect the first results in roughly 30–60 minutes: every surviving candidate is watched for at least 30 seconds of real video, then re-verified over a new connection at least 30 minutes later. The wait is the quality gate, not overhead.

After the run, view the local results:

```bash
python3 -m iptv_qa dashboard --data-dir ./data --host 127.0.0.1 --port 8088
```

Open `http://127.0.0.1:8088/`. **OBS is off by default**; local checking, output files and the dashboard work without cloud storage.

### Run the full pipeline

Without `--source`, `run` starts from the default public catalogues (tens of thousands of candidate endpoints), discovers and checks everything, and publishes the ranked subscriptions — this is how the example subscriptions above are produced. A full run can take hours depending on source count:

```bash
python3 scripts/configure.py --output config.local.json --data-dir ./data
python3 -m iptv_qa run --config config.local.json
```

For scheduling and optional OBS uploads, follow the [deployment guide](../../../docs/DEPLOYMENT.md).

### Watch without checking

Browser playback works without running QA — import any M3U:

```bash
export IPTV_TV_PASSWORD='choose-a-private-password'
python3 -m iptv_qa play --port 8089
```

Open `http://127.0.0.1:8089/tv/`. FFmpeg is needed only for fallback transcoding. Source checkout uses the standard library; `python3 -m pip install .` also installs the CLI and complete player assets.
```

- [ ] **Step 2: 在 `## Why iptv-QA?` 标题前插入功能清单节**

```markdown
## What it does

- Real video checks: decoded frames, media progression, HLS playlist freshness, and re-verification over a new connection at least 30 minutes later.
- Deduplication and channel merging, with lines ranked by startup, recent results and historical reliability.
- Ranked subscriptions: Top3, Top5, three single-source sets, a stable-only list and a separate English selection.
- Browser playback: channel search, hedged multi-line racing, backup fallback and on-demand H.264/AAC transcoding.
- Unattended operation: scheduling, per-host concurrency control, retry handling and publication guards.
- Reporting: dashboard, exported CSV/JSON history and optional Huawei OBS uploads with stable object names.

**Not included:** uninterrupted-playback guarantees, a bundled fixed channel library, EPG, or access to paid/DRM-protected sources. Playback depends on the source, network and receiver; see [capability boundaries](../../../docs/DESIGN.md#known-limitations).
```

- [ ] **Step 3: 自检渲染与链接**

- `## Quick start` 锚点仍存在（文件头导航 `[Quick start](#quick-start)` 依赖它）。
- `docs/DESIGN.md#known-limitations` 锚点已存在于现有 README 第 142 行的链接中，无需改动 DESIGN。
- 通读一遍新节，确认与 "Use the modules you need" 表（`check` 描述）不矛盾。

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Restructure README quick start around importing your own playlist

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: README.zh-CN.md 同步重构（中文）

**Files:**
- Modify: `README.zh-CN.md`（替换 `## 快速开始` 整节；在 `## 为什么选择 iptv-QA？` 前插入 `## 功能清单` 节）

**Interfaces:**
- Consumes: Task 2 的英文版结构（内容必须逐节对应）。
- Produces: 锚点 `#检测自己的播放列表`（DEPLOYMENT.zh-CN.md 在 Task 4 引用）。

- [ ] **Step 1: 替换 `## 快速开始` 整节**

删除现有 `## 快速开始` 节（从标题到 `## 使用文档` 之前的全部内容，即当前第 102–128 行区域），替换为：

```markdown
## 快速开始

**环境要求：** Linux、Python 3、FFmpeg 和 ffprobe；参考媒体工具版本为 FFmpeg 7.x。OBS 与云存储均为可选。

### 检测自己的播放列表

手里已有 M3U 订阅或文件？直接指向它，只检测这一份：

```bash
git clone https://github.com/Nzy189/iptv-QA.git
cd iptv-QA
python3 scripts/configure.py --output config.local.json --data-dir ./data --source /path/to/your.m3u
python3 -m iptv_qa check --config config.local.json
```

- `--source` 接受 `http(s)` 地址或本地播放列表文件，可重复传入多个订阅。
- `check` 跳过目录发现，只检测你配置的源。
- 首轮结果约 30–60 分钟后产出：每个入围候选先持续观察至少 30 秒真实视频，再间隔至少 30 分钟用新连接复验。等待本身就是质量门槛，不是额外开销。

检测结束后，查看本地结果：

```bash
python3 -m iptv_qa dashboard --data-dir ./data --host 127.0.0.1 --port 8088
```

访问 `http://127.0.0.1:8088/`。**OBS 默认关闭**，检测、文件输出和本地页面都能独立使用。

### 运行完整流水线

不传 `--source` 时，`run` 从默认公共目录（数万候选端点）开始，先发现再全量检测，最后发布排名订阅——上方示例订阅即由此产出。一轮耗时视源数量可达数小时：

```bash
python3 scripts/configure.py --output config.local.json --data-dir ./data
python3 -m iptv_qa run --config config.local.json
```

定时任务与可选 OBS 上传见[部署配置](../../../docs/DEPLOYMENT.zh-CN.md)。

### 不检测，直接看

浏览器放映不依赖检测结果，导入任意 M3U 即可观看：

```bash
export IPTV_TV_PASSWORD='choose-a-private-password'
python3 -m iptv_qa play --port 8089
```

访问 `http://127.0.0.1:8089/tv/`。FFmpeg 仅在需要回退转码时使用。源码运行无需安装 Python 依赖；也可用 `python3 -m pip install .` 安装命令和完整网页资源。
```

- [ ] **Step 2: 在 `## 为什么选择 iptv-QA？` 标题前插入功能清单节**

```markdown
## 功能清单

- 真实视频检测：解码画面、观察媒体推进、HLS 播放列表更新检查，以及间隔至少 30 分钟的新连接复验。
- 去重与频道合并，按起播速度、当次表现和历史可靠性对线路排名。
- 多套排名订阅：Top3、Top5、三套单线路、纯稳定清单和独立英语频道。
- 浏览器放映：频道搜索、多线路竞速、备用自动回退和按需 H.264/AAC 转码。
- 无人值守运行：定时任务、按主机并发控制、失败重试和发布保护。
- 结果可见：仪表盘、CSV/JSON 历史导出，以及可选的华为 OBS 固定地址上传。

**不包含：** 不保证不间断播放、不内置固定频道库、不做 EPG、不提供付费/DRM 源的访问。播放效果取决于上游、网络和接收设备，能力边界见[设计文档](../../../docs/DESIGN.zh-CN.md#现有边界)。
```

- [ ] **Step 3: 中英对照自检**

逐节核对 README.md 与 README.zh-CN.md：三个小节标题、要点列表、"Not included / 不包含"逐条对应；`docs/DESIGN.zh-CN.md#现有边界` 锚点已存在于现有 README 第 143 行，无需改动。

- [ ] **Step 4: 提交**

```bash
git add README.zh-CN.md
git commit -m "$(cat <<'EOF'
Sync Chinese README quick start with the two-path structure

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: DEPLOYMENT 双语同步 `--source` 说明

**Files:**
- Modify: `docs/DEPLOYMENT.md:50`
- Modify: `docs/DEPLOYMENT.zh-CN.md:50`

**Interfaces:**
- Consumes: Task 2 锚点 `#check-your-own-playlist`、Task 3 锚点 `#检测自己的播放列表`、Task 1 的本地文件行为。
- Produces: 无（文档终点）。

- [ ] **Step 1: 修改 DEPLOYMENT.md 第 50 行段落**

把：

```markdown
`configure.py --source URL` can replace the default direct subscription; repeat it for multiple subscriptions. The catalogue source is still appended for the full pipeline.
```

替换为：

```markdown
`configure.py --source URL` can replace the default direct subscription; repeat it for multiple subscriptions. `--source` also accepts an existing local playlist file, stored as a `file://` URL; a value that is neither a URL nor an existing file is rejected. See [check your own playlist](../../../README.md#check-your-own-playlist) for the import path. The catalogue source is still appended for the full pipeline.
```

（该行其余内容——FFmpeg 路径、`catalog.*` 配置项——保持不变。）

- [ ] **Step 2: 修改 DEPLOYMENT.zh-CN.md 第 50 行段落**

把：

```markdown
`configure.py --source URL` 可替换直接订阅，多次填写可加入多个订阅；完整流程仍附带目录源。
```

替换为：

```markdown
`configure.py --source URL` 可替换直接订阅，多次填写可加入多个订阅；`--source` 也接受已存在的本地播放列表文件，写入配置时转为 `file://` 地址；既非 URL 也非存在文件的值会被拒绝。导入路径见[快速开始](../../../README.zh-CN.md#检测自己的播放列表)。完整流程仍附带目录源。
```

（该行其余内容保持不变。）

- [ ] **Step 3: 链接自检**

确认 `../README.md#check-your-own-playlist` 与 `../README.zh-CN.md#检测自己的播放列表` 两个锚点在 Task 2/3 产出的小节标题上（GitHub 对中文标题的锚点即标题原文）。

- [ ] **Step 4: 提交**

```bash
git add docs/DEPLOYMENT.md docs/DEPLOYMENT.zh-CN.md
git commit -m "$(cat <<'EOF'
Document local playlist import in deployment guides

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 全量验证 + PROGRESS.md 记录

**Files:**
- Modify: `PROGRESS.md`（`## Current work / 当前工作` 节顶部加一段）

**Interfaces:**
- Consumes: Task 1–4 的全部产出。
- Produces: 无。

- [ ] **Step 1: 全量测试**

```bash
python -m unittest discover -s tests -v
```

预期：原有全部通过/跳过 + `tests.test_configure_script` 3 项通过。

- [ ] **Step 2: 发布审计（含文档链接检查）**

```bash
python scripts/check_publication.py
```

预期：`PUBLICATION_AUDIT_OK ... secrets=0 broken_local_links=0`（新加的 README/DEPLOYMENT 锚点链接在此验证）。

- [ ] **Step 3: 端到端冒烟：本地 M3U 配置生成 + 配置校验**

```bash
cd /tmp && printf '#EXTM3U\n#EXTINF:-1,Demo\nhttp://example.invalid/stream\n' > demo.m3u
cd "F:\Codex Project\iptv-QA"
python scripts/configure.py --output build/smoke-config.json --data-dir build/smoke-data --force --source /tmp/demo.m3u
python iptv_checker.py --config build/smoke-config.json --check-config
```

预期：configure 输出成功；生成的 JSON 中 `sources[0].url` 为 `file:///.../demo.m3u`；`--check-config` 通过（不依赖 ffmpeg，验证配置可被主程序接受）。完成后删除 `build/smoke-config.json` 和 `build/smoke-data`。

- [ ] **Step 4: 更新 PROGRESS.md**

在 `## Current work / 当前工作` 节最上方（`### Unified channel library` 之前）插入：

```markdown
### Onboarding usability — README two-path quick start / 上手易用性：README 双路径快速开始（2026-11-18）

- Implemented / 已实现：README (en/zh) quick start now leads with "Check your own playlist" (`configure.py --source` + `check`, skipping catalogue discovery), keeps the full-pipeline `run` path with scale/time expectations, and adds a plain "What it does / not included" feature list plus first-results timing (30–60 minutes). `configure.py --source` accepts local playlist files (converted to `file://` URLs) and rejects values that are neither URL nor existing file; paired DEPLOYMENT guides cross-link the import path. / README 双语快速开始改为"检测自己的源"（`--source` + `check`，跳过目录发现）与完整流水线两条路径，新增功能清单与首轮 30–60 分钟时间预期；`--source` 支持本地文件（转 `file://`）并拒绝无效值，部署文档双向链接。
- Verification / 验证：new `tests/test_configure_script.py` (3 cases: local file → file:// URI, http URL passthrough, invalid value rejected) passes; full `python -m unittest discover -s tests -v` passes with no regressions; `scripts/check_publication.py` reports zero secrets and zero broken links; local-M3U smoke run generated a config whose source resolved to a `file://` URI and passed `iptv_checker.py --check-config`. Documentation only beyond `scripts/configure.py` — no detection, ranking or publication behavior changed; no production deployment touched. / 新增三例配置脚本测试通过、全量单测无回归、发布审计零密钥零断链；本地 M3U 冒烟生成 file:// 源并通过配置校验。除 configure.py 外纯文档改动，未触碰检测、发布与生产部署。
```

（日期与证据按实际运行输出核对后填写；若 Step 1–3 结果与预期不符，先修复再记录。）

- [ ] **Step 5: 提交**

```bash
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
Record onboarding usability work in progress

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review 结果

- **规格覆盖**：规格 7 项改动 → Task 1（代码+测试）、Task 2/3（README 双语）、Task 4（DEPLOYMENT 双语）、Task 5（PROGRESS + 验收标准全部落在 Step 1–3）。无缺口。
- **占位符扫描**：无 TBD/TODO；所有代码与文档内容均给出全文。
- **类型/命名一致性**：`normalize_source` 在 Task 1 定义并仅 Task 1 使用；锚点 `#check-your-own-playlist` / `#检测自己的播放列表` 由 Task 2/3 产出、Task 4 消费，一致。
