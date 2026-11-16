# 公开来源说明

[English](SOURCES.md) | [简体中文](SOURCES.zh-CN.md) · [首页](../README.zh-CN.md)

机器可读登记表为 [sources/registry.json](../sources/registry.json)。登记只代表找到入口，不代表每条流都能播放。测试场景：阿联酋阿布扎比；资料核对日期：2026-11-16。

| 来源 | 状态 | 用法 |
| --- | --- | --- |
| [zbds 公开列表](https://live.zbds.top/tv/iptv4.m3u) | 配置中的直接输入 | 下载、归并、实测 |
| [Tvlist-awesome](https://github.com/imDazui/Tvlist-awesome-m3u-m3u8) | 已接入目录 | 下载归档，只读取列表文字，不解压执行仓库代码 |
| [iptv-org 普通总表](https://iptv-org.github.io/iptv/index.m3u) | 已接入目录 | 中英文分流；国家/语言子列表可能与总表重复 |
| [iptv-org 频道资料](https://iptv-org.github.io/api/channels.json) | 辅助资料 | 频道 ID、国家和标签用于推断 |
| [Free-TV/IPTV](https://github.com/Free-TV/IPTV) | 待测候选 | 提供公开列表，默认未开启，尚未在此完成实播验证 |
| [CGTN 官方直播](https://www.cgtn.com/tv) | 官方网页候选 | 需要适当的公开播放适配，网页本身不是稳定 M3U 地址 |

原始目录快照读取仓库 224 个文件及一份普通总表，共 69,088 条 EXTINF、50,973 个原始去重地址。语言规则识别出 31,976 个中文候选地址和 464 个英语候选地址。这些是**待测输入**，不是实测可用电视台；后续分流和视频检查会继续减少数量。

`catalog.playlist_urls` 可以补充公开列表，`sources` 可以加入直接订阅。私人或付费订阅只放入被 Git 忽略的配置；发布包不包含私人或聚合原始播放地址快照。

## 广播与内容策略

旧实现允许纯音频通过，导致 FM/广播混入电视列表。当前公开版采用两道门槛：明确的电台标签、名称或分组排除；媒体探测必须有真实视频。即使电台带编码封面画面，也不应仅凭“存在视频轨道”重新进入电视列表。不能将 TVB 或音乐电视误删为电台。标签仍可能含混，需要结合媒体证据和实播反馈。

中文进入主列表，英语独立输出；默认排除其他语言、语言不明及 NSFW。频道资料可能缺失或过时，国家/语言推断不是语音内容识别。

## 工具不等于新源库

- [fanmingming/live](https://github.com/fanmingming/live)：当前说明侧重台标、EPG 和工具，不能算已验证的新源池。
- [Guovin/iptv-api](https://github.com/Guovin/iptv-api)：聚合和检测工具，其教程要求自行提供订阅。
- [zhimin-dev/iptv-checker](https://github.com/zhimin-dev/iptv-checker)：参考检测实现，不是播放列表来源，也不是本项目运行依赖。

同一地址出现在多个仓库仍然只有一个上游。来源评估应比较**新增去重地址、新增可用频道和最终入选贡献**。上游数据与节目流有各自条款，本项目 MIT 许可证只覆盖软件。
