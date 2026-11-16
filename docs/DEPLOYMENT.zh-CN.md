# 部署配置

[English](DEPLOYMENT.md) | [简体中文](DEPLOYMENT.zh-CN.md) · [首页](../README.zh-CN.md)

## 独立模块

部署必须包含整个 `iptv_qa/` 包（含 `playback/assets/`），不要只拷贝旧根目录脚本。可运行源码，也可 `python3 -m pip install .` 安装。旧脚本保持兼容，推荐新命令明确表达用途。

```bash
python3 -m iptv_qa catalog --config config.local.json
python3 -m iptv_qa check --config config.local.json
python3 -m iptv_qa publish --config config.local.json
python3 -m iptv_qa report --data-dir ./data
python3 -m iptv_qa dashboard --data-dir ./data --port 8088
# 先在私有服务环境设置 IPTV_TV_PASSWORD
python3 -m iptv_qa play --data-dir ./data --port 8089
# 或使用单端口共用模式
python3 -m iptv_qa serve --data-dir ./data --port 8088
```

`catalog` 只采集；`check` 检测配置中的源并按配置发布；`run` 才是完整采集及中英文检测流程。`publish` 只重传已验证的当前导出，不读源、不重检；仅上传英语时加 `--scope english`。`report` 无需源配置或媒体工具。未开启 OBS 时上传入口返回未配置状态。

HTTP 部署二选一：原 `iptv-qa-web.service` 保留报告与放映共用 8088；或用 `iptv-qa-dashboard.service`（8088）加 `iptv-qa-player.service`（8089）拆开运行。不要同时开启占用同一端口的共用服务和报告服务。单独放映也支持外部清单，无需本地检测数据。新模板转码参数默认并发 2、空闲 90 秒；通过命令参数调整资源，不继承纯报告服务的低 CPU 配额。

播放器密码来自私有 `tv.env`（参考 `config/tv.env.example`），不提交到 Git；FFmpeg 可经 `IPTV_TV_FFMPEG` 或 `--ffmpeg` 指定。`dashboard` 明确不加载播放器；`serve` 未设置密码时只提供报告；`play` 缺少密码时拒绝启动。报告页面不自动继承播放器鉴权。完整行为及边界见[放映指南](PLAYBACK.zh-CN.md)。

这里只更新源码及服务模板，不会自动重启已有部署。升级先在独立目录验证，然后由部署者选择服务模式、端口和环境配置。


## 依赖及直接运行

正式环境使用 Linux、Python 3 和 FFmpeg/ffprobe；参考版本为 FFmpeg 7.x，视频流程需要支持 `-fps_mode`。核心检测只使用 Python 标准库。旧服务器验证过 Python 3.6.8 的兼容性，新部署建议使用仍受维护的系统和 Python。

```bash
python3 scripts/configure.py --output config.local.json --data-dir ./data
python3 iptv_checker.py --config config.local.json --check-config
python3 -m iptv_qa run --config config.local.json
```

`configure.py --source URL` 可替换直接订阅，多次填写可加入多个订阅；完整流程仍附带目录源。静态 FFmpeg 请修改配置中的路径。`catalog.archive_url`、`catalog.metadata_url`、`catalog.playlist_urls` 控制发现输入；需要测试 Free-TV 时再明确加入其地址。

## 可选 OBS 上传

默认不启用 OBS，也不需要安装 SDK。启用时隔离安装依赖：

```bash
python3 -m pip install --target ./obs-sdk -r requirements-obs.txt
export PYTHONPATH="$PWD/obs-sdk${PYTHONPATH:+:$PYTHONPATH}"
```

私有 JSON 配置示例：

```json
{
  "obs": {
    "enabled": true,
    "region": "me-east-1",
    "endpoint": "",
    "bucket": "YOUR_BUCKET_NAME",
    "prefix": "iptv",
    "object_names": {"iptv_top5.m3u": "iptv_ok.m3u"},
    "publish_sets": true,
    "upload_report": false,
    "upload_retries": 3,
    "cache_control": "max-age=300",
    "public_base_url": ""
  }
}
```

