"""`foxclaw skills install` 的来源解析与落地。

全部离线:远端用 monkeypatch 替掉 `_http_get`,不打真网 —— 否则 CI 会因 ClawHub
抖动而红,这类测试一旦不稳就会被跳过,等于没测。
"""
from __future__ import annotations

import io
import json
import tarfile
import zipfile

import pytest

from foxclaw.skills import fetch as fetch_mod
from foxclaw.skills.archive import PackageError, has_code, read_package

MD = "---\nname: Demo Skill\ndescription: 演示用技能\nversion: 1.2.3\n---\n\n正文指示"


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def _tgz(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text in files.items():
            raw = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            t.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def _mock_http(monkeypatch, routes: dict) -> None:
    def fake(url: str, timeout: float = 25.0, max_bytes: int = 10 * 1024 * 1024):
        for key, val in routes.items():
            if key in url:
                return val
        raise PackageError(f"测试未配置这条路由: {url}")

    monkeypatch.setattr(fetch_mod, "_http_get", fake)


def test_read_zip_root_skill_md():
    pkg = read_package(_zip({"SKILL.md": MD, "_meta.json": '{"slug": "demo"}'}))
    assert pkg["skill_md"] == MD
    assert pkg["meta"]["slug"] == "demo"


def test_read_targz_picks_shallowest():
    pkg = read_package(_tgz({
        "repo-abc/SKILL.md": MD,
        "repo-abc/docs/SKILL.md": "更深的同名副本应被忽略",
    }))
    assert pkg["skill_md"] == MD


def test_read_package_rejects_traversal():
    with pytest.raises(PackageError):
        read_package(_zip({"../evil/SKILL.md": MD}))


def test_read_package_without_skill_md():
    with pytest.raises(PackageError, match="未找到 SKILL.md"):
        read_package(_zip({"README.md": "nothing here"}))


def test_subpath_narrows_search():
    with pytest.raises(PackageError):
        read_package(_tgz({"repo/a/SKILL.md": MD}), subpath="other")


def test_has_code_detects_scripts():
    assert has_code(["SKILL.md", "scripts/run.py"])
    assert has_code(["SKILL.md", "tool.sh"])
    assert not has_code(["SKILL.md", "skill-card.md"])


def test_ref_routes_scoped_name_to_clawhub(monkeypatch):
    seen: dict = {}

    def fake(owner, slug, timeout):
        seen["id"] = (owner, slug)
        return fetch_mod.SkillPackage(skill_md=MD, source="clawhub", origin="x")

    monkeypatch.setattr(fetch_mod, "_from_clawhub", fake)
    assert fetch_mod.fetch("@snowfox/demo").origin == "x"
    assert seen["id"] == ("snowfox", "demo")
    fetch_mod.fetch("snowfox/demo")
    assert seen["id"] == ("snowfox", "demo")


def test_unknown_ref_rejected():
    with pytest.raises(PackageError, match="无法识别"):
        fetch_mod.fetch("no-such-thing-xyz")


def test_local_dir_reads_skill_md(tmp_path):
    (tmp_path / "SKILL.md").write_text(MD, encoding="utf-8")
    pkg = fetch_mod.fetch(str(tmp_path))
    assert pkg.source == "local"
    assert "演示用技能" in pkg.skill_md


def test_local_dir_without_skill_md(tmp_path):
    with pytest.raises(PackageError):
        fetch_mod.fetch(str(tmp_path))


def test_clawhub_blocks_malware(monkeypatch):
    _mock_http(monkeypatch, {
        "/api/v1/skills/": (json.dumps({
            "moderation": {"isMalwareBlocked": True, "summary": "窃取密钥"},
            "latestVersion": {"version": "1.0.0"},
        }).encode(), "application/json"),
    })
    with pytest.raises(fetch_mod.MalwareBlocked):
        fetch_mod.fetch("@bad/skill")


def test_clawhub_github_handoff(monkeypatch):
    """download 返回 JSON 交接(GitHub 托底)时要改下 archiveUrl,而不是当 zip 解。"""
    _mock_http(monkeypatch, {
        "/api/v1/skills/": (json.dumps({
            "moderation": {"isSuspicious": True},
            "latestVersion": {"version": "2.0.0"},
        }).encode(), "application/json"),
        "/api/v1/download": (json.dumps({
            "archiveUrl": "https://codeload.example/archive.tar.gz",
        }).encode(), "application/json"),
        "archive.tar.gz": (_tgz({"repo-main/SKILL.md": MD}), "application/gzip"),
    })
    pkg = fetch_mod.fetch("@snowfox/demo")
    assert pkg.version == "2.0.0"
    assert pkg.suspicious is True
    assert "repo-main/SKILL.md" in pkg.files


def test_clawhub_not_found(monkeypatch):
    def boom(url, timeout=25.0, max_bytes=10 * 1024 * 1024):
        raise fetch_mod.HttpError(404, url, "Not found")

    monkeypatch.setattr(fetch_mod, "_http_get", boom)
    with pytest.raises(PackageError, match="找不到"):
        fetch_mod.fetch("@ghost/missing")


def test_github_subpath(monkeypatch):
    _mock_http(monkeypatch, {
        "codeload.github.com": (_tgz({
            "repo-main/skills/demo/SKILL.md": MD,
            "repo-main/skills/other/SKILL.md": "x",
        }), "application/gzip"),
    })
    pkg = fetch_mod.fetch("github:snowfox/repo/skills/demo@main")
    assert pkg.source == "github"
    assert pkg.skill_md == MD
    assert pkg.origin == "github:snowfox/repo/skills/demo@main"


def _src(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_text(MD, encoding="utf-8")
    return src


def test_cli_install_writes_file_when_service_down(tmp_path, monkeypatch):
    from foxclaw import cli
    from foxclaw.skills import install_cmd

    data = tmp_path / "data"
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(data))
    monkeypatch.setattr(install_cmd, "_api_alive", lambda base: False)

    assert cli.main(["skills", "install", str(_src(tmp_path)), "--yes"]) == 0
    saved = json.loads((data / "markdown_skills.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in saved["skills"]] == ["demo_skill"]
    assert saved["skills"][0]["version"] == "1.2.3"


def test_cli_install_respects_explicit_id(tmp_path, monkeypatch):
    from foxclaw import cli
    from foxclaw.skills import install_cmd

    data = tmp_path / "data"
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(data))
    monkeypatch.setattr(install_cmd, "_api_alive", lambda base: False)

    assert cli.main(["skills", "install", str(_src(tmp_path)), "--yes",
                     "--id", "my_demo"]) == 0
    saved = json.loads((data / "markdown_skills.json").read_text(encoding="utf-8"))
    assert saved["skills"][0]["id"] == "my_demo"


def test_cli_install_refuses_without_confirmation(tmp_path, monkeypatch):
    from foxclaw import cli
    from foxclaw.skills import install_cmd

    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(install_cmd, "_api_alive", lambda base: False)
    monkeypatch.setattr(install_cmd, "_confirm", lambda: False)

    assert cli.main(["skills", "install", str(_src(tmp_path))]) == 1
    assert not (tmp_path / "data" / "markdown_skills.json").exists()


def test_cli_list_reads_file_when_service_down(tmp_path, monkeypatch, capsys):
    from foxclaw import cli
    from foxclaw.skills import install_cmd

    data = tmp_path / "data"
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(data))
    monkeypatch.setattr(install_cmd, "_api_alive", lambda base: False)
    cli.main(["skills", "install", str(_src(tmp_path)), "--yes"])
    capsys.readouterr()

    assert cli.main(["skills", "list", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["source"] == "file"
    assert got["skills"][0]["id"] == "demo_skill"


def test_cli_remove_deletes_skill(tmp_path, monkeypatch):
    from foxclaw import cli
    from foxclaw.skills import install_cmd

    data = tmp_path / "data"
    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(data))
    monkeypatch.setattr(install_cmd, "_api_alive", lambda base: False)
    cli.main(["skills", "install", str(_src(tmp_path)), "--yes"])

    assert cli.main(["skills", "remove", "demo_skill"]) == 0
    saved = json.loads((data / "markdown_skills.json").read_text(encoding="utf-8"))
    assert saved["skills"] == []


def test_cli_remove_unknown_id_fails(tmp_path, monkeypatch):
    from foxclaw import cli
    from foxclaw.skills import install_cmd

    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(install_cmd, "_api_alive", lambda base: False)
    assert cli.main(["skills", "remove", "nope"]) == 1


def test_cli_install_uses_api_when_service_alive(tmp_path, monkeypatch):
    """服务在跑时必须打 API:直接改 json 会被运行中注册表的下次 save 整份覆盖。"""
    from foxclaw import cli
    from foxclaw.skills import install_cmd

    monkeypatch.setenv("FOXCLAW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(install_cmd, "_api_alive", lambda base: True)
    calls: list = []

    def fake_json(base, method, path, payload=None):
        calls.append((method, path, payload))
        return {"created": True}

    monkeypatch.setattr(install_cmd, "_api_json", fake_json)

    assert cli.main(["skills", "install", str(_src(tmp_path)), "--yes"]) == 0
    method, path, payload = calls[0]
    assert (method, path) == ("POST", "/api/ahs/md-skills")
    assert payload["acknowledged"] is True
    assert payload["id"] == "demo_skill"
    assert "正文指示" in payload["content"]
    assert not (tmp_path / "data" / "markdown_skills.json").exists()


def test_local_zip_package(tmp_path):
    p = tmp_path / "demo.zip"
    p.write_bytes(_zip({"SKILL.md": MD, "_meta.json": '{"slug": "pack"}'}))
    pkg = fetch_mod.fetch(str(p))
    assert pkg.source == "local"
    assert pkg.meta["slug"] == "pack"
