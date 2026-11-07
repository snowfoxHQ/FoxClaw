"""配置校验(core/settings.py)的行为测试。

立这些档的目的是让"配置错时的提示"本身成为被测试的对象:它比崩溃更容易腐坏 ——
提示错了没人会发现,只会让用户在错误的方向上改。
"""
import json

import pytest

from foxclaw import cli
from foxclaw.core import settings


@pytest.fixture(autouse=True)
def _reset_warned(monkeypatch):
    # _warned 是进程级的"只喊一次"账本,测试之间必须清,否则 get_safe 的日志断言会串
    monkeypatch.setattr(settings, "_warned", set())


# ── 类型化读取 ───────────────────────────────────────────────────────────
def test_get_int_reads_value(monkeypatch):
    monkeypatch.setenv("FOXCLAW_PORT", "9001")
    assert settings.get_int("FOXCLAW_PORT") == 9001


def test_get_int_blank_means_default(monkeypatch):
    # `.env` 里写 `FOXCLAW_PORT=` 曾经会 int("") 崩掉;空串按未设置处理
    monkeypatch.setenv("FOXCLAW_PORT", "   ")
    assert settings.get_int("FOXCLAW_PORT") == 8000


def test_get_int_unset_means_default(monkeypatch):
    monkeypatch.delenv("FOXCLAW_PORT", raising=False)
    assert settings.get_int("FOXCLAW_PORT") == 8000


def test_get_int_bad_value_names_variable_and_caller(monkeypatch):
    monkeypatch.setenv("FOXCLAW_PORT", "80oo")
    with pytest.raises(settings.ConfigError) as ei:
        settings.get_int("FOXCLAW_PORT")
    msg = str(ei.value)
    assert "FOXCLAW_PORT" in msg and "80oo" in msg
    assert "期望" in msg and "8000" in msg
    # 调用点必须是本测试文件,否则用户还是不知道该去哪改
    assert "test_settings.py" in msg


def test_get_int_below_min(monkeypatch):
    monkeypatch.setenv("FOXCLAW_GATEWAY_RATE", "-5")
    with pytest.raises(settings.ConfigError):
        settings.get_int("FOXCLAW_GATEWAY_RATE")


def test_fractional_timeout_allowed(monkeypatch):
    # 工具超时可以小于 1 秒(tests/test_tool_parallel.py 就用 0.3 模拟慢工具)
    monkeypatch.setenv("FOXCLAW_TOOL_TIMEOUT", "0.3")
    assert settings.get_float("FOXCLAW_TOOL_TIMEOUT") == pytest.approx(0.3)


def test_get_float_accepts_int_text(monkeypatch):
    monkeypatch.setenv("FOXCLAW_LLM_COOLDOWN", "30")
    assert settings.get_float("FOXCLAW_LLM_COOLDOWN") == 30.0


def test_get_safe_degrades_and_warns_once(monkeypatch, caplog):
    monkeypatch.setenv("FOXCLAW_TRACE_SLOW_MS", "慢")
    with caplog.at_level("WARNING", logger="foxclaw.config"):
        assert settings.get_safe("FOXCLAW_TRACE_SLOW_MS") == 1000.0
        assert settings.get_safe("FOXCLAW_TRACE_SLOW_MS") == 1000.0   # 第二次不重复喊
    assert sum("FOXCLAW_TRACE_SLOW_MS" in r.message for r in caplog.records) == 1


def test_get_safe_unknown_name_returns_default(monkeypatch):
    monkeypatch.setenv("SOME_UNDECLARED_KNOB", "abc")
    assert settings.get_safe("SOME_UNDECLARED_KNOB", "dflt") == "abc"    # str 恒可解析
    assert settings.get_safe("NOT_SET_AT_ALL_X", 7) == 7


# ── check():一次汇总所有问题 ─────────────────────────────────────────────
def _clean(monkeypatch, *keys):
    """清掉一组变量,避免宿主机上真实存在的键干扰断言。"""
    for k in keys:
        monkeypatch.delenv(k, raising=False)


