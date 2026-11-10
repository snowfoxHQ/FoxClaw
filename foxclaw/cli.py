"""命令行入口 `foxclaw`。

与 PyPI 上 `foxclaw` 0.1.0 占位包发布的入口保持一致：包名 `foxclaw`,
命令名 `foxclaw`。

    foxclaw                # 打印版本与提示
    foxclaw serve          # 启动 API 服务(默认 http://127.0.0.1:8000)
    foxclaw config check   # 检查 .env / 环境变量:类型写错、拼错的键、互相矛盾的组合
    foxclaw config show     # 打印当前生效配置(密钥默认脱敏)
    foxclaw skills install @owner/slug   # 装 SKILL.md 技能(ClawHub/GitHub/URL/本地包)
    foxclaw skills list                  # 列出已装技能
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

    issues = settings.check()
    fatal = settings.fatal_issues(issues)
    if fatal:
        print(settings.format_issues(issues), file=sys.stderr)
        print("\n以上配置会让启动直接失败,已中止。改好后重试。", file=sys.stderr)
        return 2
    if issues:
        print(settings.format_issues(issues, limit=8), file=sys.stderr)
        print("(以上不阻断启动;`foxclaw config check` 看完整诊断)", file=sys.stderr)

    import uvicorn

    uvicorn.run(
        APP_TARGET,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=bool(args.reload),
        reload_dirs=[str(_repo_root() / "foxclaw")] if args.reload else None,
    )
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


def _cmd_skills(args: argparse.Namespace) -> int:
    from foxclaw.skills.install_cmd import run

    return run(args)


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

    skills = subparsers.add_parser("skills", help="安装/查看/卸载指令技能(SKILL.md)")
    skills_sub = skills.add_subparsers(dest="skills_action", metavar="<action>")

    s_install = skills_sub.add_parser("install", help="从 ClawHub/GitHub/URL/本地包装一个技能")
    s_install.add_argument(
        "ref",
        help="@owner/slug(ClawHub) | github:owner/repo[/子路径][@ref] | "
             "http(s)://包地址 | 本地 .zip/.tar.gz/SKILL.md/目录")
    s_install.add_argument("--yes", action="store_true",
                           help="跳过确认(脚本/CI 必填;非终端环境没有它一定失败)")
    s_install.add_argument("--id", default="", help="覆盖技能 id(默认取 SKILL.md 里的 name)")
    s_install.add_argument("--json", action="store_true", help="输出 JSON")
    s_install.set_defaults(handler=_cmd_skills)

    s_list = skills_sub.add_parser("list", help="列出已安装的指令技能")
    s_list.add_argument("--json", action="store_true", help="输出 JSON")
    s_list.set_defaults(handler=_cmd_skills)

    s_remove = skills_sub.add_parser("remove", help="卸载一个指令技能")
    s_remove.add_argument("id", help="技能 id(`foxclaw skills list` 第一列)")
    s_remove.set_defaults(handler=_cmd_skills)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        if getattr(args, "command", None) == "config":
            print("用法:`foxclaw config check` 或 `foxclaw config show`")
            return 2
        if getattr(args, "command", None) == "skills":
            print("用法:`foxclaw skills install <ref>` / `foxclaw skills list` / "
                  "`foxclaw skills remove <id>`")
            return 2
        print(f"foxclaw {__version__}")
        print("用 `foxclaw serve` 启动本地服务;`foxclaw config check` 检查配置;"
              "`foxclaw --help` 查看全部子命令。")
        return 0
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
