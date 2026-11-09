"""pytest 共享配置:sys.path 注入 + 环境隔离(等价于 run_all.py 的 env 注入)。

关键点:env 必须在**模块级**设置(而非 fixture)。因为 pytest 收集阶段就会 import
每个测试文件,若 env 晚于 import 设置,测试文件顶层的 `Foxclaw(...)` /
`TestClient(app)` 会读到未隔离的环境(真实 Key 打外部 API、读真实数据目录等)。
conftest.py 由 pytest 在收集前加载,故模块级赋值能保证先于任何测试文件 import 生效。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_VENDOR_HKC = _REPO_ROOT / "vendor" / "hkc"
if _VENDOR_HKC.exists() and str(_VENDOR_HKC) not in sys.path:
    sys.path.insert(0, str(_VENDOR_HKC))

if sys.platform == "win32":
    import _pytest.pathlib as _ppl

    def _skip_force_symlink(root, target, link_to):
        return None

    _ppl._force_symlink = _skip_force_symlink

os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
os.environ["FOXCLAW_SHADOW_ROUNDS"] = "50"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HKC_EMBEDDING"] = "stub"
os.environ["FOXCLAW_MCP_SERVER"] = "0"
os.environ["FOXCLAW_PROVIDER"] = ""
os.environ["FOXCLAW_MODEL"] = ""
for _k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
           "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "FOXCLAW_API_KEY"):
    os.environ[_k] = ""

os.environ.setdefault("FOXCLAW_DATA_DIR", tempfile.mkdtemp(prefix="foxclaw_test_"))


@pytest.fixture
def test_dir(tmp_path) -> str:
    """字符串路径的临时目录,替代脚本式测试里的 tempfile.mkdtemp。

    注意不能直接用 pytest 内置 `tmpdir`(旧式 py.path.local,在 Windows + Python 3.14
    下会触发 WinError 448),故返回 str(tmp_path) 供 `Path()` / `tempfile.mkdtemp(dir=...)`
    等接受字符串的调用点使用。
    """
    return str(tmp_path)
