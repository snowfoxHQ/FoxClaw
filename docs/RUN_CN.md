# Hestia OS 运行指南(中文版)

## ⚠ Windows + OneDrive 路径问题(最常见的坑)

如果在 Windows 上运行测试报 `FileNotFoundError: [WinError 2] ... test_data`:

**原因**:项目被放在 OneDrive 接管的目录(文档/桌面/图片),这些是 OneDrive 重定向占位符,Python 无法在其中创建目录。**典型症状**:PowerShell 能 `cd` 进去,但 Python 建子目录失败。这和代码无关。

**解法**:移到不受 OneDrive 管理的路径:

```powershell
Copy-Item -Recurse "<你的当前路径>\hestia_os" "C:\hestia_os"
cd C:\hestia_os
python tests/run_all.py
```

## 一、安装依赖

```bash
pip install fastapi uvicorn "httpx<0.28" openai cryptography websockets pyyaml numpy scikit-learn rich pydantic
# Windows 上 pip 装系统包可能需要 --break-system-packages

# 可选(更精确语义检测,约 800MB,不装则降级 n-gram):
pip install sentence-transformers
```

依赖说明:`cryptography` 用于飞书加密事件解密;`websockets` 用于 Discord Gateway;`httpx` 用于各渠道 HTTP 调用与 Web 工具。未装 `sentence-transformers` 时线束语义检测自动降级为 n-gram 哈希,不会崩溃。

## 二、验证(无需 API key)

```bash
# 全量回归(应 35/35 通过)
python tests/run_all.py

# 单独验证认知运行时 / API 层
python tests/test_e2e.py
python tests/test_api.py
```

这些测试在没有大模型 key、没有真实 HMR 的情况下也能跑 —— 证明认知运行时与大模型解耦。

## 三、启动服务

```bash
# 配 key 则真实调模型,不配则降级 stub(其他功能照常)
export DEEPSEEK_API_KEY=sk-...         # 或 ANTHROPIC_API_KEY
                                       # Windows: $env:DEEPSEEK_API_KEY="sk-..."
export HESTIA_PROVIDER=deepseek        # 可选:显式指定 provider
export HESTIA_DATA_DIR=./hestia_data   # 数据目录

python -m uvicorn hestia.api:app --host 0.0.0.0 --port 8000
```

浏览器访问 http://localhost:8000 。

## 四、接入消息平台

四个主流渠道(微信/飞书/Telegram/Discord)各按官方规范实现,凭证由你本地注入。完整配置(怎么拿 token、各渠道启动方式、多渠道并跑)见 [CHANNELS.md](CHANNELS.md)。最简验证(Telegram,@BotFather 拿 token):

```python
from hestia import HestiaOS
from hestia.channels.dispatcher import ChannelDispatcher
from hestia.channels.telegram import TelegramAdapter
import time

os_ = HestiaOS(data_dir="./data")
disp = ChannelDispatcher(os_.supervisor)
tg = TelegramAdapter(bot_token="你的token")
disp.register_channel(tg)
while True:
    tg.poll_once()   # 收消息 → 主管处理 → 自动回复
    time.sleep(1)
```

## 五、运行时治理

幂等/崩溃恢复/循环检测等已自动接入,无需额外配置。详见 [GOVERNANCE.md](GOVERNANCE.md)。崩溃重启后,系统启动时会打印上次被中断的任务。

## 六、关于内嵌的 HMR

HMR v2.0 已内嵌在 `vendor/hmr/`,启动自动接入,记忆落盘到数据目录的 `hmr/` 子目录。替换为新版 HMR 只需 API 兼容地替换 `vendor/hmr/`,适配层(`hestia/core/memory_adapter.py`)和主体不用改。

## 七、主要 API 端点

| 端点 | 说明 |
|------|------|
| `GET  /api/status` | 五个智能体状态 |
| `POST /api/task` | 执行任务 `{task, agent_id}` |
| `GET  /api/thoughtchain/{agent}/chain` | 推理链 |
| `GET  /api/thoughtchain/{agent}/summary` | 推理链摘要 |
| `GET  /api/harness/{agent}/status` | 线束阈值/权重/策略 |
| `GET  /api/harness/{agent}/policy` | 完整策略 YAML |
| `WS   /ws/harness` | 实时线束事件流 |

(完整 35 个端点见 `hestia/api.py`,含财务/学习/行程内核与 AHS 相关接口。)

## 八、环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` | 大模型 key,不配则 stub |
| `HESTIA_PROVIDER` | 显式指定 provider(deepseek/anthropic…) |
| `HESTIA_DATA_DIR` | 数据目录 |
| `HESTIA_SHADOW_ROUNDS` | 影子校准轮数 |