明确填写 HTTPS Endpoint 时优先使用它；否则由区域生成 `https://obs.REGION.myhuaweicloud.com`。前缀为空表示桶根目录。`playlist_files` 可限制上传文件；英文流程只选 Top3 并映射为 `iptv_english.m3u`。`object_names` 的值只能是简单文件名。`public_base_url` 覆盖 HTML 的公开访问基址；默认导航采用文档中的标准文件名，自定义改名时应由维护者提供相应链接。

密钥放在仓库以外：

```bash
sudo install -d -m 0750 /etc/iptv-qa
sudo install -m 0600 config/obs.env.example /etc/iptv-qa/obs.env
# 在文件里填写 OBS_ACCESS_KEY_ID 和 OBS_SECRET_ACCESS_KEY。
# 临时凭据可增加 OBS_SECURITY_TOKEN。
```

systemd 会加载该文件；手动执行时使用环境变量，避免将值写入命令历史。程序不会创建桶、开放访问或修改 ACL。公开读取方式由部署者设置。TLS 校验使用 certifi。九个对象逐个覆盖，部分失败时记录错误，可用 `--upload-only` 重试。

## 精选国际新闻分组

在主机上放置精选 M3U 文件，并在配置中设置 `international_news.path`，每次发布时其条目会作为一个分组（默认名 `International News`，可用 `group_title` 覆盖）附加到 Top3 与三套。文件由运维者精选，检测器不会重新验证；文件缺失仅记录告警，其余内容照常发布。`iptv-qa-sets.service` 可不重测、直接从当前 Top5 刷新三套；Top3 在下一次完整运行时带上该分组。

## systemd 部署

已有生产检测时，不要让第二个服务共用其数据目录。公开项目的目录名与原私有部署分开。

```bash
sudo useradd --system --home-dir /var/lib/iptv-qa --shell /sbin/nologin iptv-qa
sudo install -d -m 0755 /opt/iptv-QA
# 将仓库源码放入 /opt/iptv-QA，排除 .git 和私有配置。
sudo install -d -o iptv-qa -g iptv-qa -m 0750 /var/lib/iptv-qa
sudo install -d -o root -g iptv-qa -m 0750 /etc/iptv-qa
sudo install -o root -g iptv-qa -m 0640 config/config.example.json /etc/iptv-qa/config.json
# 启动前修改源地址、媒体工具路径及可选 OBS 参数。
sudo cp systemd/*.service /etc/systemd/system/
sudo cp systemd/iptv-qa.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iptv-qa.timer
sudo systemctl start --no-block iptv-qa.service
```

可选 SDK 放在 `/opt/iptv-QA/obs-sdk`。服务允许使用全部 CPU，示例内存限制为 12 GiB，小机器应调整。`TimeoutStartSec=0` 关闭整轮 oneshot 超时，兼容 systemd 219；单地址仍有超时保护。文件锁防止重叠运行，执行中的服务不会额外排队一轮。

### 定时

示例调度为每周一、周三、周日 **UTC 00:00**，可根据部署需求和时区调整。正常模板明确指定 UTC。systemd 219 不支持时区后缀；仅在宿主机为 `Asia/Shanghai` 时，可将 `systemd/iptv-qa.timer.cst-legacy.example` 安装为 `iptv-qa.timer`。其他旧版宿主时区必须重新换算，部署后检查：

```bash
systemctl list-timers iptv-qa.timer --all
systemctl show iptv-qa.service -p MainPID -p ActiveState
cat /var/lib/iptv-qa/status.json
journalctl -u iptv-qa.service -n 40 --no-pager
```

## 网页与维护

网页服务默认绑定 localhost:8088，没有内置鉴权；需要远程访问时，由部署者明确配置带鉴权的反向代理。没有 `--public-base` 时，允许的订阅下载可能带上游令牌；网页不会开放配置、环境变量、数据库和原始 JSONL。

```bash
sudo systemctl enable --now iptv-qa-web.service
sudo systemctl start iptv-qa-upload.service
sudo systemctl start iptv-qa-summary.service
sudo systemctl start iptv-qa-sets.service
```

详细记录默认保留十四天，当前发布版本和 SQLite 汇总长期保留。私有配置及运行数据单独备份。升级前先读 [PROGRESS](../PROGRESS.md)。
