"""发布前校验脚本(tools/release_check.py)的规则测试。

锁住三件事:
1. 四处版本号必须一致 —— 只改一处的发布物会让用户装到对不上的版本;
2. tag 必须指向本次版本 —— 发布不可撤回,yank 也仍占号;
3. 产物里不能有 .env / docs/internal / 运行期数据,缺 vendor 只算提示(占位包现状)。

脚本放在 tools/ 不在包里,故按文件路径加载;测试只构造最小归档,不跑真构建(秒级)。
"""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "release_check", REPO / "tools" / "release_check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_check"] = mod
    spec.loader.exec_module(mod)
    return mod


rc = _load()


def _wheel(dist: Path, version="0.1.0", name="foxclaw", extra=(),
           description=True) -> Path:
    p = dist / f"{name}-{version}-py3-none-any.whl"
    body = ("\n\n" + "long description body. " * 12) if description else ""
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(f"{name}/__init__.py", "__version__ = '0.1.0'\n")
        z.writestr(f"{name}/cli.py", "def main(): pass\n")
        z.writestr(f"{name}-{version}.dist-info/METADATA",
                   f"Name: {name}\nVersion: {version}{body}\n")
        for e in extra:
            z.writestr(e, "x")
    return p


def _sdist(dist: Path, version="0.1.0", name="foxclaw", extra=()) -> Path:
    p = dist / f"{name}-{version}.tar.gz"
    with tarfile.open(p, "w:gz") as t:
        for arc in (f"{name}-{version}/foxclaw/cli.py",
                    f"{name}-{version}/PKG-INFO", *extra):
            data = b"x"
            info = tarfile.TarInfo(arc)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return p


def _tgz(dist: Path, version="0.1.0", name="foxclaw", pkg=None, extra=()) -> Path:
    p = dist / f"{name}-{version}.tgz"
    payload = json.dumps(pkg if pkg is not None else
                         {"name": name, "version": version}).encode()
    with tarfile.open(p, "w:gz") as t:
        for arc, data in (("package/package.json", payload),
                          ("package/index.js", b"export default {}"),
                          *[(e, b"x") for e in extra]):
            info = tarfile.TarInfo(arc)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return p


def _mini_repo(root: Path, version: str, npm_version: str) -> Path:
    """只含四处版本来源的最小仓库,用来测"漏改一处"。"""
    (root / "foxclaw").mkdir(parents=True)
    (root / "npm").mkdir()
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "foxclaw"\nversion = "{version}"\n', encoding="utf-8")
    (root / "foxclaw" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8")
    (root / "npm" / "package.json").write_text(
        json.dumps({"name": "foxclaw", "version": npm_version}), encoding="utf-8")
    (root / "npm" / "index.js").write_text(
        f'export const version = "{npm_version}";\n', encoding="utf-8")
    return root


def test_repo_versions_are_consistent():
    """真仓库当前状态:四处一致,无 error。"""
    assert [i for i in rc.check_versions(REPO) if i.level == "error"] == []


def test_single_missed_source_is_an_error(tmp_path):
    root = _mini_repo(tmp_path, "0.2.0", npm_version="0.1.0")
    errs = [i for i in rc.check_versions(root) if i.level == "error"]
    assert errs and any("npm" in i.msg for i in errs), errs


def test_unreadable_source_is_an_error(tmp_path):
    root = _mini_repo(tmp_path, "0.1.0", "0.1.0")
    (root / "npm" / "index.js").write_text("// 没有导出\n", encoding="utf-8")
    assert any(i.code == "unreadable" for i in rc.check_versions(root))


@pytest.mark.parametrize("tag", ["v0.1.0", "0.1.0"])
def test_tag_matching_version_passes(tag):
    assert rc.check_tag(tag, "0.1.0") == []


@pytest.mark.parametrize("tag", ["v0.2.0", "v0.1.0-rc1"])
def test_tag_drift_is_an_error(tag):
    errs = rc.check_tag(tag, "0.1.0")
    assert errs and errs[0].code == "tag_mismatch"


def test_empty_tag_is_skipped():
    assert rc.check_tag("", "0.1.0") == []


def test_repo_declares_readme():
    """真仓库必须声明正文:0.1.1 在 PyPI 上的项目页就是空白的,这就是回归点。"""
    assert [i for i in rc.check_metadata(REPO) if i.level == "error"] == []
    assert rc.readme_target(REPO).endswith(".md")


def test_missing_readme_is_error(tmp_path):
    root = _mini_repo(tmp_path, "0.1.0", "0.1.0")
    assert any(i.code == "no_readme" for i in rc.check_metadata(root))


def test_short_readme_is_error(tmp_path):
    root = _mini_repo(tmp_path, "0.1.0", "0.1.0")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "foxclaw"\nversion = "0.1.0"\nreadme = "README.md"\n',
        encoding="utf-8")
    (root / "README.md").write_text("短\n", encoding="utf-8")
    assert any(i.code == "readme_too_short" for i in rc.check_metadata(root))


