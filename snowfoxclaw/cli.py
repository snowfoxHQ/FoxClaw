"""命令行入口 `foxclaw`。

与 PyPI 上 `snowfoxclaw` 0.1.0 占位包发布的入口保持一致：包名 `snowfoxclaw`,
命令名 `foxclaw`。

    foxclaw            # 打印版本与提示
    foxclaw serve      # 启动 API 服务(默认 http://127.0.0.1:8000)
    foxclaw --version  # 打印版本

`serve` 用字符串目标 `snowfoxclaw.api:app` 交给 uvicorn 导入,避免 CLI 进程
自己先把整个应用栈加载一遍(含 HKC/HMR 与模型);`--reload` 时 uvicorn 也需要
字符串目标才能重建子进程。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snowfoxclaw import __version__

APP_TARGET = "snowfoxclaw.api:app"


def _repo_root() -> Path:
    """仓库根目录(可编辑安装时即包含 snowfoxclaw/ 的那一层)。"""
    return Path(__file__).resolve().parents[1]


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    kwargs: dict[str, object] = {
        "host": args.host,
        "port": args.port,
        "log_level": args.log_level,
    }
    if args.reload:
        # 只监听源码目录,避免 vendor/ 与测试临时文件触发重启
        kwargs["reload"] = True
        kwargs["reload_dirs"] = [str(_repo_root() / "snowfoxclaw")]
    uvicorn.run(APP_TARGET, **kwargs)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foxclaw",
        description="Snowfoxclaw —— 认知 Agent 操作系统",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"snowfoxclaw {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    serve = subparsers.add_parser("serve", help="启动 API 服务")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址,默认仅本机回环")
    serve.add_argument("--port", type=int, default=8000, help="监听端口,默认 8000")
    serve.add_argument("--reload", action="store_true", help="改代码自动重启(开发用)")
    serve.add_argument("--log-level", default="info", help="uvicorn 日志级别")
    serve.set_defaults(handler=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        print(f"snowfoxclaw {__version__}")
        print("用 `foxclaw serve` 启动本地服务;`foxclaw --help` 查看全部子命令。")
        return 0
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
