# Foxclaw 手机版可行性评估

## 一、现状盘点（评估依据）

| 事实 | 证据 |
|---|---|
| 后端 FastAPI + WebSocket，API 与 UI 完全分离 | `foxclaw/api/__init__.py`，`vite.config.js` 代理 `/api` `/ws` → :8000 |
| 前端主界面 v3 是 **Vue 3 + Vite + Pinia 式 stores + 中英 i18n** | `frontend_src/package.json`（`foxclaw-deck-next`），23 个 `.vue` 组件 |
| 构建产物输出到 `frontend/v3/`，由 bootstrap 静态托管 | `vite.config.js` `outDir: '../frontend/v3'`；`foxclaw/api/bootstrap.py:72-114` |
| 当前是**桌面三栏布局**（LeftRail + Stage + Inspector） | `frontend_src/src/styles/base.css` |
| 唯一断点 `@media (max-width:1180px)` 只是窄桌面两栏化，**无触屏/手机设计** | `base.css:116` |
| 已有 4 条消息渠道（微信/飞书/Telegram/Discord）本身就是移动入口 | `docs/CHANNELS.md` |
| docs/ 中无任何移动端相关文档 | 全文搜索 0 命中 |

**关键结论：Python 后端一行都不用改。** 手机版是纯前端工作。

## 二、三条路线对比

### 路线 A：v3 响应式改造（推荐起步）
在现有 v3 上增加移动断点：LeftRail 收成底部 Tab / 汉堡抽屉，Inspector 面板改为全屏 Sheet，ChatDock 吸底。
- 优点：一套代码、一套构建，工作量最小；Pinia stores / i18n / WebSocket 层全部直接复用。
- 缺点：v3 面板很重（如 ToolsPanel 单组件 26KB），强行压缩到手机屏容易"能用不好用"。
- 工作量：约等于给 23 个组件补移动形态，估计 3–5 天。

### 路线 B：新增 `/m` 轻量移动端（体验最好）
在 `frontend_src` 内新建移动版入口（独立 html + 独立路由），复用 stores、API client、i18n，UI 从零按手机形态设计（对话流为主 + 简洁的记忆/任务视图）。
- 后端配套极小：`bootstrap.py` 的 `_frontend_dir()` / `/` 路由模式照抄一个 `/m` 分支即可；`tools/bundle_web.py` 打包清单加一行。
- 优点：桌面版零风险不动；手机端交互按触屏原生逻辑设计，体验上限高。
- 缺点：多一套 UI 要维护。
- 工作量：约 1–2 周（核心对话流 + 记忆查看 + 任务/日程）。

### 路线 C：微信小程序 / 原生 App
- 小程序：与项目已接入的微信渠道同生态，但微信小程序对 WebSocket、域名备案、审核有额外成本；且 `npm/` 分发壳（CLI 分发 Python 包）模式覆盖不了小程序。
- 原生 App（Capacitor 套壳 / React Native）：适合路线 B 成熟后再套壳分发，现在做属于过早。
- 结论：**现阶段不建议**，Web 手机版（A→B）是性价比最高的路径。

## 三、建议路径

1. **先做 A**：补移动断点（≤768px 主断点），保证手机打开 `/` 基本可用——低成本兜底。
2. **再做 B**：以"对话流为中心"重设计 `/m`，桌面 v3 只做管理台。这是长期正解，因为两个场景的使用心智本来就不同（管理 vs 随手问）。
3. **渠道补位**：日常轻交互继续走微信/Telegram 渠道，Web 手机版承载渠道做不到的（记忆可视、审批流、ThoughtChain 查看）。

## 四、无论选哪条都要做的清单

- [ ] 触控目标 ≥ 44px、去掉 hover 依赖交互
- [ ] 移动端软键盘弹出时的 ChatDock 高度处理（`visualViewport`）
- [ ] WebSocket 断线重连在移动网络切换下的健壮性（后端已有 idempotency guard，前端需配合）
- [ ] `npm/build.cjs` / `tools/bundle_web.py` 同步新增的构建产物
- [ ] `docs/RUN.md` 补手机访问方式（局域网 IP + 端口，或后续加反代）

> 注意：`bootstrap.py` 静态托管走的是 `frontend/`（源码仓）或包内 `foxclaw/web/`（wheel），发布链路（npm → python → frontend）改动后需三条链路同步验证。