def test_check_flags_type_error_without_raising(monkeypatch):
    monkeypatch.setenv("FOXCLAW_PORT", "80oo")
    issues = settings.check({"FOXCLAW_PORT": "80oo", "DEEPSEEK_API_KEY": "x"})
    errs = [i for i in issues if i.level == "error"]
    assert len(errs) == 1 and errs[0].code == "type" and errs[0].name == "FOXCLAW_PORT"
    # 提示不该与消息重复(Exception 里已写"期望")
    assert errs[0].hint == ""


def test_check_collects_multiple_errors(monkeypatch):
    env = {"FOXCLAW_PORT": "x", "FOXCLAW_GATEWAY_RATE": "y",
           "DEEPSEEK_API_KEY": "k"}
    names = {i.name for i in settings.check(env) if i.level == "error"}
    assert {"FOXCLAW_PORT", "FOXCLAW_GATEWAY_RATE"} <= names


def test_check_choice_error(monkeypatch):
    issues = settings.check({"LLM_PROVIDER": "deepsek", "DEEPSEEK_API_KEY": "k"})
    codes = {(i.code, i.name) for i in issues if i.level == "error"}
    assert ("choice", "LLM_PROVIDER") in codes


def test_check_clean_env_has_no_error():
    assert [i for i in settings.check({"DEEPSEEK_API_KEY": "k"}) if i.level == "error"] == []


