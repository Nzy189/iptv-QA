<div align="center">

# iptv-QA

### 检测优选，订阅发布，浏览器直接看。

**真实视频检测 · 多套精选订阅 · 网页放映 · 模块独立运行**

[English](README.md) | [简体中文](README.zh-CN.md)

[![Tests](https://github.com/Nzy189/iptv-QA/actions/workflows/ci.yml/badge.svg)](https://github.com/Nzy189/iptv-QA/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Nzy189/iptv-QA)](https://github.com/Nzy189/iptv-QA/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[**频道订阅**](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_ok.m3u) · [**下载 iptv-QA**](https://github.com/Nzy189/iptv-QA/releases/latest)

[快速开始](#快速开始) · [订阅与下载](#订阅与下载) · [使用文档](#使用文档) · [项目进度](PROGRESS.md)

</div>

iptv-QA 将分散的 IPTV 来源汇总、去重、检测和排序，自动生成适合播放器导入的频道订阅。通过真实视频解码、直播更新检查和间隔重连复检，帮助筛掉失效、纯音频和表现不稳定的候选线路。

部署到自己的服务器，把结果导入 M3U 播放器，或直接使用内置网页放映；后续更新交给定时任务。

分层检测区分本轮音视频可播与稳定性：Top3/Top5/三套优先两次深检通过的源，再保留基础可播备用；`iptv_stable.m3u` 为纯稳定精选。详见[检测分层](docs/DESIGN.zh-CN.md#可播与稳定分层)。

可选[频道管理](docs/LIBRARY.zh-CN.md)提供统一总表、五线路播放、可恢复清理、版本化发布和私有加密备份。

## v0.2.0 性能改进

| 改进 | 效果 |
| --- | --- |
| **多源竞速起播** | 点开频道时全部线路（最多五条）同时加载，先出画面的一条立即播放，其余马上释放——换台等待从"碰运气"变成"取最快"。 |
| **慢启动频道不再误杀** | 深检起播预算从 12 秒放宽到 30 秒：起播慢但播放稳定的频道得以保留；起播速度只影响排序，不再一票否决。 |
| **统一频道总表** | 频道身份与线路分开管理、版本化发布、可恢复清理和加密备份，五线路播放一目了然。 |

## 功能清单

- 真实视频检测：解码画面、观察媒体推进、HLS 播放列表更新检查，以及间隔至少 30 分钟的新连接复验。
- 去重与频道合并，按起播速度、当次表现和历史可靠性对线路排名。
- 多套排名订阅：Top3、Top5、三套单线路、纯稳定清单和独立英语频道。
- 浏览器放映：频道搜索、多线路竞速、备用自动回退和按需 H.264/AAC 转码。
- 无人值守运行：定时任务、按主机并发控制、失败重试和发布保护。
- 结果可见：仪表盘、CSV/JSON 历史导出，以及可选的华为 OBS 固定地址上传。

**不包含：** 不保证不间断播放、不内置固定频道库、不做 EPG、不提供付费/DRM 源的访问。播放效果取决于上游、网络和接收设备，能力边界见[设计文档](docs/DESIGN.zh-CN.md#现有边界)。

## 为什么选择 iptv-QA？

| 优势 | 带来的价值 |
| --- | --- |
| **真正检查视频** | 实际解码画面、观察视频推进，避免只凭地址能响应就判断可用。 |
| **换台更快** | 多源竞速让多条线路同时起播、先出画面先用，无需逐条等待。 |
| **频道更整齐，选择更简单** | 合并重复频道，结合起播、当次表现和历史可靠性优选线路。 |
| **主用与备用一次备齐** | 一张频道清单按需导出完整、精简和单线路视图，主用备用都齐。 |
| **自带精选分组** | 可选附加运维者精选的分组（例如国际新闻）到 Top3 与三套，无需重新检测。 |
| **电视与广播分开处理** | 过滤明确电台和纯音频电视候选，英语频道独立输出。 |
| **自动完成日常维护** | 支持定时运行、按主机控制并发、失败重试和发布保护。 |
| **筛选过程看得见** | 网页和历史报告展示来源贡献、失败原因及结果变化。 |
| **本地可用，云端可选** | 不依赖对象存储即可运行；需要固定订阅地址时，可配置华为 OBS 自动覆盖上传。 |

## 订阅与下载

### 一张清单，多种视图

所有订阅文件都是同一份频道清单的不同视图：主订阅带全部排名线路，其余视图只是按场景保留更少的线路。内置播放器提供三个入口——综合频道（主视图，每频道最多五条排名线路，自动竞速选线）、今日补充和英语频道。

点击文件下载，或复制链接到支持 M3U 订阅的播放器。

| 视图 | 每频道线路 | 适合什么场景 | 文件入口 |
| --- | --- | --- | --- |
| **主订阅** | 最多 5 条 | 日常使用：备用线路齐全，播放器自动选线 | [iptv_ok.m3u](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_ok.m3u) |
| **精简订阅** | 最多 3 条 | 列表更短，适合轻量播放器 | [iptv_top3.m3u](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_top3.m3u) |
| **单线路兼容版** | 1 条 | 老设备或只认单线路的播放器 | [Set 1](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_set1.m3u) · [Set 2](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_set2.m3u) · [Set 3](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_set3.m3u) |
| **英语频道** | 最多 3 条 | 英语频道单独使用 | [iptv_english.m3u](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_english.m3u) |
| **检测报告** | — | 查看最近发布结果与历史记录 | [iptv_summary.html](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_summary.html) |

各视图覆盖同一批入选频道，区别只是每频道保留的线路数量；单线路版是给老设备的兼容导出，内容与主订阅的第一名一致。主订阅指频道清单的完整视图，不是未经检测的原始候选大包。备用不足时，单线路版复用最后一条可用源。

这些链接提供最近一次已发布快照；新任务执行期间，文件可能落后于当前程序规则，请通过报告确认发布时间和状态。源码不附带固定频道库，自行部署时可使用自己的来源和输出地址。

<details>
<summary>更多输出及接入说明</summary>

- [CSV 历史](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_history.csv)和 [JSON 历史](https://temppa.obs.me-east-1.myhuaweicloud.com/iptv_history.json)可用于分析和程序接入。
- 本地 Top5 文件名为 `iptv_top5.m3u`，默认公开文件名为 `iptv_ok.m3u`。
- 不启用 OBS 时，英语结果位于 `data/english/published/iptv_top3.m3u`，网页以 `iptv_english.m3u` 提供。
- 中英文结果分阶段发布，详细配置见[部署文档](docs/DEPLOYMENT.zh-CN.md)。

</details>

## 它如何工作？

**采集 → 去重 → 探测 → 解码 → 持续观察 → 间隔重连 → 排名 → 发布**

入围候选需要同时满足至少 30 秒视频时间推进和实际观察，再至少间隔 30 分钟，用新连接重新验证；HLS 还会检查播放列表是否持续更新。两次深检通过的源进入稳定优选；分层模式保留本轮基础音视频可播源作为低优先级备用，严格模式仍要求所有入选源两次深检通过。

结果为零或数量异常下降时，发布保护会保留上一版，避免坏结果覆盖已有订阅。完整规则与可调参数见[设计文档](docs/DESIGN.zh-CN.md)。

## 按需使用五个模块

**来源采集 → 质量检测 → 订阅发布**，配合独立的**结果报告**和**浏览器放映**。只想看电视，可以直接导入 M3U；只想筛源，无需运行播放器；OBS 上传可选。查看[架构与目录说明](docs/ARCHITECTURE.zh-CN.md)。

| 想做什么 | 命令入口 |
| --- | --- |
| 只收集候选 / 只检测 | `catalog` / `check` |
| 上传已有结果 / 重建报告 | `publish` / `report` |
| 只看报告 / 只放映 | `dashboard` / `play` |
| 报告和放映共用端口 / 整轮定时任务 | `serve` / `run` |

播放器支持频道搜索、数字换台、随机播放、备用线路自动回退，以及按需 H.264/AAC 转码。详情见[放映指南](docs/PLAYBACK.zh-CN.md)。

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

定时任务与可选 OBS 上传见[部署配置](docs/DEPLOYMENT.zh-CN.md)。

### 不检测，直接看

浏览器放映不依赖检测结果，导入任意 M3U 即可观看：

```bash
export IPTV_TV_PASSWORD='choose-a-private-password'
python3 -m iptv_qa play --port 8089
```

访问 `http://127.0.0.1:8089/tv/`。FFmpeg 仅在需要回退转码时使用。源码运行无需安装 Python 依赖；也可用 `python3 -m pip install .` 安装命令和完整网页资源。

## 使用文档

| 了解更多 | 中文 | English |
| --- | --- | --- |
| 检测、排序与参数 | [设计思路](docs/DESIGN.zh-CN.md) | [Design](docs/DESIGN.md) |
| 安装、定时与 OBS | [部署配置](docs/DEPLOYMENT.zh-CN.md) | [Deployment](docs/DEPLOYMENT.md) |
| 来源发现与登记 | [来源说明](docs/SOURCES.zh-CN.md) | [Sources](docs/SOURCES.md) |
| 测试与复现 | [测试说明](docs/TESTING.zh-CN.md) | [Testing](docs/TESTING.md) |
| 项目架构与模块边界 | [架构说明](docs/ARCHITECTURE.zh-CN.md) | [Architecture](docs/ARCHITECTURE.md) |
| 浏览器放映与转码 | [放映指南](docs/PLAYBACK.zh-CN.md) | [Playback](docs/PLAYBACK.md) |

[项目进度](PROGRESS.md) · [后续计划](ROADMAP.md) · [安全说明](SECURITY.md) · [来源清单](sources/registry.json)

iptv-QA 提供筛选、发布、报告和浏览器放映，实际播放仍取决于上游、网络及接收设备。能力边界见[设计文档](docs/DESIGN.zh-CN.md#现有边界)。软件使用 [MIT 许可证](LICENSE)，上游列表与媒体流保留各自条款。
