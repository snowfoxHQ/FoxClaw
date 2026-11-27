# 安装与发布说明

## 安装渠道

| 渠道 | 命令 | 说明 |
|---|---|---|
| PyPI | `pip install foxclaw` | 后端运行时与前端界面 |
| npm | `npm i foxclaw` | 完整源码树，需自备 Python 运行环境及第三方依赖 |
| GitHub Release | 下载发布附件 | 包含上述产物 |

旧版本的包可能是占位包：内嵌的知识与记忆运行时不在包内，安装后只能导入骨架。遇到这种情况请从仓库获取完整运行时。0.1.4 起 PyPI 包已包含前端界面。

## 运行环境

- Python 3.11 及以上
- 第三方依赖（含 torch / faiss）需通过 `requirements.txt` 安装
- npm 包不携带这些依赖，需自行准备

## 安全提示

- 服务默认只监听本机。对外暴露前请设置访问密钥。
- 密钥通过环境变量或 `.env` 注入，不要写进镜像或提交到仓库。
- 数据目录建议持久化挂载。

## 从源码运行

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e . --no-deps
python -m uvicorn foxclaw.api:app --port 8000
```

详见 [RUN.md](RUN.md) / [RUN_CN.md](RUN_CN.md)。
