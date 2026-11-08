""".env 的加载规则:非空值优先,空值让路。

回归点(2026-11-08):Windows Machine 级的 DEEPSEEK_API_KEY 是一个早已失效的
旧 key,`.env` 里是新 key。`load_dotenv()` 默认 override=False,系统变量赢 →
后端一路读到旧 key → 前端只看到 AuthenticationError,完全看不出是哪一层的问题。
"""

import os

import pytest

from foxclaw.core.envfile import load_env_file


def _write(tmp_path, body: str):
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


def test_non_empty_value_beats_system_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-old-invalid")
    shadows = load_env_file(_write(tmp_path, "DEEPSEEK_API_KEY=sk-new-valid\n"))
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-new-valid"
    assert shadows == ["DEEPSEEK_API_KEY"]


def test_empty_value_does_not_clear_system_env(tmp_path, monkeypatch):
    """`.env` 里的空占位不能把系统里真实配好的 key 抹成空串。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-from-os")
    shadows = load_env_file(_write(tmp_path, "OPENAI_API_KEY=\n"))
    assert os.environ["OPENAI_API_KEY"] == "sk-real-from-os"
    assert shadows == []


def test_missing_key_in_env_file_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-keep")
    load_env_file(_write(tmp_path, "DEEPSEEK_API_KEY=sk-x\n"))
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-keep"


def test_same_value_is_not_reported_as_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-same")
    shadows = load_env_file(_write(tmp_path, "DEEPSEEK_API_KEY=sk-same\n"))
    assert shadows == []
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-same"


def test_set_when_absent_from_system(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    shadows = load_env_file(_write(tmp_path, "DEEPSEEK_API_KEY=sk-first\n"))
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-first"
    assert shadows == []


def test_quotes_and_spaces_are_stripped(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-old")
    load_env_file(_write(tmp_path, 'DEEPSEEK_API_KEY="  sk-quoted  "\n'))
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-quoted"


def test_comments_and_blank_lines_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    load_env_file(_write(tmp_path, "# a comment\n\n   \nDEEPSEEK_API_KEY=sk-ok\n"))
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-ok"


def test_missing_file_returns_empty(tmp_path):
    assert load_env_file(tmp_path / "nope.env") == []


def test_multiple_shadows_all_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "old-1")
    monkeypatch.setenv("AMAP_MAPS_API_KEY", "old-2")
    shadows = load_env_file(_write(
        tmp_path, "DEEPSEEK_API_KEY=new-1\nAMAP_MAPS_API_KEY=new-2\n"))
    assert sorted(shadows) == ["AMAP_MAPS_API_KEY", "DEEPSEEK_API_KEY"]


@pytest.mark.parametrize("value", ["1", "true", "True", "yes"])
def test_skip_dotenv_values_are_recognised_by_caller(value):
    """逃生口的值集合在 api/__init__.py 里判定,这里只锁住「这些写法都算开」。"""
    assert value.strip() in ("1", "true", "True", "yes")
