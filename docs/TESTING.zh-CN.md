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

GitHub Actions 在 Linux 上验证 Python 3.10、3.12、3.14，真实媒体样本在 Python 3.12 执行；兼容性验证还覆盖 Python 3.6.8 和 FFmpeg 7.0.2。新部署建议使用仍受维护的系统和 Python，已验证结果见[验证记录](VALIDATION.json)。

整理公开项目前，现用版本通过了 57 项 Linux 回归测试。公开版另加 OBS 可选配置、本地订阅网页和广播分类测试。实际最新测试数和发布验证以 [PROGRESS](../PROGRESS.md) 为准。

## 回归覆盖

回归测试覆盖纯音频拒绝、带视频画面的明确电台排除、正常电视名称例外、停滞或过期 HLS、重连失败、发布保护和无云存储运行。这些受控样本用于验证策略，不是随机频道抽样，也不是实播准确率评估。

## 边界

单元测试证明规则实现一致，不证明终端兼容、高峰稳定、频道内容正确或某个实播成功率。后续应以常看频道的接收设备反馈，统计误放行和误淘汰，再校准阈值。公开证据不得含操作者的地址和令牌。


## Module and package validation / 模块与打包验证

模块回归另见 `tests/test_modules.py`：独立命令、导入边界、维护锁、纯放映路由和资源回收。打包验证：先 `python3 -m pip wheel . --no-deps --wheel-dir dist/wheels`，再 `python3 scripts/validate_package.py --wheel dist/wheels/iptv_qa-0.2.0-py3-none-any.whl`。该验证从独立临时目录加载构建产物，检查网页资源和兼容入口。

## 分层可播回归

`tests/test_tiers.py` 覆盖瞬时失败降级、硬性排除、独立音频时长、稳定优先排序及发布策略迁移。`scripts/validate_quality.py --bin-dir PATH` 还用真实音视频 HLS 验证分层发布、代理取流及无音轨视频的拒绝。测试夹具通过不等于所有接收设备播放保证。

运行 `node tests/player_navigation.test.js`，验证浏览器端备用耗尽后顺序换台、超过15台的遍历、随机不重复、迟到回调隔离、超时换台、五线竞速、起播清理预算与自动播放提示处理，不依赖外部直播。
