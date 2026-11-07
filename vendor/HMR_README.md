# HMR v1.5 — Foxclaw Memory Runtime

持续认知运行时，为长期运行的 AI 系统而生。

## 安装

```bash
pip install pydantic numpy

# 推荐（更好的语义理解，选其一）
pip install openai              # OpenAI Embedding
pip install sentence-transformers  # 本地模型，无需 API Key
```

## 快速开始

```python
from hmr.core.hmr import HMR

hmr = HMR(storage_path="./my_project")

hmr.ingest("IPC 应使用异步消息队列", memory_type="concept", title="IPC 设计原则")
hmr.save_runtime_state(goal="设计调度器", plan=["研究", "设计", "实现"])

# 重启后完整恢复
hmr2 = HMR(storage_path="./my_project")
state = hmr2.restore_runtime_state()
result = hmr2.recall(query="调度器 IPC 设计")
```

## 目录结构

```
hmr/
├── core/
│   ├── models.py          数据结构
│   └── hmr.py             主引擎
├── engines/
│   ├── semantic.py        语义检索
│   ├── runtime_state.py   运行时状态持久化
│   ├── recall.py          主动召回
│   ├── temporal.py        SM-2 遗忘曲线
│   ├── jit_compiler.py    多步推理检索
│   ├── lifecycle.py       自动压缩/剪枝
│   └── scheduler.py       策略调度 + 热缓存
├── storage/
│   ├── memory_fs.py       文件系统存储
│   └── vector_store.py    向量索引（持久化）
└── graph/
    ├── cwg.py             认知工作图
    └── memory_graph.py    实体/因果图
```

## 文档

详见 `docs/` 目录：
- `USER_MANUAL_CN.md` — 中文操作手册
- `USER_MANUAL_EN.md` — English User Manual
- `BUGFIX_CN.md`      — v1.1 修复说明
- `ARCHITECTURE_CN.md`— 系统架构
- `API_CN.md`         — API 参考

## 许可证

MIT License
