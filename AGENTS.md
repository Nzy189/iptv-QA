# Working on iptv-QA

1. Read `PROGRESS.md` first. It is the continuation checklist and records what is implemented, verified, pending and published.
2. Read `docs/DESIGN.md` / `docs/DESIGN.zh-CN.md` before changing detection rules. Read `docs/DEPLOYMENT.md` before changing scheduling or OBS behavior.
3. If present, `.local-context.md` contains private operational paths for this checkout. It is ignored by Git. Never copy its contents, credentials, actual subscriptions or runtime playlists into a public commit.
4. Update `PROGRESS.md` after meaningful work and before handoff. State actual evidence and commands; mark plans separately from released capabilities. Do not claim a release, push, OBS update or full validation until verified.
5. Preserve the existing production checker while testing a new release in an isolated directory. A public repository update does not automatically deploy to a running server.
6. Core tests: `python3 -m unittest discover -s tests -v`. Real-media tests: `python3 scripts/validate_media.py --bin-dir PATH` and `python3 scripts/validate_quality.py --bin-dir PATH`. Publication audit: `python3 scripts/check_publication.py`.
7. Documents are bilingual. Keep `README.md` and `README.zh-CN.md`, and the paired design/deployment/source/testing pages consistent. Preserve language switch links.
8. Before publishing, audit only the intended source tree and archive. Exclude private configurations, keys, raw sources, databases, JSONL, generated subscriptions and binary dependencies.

中文：每次先读 `PROGRESS.md`；必要时读被 Git 忽略的 `.local-context.md`。进度必须区分已实现、已验证、待办和已发布。修改后同步更新中英文文档，不影响正在运行的私有部署。

9. Keep public-facing documentation product-oriented. Do not include individual deployment location, hardware profiles or private operational incident logs. Explicitly approved public demo output links may appear in README; raw private subscriptions and credentials must remain excluded.
