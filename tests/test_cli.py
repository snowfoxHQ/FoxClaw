"""`foxclaw` 命令行入口的最小回归。

不启动真实服务(uvicorn 不在测试里跑),只锁住入口名、参数默认值与版本输出——
这三样是发布到 PyPI/npm 后用户第一眼会碰到的契约。
"""

from __future__ import annotations

import pytest

from foxclaw import __version__
from foxclaw.cli import APP_TARGET, build_parser, main


def test_target_is_api_app() -> None:
    assert APP_TARGET == "foxclaw.api:app"


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "foxclaw" in out
    assert __version__ in out


def test_no_args_prints_hint(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert __version__ in out
    assert "foxclaw serve" in out


def test_serve_defaults() -> None:
    args = build_parser().parse_args(["serve"])
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.reload is False
    assert args.handler is not None


def test_serve_overrides() -> None:
    args = build_parser().parse_args(["serve", "--port", "9001", "--reload"])
    assert args.port == 9001
    assert args.reload is True
