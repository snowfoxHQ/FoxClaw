# syntax=docker/dockerfile:1

# Foxclaw — 认知智能体操作系统
#
# 一个镜像两种用法：
#   1) 跑服务：docker run -p 8000:8000 foxclaw
#   2) 跑全量回归（构建/CI 自检）：docker run --rm foxclaw python tests/run_all.py
#
# 镜像较大（>2GB）是 sentence-transformers/torch 的代价——语义检索与治理漂移检测
# 都依赖真实 embedding，这是能力而非可裁剪项。测试锁 HF_HUB_OFFLINE 离线读预热缓存。

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface

WORKDIR /app

# 系统依赖：torch 需要 libgomp1（OpenMP 运行时）。slim 镜像默认缺失，
# 缺了会在 import torch 时报 libgomp.so.1 找不到。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ── 依赖层（利用 Docker layer 缓存：改源码不重装 torch）──────────────
# 必须拷 foxclaw/ 与 vendor/ 才能 editable install（HMR/HKC 是 vendor 内嵌、
# 走 sys.path 加载，非 PyPI 依赖，见 pyproject.toml 注记）。
COPY pyproject.toml ./
COPY foxclaw/ foxclaw/
COPY vendor/ vendor/

# 依赖以 pyproject.toml 为唯一来源；dev extra 补 pytest/ruff/mypy/coverage +
# openpyxl/python-docx（默认回归 test_file_ingest/test_domain_import 硬依赖），
# 使镜像既能跑服务又能跑全量回归（与 CI 对齐）。
# 【关键】先装 CPU 版 torch：PyPI 默认 torch 在 Linux 上会拖入整套 CUDA toolkit
# （nvidia-* 十几个包 + cuda-toolkit，镜像 +4GB、下载极慢），而 Foxclaw 语义检索
# 只需 CPU。先从 PyTorch 官方 CPU index 装好 torch，sentence-transformers 见
# 约束已满足即不再重装 CUDA 版。
# --mount=type=cache 复用 pip 下载缓存：改依赖后重建无需重下 torch 等大 wheel
# --retries/--timeout：国内网络抖动时 pip 默认重试(5次/15s)偏短,会把 filelock 等
# 依赖的 index 元数据请求超时误判为"无匹配发行版"(ResolutionImpossible),放宽后更稳。
RUN --mount=type=cache,target=/root/.cache/pip pip install --upgrade pip \
    && pip install --retries 8 --timeout 60 torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --retries 8 --timeout 60 -e ".[dev]"

# ── 预热默认 embedding（all-MiniLM-L6-v2）────────────────────────────
# run_all.py 内部注入 HF_HUB_OFFLINE=1 离线读取。若镜像内没有缓存，
# sentence-transformers 会降级为 n-gram 哈希嵌入——对中文几乎无区分度，
# 语义漂移分恒为 1.0 → 治理测试全红（见 .github/workflows/ci.yml 的教训）。
# 故在 build 期一次性下载好，运行期离线可用、不依赖外网。
# 【HF_ENDPOINT】默认走 hf-mirror.com（国内直连 huggingface.co 常被墙、DNS 解析失败）；
# 海外/CI 可 `docker build --build-arg HF_ENDPOINT= ...` 覆盖，或留空走官方 HuggingFace。
ARG HF_ENDPOINT=https://hf-mirror.com
RUN HF_ENDPOINT="${HF_ENDPOINT}" python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# ── 其余仓库内容（frontend 前端静态 / skills / docs 等）──────────────
COPY . .

EXPOSE 8000

# 默认启动 API 服务；跑回归用：docker run --rm foxclaw python tests/run_all.py
CMD ["python", "-m", "uvicorn", "foxclaw.api:app", "--host", "0.0.0.0", "--port", "8000"]
