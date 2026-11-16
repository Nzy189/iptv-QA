# 部署配置

[English](DEPLOYMENT.md) | [简体中文](DEPLOYMENT.zh-CN.md) · [首页](../README.zh-CN.md)

## 依赖及直接运行

正式环境使用 Linux、Python 3 和 FFmpeg/ffprobe；参考版本为 FFmpeg 7.x，视频流程需要支持 `-fps_mode`。核心检测只使用 Python 标准库。旧服务器验证过 Python 3.6.8 的兼容性，新部署建议使用仍受维护的系统和 Python。

```bash
python3 scripts/configure.py --output config.local.json --data-dir ./data
python3 iptv_checker.py --config config.local.json --check-config
python3 daily.py --config config.local.json
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

## 网页电视播放器

仪表盘内置密码门禁的网页电视，路径 `/tv/`：加载订阅后提供频道列表、搜索、上一台/下一台/随机、遥控器式台号输入、全屏和手机布局。源选择栏可直接点选本页发布的最新订阅（Set1/Set2/Set3/Top3），也可粘贴任意 m3u 链接。

频道失败时自动按 **Set1 → Set2 → Set3** 逐套尝试备用源（三套频道名与顺序对齐；识别方式为订阅链接末段文件名）。随机按钮旁有模式开关：**普通模式**在所有套都失败后显示错误；**增强模式**在此基础上 10 秒无数据即跳下一套，全部失败后自动随机换台重试整个链路（最多 15 次），直到找到可播频道。

服务端代取订阅并同源代理 HLS 清单、分片、密钥与音轨（保留每频道 `User-Agent`/`Referer` 选项），浏览器用内置 hls.js 播放。**默认不转码**：只有浏览器解不了编码或"有声音无画面"时，前端自动请求服务器按需转码（FFmpeg 转 H.264/AAC，封顶 720p/2Mbps）；最多 2 路并发，闲置 90 秒自动停止，不占用检测资源。纯音频频道会明确提示"只有声音，没有画面"。

设置访问密码（不设则播放器自动禁用）：

```bash
sudo install -m 0600 config/tv.env.example /etc/iptv-qa/tv.env
# 编辑该文件，将 IPTV_TV_PASSWORD 改为真实密码；需要转码时可设 IPTV_TV_FFMPEG 指向 ffmpeg 路径。
sudo systemctl restart iptv-qa-web.service
```

安全边界：密码错误会延迟一秒并返回 401；会话 Cookie 为 HttpOnly、SameSite=Strict，服务重启后失效；代理只转发已加载清单中发现过的地址，不能当作任意代理；云元数据地址被拒绝。密码不要外传，生产环境建议配合防火墙限制来源 IP。
