# 频道总表与管理

[简体中文](LIBRARY.zh-CN.md) | [English](LIBRARY.md) · [首页](../README.zh-CN.md)

可选频道总表是受管播放和发布的唯一数据来源。启用 `channel_library.enabled`，指定共享 `path`、`data_root`，通过 `--library-config` 或 `IPTV_LIBRARY_CONFIG` 将私有配置传给网页服务。在 `/tv/manage/` 使用播放器密码登录。自定义远程订阅仍为只读输入。

频道使用持久 UUID；别名和完整请求身份辅助匹配。元数据ID按分组建立映射，名称冲突时不盲目合并。同一频道可属于综合、补充、英语多个视图。分组成员和线路各自保留人工启停状态，自动检测可用资格与人工控制分开，再导入不会覆盖清理或停用标记。模糊匹配仍需人工检查。

管理页支持搜索分页、名称和分组修改、线路优先级、线路停用恢复、按分组清理恢复、影响预览、发布任务、版本回退及加密备份请求。写操作带版本检查，旧页面收到409而不是覆盖新数据。服务端检测和浏览器失败分开记录。列表API不返回原始源URL或凭据。

播放器每台显示一行，按优先级最多尝试五条线路；综合入口读取总表Top5视图，Set1–3和Top3保留为兼容输出。首页突出播放和管理，历史及兼容文件折叠，不再突出Top5订阅按钮。当前总表数量与最近一轮检测分开，发布被拦截的检测不会冒充正在提供的全部频道。

毁灭模式默认关闭，仅对受管分组有效。每台五条候选共享20秒起播预算，线路全部失败或预算耗尽时移入当前分组清理区，并排队发布；实际播放成功、自动播放被浏览器拒绝、手动切台或换分组时取消起播清理计时。登录失效或整体断网不触发清理。可在管理页恢复，不修改原始采集来源，自定义订阅不能触发总表清理。

## 迁移和发布

```bash
python3 -m iptv_qa library migrate --config /etc/iptv-qa/config.json
python3 -m iptv_qa library publish --config /etc/iptv-qa/config.json
python3 -m iptv_qa library worker --config /etc/iptv-qa/config.json
```

先在隔离目录迁移验证并保留当前订阅。迁移读取已有主表Top5/Top3、补充Set4和英语输出，不把导入称作重新媒体验证。后续合格QA结果更新总表资格；迁移前仍在运行的旧任务若成功发布，后台可接入，失败和诊断样本不能替换总表。

每次发布生成不可变版本目录，单个发布器串行执行。先生成本地输出，OBS先写版本化对象再写固定对象，脱敏完成清单最后上传。OBS多对象更新不是原子事务，部分成功会保留失败状态并重试同一版本。排队期间总表改变则旧发布请求作废，不能悄悄发布未经复核的后续编辑。回退恢复所选版本的人工控制状态，再生成新发布，不抹掉操作记录，也不会恢复缺少当前检测资格的线路。

## 私有备份与恢复

在后台服务环境安装可选加密依赖 `cryptography`；配置 `channel_library.backup` 的 `enabled`、独立私有 `bucket`、`prefix` 和 `key_path`。凭据放服务环境，不进源码。后台需要OBS SDK；网页只写数据库，不需要桶密钥。

通过SQLite备份接口（旧Python使用事务一致的SQL快照）生成数据库快照，配合校验清单和去密钥配置，用Fernet认证加密；解密密钥不打包。上传前每份备份先实际恢复验证；对象明确设为私有，再检查公开组授权、验证匿名不能读取、认证下载核对字节。失败不能显示成已验证。后台每日生成版本化备份；保留期限和清理生命周期另在私有桶配置。

```bash
python3 -m iptv_qa library backup --config /etc/iptv-qa/config.json
python3 -m iptv_qa library restore --config /etc/iptv-qa/config.json \
  --snapshot PRIVATE_BACKUP.fernet --destination NEW_RECOVERY_DIRECTORY/catalog.sqlite3
```

恢复仅写入不存在的新目标，验证认证、哈希、结构版本和数据库完整性。切换恢复后的总表前须停止写入者，并保留原数据库及配置供回退。解密密钥另存一份，否则ECS完全损坏后只有备份也无法恢复。数据库和备份均不能通过公开HTTP托管或提交Git。
