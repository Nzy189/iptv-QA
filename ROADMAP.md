# Roadmap / 后续计划

[English home](README.md) | [中文首页](README.zh-CN.md) · [Current progress / 当前进度](PROGRESS.md)

Released capabilities must be distinguished from plans. / 以下计划不能当作当前已经实现。

- Separate audio/video decoding, A/V timestamp drift, and receiver-aware codec/rendition validation. 音画双检、同步和终端兼容。
- Longer observation for frequently watched Top1 channels and labelled receiver comparisons. 常看 Top1 长测及实播对照。
- Black/freeze/silence flags with repeated verification instead of automatic deletion on a single static scene. 黑屏、冻结和静音标记及复查。
- Source contribution and confidence-aware historical ranking. 来源贡献和考虑样本数量的可靠性排序。
- Optional small peak-hour samples; no automatic change to the existing three-night schedule. 可选高峰抽查，不擅自改变三晚调度。
- Improve the implemented browser player: restart persistence, deployment-specific transcode profiles and receiver compatibility. 已有网页放映后续优化：重启会话保持、转码档位和终端兼容；见[放映指南](docs/PLAYBACK.zh-CN.md)。