def test_readme_pointing_to_missing_file_is_error(tmp_path):
    root = _mini_repo(tmp_path, "0.1.0", "0.1.0")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "foxclaw"\nversion = "0.1.0"\nreadme = "NOPE.md"\n',
        encoding="utf-8")
    assert any(i.code == "readme_missing" for i in rc.check_metadata(root))


@pytest.mark.parametrize("entry", [
    "foxclaw/.env",
    "foxclaw/.env.local",
    "foxclaw-0.1.0/docs/internal/secret.md",
    "foxclaw/foxclaw_data/sessions.db",
    "foxclaw/old.zip",
    "foxclaw/__pycache__/x.pyc",
    "foxclaw/.venv/lib/x.py",
])
def test_forbidden_entries_detected(entry):
    assert rc.forbidden_entry(entry), entry


@pytest.mark.parametrize("entry", [
    "foxclaw/.env.example",
    "foxclaw/cli.py",
    "foxclaw/vendor/hmr/hmr/__init__.py",
])
def test_allowed_entries_pass(entry):
    assert rc.forbidden_entry(entry) is None


def test_check_dist_passes_with_vendor_warning_only(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist)
    _sdist(dist)
    issues = rc.check_dist(dist, "foxclaw", "0.1.0")
    assert [i for i in issues if i.level == "error"] == []
    assert any(i.code == "no_vendor" for i in issues), "缺 vendor 应显式提示"


def test_check_dist_flags_missing_wheel(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _sdist(dist)
    assert any(i.code == "wheel_count" for i in rc.check_dist(dist, "foxclaw", "0.1.0"))


def test_check_dist_flags_version_drift_in_filename(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist, version="0.2.0")
    _sdist(dist, version="0.1.0")
    codes = {i.code for i in rc.check_dist(dist, "foxclaw", "0.1.0")}
    assert "wheel_name" in codes or "meta_mismatch" in codes, codes


def test_check_dist_flags_secret_inside_archive(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist, extra=(".env",))
    _sdist(dist)
    msgs = [i.msg for i in rc.check_dist(dist, "foxclaw", "0.1.0")
            if i.code == "forbidden_entry"]
    assert msgs and any(".env" in m for m in msgs), msgs


def test_check_dist_flags_empty_long_description(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist, description=False)
    _sdist(dist)
    codes = {i.code for i in rc.check_dist(dist, "foxclaw", "0.1.0")}
    assert "no_long_description" in codes, codes


def test_check_dist_missing_dir_is_error(tmp_path):
    assert any(i.code == "no_dist"
               for i in rc.check_dist(tmp_path / "nope", "foxclaw", "0.1.0"))


def test_npm_tgz_ok(tmp_path):
    assert rc.check_npm_tgz(_tgz(tmp_path), "foxclaw", "0.1.0") == []


def test_npm_tgz_name_drift_detected(tmp_path):
    tgz = _tgz(tmp_path, pkg={"name": "foxclaw", "version": "0.2.0"})
    codes = {i.code for i in rc.check_npm_tgz(tgz, "foxclaw", "0.1.0")}
    assert "npm_meta" in codes, codes


def test_npm_tgz_filename_drift_detected(tmp_path):
    tgz = _tgz(tmp_path, version="0.2.0")
    assert any(i.code == "npm_name" for i in rc.check_npm_tgz(tgz, "foxclaw", "0.1.0"))


def test_run_end_to_end(tmp_path):
    """run() 串起来:真仓库 + 构造的 dist/tgz,无 tag 也应通过。"""
    dist = tmp_path / "dist"
    dist.mkdir()
    issues = rc.run(REPO, tag="", dist=dist, npm_tgz=None)
    assert any(i.level == "error" for i in issues), "空 dist 应报错"
    version = rc.collect_versions(REPO)["pyproject.toml:project.version"]
    _wheel(dist, version=version)
    _sdist(dist, version=version)
    tgz = _tgz(dist, version=version)
    assert [i for i in rc.run(REPO, f"v{version}", dist, tgz) if i.level == "error"] == []
    shutil.rmtree(dist)
