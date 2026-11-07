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

# ── HKC vendor 隔离 ────────────────────────────────────────────────────────────
# IDE/Cursor 等 shim 常把 `C:\GitHub\foxclaw-hkc\src` 注入到 sys.path 第一位,
# 而那是上游低质量代码、跟本项目 vendor/hkc/ 是不同等级。**测试必须打 vendor 真代码**,
# 否则会出现 tests/test_vendor_path_isolation.py 里那类「绿了但打错对象」的事故。
# 把 vendor/hkc 强制插到 sys.path[0](即使 shim 在前也覆盖之),后续 `import hkc_*`
# 全部解析到本仓库 vendor 副本。foxclaw.knowledge 自身也调 _ensure_hkc_importable()
# 做同样事,但那只在 import foxclaw.knowledge 之后才生效;本测试文件顶层直接
# import hkc_search 早于它,故在 conftest 阶段就注入更稳。
_VENDOR_HKC = _REPO_ROOT / "vendor" / "hkc"
if _VENDOR_HKC.exists() and str(_VENDOR_HKC) not in sys.path:
    sys.path.insert(0, str(_VENDOR_HKC))

# ── 规避 pytest 在 Windows + Python 3.14 下的 junction 清理 bug ────────────────
# pytest 用 Path.symlink_to() 创建 `pytest-current` / `test_*current` 目录 junction
# 作为「指向最新临时目录」的便利链接。Windows 会把这类 junction 标记为
# 「不受信任的装入点(untrusted mount point)」,Python 3.14 的 ntpath.realpath 在
# pytest_sessionfinish 的 cleanup_dead_symlinks 里对它调用 Path.resolve() 时抛
# WinError 448,并令 pytest 退出码变 1(测试本身全通过)。该 junction 仅作调试便利,
# 不影响任何测试功能,故在 Windows 上禁用其创建(必须在 basetemp 建立前生效,
# 而 conftest 的模块级代码早于 basetemp 创建执行,时序安全)。
if sys.platform == "win32":
    import _pytest.pathlib as _ppl

    def _skip_force_symlink(root, target, link_to):
        return None

    _ppl._force_symlink = _skip_force_symlink

# ── 环境隔离(与 run_all.py 的 env 注入保持一致,强制覆盖继承环境)──────────────
# FOXCLAW_SKIP_DOTENV=1:让 api.py 完全不读项目根 .env,防止真实 Key 灌回 / 写真实数据目录。
os.environ["FOXCLAW_SKIP_DOTENV"] = "1"
# 锁 shadow 轮数、stub 嵌入、关 MCP、清 provider/model:回归保持离线/免费/确定性。
os.environ["FOXCLAW_SHADOW_ROUNDS"] = "50"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HKC_EMBEDDING"] = "stub"
os.environ["FOXCLAW_MCP_SERVER"] = "0"
os.environ["FOXCLAW_PROVIDER"] = ""
os.environ["FOXCLAW_MODEL"] = ""
# 清空外部 API key(第二道防线):鉴权/网关测试要自行控制 FOXCLAW_API_KEY。
for _k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
           "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "FOXCLAW_API_KEY"):
    os.environ[_k] = ""

# 兜底:为未自设 FOXCLAW_DATA_DIR 的测试提供一个临时目录,避免 KeyError。
# (各测试文件顶层通常会自行 mkdtemp 覆盖此值,此处仅作安全网。)
os.environ.setdefault("FOXCLAW_DATA_DIR", tempfile.mkdtemp(prefix="foxclaw_test_"))


@pytest.fixture
def test_dir(tmp_path) -> str:
    """字符串路径的临时目录,替代脚本式测试里的 tempfile.mkdtemp。

    注意不能直接用 pytest 内置 `tmpdir`(旧式 py.path.local,在 Windows + Python 3.14
    下会触发 WinError 448),故返回 str(tmp_path) 供 `Path()` / `tempfile.mkdtemp(dir=...)`
    等接受字符串的调用点使用。
    """
    return str(tmp_path)
