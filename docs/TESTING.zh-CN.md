# 测试与验证

[English](TESTING.md) | [简体中文](TESTING.zh-CN.md) · [首页](../README.zh-CN.md)

## Linux 复现

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_media.py --bin-dir /usr/bin
python3 scripts/validate_quality.py --bin-dir /usr/bin
python3 scripts/check_publication.py
```

静态媒体工具使用 `--bin-dir DIRECTORY` 或 `IPTV_FFMPEG_DIR`。单元测试使用模拟对象、本地 HTTP、SQLite 和子进程，不会下载随机公开订阅，也不会连接真实 OBS。真实媒体测试自行生成音视频和实时 HLS，不依赖公网电视台当时可用。

`validate_media.py` 验证真实解码、提前结束、短文件拒绝、完整 Top3/Top5 发布，以及零结果保留旧版。`validate_quality.py` 验证纯音频拒绝、真实视频帧、实际观察时长、两次独立连接、HLS 更新和完整发布。测试配置缩短观察及等待时间；正式默认仍为三十秒和一千八百秒。

## 环境

参考测试地点：**阿联酋阿布扎比**。部署案例使用 4 vCPU、约 15 GiB 内存、Linux/CentOS 7、Python 3.6.8、FFmpeg/ffprobe 7.0.2。新部署不应为了复现而采用停止维护的系统。项目提供现代 Python 的 GitHub Actions 矩阵；存在工作流文件不代表某次远端 CI 已经通过。

整理公开项目前，现用版本通过了 57 项 Linux 回归测试。公开版另加 OBS 可选配置、本地订阅网页和广播分类测试。实际最新测试数和发布验证以 [PROGRESS](../PROGRESS.md) 为准。

## 现场样本如何解释

曾有目的地选择十六个旧 Top1，包含已知纯音频和不同编码：四个只返回音频，一个媒体读取错误；修正缓冲判断后，四个被选作持续观察的视频源通过三十秒测试，另验证两个 HLS 源分片持续推进。该样本不是随机抽样，**不能由此计算总体准确率**。

旧版已发布 set1 的 1579 个频道中，有 495 个首选地址被记录为仅音频。原始文件第 1094 条与用户界面显示的广播分界不一致，客户端可能有不同分组或顺序。根因是媒体策略接受音频，不是某个数字之后必须删除。新代码正在跑，与旧 OBS 文件已更新，是两个不同状态，必须分别验证。

## 边界

单元测试证明规则实现一致，不证明终端兼容、高峰稳定、频道内容正确或某个实播成功率。后续应以常看频道的接收设备反馈，统计误放行和误淘汰，再校准阈值。公开证据不得含操作者的地址和令牌。