def test_check_secret_value_is_masked_in_message():
    # 密钥写错时不能把原文打进日志/终端
    issues = settings.check({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": " sk-live-1234567890 "})
    msg = " ".join(i.message for i in issues)
    assert "sk-live-1234567890" not in msg


# ── 语义检查:选了 provider 却没 key ────────────────────────────────────
def test_provider_without_key_is_error():
    issues = settings.check({"LLM_PROVIDER": "deepseek", "ANTHROPIC_API_KEY": ""})
    hit = [i for i in issues if i.code == "provider_key"]
    assert hit and hit[0].level == "error" and hit[0].name == "DEEPSEEK_API_KEY"


def test_provider_with_key_is_fine():
    issues = settings.check({"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "k"})
    assert not [i for i in issues if i.code == "provider_key"]


def test_legacy_provider_name_also_checked():
    issues = settings.check({"FOXCLAW_PROVIDER": "openai"})
    assert [i for i in issues if i.code == "provider_key"]


def test_no_key_at_all_is_warn_not_error():
    issues = settings.check({})
    hit = [i for i in issues if i.code == "no_llm"]
    assert hit and hit[0].level == "warn"


# ── 未知键:拼错的键必须被点名 + 给出正确写法 ────────────────────────────
def test_typo_key_is_flagged_with_suggestion():
    issues = settings.check({"DEEPSEEK_API_KEY": "k", "FOXCLAW_TOOL_TIMEOUTS": "60"})
    hit = [i for i in issues if i.name == "FOXCLAW_TOOL_TIMEOUTS"]
    assert hit and hit[0].code == "unknown_key"
    assert "FOXCLAW_TOOL_TIMEOUT" in hit[0].hint


def test_known_key_is_not_flagged():
    issues = settings.check({"FOXCLAW_TOOL_TIMEOUT": "60", "DEEPSEEK_API_KEY": "k"})
    assert not [i for i in issues if i.name == "FOXCLAW_TOOL_TIMEOUT"]


def test_system_env_vars_are_not_flagged():
    # 不是我们的命名空间就不该管(PATH/HOME 之类)
    issues = settings.check({"PATH": "/usr/bin", "DEEPSEEK_API_KEY": "k"})
    assert not [i for i in issues if i.name == "PATH"]


def test_known_keys_covers_real_usage():
    known = settings.known_keys()
    for name in ("FOXCLAW_PORT", "FOXCLAW_TOOL_TIMEOUT", "DEEPSEEK_API_KEY",
                 "FOXCLAW_CHANNEL_SIGNING_SECRET"):
        assert name in known
    assert "NOT_A_REAL_KEY_NAME" not in known


# ── 组合矛盾 ────────────────────────────────────────────────────────────
def test_signing_key_id_without_secret_warns():
    issues = settings.check({"DEEPSEEK_API_KEY": "k",
                             "FOXCLAW_CHANNEL_SIGNING_KEY_ID": "k2"})
    assert [i for i in issues if i.code == "signing_pair"]


def test_short_signing_secret_warns():
    issues = settings.check({"DEEPSEEK_API_KEY": "k",
                             "FOXCLAW_CHANNEL_SIGNING_SECRET": "short"})
    assert [i for i in issues if i.code == "weak_secret"]


def test_open_cors_without_api_key_warns():
    issues = settings.check({"DEEPSEEK_API_KEY": "k", "FOXCLAW_CORS_ORIGINS": "*"})
    assert [i for i in issues if i.code == "open_cors"]


def test_gateway_off_with_api_key_warns():
    issues = settings.check({"DEEPSEEK_API_KEY": "k", "FOXCLAW_API_KEY": "s3cret",
                             "FOXCLAW_GATEWAY": "0"})
    assert [i for i in issues if i.code == "gateway_off"]


# ── 输出 / 脱敏 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("value,expect", [("", "(空)"), ("abcd", "****"),
                                          ("sk-1234567890", "sk******90")])
def test_mask(value, expect):
    assert settings.mask(value) == expect


def test_is_secret_heuristics():
    assert settings.is_secret("DEEPSEEK_API_KEY")
    assert settings.is_secret("SOME_TOKEN_X")
    assert settings.is_secret("AMAP_JS_SCODE")
    assert not settings.is_secret("FOXCLAW_PORT")


def test_format_issues_counts_and_limit():
    issues = [settings.Issue("error", "type", f"K{i}", "m") for i in range(5)]
    out = settings.format_issues(issues, limit=2)
    assert "5 个错误" in out and "另有 3 条" in out


def test_format_issues_clean():
    assert "通过" in settings.format_issues([])


def test_fatal_issues_only_type_and_choice():
    issues = [settings.Issue("error", "type", "A", "m"),
              settings.Issue("error", "provider_key", "B", "m"),
              settings.Issue("warn", "type", "C", "m")]
    assert [i.name for i in settings.fatal_issues(issues)] == ["A"]


# ── CLI ─────────────────────────────────────────────────────────────────
def test_cli_config_check_exit_code_on_error(monkeypatch, capsys):
    monkeypatch.setenv("FOXCLAW_PORT", "80oo")
    assert cli.main(["config", "check"]) == 1
    out = capsys.readouterr().out
    assert "FOXCLAW_PORT" in out and "80oo" in out


def test_cli_config_check_clean_exit_zero(monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.delenv("FOXCLAW_PORT", raising=False)
    assert cli.main(["config", "check"]) == 0
    assert "配置检查" in capsys.readouterr().out


def test_cli_config_check_json(monkeypatch, capsys):
    monkeypatch.setenv("FOXCLAW_PORT", "80oo")
    cli.main(["config", "check", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and data[0]["name"] == "FOXCLAW_PORT"


def test_cli_config_without_action_is_usage_error(capsys):
    assert cli.main(["config"]) == 2
    assert "config check" in capsys.readouterr().out


def test_cli_config_show_masks_secrets(monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abcdef123456")
    cli.main(["config", "show"])
    out = capsys.readouterr().out
    assert "sk-abcdef123456" not in out and "DEEPSEEK_API_KEY" in out


def test_cli_config_show_reveal(monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abcdef123456")
    cli.main(["config", "show", "--reveal"])
    assert "sk-abcdef123456" in capsys.readouterr().out


def test_serve_aborts_before_starting_on_bad_type(monkeypatch):
    # 关键:拦在 uvicorn 之前 —— 否则用户看到的是 40 行 traceback 里的 int()
    monkeypatch.setenv("FOXCLAW_PORT", "80oo")
    args = cli.build_parser().parse_args(["serve"])
    assert cli._cmd_serve(args) == 2


def test_serve_semantic_warning_does_not_block(monkeypatch):
    monkeypatch.setenv("FOXCLAW_CORS_ORIGINS", "*")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    args = cli.build_parser().parse_args(["serve"])
    # 走到 uvicorn.run 之前就返回,说明没有 fatal(用假 uvicorn 防止真的起服务)
    import sys
    import types
    fake = types.ModuleType("uvicorn")
    fake.run = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    assert cli._cmd_serve(args) == 0
