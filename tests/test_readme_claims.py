"""README 与仓库实际的一致性巡检。

README 里的结构和数字曾经长期漂移:目录注释写「五智能体」而实际三个、
写「76 个测试文件」而实际 110、写「77 个依赖」而实际 96 条。
这类不一致第一眼就被识破,所以把可机器核对的部分钉在这里:
改了代码不改文档,这个文件会红。

刻意不钉的东西:测试文件个数、代码行数、依赖条目数——它们每次提交都在变,
写进 README 就是下一个「76」。需要真实数字时用命令现取,不抄文档。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README_EN = ROOT / "README.md"
README_CN = ROOT / "README_CN.md"

HKC_ROUTES = ROOT / "vendor" / "hkc" / "hkc_api" / "routes" / "knowledge.py"
KDE = ROOT / "vendor" / "hkc" / "hkc_kde" / "kde.py"
CHUNKER = ROOT / "vendor" / "hkc" / "hkc_kde" / "chunker.py"
EXTRACTOR = ROOT / "vendor" / "hkc" / "hkc_kde" / "extractor.py"


@pytest.fixture(scope="module")
def docs() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in (README_EN, README_CN)}


def _num(path: Path, pattern: str) -> int:
    m = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
    assert m, f"{path.relative_to(ROOT)} 中找不到模式 {pattern!r}"
    return int(m.group(1))


def test_agents_are_three(docs: dict[str, str]) -> None:
    impl = sorted(
        p.stem
        for p in (ROOT / "foxclaw" / "agents" / "impl").glob("*.py")
        if not p.stem.startswith("_")
    )
    assert impl == ["finance", "office", "supervisor"], f"实际智能体实现: {impl}"

    for name, text in docs.items():
        assert "五智能体" not in text, f"{name} 仍写着五智能体"
        assert "五个智能体" not in text, f"{name} 仍写着五个智能体"
        assert "five agents" not in text.lower(), f"{name} 仍写着 five agents"

    assert "Three agents" in docs["README.md"]
    assert "三个智能体" in docs["README_CN.md"]


def test_channels_are_four() -> None:
    for adapter in ("weixin", "feishu", "telegram", "discord"):
        assert (ROOT / "foxclaw" / "channels" / f"{adapter}.py").exists(), adapter
    assert (ROOT / "foxclaw" / "channels" / "dispatcher.py").exists()

    text = README_EN.read_text(encoding="utf-8")
    for brand in ("WeChat", "Feishu", "Telegram", "Discord"):
        assert brand in text, brand


def test_runtime_governance_modules_exist() -> None:
    for rel in (
        "foxclaw/core/idempotency.py",
        "foxclaw/core/recovery.py",
        "foxclaw/harness/loop_detection.py",
        "foxclaw/core/compaction.py",
        "foxclaw/core/steering_queue.py",
        "docs/GOVERNANCE.md",
    ):
        assert (ROOT / rel).exists(), rel


def test_ingest_limits_match_docs(docs: dict[str, str]) -> None:
    upload_mb = _num(HKC_ROUTES, r"MAX_UPLOAD_BYTES\s*=\s*(\d+)\s*\*\s*1024\s*\*\s*1024")
    assert upload_mb == 20, f"单文件上传上限已变为 {upload_mb}MB,README 写的 20MB 要改"

    assert _num(HKC_ROUTES, r"MAX_INGEST_CHARS\s*=\s*([\d_]+)") == 2_000_000
    assert _num(KDE, r"DEFAULT_MAX_CHUNKS_PER_DOC\s*=\s*(\d+)") == 800
    assert _num(EXTRACTOR, r'"HKC_EXTRACT_MAX_TOKENS",\s*"(\d+)"') == 8192

    for name, text in docs.items():
        assert "20 MB" in text or "20MB" in text, name
        assert "2,000,000" in text, name
        assert "800" in text, name
        assert "8192" in text, name


def test_chunk_geometry_matches_docs(docs: dict[str, str]) -> None:
    max_tokens = _num(CHUNKER, r"^MAX_TOKENS\s*=\s*(\d+)")
    overlap_tokens = _num(CHUNKER, r"^OVERLAP_TOKENS\s*=\s*(\d+)")
    m = re.search(r"^CHARS_PER_TOKEN\s*=\s*([\d.]+)", CHUNKER.read_text(encoding="utf-8"), re.M)
    assert m, "chunker.py 中找不到 CHARS_PER_TOKEN"
    chars_per_token = float(m.group(1))

    max_chars = int(max_tokens * chars_per_token)
    overlap_chars = int(overlap_tokens * chars_per_token)
    step = max_chars - overlap_chars

    assert (max_chars, overlap_chars, step) == (1792, 224, 1568), (
        f"分块几何已变为 {max_chars}/{overlap_chars}/{step},README 写的 1792/224/1568 要改"
    )
    for name, text in docs.items():
        for value in ("1792", "224", "1568"):
            assert value in text, f"{name} 未出现 {value}"


def test_supported_formats_match_docs(docs: dict[str, str]) -> None:
    src = HKC_ROUTES.read_text(encoding="utf-8")
    m = re.search(r"ALLOWED_SUFFIXES\s*=\s*\((.*?)\)", src, re.S)
    assert m, "knowledge.py 中找不到 ALLOWED_SUFFIXES"
    suffixes = re.findall(r'"(\.\w+)"', m.group(1))
    assert len(suffixes) == 10, f"支持格式已变为 {suffixes}"

    for name, text in docs.items():
        for suffix in suffixes:
            assert suffix in text, f"{name} 未列出支持格式 {suffix}"


def test_relative_links_exist(docs: dict[str, str]) -> None:
    for name, text in docs.items():
        for target in re.findall(r"\]\(([^)#:]+)\)", text):
            if target.startswith(("http", "mailto")):
                continue
            assert (ROOT / target).exists(), f"{name} 的链接指向不存在的 {target}"


def test_directory_tree_paths_exist() -> None:
    for rel in (
        "foxclaw/core",
        "foxclaw/kernel",
        "foxclaw/pipeline",
        "foxclaw/planning",
        "foxclaw/cognitive",
        "foxclaw/knowledge",
        "foxclaw/agents",
        "foxclaw/harness",
        "foxclaw/finance",
        "foxclaw/learning",
        "foxclaw/itinerary",
        "foxclaw/channels",
        "foxclaw/skills",
        "foxclaw/search",
        "foxclaw/browser",
        "foxclaw/capability",
        "foxclaw/governance",
        "vendor/hmr",
        "vendor/hkc",
        "examples",
        "tests",
        "docs",
    ):
        assert (ROOT / rel).is_dir(), f"目录树列出的 {rel} 不存在"


def test_referenced_skills_exist(docs: dict[str, str]) -> None:
    for skill in ("general-assistant", "schedule-itinerary"):
        assert (ROOT / "skills" / skill).is_dir(), skill
        assert skill in docs["README_CN.md"], skill
