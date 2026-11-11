# 更新日志

从 0.1.0 首次公开到当前版本的变更记录，最新的排在最前面。

## 0.1.3 — 2026-11-11

界面双语化，并把「设置」放到该在的位置。

**界面**

- 新增 i18n 基建（`lib/i18n.js` + 中英字典），侧栏、菜单、对话、舞台、设置、提示文案统一走字典；缺词回退 key，不会渲染成空白。
- 设置面板新增「界面 → 语言」，中文 / English 即时切换，写入 `localStorage`（`foxclawLang`），刷新后保持。
- 日期与数字格式随语言切换（`zh-CN` / `en-US`），`<html lang>` 同步。
- 「设置」从「更多」弹层移到左栏底部常驻入口，带分隔线，不随会话列表滚动。
- 顶栏版本号改为读后端 `/api/system/info`，取不到时不再显示硬编码的 `v3.0`。

**文档**

- README 按仓库实际校准：智能体数量、渠道、摄取上限、分块几何、支持格式、内部链接全部逐条核对，并加测试防止再次漂移。

## 0.1.2 — 2026-11-10

版本号收口、技能安装成型。

- 版本号五处统一（`pyproject.toml` / `foxclaw/__init__.py` / `npm/package.json` / `npm/index.js` / `frontend_src/package.json`），并由 `tests/test_version_sync.py` 盯住，防 npm 与 PyPI 漂移。
- `foxclaw skills install` 支持 ClawHub / GitHub / URL / 本地包；工具面板可直接上传 `SKILL.md` 或 OpenClaw 包安装指令技能。
- 首次启动引导询问称呼，可在设置中修改；新增可选的新版本检查提示。
- `tools/sync_public.ps1` 改为直接构造公开树推远端，不再依赖本地克隆。
- 类型标注收口：mypy 从 645 降到 0，CI 改为棘轮门（只降不升）。
- 全仓剥离解释性注释，公开树下不再混入内部说明。

## 0.1.1 — 2026-11-09

只在 npm 通道补发，仓库内没有对应版本号的提交记录。

- npm 包排除 `docs/reports` 等内部文档与调试残留，自检命中即中止打包（0.1.1 曾把内部文档打进公开包）。
- `npm pack` 前先跑 `build.cjs`，修 GitHub Release 附件只有 2KB 空壳。
- 修 `pyproject` 缺 `readme`，PyPI 项目页不再没有项目描述。
- `requirements.txt` 改用 `uv pip compile --universal`，平台相关依赖带上 marker；CI 增加依赖审计门。
- 补 MIT 许可证与 metadata。

## 0.1.0 — 2026-11-06

首个公开版本，PyPI `foxclaw` 与 npm `foxclaw` 双通道。

- 运行时：FastAPI 服务 + `foxclaw` 命令行入口。
- 智能体：多智能体收敛为三（助手 / 办公 / 知识），其余能力下放为指令技能。
- 知识库（HKC）与记忆（HMR）以内嵌 `vendor/` 形式接入：知识可抽取、可检索，复习走 SM-2 排程。
- 治理层：策略门、审计哈希链、影子学习与断路器；导入路径白名单，技能副作用默认 fail-closed。
- 配置：从 `.env` 改为类型化读取，`foxclaw config check` 把「配置写错」从崩溃变成提示。
- 安全：堵 DNS rebinding、浏览器 SSRF、渠道陌生指令三个可利用面；渠道路径签名 + 动作一次性 nonce 确认。
- 链路：计划可见化、推理过程独立 SSE 事件、步骤级 checkpoint 断点续跑。
- 前端：Vue3 三栏渲染层，左栏功能入口常驻，右栏双态检视。

> 两个包都是**占位包**：`vendor/`（HKC / HMR）不入包，装完只能 import 骨架，完整运行时从仓库获取。详见 [docs/RELEASE.md](docs/RELEASE.md)。
