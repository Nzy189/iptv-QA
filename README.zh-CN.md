# iptv-QA

[English](README.md) | [简体中文](README.zh-CN.md)

[项目进度 / Progress](PROGRESS.md) · [网页播放器可行性](docs/WEB_PLAYER_FEASIBILITY.zh-CN.md)

通过真实视频解码、间隔复检和历史可靠性，筛选 IPTV 直播源，生成 Top3、Top5、三套单线路订阅及检测页面。**华为 OBS 上传是可选配置，默认关闭。**

**测试地点：阿联酋阿布扎比。** 部署案例为 4 vCPU、约 15 GiB 内存、Linux、Python 3.6.8、FFmpeg 7.0.2。测试结果只代表这个网络观察点，不能保证所有运营商、Apple TV 型号和播放器都能播放。新部署建议使用仍受维护的 Linux 和 Python。

## 现有能力

- 下载订阅和公开源目录，按地址及请求头去重，合并同名频道。
- 中文进入主列表；英语单独输出；默认排除其他语言、语言不明及 NSFW 条目。
- ffprobe 读取媒体信息；电视源必须有真实视频，纯音频和封面图不进入电视列表。
- 初筛后，每频道先对最多 10 个候选做深检；不足 5 个通过时继续检查剩余候选。
- 深检同时要求至少 30 秒实际观察和视频时间推进，HLS 长分片会自动延长观察窗口。
- 检查 HLS 是否继续更新，至少间隔 30 分钟重新连接；两次通过才能发布。
- 综合起播快慢、重试、历史可靠性、起播耗时档位、分辨率和总检测耗时排序，尽量选择不同主机的备用线路。
- 本地生成结果与历史网页；可选上传 OBS，不需要云存储也能运行。
- 服务可独立运行，提供每周一、周三、周日 **阿布扎比 04:00 / UTC 00:00 / 北京 08:00** 的定时配置。

## 快速开始

正式运行需要 Linux 的文件锁和原子符号链接。先通过系统包管理器安装 Python 和 FFmpeg：

```bash
git clone https://github.com/Nzy189/iptv-QA.git
cd iptv-QA
python3 scripts/configure.py --output config.local.json --data-dir ./data
python3 iptv_checker.py --config config.local.json --check-config
python3 daily.py --config config.local.json
```

完整流程可能运行数小时。`daily.py` 先同步公开目录，再检测中文，最后处理英文。只检查配置中的订阅时，直接运行 `iptv_checker.py`；添加 `--limit 20` 可做小样本诊断，诊断不会发布文件或更新历史可靠性。

首次检测生成报告后，启动网页：

```bash
python3 web.py --data-dir ./data --host 127.0.0.1 --port 8088
```

访问 `http://127.0.0.1:8088/`。未设置 `--public-base` 时，网页读取本地允许的六个订阅文件；设置 `--public-base https://你的公开域名/前缀` 后，订阅入口跳转到该地址。网页当前为中文界面，项目文档提供中英文版本。

## 输出文件

| 文件 | 内容 |
| --- | --- |
| `iptv_ok.m3u` | 中文 Top5，每频道最多五条通过验证的源 |
| `iptv_top3.m3u` | 每频道 Top5 的前三条 |
| `iptv_set1.m3u` | 每频道一条，使用第 1 名 |
| `iptv_set2.m3u` | 每频道一条，使用第 2 名 |
| `iptv_set3.m3u` | 每频道一条，使用第 3 名 |
| `iptv_english.m3u` | 英语频道，每频道最多三条 |
| `iptv_summary.html` | 最新汇总和历史记录 |
| `iptv_history.csv` | 可用表格软件查看的汇总 |
| `iptv_history.json` | 机器可读的汇总 |

备用不足时，三套单线路文件复用最后一条可用源。中文完成后先发布，英语随后发布；九个远端对象不是一次原子事务。内部 `iptv_top5.m3u` 映射为公开文件名 `iptv_ok.m3u`。不启用 OBS 时，英语结果在 `data/english/published/iptv_top3.m3u`，网页以 `iptv_english.m3u` 提供。

## 配置源与 OBS

修改生成的私有 `config.local.json`。[来源登记表](sources/registry.json)区分现有输入、待测候选和参考工具；不包含个人订阅、服务器地址、桶名或密钥。

OBS 的开关、区域、Endpoint、桶名、前缀、文件名映射、重试次数、缓存头和公开访问地址均可配置。AK/SK 只通过环境变量读取；默认关闭时只输出本地文件。详见[部署文档](docs/DEPLOYMENT.zh-CN.md#可选-obs-上传)。

## 文档

| 内容 | 中文 | English |
| --- | --- | --- |
| 设计及边界 | [设计思路](docs/DESIGN.zh-CN.md) | [Design](docs/DESIGN.md) |
| 服务器、定时、OBS | [部署配置](docs/DEPLOYMENT.zh-CN.md) | [Deployment](docs/DEPLOYMENT.md) |
| 已找到的公开来源 | [来源说明](docs/SOURCES.zh-CN.md) | [Sources](docs/SOURCES.md) |
| 测试与复现 | [测试说明](docs/TESTING.zh-CN.md) | [Testing](docs/TESTING.md) |

## 验证与能力边界

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_media.py --bin-dir /usr/bin
python3 scripts/validate_quality.py --bin-dir /usr/bin
python3 scripts/check_publication.py
```

测试证明程序符合既定规则，不等于实播成功率。起播时间是 FFmpeg 进度采样的上界估计，并非 Apple TV 实际出画面时间。当前深检单独验证视频；音频解码、音画同步、黑屏/冻结、频道内容和终端兼容性尚未完整验证。5/12/30 秒是项目经验参数，不是行业统一标准。详见[设计边界](docs/DESIGN.zh-CN.md#现有边界)。

软件使用 MIT 许可证。上游列表和节目流保持各自条款；仓库提供来源引用和配置，不打包个人订阅或媒体内容。[安全说明](SECURITY.md) · [后续计划](ROADMAP.md)。
