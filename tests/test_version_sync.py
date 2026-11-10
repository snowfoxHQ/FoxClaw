"""版本号单一来源校验。

版本号目前硬编码在五个地方，任何一处漏改都会造成「本地是 0.2.0、npm 上
还是 0.1.0」这类对外不一致（`npm/index.js` 的 version 是发布物里用户
`import` 直接读到的值，最容易漏）：

- ``pyproject.toml`` 的 ``project.version``        —— PyPI 包版本
- ``foxclaw/__init__.py`` 的 ``__version__``   —— 运行时 ``foxclaw --version``
- ``npm/package.json`` 的 ``version``               —— npm 包版本
- ``npm/index.js`` 的 ``export const version``      —— npm 包对外导出的版本
- ``frontend_src/package.json`` 的 ``version``     —— 前端构建工程版本

本测试只做「五处必须相等」，不校验具体数值，发版时改哪一处都不用动它。
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from foxclaw import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
NPM_DIR = REPO_ROOT / "npm"


def _project_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _project_name() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["name"]


def _npm_package() -> dict:
    return json.loads((NPM_DIR / "package.json").read_text(encoding="utf-8"))


def test_python_versions_agree() -> None:
    """pyproject 与运行时 __version__ 一致。"""
    assert __version__ == _project_version()


def test_npm_package_version_agrees() -> None:
    """npm/package.json 与 PyPI 侧版本一致。"""
    assert _npm_package()["version"] == _project_version()


def test_npm_index_export_version_agrees() -> None:
    """npm/index.js 导出值（发布物对外可见）与 package.json 一致。"""
    source = (NPM_DIR / "index.js").read_text(encoding="utf-8")
    match = re.search(r'export\s+const\s+version\s*=\s*["\']([^"\']+)["\']', source)
    assert match, "npm/index.js 里找不到 `export const version = \"...\"`"
    assert match.group(1) == _npm_package()["version"]


def test_frontend_package_version_agrees() -> None:
    """frontend_src/package.json 与 PyPI 侧版本一致。

    这个工程是 ``private`` 的构建工程,不发布,版本号曾长期停在 3.0.0
    (Vue3 渲染层代号),与后端 0.1.2 对不上,容易误读成产品版本。
    """
    data = json.loads(
        (REPO_ROOT / "frontend_src" / "package.json").read_text(encoding="utf-8"))
    assert data["version"] == _project_version()


def test_package_names_are_foxclaw() -> None:
    """PyPI 与 npm 两侧包名必须同名，且为 foxclaw。"""
    assert _project_name() == "foxclaw"
    assert _npm_package()["name"] == _project_name()


def test_npm_index_export_name_agrees() -> None:
    """npm/index.js 导出的 name 与 package.json 一致。"""
    source = (NPM_DIR / "index.js").read_text(encoding="utf-8")
    match = re.search(r'export\s+const\s+name\s*=\s*["\']([^"\']+)["\']', source)
    assert match, "npm/index.js 里找不到 `export const name = \"...\"`"
    assert match.group(1) == _npm_package()["name"]


def test_frontend_does_not_hardcode_version() -> None:
    """顶部栏版本号必须来自后端,不能在 Vue 里写死。

    2026-11-10:左上角硬编码 ``v3.0``,而后端已发到 0.1.2,用户看到的版本
    与仓库/PyPI 对不上。取值链路是 ``stores/system.js`` 请求
    ``/api/system/info``,TopBar 只读 ``system.version``。
    """
    topbar = (REPO_ROOT / "frontend_src" / "src" / "components" / "TopBar.vue").read_text(
        encoding="utf-8")
    assert not re.search(r"\bv\d+\.\d+", topbar), "TopBar.vue 里出现了硬编码版本号"
    assert "system.version" in topbar, "TopBar.vue 应从 system store 读 version"

    store = (REPO_ROOT / "frontend_src" / "src" / "stores" / "system.js").read_text(
        encoding="utf-8")
    assert "/system/info" in store, "system store 应从 /api/system/info 取 version"
