"""命令行入口 `foxclaw`。

与 PyPI 上 `foxclaw` 0.1.0 占位包发布的入口保持一致：包名 `foxclaw`,
命令名 `foxclaw`。

    foxclaw                # 打印版本与提示
    foxclaw serve          # 启动 API 服务(默认 http://127.0.0.1:8000)
    foxclaw config check   # 检查 .env / 环境变量:类型写错、拼错的键、互相矛盾的组合
    foxclaw config show     # 打印当前生效配置(密钥默认脱敏)
    foxclaw --version      # 打印版本

`serve` 用字符串目标 `foxclaw.api:app` 交给 uvicorn 导入,避免 CLI 进程
自己先把整个应用栈加载一遍(含 HKC/HMR 与模型);`--reload` 时 uvicorn 也需要
字符串目标才能重建子进程。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from foxclaw import __version__

APP_TARGET = "foxclaw.api:app"


def _repo_root() -> Path:
    """仓库根目录(可编辑安装时即包含 foxclaw/ 的那一层)。"""
    return Path(__file__).resolve().parents[1]


def _cmd_serve(args: argparse.Namespace) -> int:
    from foxclaw.core import settings

    # 启动前先查配置:类型写错的值今天会崩在很远的地方(uvicorn 40 行 traceback 中间的
    # `int()`),用户看不出是自己 .env 写错了。这里提前拦下并说清怎么改。
    issues = settings.check()
    fatal = settings.fatal_issues(issues)
    if fatal:
        print(settings.format_issues(issues), file=sys.stderr)
        print("\n以上配置会让启动直接失败,已中止。改好后重试。", file=sys.stderr)
        return 2
    if issues:
        # 不影响启动的问题(可疑但能跑)只提醒一次,不拦
        print(settings.format_issues(issues, limit=8), file=sys.stderr)
        print("(以上不阻断启动;`foxclaw config check` 看完整诊断)", file=sys.stderr)

    import uvicorn

    kwargs: dict[str, object] = {
        "host": args.host,
        "port": args.port,
        "log_level": args.log_level,
    }
    if args.reload:
        # 只监听源码目录,避免 vendor/ 与测试临时文件触发重启
        kwargs["reload"] = True
        kwargs["reload_dirs"] = [str(_repo_root() / "foxclaw")]
    uvicorn.run(APP_TARGET, **kwargs)
    return 0


def _cmd_config_check(args: argparse.Namespace) -> int:
    from foxclaw.core import settings

    issues = settings.check()
    if args.json:
        import json as _json
        print(_json.dumps(
            [{"level": i.level, "code": i.code, "name": i.name,
              "message": i.message, "hint": i.hint} for i in issues],
            ensure_ascii=False, indent=2))
    else:
        print(settings.format_issues(issues))
    return 1 if any(i.level == "error" for i in issues) else 0


def _cmd_config_show(args: argparse.Namespace) -> int:
    from foxclaw.core import settings

    current = ""
    for opt in settings.SPECS:
        if opt.group != current:
            current = opt.group
            print(f"[{current}]")
        raw = os.environ.get(opt.name)
        if raw is None or raw.strip() == "":
            shown = f"{settings.mask(opt.default) if opt.secret else opt.default}" \
                if opt.default not in ("", None) else "(未设置)"
            suffix = "  默认"
        else:
            shown = settings.mask(raw) if (opt.secret and not args.reveal) else raw
            suffix = "  已设置"
        print(f"  {opt.name} = {shown}{suffix}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foxclaw",
        description="Foxclaw —— 认知 Agent 操作系统",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"foxclaw {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    serve = subparsers.add_parser("serve", help="启动 API 服务")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址,默认仅本机回环")
    serve.add_argument("--port", type=int, default=8000, help="监听端口,默认 8000")
    serve.add_argument("--reload", action="store_true", help="改代码自动重启(开发用)")
    serve.add_argument("--log-level", default="info", help="uvicorn 日志级别")
    serve.set_defaults(handler=_cmd_serve)

    config = subparsers.add_parser("config", help="查看/检查配置(.env 与环境变量)")
    config_sub = config.add_subparsers(dest="config_action", metavar="<action>")

    c_check = config_sub.add_parser(
        "check", help="检查配置:类型、取值范围、拼错的键、互相矛盾的组合")
    c_check.add_argument("--json", action="store_true", help="输出 JSON(便于脚本/前端消费)")
    c_check.set_defaults(handler=_cmd_config_check)

    c_show = config_sub.add_parser("show", help="打印当前生效配置")
    c_show.add_argument("--reveal", action="store_true",
                        help="显示密钥原文(默认脱敏;别在截图/日志里用)")
    c_show.set_defaults(handler=_cmd_config_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        if getattr(args, "command", None) == "config":
            print("用法:`foxclaw config check` 或 `foxclaw config show`")
            return 2
        print(f"foxclaw {__version__}")
        print("用 `foxclaw serve` 启动本地服务;`foxclaw config check` 检查配置;"
              "`foxclaw --help` 查看全部子命令。")
        return 0
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
