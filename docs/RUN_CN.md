# Foxclaw 运行指南（中文版）

## Windows + OneDrive 路径问题

如果在 Windows 上运行测试报 `FileNotFoundError: [WinError 2] ... test_data`，通常是项目被放在 OneDrive 接管的目录（文档/桌面/图片）。这些是重定向占位符，Python 无法在其中创建目录。把项目移到不受 OneDrive 管理的路径：

```powershell
Copy-Item -Recurse "<你的当前路径>\foxclaw" "C:\foxclaw"
cd C:\foxclaw
python tests/run_all.py
```

## 一、安装依赖

```bash
pip install -e .                # 依赖以 pyproject.toml 为唯一来源（推荐）
```

依赖说明：`cryptography` 用于飞书事件解密，`websockets` 用于 Discord，`httpx` 用于各渠道 HTTP 调用。未安装 `sentence-transformers` 时部分语义能力会自动降级，不会崩溃。

## 二、验证（无需 API key）

```bash
python tests/run_all.py         # 全量回归
python tests/test_e2e.py
python tests/test_api.py
```

这些测试在没有大模型 key 的情况下也能运行。测试基于 pytest，需 `pip install pytest`（或 `pip install -e ".[dev]"`）。

## 三、启动服务

```bash
export DEEPSEEK_API_KEY=sk-...         # 或 ANTHROPIC_API_KEY；Windows: $env:DEEPSEEK_API_KEY="sk-..."
export FOXCLAW_PROVIDER=deepseek        # 可选：显式指定 provider
export FOXCLAW_DATA_DIR=./foxclaw_data   # 数据目录

python -m uvicorn foxclaw.api:app --host 127.0.0.1 --port 8000
```

浏览器访问 http://localhost:8000 。

服务默认只监听本机。对外暴露必须同时设置 `FOXCLAW_HOST=0.0.0.0` 与 `FOXCLAW_API_KEY`；未设访问密钥时不要对非回环地址开放。

## 四、接入消息平台

四个主流渠道（微信/飞书/Telegram/Discord）各按官方规范实现，凭证由你本地注入。怎么拿 token、各渠道启动方式、多渠道并跑见 [CHANNELS.md](CHANNELS.md)。

## 五、运行时治理

幂等、崩溃恢复、循环检测等已自动接入，无需额外配置。详见 [GOVERNANCE.md](GOVERNANCE.md)。崩溃重启后，系统启动时会打印上次被中断的任务。

## 六、环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` | 大模型 key，不配则使用占位实现 |
| `FOXCLAW_PROVIDER` | 显式指定 provider（deepseek / anthropic …） |
| `FOXCLAW_DATA_DIR` | 数据目录，默认 `./foxclaw_data` |
| `FOXCLAW_API_KEY` | 访问密钥：设置后 API 与知识库接口需带 `X-API-Key`，WebSocket 用 `?key=`。不设则无鉴权，仅限本机访问 |
| `FOXCLAW_HOST` / `FOXCLAW_PORT` | 绑定地址与端口，默认 `127.0.0.1:8000` |
| `FOXCLAW_CORS_ORIGINS` | 允许的跨源来源，默认仅本机；显式设 `*` 才全开（不推荐） |
| `FOXCLAW_SKIP_DOTENV` | 设为 `1` 则不加载项目 `.env`，只使用进程环境变量。供测试/CI 使用，本地开发不要设 |
